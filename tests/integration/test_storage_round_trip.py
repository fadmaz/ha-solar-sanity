"""What survives a restart, and what a file we cannot read does to setup.

The fitted loss model is the one thing this integration learns slowly. Throwing
it away costs a user a day of their verdict; inheriting the wrong one costs them
a wrong answer for the same day. Both have happened, and neither had a test.

``hass_storage`` is a dict keyed by store key, so seeding a file is writing an
entry into it before setup — the envelope Home Assistant itself writes, with
``version``, ``minor_version``, ``key`` and ``data``.
"""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.const import (
    DOMAIN,
    STORAGE_KEY_STATE,
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
)


def _envelope(key: str, *, version: int, minor: int, gamma: float) -> dict:
    """A stored file exactly as Home Assistant's ``Store`` writes one."""
    return {
        "version": version,
        "minor_version": minor,
        "key": key,
        "data": {
            "loss_model": {
                "pv_dc_gamma": gamma,
                "battery_dc_gamma": 0.05,
                "standby_w": 25.0,
                "samples": 30,
            },
            "last_status": "ok",
            "last_finding": None,
            "retention_days": 400,
        },
    }


async def test_a_fitted_loss_model_is_read_back_at_setup(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """The affirmative case, so the refusal cases below mean something."""
    entry.add_to_hass(hass)
    key = f"{STORAGE_KEY_STATE}.{entry.entry_id}"
    hass_storage[key] = _envelope(
        key, version=STORAGE_VERSION, minor=STORAGE_MINOR_VERSION, gamma=0.0625
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    assert coordinator._loss_model is not None
    assert coordinator._loss_model.pv_dc_gamma == pytest.approx(0.0625)


async def test_a_storage_file_the_code_cannot_migrate_does_not_kill_setup(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """A file from a newer release must cost the loss model, not the integration.

    ``Store`` calls ``_async_migrate_func`` whenever the stored version differs
    from the one asked for, and the default implementation raises
    ``NotImplementedError``. ``async_restore`` guards the *legacy* load in a
    ``try`` and leaves the primary one bare, so that exception travels straight
    out through ``async_setup_entry``.

    Nothing writes a version other than 1 today, so this cannot happen in the
    field yet. It happens the moment anybody bumps either constant — and it
    happens to *downgrading* users first, who are the ones already having a bad
    day. The loss model is refitted from data within a day, which is the whole
    reason it is safe to lose; setup is not.
    """
    entry.add_to_hass(hass)
    key = f"{STORAGE_KEY_STATE}.{entry.entry_id}"
    hass_storage[key] = _envelope(key, version=STORAGE_VERSION + 1, minor=1, gamma=0.0625)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator._loss_model is None


async def test_a_corrupt_stored_model_costs_the_model_and_nothing_else(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """``_loss_from_dict`` already refuses nonsense; this is the join."""
    entry.add_to_hass(hass)
    key = f"{STORAGE_KEY_STATE}.{entry.entry_id}"
    hass_storage[key] = {
        "version": STORAGE_VERSION,
        "minor_version": STORAGE_MINOR_VERSION,
        "key": key,
        "data": {"loss_model": {"pv_dc_gamma": "not a number"}},
    }

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator._loss_model is None


async def test_two_installations_do_not_share_one_loss_model(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict,
    hass_storage: dict,
) -> None:
    """The 0.5.0 defect, which no test has ever covered.

    Two installations shared one state file and overwrote each other's fitted
    model on every analysis. The second write won, and the first house silently
    inherited a model fitted on a different roof. The fix keys the file on the
    entry id; this is what says so.
    """
    first = MockConfigEntry(domain=DOMAIN, data=entry_data, title="House")
    second = MockConfigEntry(domain=DOMAIN, data=entry_data, title="Barn")
    for config_entry, gamma in ((first, 0.02), (second, 0.09)):
        config_entry.add_to_hass(hass)
        key = f"{STORAGE_KEY_STATE}.{config_entry.entry_id}"
        hass_storage[key] = _envelope(
            key, version=STORAGE_VERSION, minor=STORAGE_MINOR_VERSION, gamma=gamma
        )
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert first.runtime_data.coordinator._loss_model.pv_dc_gamma == pytest.approx(0.02)
    assert second.runtime_data.coordinator._loss_model.pv_dc_gamma == pytest.approx(0.09)


async def test_this_entrys_own_file_beats_the_legacy_shared_one(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """The fallback is a one-time rescue, not a competitor.

    Reading the shared file when this entry has its own would reintroduce the
    very defect the per-entry key was added to fix, and it would do it silently.
    """
    entry.add_to_hass(hass)
    own = f"{STORAGE_KEY_STATE}.{entry.entry_id}"
    hass_storage[own] = _envelope(
        own, version=STORAGE_VERSION, minor=STORAGE_MINOR_VERSION, gamma=0.0625
    )
    hass_storage[STORAGE_KEY_STATE] = _envelope(
        STORAGE_KEY_STATE, version=STORAGE_VERSION, minor=STORAGE_MINOR_VERSION, gamma=0.01
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.coordinator._loss_model.pv_dc_gamma == pytest.approx(0.0625)


async def test_the_legacy_file_is_still_inherited_when_this_entry_has_none(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """The other half: the rescue has to still work."""
    entry.add_to_hass(hass)
    hass_storage[STORAGE_KEY_STATE] = _envelope(
        STORAGE_KEY_STATE, version=STORAGE_VERSION, minor=STORAGE_MINOR_VERSION, gamma=0.01
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.coordinator._loss_model.pv_dc_gamma == pytest.approx(0.01)
