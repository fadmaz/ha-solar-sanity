"""A setting the engine has always honoured and nothing could ever write.

``suppressed_codes`` is read in five places in ``analysis/engine.py`` — it skips
screens, drops hypotheses, and short-circuits the expensive counterfactual when
both findings it serves are unwanted, which has its own performance test. The
engine has kept faith with this option for a long time. The product never
offered it.

That is the shape of dishonesty this project exists to avoid, turned inward: a
capability the code implies and the user cannot reach.
"""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.config_flow import _readable
from custom_components.solar_sanity.const import (
    CONF_CHANNELS,
    CONF_ENTITY_ID,
    CONF_ROLE,
    DOMAIN,
    OPT_BATTERY_SOC,
    OPT_SUPPRESSED,
)


async def _loaded(hass: HomeAssistant, entry: MockConfigEntry):
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_the_option_can_be_written_and_survives(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict,
) -> None:
    """The half that was missing. Round-tripped through the real options flow
    rather than by writing the key directly, because writing the key directly is
    exactly what nobody could do."""
    entry = await _loaded(
        hass,
        MockConfigEntry(
            domain=DOMAIN,
            data=entry_data,
            options={OPT_SUPPRESSED: ["duplicate_channel_pair"]},
        ),
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    done = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_SUPPRESSED: ["duplicate_channel_pair"]}
    )
    await hass.async_block_till_done()

    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[OPT_SUPPRESSED] == ["duplicate_channel_pair"]


async def test_what_is_already_dismissed_is_always_offered(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict,
) -> None:
    """A setting you cannot see is a setting you cannot undo.

    A code stays on the list once dismissed even though, by construction, it is
    no longer among the findings this installation is being given — because it
    is suppressed. Leaving it off would make the dismissal permanent.
    """
    entry = await _loaded(
        hass,
        MockConfigEntry(
            domain=DOMAIN, data=entry_data, options={OPT_SUPPRESSED: ["missing_export_channel"]}
        ),
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert OPT_SUPPRESSED in str(result["data_schema"].schema)


async def test_a_house_with_nothing_to_dismiss_is_not_asked(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """Offering somebody the chance to dismiss a diagnosis they have never been
    given is how a settings page teaches people to ignore it."""
    await _loaded(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert OPT_SUPPRESSED not in str(result["data_schema"].schema)


def test_the_labels_are_sentences_rather_than_symbols() -> None:
    """These identifiers are a public contract — they appear in entity
    attributes and in the finding-raised event — so they cannot be renamed to
    read well. Turning them over for display costs nothing."""
    assert _readable("signed_net_in_dedicated_slot") == "Signed net in dedicated slot"
    assert _readable("missing_export_channel") == "Missing export channel"


async def test_a_battery_charge_level_can_be_mapped_and_survives(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """The one option that is not about the balance at all.

    State of charge takes no part in the arithmetic — it exists so that when a
    battery meter's reported throughput steps, something outside that meter can
    say whether the battery changed or the meter did. Round-tripped through the
    real options flow rather than written directly, because writing it directly
    is exactly what a user cannot do.
    """
    await _loaded(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert OPT_BATTERY_SOC in str(result["data_schema"].schema)

    done = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_BATTERY_SOC: "sensor.battery_level"}
    )
    await hass.async_block_till_done()

    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[OPT_BATTERY_SOC] == "sensor.battery_level"


async def test_no_charge_level_mapped_means_no_swing_and_no_error(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """The overwhelmingly common case. It must be silent, not merely survivable —
    the report then names both causes, which is what shipped before this."""
    entry = await _loaded(hass, entry)
    coordinator = entry.runtime_data.coordinator

    assert await coordinator._async_soc_swing() == {}


async def test_the_wizard_offers_the_charge_level_not_only_the_options(
    hass: HomeAssistant,
    enable_custom_integrations: None,
) -> None:
    """It shipped in the options alone and the first person to use it went
    looking in the wizard, beside the two battery channels — which is where
    somebody mapping a battery expects to be asked. Reachable only afterwards
    made it something you had to already know existed."""
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
    into the channel list as though it were one, and `_channel_records` would
    then look up a Role that does not exist.

    This also covers the write itself: `async_update_reload_and_abort` takes
    `options`, which REPLACES, and has no `options_updates` — the first version
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
        {**channels, OPT_BATTERY_SOC: "sensor.battery_level"},
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    assert entry.options[OPT_BATTERY_SOC] == "sensor.battery_level"
    assert {c[CONF_ROLE] for c in entry.data[CONF_CHANNELS]} == before
