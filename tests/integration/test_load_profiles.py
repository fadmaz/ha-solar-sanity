"""Exercise shared saved loads through native actions and the entry lifecycle."""

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError, Unauthorized

from custom_components.solar_sanity.analysis.forecast import Correction
from custom_components.solar_sanity.analysis.profile import ForecastScale
from custom_components.solar_sanity.const import CONF_CHANNELS
from tests.integration.test_load_planning import _solar, planning_providers  # noqa: F401
from tests.integration.test_load_reminders import (
    action,
    clock,  # noqa: F401
    install_notify,
    mock_owner,
    schedule,
    setup,
)
from tests.integration.test_simulation_service import NOW, invoke
from tests.integration.test_simulation_service import setup as setup_simulation

PUMP = {"name": "Heat pump", "power_w": 820, "duration_minutes": 45}
CLOCK = "custom_components.solar_sanity.services.dt_util.utcnow"


@pytest.fixture
def hass_config_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def hass_storage():
    """Use Core's disk writes and read-back inside the temporary config directory."""
    return {}


@pytest.fixture
def reader(hass):
    with mock_owner(hass) as user:
        yield user


async def _list(hass, entry, **extra):
    return await action(hass, entry, "list_load_profiles", **extra)


async def _save(hass, entry, revision=0, **extra):
    return await action(hass, entry, "save_load_profile", revision=revision, **{**PUMP, **extra})


async def _delete(hass, entry, saved, **extra):
    return await action(
        hass,
        entry,
        "delete_load_profile",
        **{"revision": saved["revision"], "profile_id": saved["profiles"][0]["id"], **extra},
    )


async def test_saved_loads_separate_devices_and_survive_reload(
    hass, enable_custom_integrations, entry_data, reader
):
    """Editing keeps the ID and reloading restores the shared list from disk."""
    first, _, _ = await setup_simulation(hass, entry_data)
    other = {
        **entry_data,
        CONF_CHANNELS: [
            {**row, "entity_id": row["entity_id"].replace("sensor.", "sensor.other_")}
            for row in entry_data[CONF_CHANNELS]
        ],
    }
    second, _, _ = await setup_simulation(hass, other)
    assert (await _list(hass, first)) == {"profiles": [], "revision": 0, "storage_error": False}
    created = await _save(hass, first, name="  Heat pump  ")
    profile = created["profiles"][0]
    assert profile == {"id": profile["id"], **PUMP}
    assert (await _list(hass, second))["profiles"] == []
    independent = await _save(hass, second, power_w=0, duration_minutes=1440)
    with pytest.raises(ServiceValidationError, match="does not exist"):
        await _delete(hass, second, created)
    updated = await _save(hass, first, 1, profile_id=profile["id"], name="Heat pump eco")
    assert updated["profiles"][0]["id"] == profile["id"]
    assert await hass.config_entries.async_reload(first.entry_id)
    assert await _list(hass, first) == updated
    assert await _list(hass, second) == independent
    path = Path(first.runtime_data.coordinator.load_profiles._store.path)
    assert (await _delete(hass, first, updated))["profiles"] == []
    await hass.config_entries.async_remove(first.entry_id)
    assert not await hass.async_add_executor_job(path.exists)
    assert await _list(hass, second) == independent


async def test_shared_loads_require_installation_read_and_active_admin_writes(
    hass, enable_custom_integrations, entry_data, reader
):
    """A non-admin reader may reuse profiles, while only active admins can edit them."""
    entry, _, _ = await setup_simulation(hass, entry_data)
    saved = await _save(hass, entry)
    reader.is_admin = False
    assert await _list(hass, entry) == saved
    with pytest.raises(Unauthorized):
        await _save(hass, entry, 1, name="Dryer")
    with pytest.raises(Unauthorized):
        await _delete(hass, entry, saved)
    reader.permissions.check_entity.return_value = False
    with pytest.raises(Unauthorized):
        await _list(hass, entry)
    reader.is_admin = True
    with pytest.raises(Unauthorized):
        await _delete(hass, entry, saved)
    reader.permissions.check_entity.return_value = True
    reader.is_active = False
    with pytest.raises(Unauthorized):
        await _delete(hass, entry, saved)
    with pytest.raises(Unauthorized):
        await _save(hass, entry, 1, context=Context())


async def test_saved_load_validation_and_per_installation_limit(
    hass, enable_custom_integrations, entry_data, reader
):
    """Names are trimmed and deduplicated, and the load follows the planner's validation."""
    entry, _, _ = await setup_simulation(hass, entry_data)
    for invalid in (
        {"name": " \t"},
        {"name": "x" * 61},
        {"power_w": True},
        {"power_w": -1},
        {"power_w": "inf"},
        {"duration_minutes": 0},
        {"duration_minutes": 1.5},
        {"duration_minutes": 1441},
        {"revision": True},
    ):
        with pytest.raises((vol.Invalid, ServiceValidationError)):
            await _save(hass, entry, **invalid)
    await _save(hass, entry, name="Straße")
    with pytest.raises(ServiceValidationError, match="uses this name"):
        await _save(hass, entry, 1, name=" STRASSE ")
    for revision in range(1, 20):
        await _save(hass, entry, revision, name=f"Load {revision}")
    with pytest.raises(ServiceValidationError, match="20 saved loads"):
        await _save(hass, entry, 20)
    roster = await _list(hass, entry)
    with pytest.raises(ServiceValidationError, match="changed"):
        await _delete(hass, entry, roster, revision=19)
    assert roster["revision"] == len(roster["profiles"]) == 20


async def test_saved_load_edits_are_serialized_and_durable(
    hass, enable_custom_integrations, entry_data, reader
):
    """Two editors using the same revision cannot both write successfully."""
    entry, _, _ = await setup_simulation(hass, entry_data)
    saved = await _save(hass, entry)
    profile_id = saved["profiles"][0]["id"]
    results = await asyncio.gather(
        _save(hass, entry, 1, profile_id=profile_id, power_w=600),
        _delete(hass, entry, saved),
        return_exceptions=True,
    )
    rejected = [result for result in results if isinstance(result, ServiceValidationError)]
    assert len(rejected) == 1 and "changed" in str(rejected[0])
    accepted = next(result for result in results if isinstance(result, dict))
    assert accepted["revision"] == 2
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert await _list(hass, entry) == accepted


async def test_reload_waits_for_an_admitted_profile_write(
    hass, enable_custom_integrations, entry_data, reader
):
    """The replacement coordinator restores the revision acknowledged by the old one."""
    entry, journal, _ = await setup_simulation(hass, entry_data)
    profiles = journal.coordinator.load_profiles
    started, quiescing, resume = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_save = profiles._store.async_save
    original_quiesce = profiles.async_quiesce

    async def pause_write(payload):
        started.set()
        await resume.wait()
        await original_save(payload)

    async def quiesce():
        quiescing.set()
        await original_quiesce()

    with (
        patch.object(profiles._store, "async_save", side_effect=pause_write),
        patch.object(profiles, "async_quiesce", side_effect=quiesce),
    ):
        write = hass.async_create_task(_save(hass, entry))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            reload = hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))
            await asyncio.wait_for(quiescing.wait(), timeout=5)
            assert not reload.done()
            assert profiles.view()["profiles"] == []
        finally:
            resume.set()
        accepted = await write
        assert await reload
    assert await _list(hass, entry) == accepted


async def test_undurable_write_does_not_publish_and_manual_simulation_continues(
    hass, enable_custom_integrations, entry_data, reader
):
    """Core may report a completed save without changing the backing file."""
    entry, journal, _ = await setup_simulation(hass, entry_data)
    saved = await _save(hass, entry)
    store = journal.coordinator.load_profiles._store
    with (
        patch.object(store, "async_save", new=AsyncMock()),
        pytest.raises(ServiceValidationError, match="Could not save"),
    ):
        await _delete(hass, entry, saved)
    assert await _list(hass, entry) == {"profiles": [], "revision": 1, "storage_error": True}
    assert await store.async_load() == {key: saved[key] for key in ("profiles", "revision")}
    with patch(CLOCK, return_value=NOW):
        assert (await invoke(hass, entry))["additional_energy_kwh"] == 1


@pytest.mark.parametrize("content", [None, "{broken JSON", "null"])
async def test_an_unrecognized_store_is_preserved(
    hass, enable_custom_integrations, entry_data, reader, content
):
    """Even a future minor version or corrupted file stays untouched after reload."""
    entry, journal, _ = await setup_simulation(hass, entry_data)
    await _save(hass, entry)
    store = journal.coordinator.load_profiles._store
    path = Path(store.path)
    if content is None:
        content = json.dumps(
            {
                "version": 1,
                "minor_version": 2,
                "key": store.key,
                "data": {"revision": 1, "profiles": []},
            }
        )
    await hass.async_add_executor_job(path.write_bytes, content.encode())
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert (await _list(hass, entry))["storage_error"]
    with pytest.raises(ServiceValidationError, match="storage is unavailable"):
        await _save(hass, entry)
    assert await hass.async_add_executor_job(path.read_bytes) == content.encode()


@pytest.mark.usefixtures("clock")
async def test_editing_or_deleting_a_saved_load_preserves_an_existing_reminder(
    hass, enable_custom_integrations, entry_data
):
    """Both battery calculations and the reminder use the copied power and duration."""
    entry, journal, state = await setup(hass, entry_data)
    state.scale = ForecastScale(1, 30, 5)
    install_notify(hass, journal.coordinator.reminders)
    saved = await _save(hass, entry)
    values = {key: saved["profiles"][0][key] for key in ("power_w", "duration_minutes")}
    manual = {"power_w": 820, "duration_minutes": 45}
    deadline = (NOW + timedelta(hours=3)).isoformat()
    assert await invoke(hass, entry, **values) == await invoke(hass, entry, **manual)
    assert await invoke(hass, entry, action="find_best_time", finish_by=deadline, **values) == (
        await invoke(hass, entry, action="find_best_time", finish_by=deadline, **manual)
    )
    reminder = await schedule(hass, entry, **values)
    updated = await _save(hass, entry, 1, profile_id=saved["profiles"][0]["id"], power_w=600)
    await _delete(hass, entry, updated)
    assert (await action(hass, entry, "get_load_reminder"))["reminder"] == reminder


async def test_solar_only_planning_accepts_the_same_values_from_the_saved_list(
    hass, enable_custom_integrations, entry_data, reader, request
):
    """The shared list is independent of forecast availability on a battery-free entry."""
    with patch(CLOCK, return_value=NOW):
        entry, coordinator, _, _, hour = await _solar(
            hass, entry_data, request.getfixturevalue("planning_providers")
        )
    with patch(CLOCK, return_value=NOW + timedelta(hours=4)):
        with pytest.raises(ServiceValidationError):
            await invoke(hass, entry)
        saved = await _save(hass, entry)
    values = {key: saved["profiles"][0][key] for key in ("power_w", "duration_minutes")}
    manual = {"power_w": 820, "duration_minutes": 45}
    with (
        patch(CLOCK, return_value=NOW),
        patch.object(coordinator, "_correction_for", return_value=Correction(1, "score", 3)),
        patch(
            "custom_components.solar_sanity.load_planning.get_astral_event_date",
            side_effect=lambda _hass, event, _day: (
                hour + timedelta(hours=4 if event == "sunset" else -4)
            ),
        ),
    ):
        actual = await invoke(hass, entry, **values)
        assert actual["planning_mode"] == "solar_only"
        assert actual == await invoke(hass, entry, **manual)
