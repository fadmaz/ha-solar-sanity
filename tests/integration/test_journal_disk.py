"""Exercise journal restore and read-back with real Core storage files."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED

from custom_components.solar_sanity.const import CONF_FORECAST_ENTRIES, DOMAIN
from custom_components.solar_sanity.journal import JOURNAL_KEY, JOURNAL_VERSION, OutlookJournal
from custom_components.solar_sanity.statistics_source import (
    async_forecast_series,
    forecast_statistic_id,
)
from tests.integration.ha_harness import MockConfigEntry, async_wait_recording_done
from tests.integration.test_battery_outlook import BATTERY, _loaded
from tests.integration.test_outlook_journal import NOW, _journal


@pytest.fixture
def hass_config_dir(tmp_path):
    return str(tmp_path)


@pytest.fixture
def hass_storage():
    return {}


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)


@pytest.mark.parametrize("legacy", [False, True])
async def test_a_real_journal_survives_entry_reload(
    hass, enable_custom_integrations, entry_data, freezer, legacy
):
    """Valid Store 1.1 records load unchanged, including envelopes without a minor."""
    freezer.move_to(NOW)
    entry, journal = await _journal(hass, entry_data)
    assert journal.writable and journal.error is None
    assert journal.capture(NOW)
    await journal.async_save()
    assert journal.error is None
    expected = journal.records[0].copy()
    path = Path(journal._store.path)

    assert await hass.config_entries.async_unload(entry.entry_id)
    envelope = json.loads(await hass.async_add_executor_job(path.read_bytes))
    assert envelope["version"] == envelope["minor_version"] == 1
    if legacy:
        envelope.pop("minor_version")
        await hass.async_add_executor_job(_write, path, json.dumps(envelope).encode())
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    restored = entry.runtime_data.coordinator.journal
    assert restored.error is None
    assert restored.records == [expected]


@pytest.mark.parametrize(
    "damage",
    [
        {"version": 2},
        {"minor_version": 2},
        {"version": True},
        {"minor_version": True},
        {"key": "wrong_store"},
        {"data": None},
        {"data": {"records": None}},
        b'{"incomplete":',
    ],
)
async def test_failed_restore_leaves_the_original_file_and_blocks_writes(
    hass, enable_custom_integrations, entry_data, damage
):
    """Core would recover malformed JSON or rewrite a future minor before validating it."""
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data, options=BATTERY)
    key = f"{JOURNAL_KEY}.{entry.entry_id}"
    path = Path(hass.config.path(".storage", key))
    payload = {
        "records": [],
        "created_at": NOW.isoformat(),
        "last_checked_at": NOW.isoformat(),
    }
    data = (
        damage
        if isinstance(damage, bytes)
        else json.dumps(
            {"key": key, "version": JOURNAL_VERSION, "minor_version": 1, "data": payload, **damage},
            indent=2,
        ).encode()
    )
    await hass.async_add_executor_job(_write, path, data)
    await _loaded(hass, entry)
    journal = entry.runtime_data.coordinator.journal
    assert journal.error == "journal_restore_failed"
    assert not journal.writable
    await journal.async_update()
    await journal.async_save()
    assert await hass.async_add_executor_job(path.read_bytes) == data


async def test_an_unreadable_journal_does_not_stop_the_recorder_archive(
    recorder_mock, hass, enable_custom_integrations, entry_data, freezer
):
    freezer.move_to(NOW)
    provider = MockConfigEntry(domain="forecast_solar", title="Synthetic roof")
    provider.add_to_hass(hass)
    provider.mock_state(hass, ConfigEntryState.LOADED)
    key = provider.entry_id
    entry = MockConfigEntry(
        domain=DOMAIN, data={**entry_data, CONF_FORECAST_ENTRIES: [key]}, options=BATTERY
    )
    path = Path(hass.config.path(".storage", f"{JOURNAL_KEY}.{entry.entry_id}"))
    data = b"null"
    await hass.async_add_executor_job(_write, path, data)
    hour = NOW.replace(minute=0)
    raw = {key: {"wh_hours": {hour.isoformat(): 1500}}}
    with (
        patch(
            "custom_components.solar_sanity.coordinator.async_get_solar_forecasts",
            new=AsyncMock(return_value=raw),
        ),
        patch.object(OutlookJournal, "_queue_verification"),
    ):
        await _loaded(hass, entry)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
        await async_wait_recording_done(hass)
    provider.mock_state(hass, ConfigEntryState.NOT_LOADED)
    archived = await async_forecast_series(hass, forecast_statistic_id(key), hour, NOW)
    assert archived == {hour: 1.5}
    assert await hass.async_add_executor_job(path.read_bytes) == data
