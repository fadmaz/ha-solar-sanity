"""Repair cards, against the real issue registry rather than a stand-in.

``tests/test_entity_honesty.py`` already covers this logic through a fake
registry built from ``SimpleNamespace``. That test is worth keeping and it
cannot catch two things: whether ``ir.async_delete_issue`` accepts the
``issue_id`` values actually being passed to it, and whether
``async_remove_entry`` reaches the sweep at all when Home Assistant is the one
calling it. A fake registry agrees with whatever the code hands it.

The defect this guards shipped twice. Deleting an installation left its repair
cards behind, offering to fix a configuration that no longer existed — and their
Fix button leads to a flow that can only abort, so the card was neither
actionable nor dismissible. It cleared on the next restart, which is not a fix.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.const import DOMAIN


def _raise_for(hass: HomeAssistant, entry_id: str, kind: str = "mapping") -> str:
    """A card shaped the way this integration shapes them: suffixed by entry id.

    The suffix is what the sweep matches on, so a test that invented a different
    shape would prove nothing about the sweep.
    """
    issue_id = f"{kind}_{entry_id}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="unmapped_export",
    )
    return issue_id


async def test_removing_an_entry_takes_its_repair_cards_with_it(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    issue_id = _raise_for(hass, entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_a_reload_keeps_the_card_the_user_has_already_seen(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Removal clears them; unload must not.

    A reload unloads too, and flapping the issue would lose the user's
    dismissal — so the card would come back after they had told it to go away,
    every time anything touched the entry.
    """
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    issue_id = _raise_for(hass, entry.entry_id)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None


async def test_removing_one_installation_leaves_the_others_cards_alone(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict,
    issue_registry: ir.IssueRegistry,
) -> None:
    """The sweep matches by suffix, which is a substring test.

    A suffix rule is right for the shape these ids have and would be wrong the
    moment one entry id ended with another — so what is asserted here is the
    outcome, on two entries at once, rather than the rule.
    """
    first = MockConfigEntry(domain=DOMAIN, data=entry_data, title="House")
    second = MockConfigEntry(domain=DOMAIN, data=entry_data, title="Barn")
    for config_entry in (first, second):
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    going = _raise_for(hass, first.entry_id)
    staying = _raise_for(hass, second.entry_id)

    await hass.config_entries.async_remove(first.entry_id)
    await hass.async_block_till_done()

    assert issue_registry.async_get_issue(DOMAIN, going) is None
    assert issue_registry.async_get_issue(DOMAIN, staying) is not None


async def test_a_card_stranded_by_an_older_release_is_swept_at_setup(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """The reference installation had one of these.

    An entry deleted half an hour before the removal hook existed left a card
    carrying an id nothing would ever match again. Every other path reconciles
    against a *live* entry's id, so only a sweep over the whole domain reaches
    it — and without that it would have sat there forever.
    """
    stranded = _raise_for(hass, "01M0X6H534EZXNCW0X86RXE1D3")
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert issue_registry.async_get_issue(DOMAIN, stranded) is None
