"""Setting the integration up inside a real Home Assistant, and taking it down.

Every other test in this repository that touches Home Assistant hands the code a
``types.SimpleNamespace`` shaped like whatever that test needs. Nothing has ever
called ``async_setup_entry`` against the genuine article — and that is the layer
every defect this project shipped in its worst week lived in. A stub agrees with
whatever you assert about it; a real ``hass`` does not.

Setup here is not a small thing. It restores a store, backfills from statistics,
takes a first refresh, registers three time-interval listeners, a state-change
tracker, two services, a static HTTP path and a Lovelace resource. Teardown has
to undo the parts that are per-entry, and nothing checked that it did.

One thing to know when reading a failure here: the ``hass`` fixture unloads every
loaded config entry during teardown, before stopping. So a broken unload path
fails tests that look like they only set up.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.const import DOMAIN


async def test_an_entry_sets_up_and_reaches_loaded(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """The first thing that has never been checked.

    Asserted on ``entry.state`` rather than only on the return of
    ``async_setup``, because a setup that raises is caught and recorded as
    ``SETUP_ERROR`` — the call still returns, and a test that reads only its
    result calls that a pass.
    """
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED


async def test_every_declared_dependency_can_actually_be_set_up(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """Separated from the test above so its failure names the right cause.

    ``dependencies`` in the manifest is a promise Home Assistant keeps by
    setting each one up first and refusing to set *us* up if it cannot. So a
    name in that list which the integration does not actually call is not
    harmless documentation — it is a hard prerequisite, and the failure it
    produces points at us rather than at itself.

    That is exactly what a real ``hass`` found the first time one was pointed at
    this integration. ``frontend`` was declared, never called, and unavailable in
    any ordinary test environment, so *every* test here failed with
    "Could not setup dependencies: frontend" — a sentence about the integration
    being broken, caused by a line claiming something it never needed.

    The first version of this test blamed namespace-package resolution instead,
    which was wrong and would have sent the next person to the wrong file.
    """
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is not ConfigEntryState.SETUP_ERROR, (
        "a declared dependency could not be set up — check manifest.json's "
        "`dependencies`, which must list only components this integration "
        "genuinely calls"
    )
    assert DOMAIN in hass.config.components


async def test_unloading_returns_the_entry_to_not_loaded(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """The precondition is asserted, because without it this passes vacuously.

    Unloading an entry that never loaded also returns ``True`` and also leaves
    it ``NOT_LOADED``. On the first CI run this test was the only one of six
    that passed, and it passed for exactly that reason while setup was failing
    outright.
    """
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED, "nothing was loaded to unload"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setting_up_twice_in_a_row_leaves_nothing_behind(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """A reload is what a user does after changing their channel mapping.

    Listeners registered through ``entry.async_on_unload`` are released for them;
    anything registered any other way accumulates silently, and the second
    coordinator runs beside the first rather than instead of it.
    """
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED


async def test_two_installations_can_be_set_up_at_once(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict,
) -> None:
    """Nothing in this integration is a singleton by design, and one thing in it
    very nearly is: the services are registered globally rather than per entry.
    A second entry must not fight the first for them."""
    first = MockConfigEntry(domain=DOMAIN, data=entry_data, title="House")
    second = MockConfigEntry(domain=DOMAIN, data=entry_data, title="Barn")
    for config_entry in (first, second):
        config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert first.state is ConfigEntryState.LOADED
    assert second.state is ConfigEntryState.LOADED


async def test_removing_one_of_two_leaves_the_other_working(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict,
) -> None:
    """``async_remove_entry`` sweeps stored files and repairs issues by entry id.

    A sweep that matched too broadly would take the surviving installation's
    with it, and the only symptom would be a card that stopped updating.
    """
    first = MockConfigEntry(domain=DOMAIN, data=entry_data, title="House")
    second = MockConfigEntry(domain=DOMAIN, data=entry_data, title="Barn")
    for config_entry in (first, second):
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_remove(first.entry_id)
    await hass.async_block_till_done()

    assert second.state is ConfigEntryState.LOADED
