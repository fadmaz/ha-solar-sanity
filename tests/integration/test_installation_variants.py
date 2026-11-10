"""Real HA collection conserves energy across synthetic installation variants."""

from datetime import UTC, datetime, timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.const import (
    CONF_CHANNELS,
    CONF_ENTITY_ID,
    CONF_ROLE,
    DOMAIN,
    OPT_BATTERY_CAPACITY_KWH,
    OPT_BATTERY_SOC,
)
from tests.integration.test_battery_outlook import _loaded


@pytest.mark.parametrize(
    ("zone", "capacity", "start"),
    [
        ("UTC", 10, datetime(2026, 11, 1, tzinfo=UTC)),
        ("America/New_York", 20, datetime(2026, 3, 8, 6, tzinfo=UTC)),
        ("America/New_York", 30, datetime(2026, 11, 1, 5, tzinfo=UTC)),
        ("Pacific/Auckland", 60, datetime(2026, 9, 26, 13, tzinfo=UTC)),
    ],
)
@pytest.mark.parametrize("export", [False, True])
@pytest.mark.parametrize("meter", ["power", "energy"])
async def test_power_and_cumulative_installations_conserve_measured_energy_across_dst(
    hass, *, enable_custom_integrations, freezer, zone, capacity, start, export, meter
):
    """Meter cadence may limit attribution; it must never create or lose total energy."""
    freezer.move_to(start)
    await hass.config.async_set_time_zone(zone)
    rates = {"pv": 1000, "load": 500, "grid_import": 0}
    if export:
        rates["grid_export"] = 500
    attributes = {
        "device_class": meter,
        "unit_of_measurement": "W" if meter == "power" else "kWh",
        "state_class": "measurement" if meter == "power" else "total_increasing",
    }
    for key, watts in rates.items():
        hass.states.async_set(
            f"sensor.synthetic_{key}", str(watts if meter == "power" else 100), attributes
        )
    hass.states.async_set("sensor.synthetic_soc", "50", {"unit_of_measurement": "%"})
    entry = await _loaded(
        hass,
        MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_CHANNELS: [
                    {CONF_ROLE: k, CONF_ENTITY_ID: f"sensor.synthetic_{k}"} for k in rates
                ]
            },
            options={OPT_BATTERY_SOC: "sensor.synthetic_soc", OPT_BATTERY_CAPACITY_KWH: capacity},
        ),
    )
    coordinator = entry.runtime_data.coordinator
    for minute in range(5, 121, 5):
        freezer.move_to(start + timedelta(minutes=minute))
        if meter == "energy":
            for key, watts in rates.items():
                hass.states.async_set(
                    f"sensor.synthetic_{key}", str(100 + watts * minute / 60_000), attributes
                )
        coordinator.accumulate()
        await hass.async_block_till_done()
    for key, watts in rates.items():
        measured = sum(
            b.wh.get(key, 0) for b in coordinator.buckets
        ) + coordinator._accumulator.get(key, 0)
        assert measured == pytest.approx(watts * 2, abs=0.001)
    assert coordinator.battery.settings.capacity_wh == capacity * 1000
    assert str(coordinator.time_zone) == zone
