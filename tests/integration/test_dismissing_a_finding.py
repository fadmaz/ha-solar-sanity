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

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.config_flow import _readable
from custom_components.solar_sanity.const import DOMAIN, OPT_SUPPRESSED


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
