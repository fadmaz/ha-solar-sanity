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

from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
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


async def test_setup_reconciles_the_panel_to_the_report_exactly(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A card the current report does not warrant is cleared, not left.

    Learned from this test failing while asserting the opposite. It was written
    to check that a reload leaves an existing card alone — and the card
    vanished, because ``async_sync_issues`` runs at setup and computes the whole
    wanted set from the report, deleting everything else belonging to this
    entry. That is correct and stronger than what was being asserted: a finding
    that has since been fixed, or one raised by an older version of the
    analysis, does not survive on the panel by inertia.

    The other half of that claim — that a card the report *still wants* survives
    a reload — is tested below, in two pieces. See
    ``test_unloading_does_not_take_the_cards_with_it``.
    """
    entry.add_to_hass(hass)
    stale = _raise_for(hass, entry.entry_id, kind="a_finding_no_longer_made")
    assert issue_registry.async_get_issue(DOMAIN, stale) is not None

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert issue_registry.async_get_issue(DOMAIN, stale) is None


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


async def test_unloading_does_not_take_the_cards_with_it(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """The sentence in ``async_remove_issues``: *not on unload*.

    This is the half of the no-flapping claim that needs no seeded statistics,
    and it is the half where the mistake would actually be made. Unload is the
    obvious place to hang a cleanup — it is symmetric with setup, and a reload
    unloads before it sets up again. Hanging it there would delete and recreate
    every card on every reload, and Home Assistant records a dismissal against
    the issue rather than against its contents, so each round trip would quietly
    un-dismiss something the user had already dealt with.

    Removal is the only correct hook, because removal is the only time the
    installation is genuinely gone.

    What this does not prove is the whole round trip: that after the reload the
    fresh analysis names the same finding and re-raises the same card. That
    needs an analysis that genuinely produces a finding, which needs a month of
    seeded statistics. The reconciliation half is covered above, so what remains
    untested is the analysis agreeing with itself across a restart — a property
    of the engine, not of this module.
    """
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    issue_id = _raise_for(hass, entry.entry_id)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None


async def test_the_orphan_sweep_spares_a_disabled_installation(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict,
    issue_registry: ir.IssueRegistry,
) -> None:
    """A disabled installation is still an installation.

    The sweep runs at every setup, against every *configured* entry rather than
    every loaded one, and the difference is the whole test. Disabling an entry
    is temporary and user-initiated — the house is still there, the mapping is
    still whatever it was, and the card is still about something real. Sweeping
    it would be the flapping failure reached by a second route: not through its
    own unload, which the test above covers, but through another installation
    being set up while it is down.

    Disabled rather than merely unloaded, and the first version of this test got
    that wrong. Setting up one entry loads the *component*, and loading a
    component sets up every configured entry belonging to it — so the
    "unloaded" entry was loaded by the time the sweep ran, and its own
    reconcile cleared the card, correctly. Home Assistant does not set up a
    disabled entry, and ``async_entries`` still lists it, which is exactly the
    gap between "configured" and "loaded" that this test needs to sit in.
    """
    absent = MockConfigEntry(
        domain=DOMAIN, data=dict(entry_data), disabled_by=ConfigEntryDisabler.USER
    )
    absent.add_to_hass(hass)
    theirs = _raise_for(hass, absent.entry_id)

    other = MockConfigEntry(domain=DOMAIN, data=dict(entry_data))
    other.add_to_hass(hass)
    await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()

    assert other.state is ConfigEntryState.LOADED
    # Still configured, still down, still holding its card.
    assert absent.entry_id in {e.entry_id for e in hass.config_entries.async_entries(DOMAIN)}
    assert absent.state is not ConfigEntryState.LOADED
    assert issue_registry.async_get_issue(DOMAIN, theirs) is not None
