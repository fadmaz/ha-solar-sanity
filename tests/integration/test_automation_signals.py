"""Exercise signal entities and shipped notification automations in Home Assistant."""

from datetime import timedelta
from pathlib import Path
from shutil import copyfile
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.solar_sanity.const import (
    CONF_CHANNELS,
    CONF_ENTITY_ID,
    CONF_ROLE,
    DOMAIN,
    OPT_MINIMUM_SURPLUS_W,
    OPT_RESERVE_WARNING_MINUTES,
    OPT_SURPLUS_HOLD_MINUTES,
)
from tests.integration.test_battery_outlook import BATTERY, SOC, _entity_id, _loaded
from tests.integration.test_setup_check import check

EXPORT = "sensor.to_grid"
POWER = {"unit_of_measurement": "W", "device_class": "power"}
BLUEPRINTS = Path(__file__).resolve().parents[2] / "blueprints" / "automation" / DOMAIN


def _binary(hass, entry, key):
    return er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_{key}"
    )


def _add_export(entry_data):
    entry_data[CONF_CHANNELS].append({CONF_ROLE: "grid_export", CONF_ENTITY_ID: EXPORT})


async def _tick(hass, freezer, seconds=31):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


def _report_power(hass, export=1600, imported=0, discharged=0):
    for entity, value in {
        "sensor.generation": 2400,
        EXPORT: export,
        "sensor.from_grid": imported,
        "sensor.out_of_battery": discharged,
    }.items():
        hass.states.async_set(entity, str(value), POWER)


async def test_brief_discharge_and_expired_reports_restart_the_hold(
    hass, enable_custom_integrations, entry_data, freezer
):
    """The hold measures continuous direct evidence, including changes between ticks."""
    _add_export(entry_data)
    _report_power(hass, export=1500)
    entry = await _loaded(
        hass,
        MockConfigEntry(
            domain=DOMAIN,
            data=entry_data,
            options={**BATTERY, OPT_SURPLUS_HOLD_MINUTES: 1},
        ),
    )
    entity = _binary(hass, entry, "solar_surplus_ready")
    assert hass.states.get(entity).state == "off"
    await _tick(hass, freezer)
    _report_power(hass, export=1500, discharged=51)
    _report_power(hass, export=1500)
    await hass.async_block_till_done()
    await _tick(hass, freezer)
    assert hass.states.get(entity).state == "off"
    await _tick(hass, freezer)
    assert hass.states.get(entity).state == "on"
    assert hass.states.get(entity).attributes["measured_w"] == 1500

    freezer.tick(timedelta(minutes=3))
    _report_power(hass, export=1500)  # Identical values re-report after the evidence expired.
    await hass.async_block_till_done()
    assert hass.states.get(entity).state == "off"
    await _tick(hass, freezer, 181)
    assert hass.states.get(entity).state == "unavailable"
    assert (
        "No fresh grid export power reading"
        in (await check(hass, entry))["automation"]["solar_surplus_ready"]["reason"]
    )
    _report_power(hass)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 61)
    assert hass.states.get(entity).state == "on"
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity).state == "off"


async def test_solar_only_needs_power_meters_but_no_battery(
    hass, enable_custom_integrations, entry_data, freezer
):
    """An export energy total cannot become a live power reading."""
    entry_data[CONF_CHANNELS] = entry_data[CONF_CHANNELS][:3]
    _add_export(entry_data)
    _report_power(hass, export=800)
    hass.states.async_set(EXPORT, "123", {"unit_of_measurement": "kWh", "state_class": "total"})
    entry = await _loaded(
        hass,
        MockConfigEntry(
            domain=DOMAIN,
            data=entry_data,
            options={OPT_MINIMUM_SURPLUS_W: 750, OPT_SURPLUS_HOLD_MINUTES: 1},
        ),
    )
    entity = _binary(hass, entry, "solar_surplus_ready")
    assert _binary(hass, entry, "reserve_risk") is None
    assert hass.states.get(entity).state == "unavailable"
    _report_power(hass, export=800, imported=50)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 61)
    assert hass.states.get(entity).state == "on"
    assert hass.states.get(entity).attributes["measured_w"] == 750
    _report_power(hass, export=800, imported=51)
    await hass.async_block_till_done()
    assert hass.states.get(entity).state == "off"


async def test_configure_applies_thresholds_to_one_installation(
    hass, enable_custom_integrations, entry_data
):
    """Changing notification thresholds leaves another entry's defaults alone."""
    first = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data, options=BATTERY))
    second = await _loaded(
        hass, MockConfigEntry(domain=DOMAIN, data=entry_data, title="Another installation")
    )
    flow = await hass.config_entries.options.async_init(first.entry_id)
    saved = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            **BATTERY,
            OPT_RESERVE_WARNING_MINUTES: 90,
            OPT_MINIMUM_SURPLUS_W: 1800,
            OPT_SURPLUS_HOLD_MINUTES: 4,
        },
    )
    assert saved["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    chosen = (await check(hass, first))["automation"]
    defaults = (await check(hass, second))["automation"]
    assert chosen["reserve_risk"]["lead_minutes"] == 90
    assert chosen["solar_surplus_ready"]["minimum_w"] == 1800
    assert chosen["solar_surplus_ready"]["hold_minutes"] == 4
    assert defaults["reserve_risk"] is None
    assert defaults["solar_surplus_ready"]["minimum_w"] == 1500
    assert defaults["solar_surplus_ready"]["hold_minutes"] == 5


async def test_reserve_uses_unsettled_crossing_and_valid_projection(
    hass, enable_custom_integrations, entry_data
):
    """A history projection can warn at the exact boundary; an expired forecast cannot."""
    hass.states.async_set(SOC, "65", {"unit_of_measurement": "%"})
    entry = await _loaded(
        hass,
        MockConfigEntry(
            domain=DOMAIN, data=entry_data, options={**BATTERY, OPT_RESERVE_WARNING_MINUTES: 40}
        ),
    )
    coordinator = entry.runtime_data.coordinator
    state = coordinator.battery.state
    now = dt_util.utcnow()
    state.outlook = SimpleNamespace(
        reserve_at=now + timedelta(minutes=40),
        horizon_end=now + timedelta(hours=5),
        takeover_at=None,
        forecast_used=False,
    )
    state.reason = None
    state.computed_at = now
    state.projection_id = "synthetic"
    state.reserve_at = now + timedelta(hours=1)
    assert coordinator.automation.reserve(now - timedelta(seconds=1)).is_on is None
    assert coordinator.automation.reserve(now).is_on is True
    assert (
        coordinator.automation.reserve(now).attributes["reserve_at"] != state.reserve_at.isoformat()
    )
    state.outlook.reserve_at += timedelta(minutes=1)
    assert coordinator.automation.reserve(now).is_on is False
    state.outlook.forecast_used = True
    assert coordinator.automation.reserve(now).is_on is None
    state.outlook = None
    state.soc_pct = 20
    coordinator.async_update_listeners()
    risk = hass.states.get(_binary(hass, entry, "reserve_risk"))
    assert risk.state == "on" and risk.attributes["at_reserve"]


async def test_blueprints_notify_once_and_estimates_do_not_enable_measured_ready(
    hass, enable_custom_integrations, entry_data, freezer
):
    """Load the real YAML files, substitute inputs and observe notification calls."""
    hass.states.async_set(SOC, "90", {"unit_of_measurement": "%"})
    entry = await _loaded(
        hass,
        MockConfigEntry(
            domain=DOMAIN, data=entry_data, options={**BATTERY, OPT_SURPLUS_HOLD_MINUTES: 1}
        ),
    )
    risk = _binary(hass, entry, "reserve_risk")
    ready = _binary(hass, entry, "solar_surplus_ready")
    spare = _entity_id(hass, entry, "spare_solar_power")
    calls = []

    async def notify(call):
        calls.append((call.service, call.data["message"]))

    hass.services.async_register("notify", "send_message", notify)
    hass.services.async_register("notify", "mobile_app_test_phone", notify)
    configs = []
    for alias, filename, inputs in (
        (
            "Reserve",
            "reserve_risk_notification.yaml",
            {
                "reserve_risk": risk,
                "notify_entity": "notify.test",
            },
        ),
        (
            "Surplus",
            "solar_surplus_notification.yaml",
            {
                "solar_surplus_ready": ready,
                "spare_solar_power": spare,
                "include_estimated": True,
                "mobile_app_action": "notify.mobile_app_test_phone",
            },
        ),
        (
            "Measured only",
            "solar_surplus_notification.yaml",
            {
                "solar_surplus_ready": ready,
                "spare_solar_power": spare,
                "notify_entity": "notify.test",
            },
        ),
    ):
        path = Path(hass.config.path("blueprints", "automation", DOMAIN, filename))
        path.parent.mkdir(parents=True, exist_ok=True)
        copyfile(BLUEPRINTS / filename, path)
        configs.append(
            {
                "id": alias,
                "alias": alias,
                "use_blueprint": {
                    "path": f"{DOMAIN}/{filename}",
                    "input": inputs,
                },
            }
        )
    assert await async_setup_component(hass, "automation", {"automation": configs})
    await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    with patch.object(coordinator.battery, "spare_power", return_value=(1600, 2000, "forecast")):
        await _tick(hass, freezer)
        assert not calls
        await _tick(hass, freezer, 61)
        await _tick(hass, freezer)
        assert hass.states.get(ready).state == "unavailable"
        assert len(calls) == 1 and calls[0][0] == "mobile_app_test_phone"
        assert "Estimated solar surplus" in calls[0][1]

    hass.states.async_set(SOC, "20", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    await _tick(hass, freezer)
    await _tick(hass, freezer)
    assert len(calls) == 2 and calls[1][0] == "send_message"
    assert "already at or below" in calls[1][1]
