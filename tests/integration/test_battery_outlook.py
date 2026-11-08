"""The battery outlook inside a real Home Assistant.

The arithmetic is tested by hand in ``tests/analysis/test_outlook.py``. What
can only be tested here is the wiring that has broken before on this project:
an entity gated on live state instead of configuration, a sensor that reads a
state during setup on an installation whose inverter publishes late, and a
settings form that writes one store while the code reads another.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.solar_sanity.analysis.model import Bucket, BucketSource, Quality
from custom_components.solar_sanity.const import (
    BUCKET_INTERVAL,
    CONF_FORECAST_ENTRIES,
    DOMAIN,
    LIVE_INTERVAL,
    OPT_BATTERY_CAPACITY_KWH,
    OPT_BATTERY_RESERVE_PCT,
    OPT_BATTERY_SOC,
    OPT_BATTERY_TARGET_PCT,
)
from custom_components.solar_sanity.diagnostics import async_get_config_entry_diagnostics

SOC = "sensor.battery_level"
OUTLOOK_KEYS = (
    "battery_full_at",
    "battery_reserve_at",
    "battery_lowest_before_sunrise",
    "safe_average_load",
    "spare_solar",
)
BATTERY = {OPT_BATTERY_SOC: SOC, OPT_BATTERY_CAPACITY_KWH: 10.0}


async def _loaded(hass: HomeAssistant, entry: MockConfigEntry) -> MockConfigEntry:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")


def _refresh(entry: MockConfigEntry) -> None:
    coordinator = entry.runtime_data.coordinator
    coordinator.battery.refresh(force=True)
    coordinator.async_update_listeners()


def _week_of_history(entry: MockConfigEntry) -> None:
    """Seven days of a house drawing 500 W, with the sun up from 06:00 to 18:00 UTC."""
    coordinator = entry.runtime_data.coordinator
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    buckets: list[Bucket] = []
    for back in range(7 * 24, 0, -1):
        start = now - timedelta(hours=back)
        pv = 2500.0 if 6 <= start.hour < 18 else 0.0
        wh = {"pv": pv, "load": 500.0, "battery_charge": 0.0, "battery_discharge": 0.0}
        buckets.append(
            Bucket(
                start_utc=start,
                seconds=3600,
                wh=wh,
                quality=dict.fromkeys(wh, Quality.OK),
                source=dict.fromkeys(wh, BucketSource.OWN_INTEGRAL),
            )
        )
    coordinator._buckets[:] = buckets


async def test_no_battery_settings_means_no_outlook_sensors(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """A sensor that can never hold a value is a small dishonesty."""
    await _loaded(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert all(_entity_id(hass, entry, key) is None for key in OUTLOOK_KEYS)


async def test_the_sensors_are_created_before_the_charge_level_exists(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict[str, Any],
) -> None:
    """An MQTT-bridged inverter publishes after Home Assistant starts. The
    entities must exist anyway, unavailable, and come alive when it does."""
    entry = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data, options=BATTERY))

    assert entry.state is ConfigEntryState.LOADED
    lowest = _entity_id(hass, entry, "battery_lowest_before_sunrise")
    assert lowest is not None
    assert hass.states.get(lowest).state == "unavailable"

    hass.states.async_set(SOC, "64", {"unit_of_measurement": "%", "device_class": "battery"})
    _refresh(entry)
    await hass.async_block_till_done()

    state = hass.states.get(lowest)
    assert state.state == "unknown"
    assert "three days" in state.attributes["reason"]
    # The card shows the battery while consumption is still being learned.
    assert state.attributes["soc_pct"] == 64.0
    assert state.attributes["computed_at"]

    hass.states.async_set(SOC, "unavailable")
    _refresh(entry)
    await hass.async_block_till_done()
    assert hass.states.get(lowest).state == "unavailable"


async def test_the_thirty_second_tick_writes_the_sensors_itself(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict[str, Any],
) -> None:
    """The tick, not a refresh called by hand as the test above does.

    Until 0.31.1 the tick was a plain function, which Home Assistant runs in a
    worker thread, and writing state from there raises for a custom
    integration. The test above passed throughout, while on the reference
    installation the sensors stayed unavailable after a restart beside a
    projection computed minutes earlier.
    """
    entry = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data, options=BATTERY))
    lowest = _entity_id(hass, entry, "battery_lowest_before_sunrise")
    assert lowest is not None
    assert hass.states.get(lowest).state == "unavailable"

    hass.states.async_set(SOC, "64", {"unit_of_measurement": "%", "device_class": "battery"})
    # State listeners are dispatched one loop turn later (call_soon in
    # _async_dispatch_entity_id_event_soon). Without this the tick fires before
    # the listener has marked the projection stale, and skips it, as it should.
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + LIVE_INTERVAL + timedelta(seconds=1))
    await hass.async_block_till_done()

    state = hass.states.get(lowest)
    assert state.state == "unknown"
    assert state.attributes["soc_pct"] == 64.0


async def test_the_five_minute_tick_recomputes_without_a_new_reading(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict[str, Any],
) -> None:
    """A battery holding one level still has an evening that moves on. The
    five-minute tick recomputes whether or not the charge level changed, and
    what it computes has to reach the sensors, not only the diagnostics."""
    hass.states.async_set(SOC, "64", {"unit_of_measurement": "%", "device_class": "battery"})
    entry = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data, options=BATTERY))
    lowest = _entity_id(hass, entry, "battery_lowest_before_sunrise")
    assert lowest is not None
    before = hass.states.get(lowest).attributes["computed_at"]
    assert before

    async_fire_time_changed(hass, dt_util.utcnow() + BUCKET_INTERVAL + timedelta(seconds=1))
    await hass.async_block_till_done()

    assert hass.states.get(lowest).attributes["computed_at"] != before


async def test_a_week_of_history_gives_a_night_outlook(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict[str, Any],
) -> None:
    """No forecast provider: the night is answered from history, and the two
    daytime read-outs say they need a forecast rather than guessing."""
    entry = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data, options=BATTERY))
    _week_of_history(entry)
    hass.states.async_set(SOC, "80", {"unit_of_measurement": "%", "device_class": "battery"})
    _refresh(entry)
    await hass.async_block_till_done()

    lowest = hass.states.get(_entity_id(hass, entry, "battery_lowest_before_sunrise"))
    safe = hass.states.get(_entity_id(hass, entry, "safe_average_load"))
    full = hass.states.get(_entity_id(hass, entry, "battery_full_at"))

    assert float(lowest.state) < 80
    assert lowest.attributes["production_from"] == "history"
    assert lowest.attributes["trajectory"]
    # One entity carries everything the card draws, from the same refresh.
    assert lowest.attributes["soc_pct"] == lowest.attributes["trajectory"][0]["soc"] == 80.0
    assert lowest.attributes["reserve_pct"] == 20.0
    assert lowest.attributes["target_pct"] == 100.0
    assert float(safe.state) > 0
    assert full.state == "unknown"
    assert "forecast" in full.attributes["reason"]


async def test_the_options_flow_writes_the_battery_settings(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    await _loaded(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    done = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            OPT_BATTERY_SOC: SOC,
            OPT_BATTERY_CAPACITY_KWH: 5.12,
            OPT_BATTERY_RESERVE_PCT: 15,
            OPT_BATTERY_TARGET_PCT: 95,
        },
    )
    await hass.async_block_till_done()

    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[OPT_BATTERY_CAPACITY_KWH] == 5.12
    assert entry.options[OPT_BATTERY_RESERVE_PCT] == 15
    assert entry.options[OPT_BATTERY_TARGET_PCT] == 95


async def test_a_target_at_or_below_the_reserve_is_refused(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    await _loaded(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    again = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {OPT_BATTERY_RESERVE_PCT: 60, OPT_BATTERY_TARGET_PCT: 50},
    )

    assert again["type"] is FlowResultType.FORM
    assert again["errors"] == {"base": "target_below_reserve"}
    assert OPT_BATTERY_TARGET_PCT not in entry.options


async def test_the_diagnostics_carry_what_a_replay_needs(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict[str, Any],
) -> None:
    entry = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data, options=BATTERY))
    _week_of_history(entry)
    hass.states.async_set(SOC, "70", {"unit_of_measurement": "%", "device_class": "battery"})
    _refresh(entry)

    report = await async_get_config_entry_diagnostics(hass, entry)

    assert report["outlook"]["reason"] is None
    assert report["outlook"]["projection"]["trajectory"]
    assert "standby_w" in report["outlook"]
    assert "forecast_scale" in report["outlook"]
    assert len(report["outlook"]["load_profile_wh"]) == 24
    assert "soc_history" in report
    assert "forecast_history" in report


async def _dayahead_twice_the_roof(
    _hass: HomeAssistant, _statistic_id: str, start: datetime, end: datetime
) -> dict[datetime, float]:
    """The day-ahead archive: 5 kWh in every hour the week of history made 2.5."""
    out = {}
    hour = start
    while hour < end:
        if 6 <= hour.hour < 18:
            out[hour] = 5.0
        hour += timedelta(hours=1)
    return out


async def _battery_with_room(
    _hass: HomeAssistant, _entity_id: str, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    rows = []
    hour = start
    while hour < end:
        rows.append({"start": hour.isoformat(), "mean": 60.0, "min": 59.0, "max": 61.0})
        hour += timedelta(hours=1)
    return rows


async def test_the_forecast_is_scaled_by_what_the_roof_delivered(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict[str, Any],
) -> None:
    """The wiring from the recorder to the projection. On the reference
    installation the forecast ran twice what the roof delivered, and the
    outlook called a full battery on 13 mornings it did not fill. Here the
    archive says 5 kWh an hour where the history made 2.5, and the projection
    must be run on half the forecast and say so."""
    data = {**entry_data, CONF_FORECAST_ENTRIES: ["provider"]}
    entry = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=data, options=BATTERY))
    _week_of_history(entry)
    hass.states.async_set(SOC, "40", {"unit_of_measurement": "%", "device_class": "battery"})
    coordinator = entry.runtime_data.coordinator
    now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    coordinator.forecast_points = {
        "provider": {
            now + timedelta(hours=h): 6.0
            for h in range(36)
            if 6 <= (now + timedelta(hours=h)).hour < 18
        }
    }
    coordinator.forecast_captured_at = dt_util.utcnow()

    with (
        patch(
            "custom_components.solar_sanity.battery_outlook.async_forecast_series",
            _dayahead_twice_the_roof,
        ),
        patch(
            "custom_components.solar_sanity.battery_outlook.async_hourly_soc",
            _battery_with_room,
        ),
    ):
        await coordinator.battery.async_learn()
    _refresh(entry)
    await hass.async_block_till_done()

    lowest = hass.states.get(_entity_id(hass, entry, "battery_lowest_before_sunrise"))
    full = hass.states.get(_entity_id(hass, entry, "battery_full_at"))
    assert lowest.attributes["forecast_scale"] == 0.5
    assert full.attributes["forecast_scale"] == 0.5
    report = await async_get_config_entry_diagnostics(hass, entry)
    assert report["outlook"]["forecast_scale"]["factor"] == 0.5
