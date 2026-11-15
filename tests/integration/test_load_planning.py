"""Use the native actions with installation-specific planning limits."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError

from custom_components.solar_sanity.analysis.forecast import Correction
from custom_components.solar_sanity.const import (
    CONF_CHANNELS,
    CONF_FORECAST_ENTRIES,
    CONF_ROLE,
    DOMAIN,
    OPT_PLANNING_MAX_GRID_KWH,
    OPT_PLANNING_MIN_SOC_PCT,
    OPT_PREFERRED_FORECAST_PROVIDER,
    OPT_SOLAR_ONLY_PLANNING,
)
from tests.integration.ha_harness import MockConfigEntry
from tests.integration.test_battery_controls import options
from tests.integration.test_battery_outlook import _loaded, _week_of_history
from tests.integration.test_setup_check import check
from tests.integration.test_simulation_service import NOW, invoke, setup

CLOCK = "custom_components.solar_sanity.services.dt_util.utcnow"


async def test_manual_battery_limit_is_additive_and_can_be_cleared(
    hass, enable_custom_integrations, entry_data
):
    """Saving a default leaves the frozen projection and omitted legacy parameters alone."""
    entry, journal, state = await setup(hass, entry_data)
    deadline = (NOW + timedelta(hours=3)).isoformat()
    with patch(CLOCK, return_value=NOW):
        before = await invoke(hass, entry, action="find_best_time", finish_by=deadline)
        limited = await invoke(
            hass, entry, action="find_best_time", finish_by=deadline, minimum_soc_pct=95
        )
        compared = await invoke(hass, entry, minimum_soc_pct=95)
        assert limited["recommendation"] is None
        assert limited["reason_code"] == "baseline_below_planning_minimum"
        assert limited["load_actions"]["reason_code"] == "manual_only"
        assert compared["meets_limit"] is False
        assert await invoke(hass, entry, action="find_best_time", finish_by=deadline) == before
        assert journal.coordinator.battery.state is state
        assert journal.records == []
        invalid = await options(hass, entry, {OPT_PLANNING_MIN_SOC_PCT: 1})
        assert invalid["errors"] == {OPT_PLANNING_MIN_SOC_PCT: "invalid_planning_minimum"}
        saved = await options(hass, entry, {OPT_PLANNING_MIN_SOC_PCT: 72})
        assert saved["type"] is FlowResultType.CREATE_ENTRY
        assert (await check(hass, entry))["planning"]["minimum_soc_pct"] == 72
        await options(hass, entry, {})
        assert OPT_PLANNING_MIN_SOC_PCT not in entry.options


@pytest.fixture
def planning_providers(hass):
    providers = [
        MockConfigEntry(domain="forecast_solar", title=title) for title in ("Preferred", "Fallback")
    ]
    for provider in providers:
        provider.add_to_hass(hass)
        provider.mock_state(hass, ConfigEntryState.LOADED)
    try:
        yield [provider.entry_id for provider in providers]
    finally:
        for provider in providers:
            provider.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def _solar(hass, entry_data, providers):
    await hass.config.async_set_time_zone("UTC")
    entry_data[CONF_CHANNELS] = [
        row for row in entry_data[CONF_CHANNELS] if row[CONF_ROLE] in {"pv", "load"}
    ]
    entry = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data))
    initial = (await check(hass, entry))["planning"]
    assert initial["reason_code"] == "solar_only_not_confirmed"
    form = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {OPT_SOLAR_ONLY_PLANNING: True, OPT_PLANNING_MAX_GRID_KWH: 0}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[OPT_PLANNING_MAX_GRID_KWH] == 0
    chosen, backup = providers
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_FORECAST_ENTRIES: [backup, chosen]},
        options={**entry.options, OPT_PREFERRED_FORECAST_PROVIDER: chosen},
    )
    coordinator = entry.runtime_data.coordinator
    _week_of_history(entry)
    hour = NOW.replace(minute=0, second=0, microsecond=0)
    coordinator.forecast_points = {
        chosen: {hour: 0.0},
        backup: {hour: 0.0, hour + timedelta(hours=1): 2.0, hour + timedelta(hours=2): 2.0},
    }
    coordinator._latest_forecast_batch = frozenset({chosen, backup})
    coordinator._forecast_captures = {chosen: NOW, backup: NOW}
    coordinator.forecast_captured_at = NOW
    return entry, coordinator, chosen, backup, hour


async def test_solar_planning_uses_one_complete_source_without_a_grid_meter(
    hass, enable_custom_integrations, entry_data, planning_providers
):
    """An incomplete preferred source yields to the next fresh corrected source."""
    with patch(CLOCK, return_value=NOW):
        entry, coordinator, chosen, backup, hour = await _solar(
            hass, entry_data, planning_providers
        )
        with (
            patch.object(coordinator, "_correction_for", return_value=Correction(1, "score", 3)),
            patch(
                "custom_components.solar_sanity.load_planning.get_astral_event_date",
                side_effect=lambda _hass, event, _day: (
                    hour + timedelta(hours=4 if event == "sunset" else -4)
                ),
            ),
        ):
            assert (await check(hass, entry))["planning"]["status"] == "ready"
            plan = await invoke(
                hass,
                entry,
                action="find_best_time",
                finish_by=(hour + timedelta(hours=3)).isoformat(),
                max_extra_grid_kwh=0,
            )
            assert plan["planning_mode"] == "solar_only"
            assert plan["basis"]["provider"] == "Fallback"
            assert plan["recommendation"]["additional_grid_kwh"] == 0
            assert plan["run_now"]["meets_limit"] is False
            compared = await invoke(hass, entry, start_at=plan["recommendation"]["start_at"])
            assert compared["grid"] == plan["recommendation"]
            assert "scenario" not in compared
            coordinator._forecast_captures[backup] = NOW - timedelta(hours=4)
            with pytest.raises(ServiceValidationError, match="missing hours"):
                await invoke(hass, entry)
            coordinator._latest_forecast_batch = frozenset()
            with pytest.raises(ServiceValidationError, match="fresh solar forecast"):
                await invoke(hass, entry)
            assert coordinator.forecast_points[chosen] == {hour: 0.0}


async def test_missing_darkness_needs_evidence_for_the_local_date(
    hass, enable_custom_integrations, entry_data, planning_providers
):
    """A dark hour can be zero only when the provider covered that local day."""
    with patch(CLOCK, return_value=NOW):
        entry, coordinator, chosen, _, hour = await _solar(hass, entry_data, planning_providers)
        coordinator._latest_forecast_batch = frozenset({chosen})
        coordinator.forecast_points[chosen] = {hour - timedelta(hours=1): 1.0}
        with (
            patch.object(coordinator, "_correction_for", return_value=Correction(1, "score", 3)),
            patch(
                "custom_components.solar_sanity.load_planning.get_astral_event_date",
                return_value=hour - timedelta(hours=2),
            ),
        ):
            result = await invoke(hass, entry)
            assert result["grid"]["baseline_grid_kwh"] == pytest.approx(0.5)
            assert result["grid"]["additional_grid_kwh"] == pytest.approx(1)
            coordinator.forecast_points[chosen] = {hour - timedelta(days=1): 1.0}
            with pytest.raises(ServiceValidationError, match="missing hours"):
                await invoke(hass, entry)
