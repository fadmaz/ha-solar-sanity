"""What 0.30.0 has to do to an installation the energy-balance check ran on.

The check is deleted, but what it left behind is not: five entities in the
registry, Repairs issues with Fix buttons that can only abort, a shared state
file, a storage file with keys nothing reads, and three answers in every
entry's data. Each of these is either cleaned up or proven harmless here.

And the two jobs the check's six-hourly analysis used to do in passing, saving
the window and scoring the forecasts, have to still happen without it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.const import (
    DOMAIN,
    STORAGE_KEY_STATE,
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
)

RETIRED = (
    ("sensor", "status"),
    ("sensor", "data_completeness"),
    ("sensor", "corrections_active"),
    ("sensor", "live_residual"),
    ("binary_sensor", "data_healthy"),
)


def _register_retired(hass: HomeAssistant, entry: MockConfigEntry) -> list[str]:
    registry = er.async_get(hass)
    return [
        registry.async_get_or_create(
            platform, DOMAIN, f"{entry.entry_id}_{key}", config_entry=entry
        ).entity_id
        for platform, key in RETIRED
    ]


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_the_retired_entities_are_removed_and_nothing_else(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """Nothing will ever write them again, and an entity restored as
    unavailable forever reads as a broken integration."""
    entry.add_to_hass(hass)
    retired = _register_retired(hass, entry)
    registry = er.async_get(hass)
    kept = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_expected_tomorrow_corrected", config_entry=entry
    ).entity_id

    await _setup(hass, entry)

    assert all(registry.async_get(entity_id) is None for entity_id in retired)
    assert registry.async_get(kept) is not None


async def test_removing_them_again_is_harmless(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """Every setup runs it, so a downgrade to 0.29 and an upgrade back, which
    recreates them, is covered too."""
    entry.add_to_hass(hass)
    _register_retired(hass, entry)
    await _setup(hass, entry)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED


async def test_only_this_domains_repairs_issues_are_removed(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """A Fix button for a finding nothing can raise any more can only abort."""
    for domain, issue_id in ((DOMAIN, "finding_abc"), ("other_integration", "keep_me")):
        ir.async_create_issue(
            hass,
            domain,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="anything",
        )
    entry.add_to_hass(hass)

    await _setup(hass, entry)

    issues = ir.async_get(hass).issues
    assert (DOMAIN, "finding_abc") not in issues
    assert ("other_integration", "keep_me") in issues


async def test_the_old_shared_state_file_is_removed(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """It predates per-entry files and only ever held the check's fitted model."""
    hass_storage[STORAGE_KEY_STATE] = {
        "version": STORAGE_VERSION,
        "minor_version": STORAGE_MINOR_VERSION,
        "key": STORAGE_KEY_STATE,
        "data": {"loss_model": {"pv_dc_gamma": 0.01}},
    }
    entry.add_to_hass(hass)

    await _setup(hass, entry)

    assert STORAGE_KEY_STATE not in hass_storage


async def test_a_storage_file_written_by_0_29_is_still_read(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """Same storage version, extra keys. Unknown keys were always ignored on
    load, which is what makes dropping them without a version bump safe."""
    entry.add_to_hass(hass)
    key = f"{STORAGE_KEY_STATE}.{entry.entry_id}"
    top = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    hass_storage[key] = {
        "version": STORAGE_VERSION,
        "minor_version": STORAGE_MINOR_VERSION,
        "key": key,
        "data": {
            "loss_model": {"pv_dc_gamma": 0.02, "samples": 30},
            "last_status": "investigating",
            "last_finding": None,
            "retention_days": 400,
            "window": {
                "keys": ["pv", "load"],
                "rows": [[(top - timedelta(hours=2)).isoformat(), 3600, [0.0, 500.0], "OO", "ii"]],
            },
        },
    }

    await _setup(hass, entry)

    assert [b.wh["load"] for b in entry.runtime_data.coordinator.buckets] == [500.0]


async def test_an_entry_still_holding_the_old_answers_loads_and_keeps_them(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict[str, Any],
) -> None:
    """No migration: nothing reads these, and leaving them where they are makes
    a rollback to 0.29 lossless."""
    old_data = {**entry_data, "has_battery": "yes", "grid_is_net": "no", "load_whole_house": "yes"}
    old_options = {"suppressed_codes": ["missing_export_channel"], "guaranteed_annual_kwh": 9000}
    entry = MockConfigEntry(domain=DOMAIN, data=old_data, options=old_options)
    entry.add_to_hass(hass)

    await _setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.data["has_battery"] == "yes"
    assert entry.options["suppressed_codes"] == ["missing_export_channel"]


async def test_the_window_is_saved_when_an_hour_closes(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """It used to be saved only from the six-hourly analysis. Saving from a
    coordinator refresh would stop whenever nothing listens to the coordinator,
    so it is saved where the hours are made, with a bound method so the
    deferred write saves the window as it is then."""
    entry.add_to_hass(hass)
    await _setup(hass, entry)
    coordinator = entry.runtime_data.coordinator
    top = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)

    with patch.object(coordinator._store, "async_delay_save") as save:
        coordinator._close_bucket(top - timedelta(hours=1), top)

    save.assert_called_once()
    payload, _delay = save.call_args.args
    assert payload == coordinator._payload
    assert set(payload()) == {"window"}


async def test_a_scoring_failure_does_not_take_the_sensors_down(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """Every entity's availability chains to the coordinator's last refresh, and
    the refresh now only scores forecasts. An archive that cannot be read must
    not take the battery outlook with it."""
    entry.add_to_hass(hass)
    await _setup(hass, entry)
    coordinator = entry.runtime_data.coordinator

    with patch(
        "custom_components.solar_sanity.scoring.async_score_providers",
        side_effect=RuntimeError("archive unreadable"),
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success


async def test_setup_without_a_provider_or_a_battery_asks_nothing_more(
    hass: HomeAssistant,
    enable_custom_integrations: None,
) -> None:
    """The step that asked about the house now shows only providers and the
    battery. With neither, there is nothing on it, and it is skipped."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"pv": "sensor.generation", "load": "sensor.consumption"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "has_battery" not in result["data"]
    assert result["data"]["forecast_entries"] == []
