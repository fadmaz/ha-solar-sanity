"""The audit's failure cases against a loaded Home Assistant integration."""

from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.analysis.model import Bucket, BucketSource, Quality
from custom_components.solar_sanity.analysis.profile import ForecastScale
from custom_components.solar_sanity.const import DOMAIN
from tests.integration.test_battery_outlook import BATTERY, SOC, _loaded


@pytest.mark.parametrize(
    "age,complete,movement,accepted",
    [
        (3, False, 0, True),
        (7, False, 0, False),
        (12, True, 0, True),
        (3, True, 1000, False),
        (3, False, 1000, False),
    ],
)
async def test_old_charge_needs_evidence_instead_of_missing_readings(
    hass, enable_custom_integrations, entry_data, *, age, complete, movement, accepted
):
    """Missing battery history once attested to zero movement indefinitely."""
    entry = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data, options=BATTERY))
    hass.states.async_set(SOC, "80", {"unit_of_measurement": "%", "device_class": "battery"})
    reported = hass.states.get(SOC).last_reported
    now = reported + timedelta(hours=age)
    coordinator = entry.runtime_data.coordinator
    hour = reported.replace(minute=0, second=0, microsecond=0)
    end = now.replace(minute=0, second=0, microsecond=0)
    coordinator._buckets.clear()
    while hour < end:
        values = {"battery_charge": float(movement), "battery_discharge": 0.0}
        if not complete:
            values["battery_discharge"] = None
        coordinator._buckets.append(
            Bucket(
                start_utc=hour,
                seconds=3600,
                wh=values,
                quality={
                    k: Quality.OK if v is not None else Quality.MISSING for k, v in values.items()
                },
                source=dict.fromkeys(values, BucketSource.OWN_INTEGRAL),
            )
        )
        hour += timedelta(hours=1)
    battery = coordinator.battery
    level, _, reason = battery._read_soc(battery.settings, now)
    assert (level is not None) is accepted
    if not accepted:
        assert reason == "soc_stale"


async def test_corrected_figures_expire_together_and_recover_without_relearning(
    hass, enable_custom_integrations, entry
):
    """Corrected sensors used an eight-hour-old capture after the outlook refused it."""
    await _loaded(hass, entry)
    coordinator = entry.runtime_data.coordinator
    now = dt_util.utcnow()
    hour = now.replace(minute=0, second=0, microsecond=0)
    coordinator.forecast_points = {"synthetic": {hour: 1.0, hour + timedelta(hours=24): 2.0}}
    coordinator._expected_tomorrow_provider = "synthetic"
    coordinator._expected_tomorrow_kwh = 20.0
    coordinator.battery._scales = {"synthetic": ForecastScale(0.5, 24, 3)}
    coordinator.forecast_captured_at = now - timedelta(hours=8)
    assert coordinator.roof_forecast() is None
    assert coordinator.expected_tomorrow_corrected_kwh is None
    assert "three hours" in coordinator.roof_forecast_attrs["reason"]
    assert "three hours" in coordinator.expected_tomorrow_corrected_attrs["reason"]
    coordinator.forecast_captured_at = now
    assert coordinator.roof_forecast() is not None
    assert coordinator.expected_tomorrow_corrected_kwh == 10.0


@pytest.mark.parametrize("state_class", [None, "measurement"])
async def test_unclassified_energy_is_excluded_and_can_recover(
    hass, enable_custom_integrations, entry, state_class, caplog
):
    """A constant total without its classification was added twelve times an hour."""
    await _loaded(hass, entry)
    coordinator = entry.runtime_data.coordinator
    attrs = {"device_class": "energy", "unit_of_measurement": "kWh"}
    if state_class is not None:
        attrs["state_class"] = state_class
    hass.states.async_set("sensor.consumption", "1", attrs)
    for _ in range(12):
        coordinator.accumulate()
    assert "load" not in coordinator._accumulator
    channel = next(c for c in coordinator.coverage_snapshot()["channels"] if c["key"] == "load")
    assert not channel["readable_now"] and channel["reason"]
    assert sum("excluded until" in r.message for r in caplog.records) == 1
    attrs["state_class"] = "total_increasing"
    hass.states.async_set("sensor.consumption", "1", attrs)
    coordinator.accumulate()
    hass.states.async_set("sensor.consumption", "1.1", attrs)
    coordinator.accumulate()
    assert coordinator._accumulator["load"] == pytest.approx(100)
