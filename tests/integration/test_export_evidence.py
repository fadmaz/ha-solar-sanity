"""Grid export options and curtailment observations through the real entry."""

from datetime import UTC, datetime, timedelta

from homeassistant.data_entry_flow import FlowResultType

from custom_components.solar_sanity.const import (
    CONF_CHANNELS,
    CONF_ENTITY_ID,
    CONF_ROLE,
    DOMAIN,
    EXPORT_FIXED,
    EXPORT_UNKNOWN,
    OPT_CURTAILMENT_SENSOR,
    OPT_GRID_EXPORT_LIMIT_W,
    OPT_GRID_EXPORT_MODE,
)
from tests.integration.ha_harness import MockConfigEntry
from tests.integration.test_battery_outlook import _loaded
from tests.integration.test_setup_check import check

CURTAILMENT = "binary_sensor.production_curtailed"


async def test_zero_export_is_saved_and_can_be_cleared(
    hass, enable_custom_integrations, entry_data
):
    """An explicit zero differs from an omitted or unverified limit."""
    entry = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data))
    assert (await check(hass, entry))["forecast"]["export"]["mode"] == "unknown"
    form = await hass.config_entries.options.async_init(entry.entry_id)
    fixed = {OPT_GRID_EXPORT_MODE: EXPORT_FIXED}
    invalid = await hass.config_entries.options.async_configure(form["flow_id"], fixed)
    assert invalid["errors"] == {OPT_GRID_EXPORT_LIMIT_W: "invalid_export_limit"}
    valid = await hass.config_entries.options.async_configure(
        form["flow_id"], {**fixed, OPT_GRID_EXPORT_LIMIT_W: 0}
    )
    await hass.async_block_till_done()
    assert valid["type"] is FlowResultType.CREATE_ENTRY
    assert (await check(hass, entry))["forecast"]["export"] == {
        "mode": "fixed",
        "limit_w": 0,
        "curtailment": "unknown",
        "confirmed_hours": 0,
    }
    form = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        form["flow_id"], {OPT_GRID_EXPORT_MODE: EXPORT_UNKNOWN}
    )
    await hass.async_block_till_done()
    assert OPT_GRID_EXPORT_LIMIT_W not in entry.options


async def test_reported_curtailment_survives_reload_but_a_gap_stays_unknown(
    hass, enable_custom_integrations, entry_data, freezer
):
    """Both occurrences of a repeated local hour keep their own attestation."""
    start = datetime(2026, 10, 25, 0, tzinfo=UTC)
    freezer.move_to(start)
    await hass.config.async_set_time_zone("Europe/Berlin")
    entry = await _loaded(
        hass,
        MockConfigEntry(
            domain=DOMAIN,
            data=entry_data,
            options={
                OPT_GRID_EXPORT_MODE: EXPORT_FIXED,
                OPT_GRID_EXPORT_LIMIT_W: 0,
                OPT_CURTAILMENT_SENSOR: CURTAILMENT,
            },
        ),
    )
    coordinator = entry.runtime_data.coordinator
    hass.states.async_set(CURTAILMENT, "off")
    await hass.async_block_till_done()
    freezer.move_to(start + timedelta(hours=1, minutes=5))
    hass.states.async_set(CURTAILMENT, "on")
    await hass.async_block_till_done()
    assert (await check(hass, entry))["forecast"]["export"]["curtailment"] == "confirmed"

    freezer.move_to(start + timedelta(hours=2))
    hass.states.async_set(CURTAILMENT, "unavailable")
    await hass.async_block_till_done()
    freezer.move_to(start + timedelta(hours=2, minutes=15))
    hass.states.async_set(CURTAILMENT, "off")
    await hass.async_block_till_done()
    freezer.move_to(start + timedelta(hours=3))
    coordinator.accumulate()
    assert coordinator.export_evidence.hours == {start: "off", start + timedelta(hours=1): "on"}

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    restored = entry.runtime_data.coordinator.export_evidence
    assert restored.hours == {start: "off", start + timedelta(hours=1): "on"}
    assert restored.observed_state is None
    assert (await check(hass, entry))["forecast"]["export"]["confirmed_hours"] == 1


async def test_a_near_limit_reading_is_possible_and_remains_installation_scoped(
    hass, enable_custom_integrations, entry_data
):
    """Live export can suggest reduction but never becomes confirmed evidence."""
    channels = entry_data[CONF_CHANNELS][:2]
    grid = "sensor.synthetic_grid_export"
    hass.states.async_set(grid, "995", {"unit_of_measurement": "W"})
    hass.states.async_set(channels[0][CONF_ENTITY_ID], "1800", {"unit_of_measurement": "W"})
    first = await _loaded(
        hass,
        MockConfigEntry(
            domain=DOMAIN,
            data={
                **entry_data,
                CONF_CHANNELS: [*channels, {CONF_ROLE: "grid_export", CONF_ENTITY_ID: grid}],
            },
            options={OPT_GRID_EXPORT_MODE: EXPORT_FIXED, OPT_GRID_EXPORT_LIMIT_W: 1000},
        ),
    )
    second = await _loaded(
        hass,
        MockConfigEntry(
            domain=DOMAIN,
            data={
                **entry_data,
                CONF_CHANNELS: [
                    {**row, CONF_ENTITY_ID: row[CONF_ENTITY_ID] + "_neighbor"} for row in channels
                ],
            },
        ),
    )
    assert (await check(hass, first))["forecast"]["export"]["curtailment"] == "possible"
    assert (await check(hass, second))["forecast"]["export"]["curtailment"] == "unknown"
    hass.states.async_set(grid, "800", {"unit_of_measurement": "W"})
    assert (await check(hass, first))["forecast"]["export"]["curtailment"] == "unknown"
    assert first.runtime_data.coordinator.export_evidence.hours == {}
