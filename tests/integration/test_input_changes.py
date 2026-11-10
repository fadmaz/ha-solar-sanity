"""Published input explanations reset independently of the production projection."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from custom_components.solar_sanity.battery_outlook import OutlookState
from custom_components.solar_sanity.sensor import OutlookSensor, _lowest_attrs
from tests.integration.test_simulation_service import NOW, setup


async def test_refresh_publishes_two_valid_inputs_and_resets_after_a_gap(
    hass, enable_custom_integrations, entry_data
):
    """The real refresh path cannot label a missing charge reading as a change."""
    _, journal, state = await setup(hass, entry_data)
    battery = journal.coordinator.battery
    second = replace(state, computed_at=NOW + timedelta(minutes=5), soc_pct=75)
    with patch.object(battery, "_compute", side_effect=[state, second, OutlookState(), second]):
        battery.refresh(force=True)
        assert battery.state.outlook_changes["status"] == "starting"
        battery.refresh(force=True)
        attrs = _lowest_attrs(battery.state)
        assert attrs["outlook_changes"]["fields"][0]["key"] == "soc_pct"
        assert "outlook_changes" in OutlookSensor._unrecorded_attributes
        battery.refresh(force=True)
        assert battery.state.outlook_changes["status"] == "unavailable"
        battery.refresh(force=True)
        assert battery.state.outlook_changes["status"] == "starting"


async def test_explanation_failure_does_not_replace_a_valid_projection(
    hass, enable_custom_integrations, entry_data
):
    """A presentation feature must not become a new production failure path."""
    _, journal, state = await setup(hass, entry_data)
    saved = deepcopy(state.inputs)
    broken = replace(state, inputs={})
    with patch.object(journal.coordinator.battery, "_compute", return_value=broken):
        journal.coordinator.battery.refresh(force=True)
    assert journal.coordinator.battery.state.outlook is state.outlook
    assert state.inputs == saved
    assert journal.records == []


async def test_source_and_zone_changes_start_new_comparisons(
    hass, enable_custom_integrations, entry_data
):
    """A switched provider or local zone needs two new valid publications."""
    _, journal, state = await setup(hass, entry_data)
    battery = journal.coordinator.battery
    with patch.object(battery, "_compute", return_value=state):
        battery.refresh(force=True)
    battery._forecast_provider_id = "another-synthetic-source"
    with patch.object(
        battery, "_compute", return_value=replace(state, computed_at=NOW + timedelta(minutes=5))
    ):
        battery.refresh(force=True)
    assert battery.state.outlook_changes["status"] == "starting"
    hass.config.set_time_zone("America/New_York")
    with patch.object(
        battery, "_compute", return_value=replace(state, computed_at=NOW + timedelta(minutes=10))
    ):
        battery.refresh(force=True)
    assert battery.state.outlook_changes["status"] == "starting"
