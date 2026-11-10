"""Real native action calls simulate frozen inputs without changing live evidence."""

from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError

from custom_components.solar_sanity.analysis.outlook import BatterySettings, Step, project
from custom_components.solar_sanity.battery_outlook import OutlookState
from custom_components.solar_sanity.const import DOMAIN
from tests.integration.test_outlook_journal import NOW, _journal
from tests.integration.test_tools_services import device


async def setup(hass, entry_data):
    entry, journal = await _journal(hass, entry_data)
    settings = BatterySettings(10_000, 20, 100)
    series = [Step(NOW + timedelta(hours=i), 1, 0, 200, 0) for i in range(12)]
    state = OutlookState(
        outlook=project(80, settings, series, forecast_used=True),
        computed_at=NOW,
        projection_id="synthetic-basis",
        reason=None,
        soc_pct=80,
        battery=settings,
        inputs={
            "steps": [{**asdict(s), "start": s.start.isoformat()} for s in series],
            "battery": asdict(settings),
        },
    )
    journal.coordinator.battery.state = state
    journal.coordinator.forecast_captured_at = NOW
    return entry, journal, state


async def invoke(hass, entry, **extra):
    return await hass.services.async_call(
        DOMAIN,
        "simulate_load",
        {
            "device_id": device(hass, entry),
            "power_w": 1000,
            "duration_minutes": 60,
            **extra,
        },
        blocking=True,
        return_response=True,
    )


async def test_native_simulation_does_not_change_state_or_the_journal(
    hass, enable_custom_integrations, entry_data
):
    """An exploratory calculation must not become an issued prediction."""
    entry, journal, state = await setup(hass, entry_data)
    saved = deepcopy(state.inputs)
    with patch("custom_components.solar_sanity.services.dt_util.utcnow", return_value=NOW):
        result = await invoke(hass, entry)
        zero = await invoke(hass, entry, power_w=0)
    assert result["additional_energy_kwh"] == 1
    assert result["basis"]["projection_id"] == "synthetic-basis"
    assert zero["baseline"] == zero["scenario"]
    assert journal.records == []
    assert state.inputs == saved
    assert journal.coordinator.battery.state is state


@pytest.mark.parametrize(
    "extra",
    [
        {"power_w": -1},
        {"power_w": "nan"},
        {"duration_minutes": 1.5},
        {"start_at": "2026-11-10T21:00:00"},
    ],
)
async def test_bad_action_input_cannot_be_coerced_into_a_different_load(
    hass, enable_custom_integrations, entry_data, extra
):
    """Naive times and fractional minutes need correction by the caller."""
    entry, _, _ = await setup(hass, entry_data)
    with pytest.raises(vol.Invalid):
        await invoke(hass, entry, **extra)


async def test_a_stale_source_or_old_projection_is_not_simulated(
    hass, enable_custom_integrations, entry_data
):
    """The action shares the live forecast expiry and rejects missed recomputations."""
    entry, journal, state = await setup(hass, entry_data)
    journal.coordinator.forecast_captured_at = NOW - timedelta(hours=4)
    with (
        patch("custom_components.solar_sanity.services.dt_util.utcnow", return_value=NOW),
        pytest.raises(ServiceValidationError, match="three hours"),
    ):
        await invoke(hass, entry)
    journal.coordinator.forecast_captured_at = NOW
    state.computed_at = NOW - timedelta(minutes=11)
    with (
        patch("custom_components.solar_sanity.services.dt_util.utcnow", return_value=NOW),
        pytest.raises(ServiceValidationError, match="current valid"),
    ):
        await invoke(hass, entry)
