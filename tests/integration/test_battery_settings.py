"""The battery settings, through the real flows a user goes through.

The charge-level sensor, capacity, reserve and target are what the battery
outlook projects from. They live in ``entry.options``, are offered both in the
setup wizard and under Configure, and have to survive Reconfigure. Every test
here goes through a real flow rather than writing the keys directly, because
writing them directly is exactly what a user cannot do.
"""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.const import (
    CONF_CHANNELS,
    CONF_ENTITY_ID,
    CONF_ROLE,
    DOMAIN,
    OPT_BATTERY_CAPACITY_KWH,
    OPT_BATTERY_RESERVE_PCT,
    OPT_BATTERY_SOC,
    OPT_BATTERY_TARGET_PCT,
)

SOC = "sensor.battery_level"


async def _loaded(hass: HomeAssistant, entry: MockConfigEntry) -> MockConfigEntry:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_a_battery_charge_level_can_be_mapped_and_survives(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    await _loaded(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert OPT_BATTERY_SOC in str(result["data_schema"].schema)

    done = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_BATTERY_SOC: SOC}
    )
    await hass.async_block_till_done()

    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[OPT_BATTERY_SOC] == SOC


async def test_the_wizard_offers_the_charge_level_not_only_the_options(
    hass: HomeAssistant,
    enable_custom_integrations: None,
) -> None:
    """It shipped in the options alone and the first person to use it went
    looking in the wizard, beside the two battery channels, which is where
    somebody mapping a battery expects to be asked."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert OPT_BATTERY_SOC in str(result["data_schema"].schema)


async def test_reconfigure_keeps_it_out_of_the_channels(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """It is not a role. Letting it through would write `battery_soc_entity`
    into the channel list as though it were one.

    This also covers the write itself: `async_update_reload_and_abort` takes
    `options`, which replaces, and has no `options_updates`. The first version
    called one that does not exist and every test still passed, because none of
    them went down this path.
    """
    entry = await _loaded(hass, entry)
    before = {c[CONF_ROLE] for c in entry.data[CONF_CHANNELS]}

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert OPT_BATTERY_SOC in str(result["data_schema"].schema)

    channels = {c[CONF_ROLE]: c[CONF_ENTITY_ID] for c in entry.data[CONF_CHANNELS]}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**channels, OPT_BATTERY_SOC: SOC},
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert entry.options[OPT_BATTERY_SOC] == SOC
    assert {c[CONF_ROLE] for c in entry.data[CONF_CHANNELS]} == before


async def test_a_capacity_typed_by_mistake_can_be_taken_back(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict,
) -> None:
    """A blanked ``vol.Optional`` is absent from ``user_input``, so a plain merge
    puts the old number back. Capacity is the one battery figure without a
    default, and clearing it is how somebody turns the outlook off; if the old
    figure came back, the outlook would project a battery that is not there."""
    entry = await _loaded(
        hass,
        MockConfigEntry(
            domain=DOMAIN,
            data=dict(entry_data),
            options={OPT_BATTERY_SOC: SOC, OPT_BATTERY_CAPACITY_KWH: 10.0},
        ),
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {OPT_BATTERY_SOC: SOC, OPT_BATTERY_RESERVE_PCT: 20, OPT_BATTERY_TARGET_PCT: 100},
    )
    await hass.async_block_till_done()

    assert OPT_BATTERY_CAPACITY_KWH not in entry.options
    assert entry.options[OPT_BATTERY_SOC] == SOC


async def test_clearing_a_field_leaves_settings_the_form_never_offered(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict,
) -> None:
    """Whatever the form does with blanks, it must not reach a key it never
    showed: another release may have written it, and may be rolled back to."""
    entry = await _loaded(
        hass,
        MockConfigEntry(
            domain=DOMAIN,
            data=dict(entry_data),
            options={"written_by_another_release": [1, 2]},
        ),
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_BATTERY_SOC: SOC, OPT_BATTERY_CAPACITY_KWH: 12.5}
    )
    await hass.async_block_till_done()

    assert entry.options["written_by_another_release"] == [1, 2]
    assert entry.options[OPT_BATTERY_CAPACITY_KWH] == 12.5
