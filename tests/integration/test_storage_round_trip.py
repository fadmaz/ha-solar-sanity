"""What survives a restart, and what a file we cannot read does to setup.

The measured window is the one thing this integration cannot get back. The
statistics backfill re-derives hours from the recorder, but an hour this
integration measured itself carries an attestation a re-derived mean does not,
and the battery outlook learns the house's consumption and the inverter's own
draw from exactly these hours. Throwing them away costs a restart's worth of
history; trusting a damaged file costs a wrong answer with no way to tell.

These were written against the fitted loss model of the energy-balance check
until it was retired; the mechanisms they protect are the window's now.

``hass_storage`` is a dict keyed by store key, so seeding a file is writing an
entry into it before setup: the envelope Home Assistant itself writes, with
``version``, ``minor_version``, ``key`` and ``data``.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.const import (
    DOMAIN,
    STORAGE_KEY_STATE,
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
)

KEYS = ["pv", "load", "grid_import", "battery_charge", "battery_discharge"]


def _window(load_wh: float, *, quality: str = "OOOOO") -> dict:
    """Three recent hours, in the columnar shape ``window_snapshot`` writes."""
    top = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    return {
        "keys": KEYS,
        "columns": ["start_utc", "seconds", "wh", "quality", "source"],
        "rows": [
            [
                (top - timedelta(hours=back)).isoformat(),
                3600,
                [0.0, load_wh, 0.0, 0.0, load_wh],
                quality,
                "iiiii",
            ]
            for back in (3, 2, 1)
        ],
    }


def _envelope(key: str, window: dict, *, version: int = STORAGE_VERSION) -> dict:
    """A stored file exactly as Home Assistant's ``Store`` writes one."""
    return {
        "version": version,
        "minor_version": STORAGE_MINOR_VERSION,
        "key": key,
        "data": {"window": window},
    }


def _loads(entry: MockConfigEntry) -> list[float]:
    return [bucket.wh["load"] for bucket in entry.runtime_data.coordinator.buckets]


async def test_a_saved_window_is_read_back_at_setup(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """The affirmative case, so the refusal cases below mean something."""
    entry.add_to_hass(hass)
    key = f"{STORAGE_KEY_STATE}.{entry.entry_id}"
    hass_storage[key] = _envelope(key, _window(612.5))

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _loads(entry) == [612.5, 612.5, 612.5]


async def test_a_storage_file_the_code_cannot_migrate_does_not_kill_setup(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """A file from a newer release must cost the window, not the integration.

    ``Store`` calls ``_async_migrate_func`` whenever the stored version differs
    from the one asked for, and the default implementation raises
    ``NotImplementedError``. Left unguarded, that exception travels out through
    ``async_setup_entry``, and it reaches *downgrading* users first, who are the
    ones already having a bad day.
    """
    entry.add_to_hass(hass)
    key = f"{STORAGE_KEY_STATE}.{entry.entry_id}"
    hass_storage[key] = _envelope(key, _window(612.5), version=STORAGE_VERSION + 1)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert _loads(entry) == []


async def test_a_damaged_window_costs_the_window_and_nothing_else(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """An unreadable quality code is not quietly treated as OK: the whole
    window is refused, because a partly restored one would launder guesses into
    the one place this integration treats as measurement."""
    entry.add_to_hass(hass)
    key = f"{STORAGE_KEY_STATE}.{entry.entry_id}"
    hass_storage[key] = _envelope(key, _window(612.5, quality="OOZOO"))

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert _loads(entry) == []


async def test_two_installations_do_not_share_one_window(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict,
    hass_storage: dict,
) -> None:
    """The 0.5.0 defect. Two installations once shared one state file and
    overwrote each other on every save; the file is keyed on the entry id, and
    this is what says so."""
    first = MockConfigEntry(domain=DOMAIN, data=entry_data, title="House")
    second = MockConfigEntry(domain=DOMAIN, data=entry_data, title="Barn")
    for config_entry, load_wh in ((first, 400.0), (second, 900.0)):
        config_entry.add_to_hass(hass)
        key = f"{STORAGE_KEY_STATE}.{config_entry.entry_id}"
        hass_storage[key] = _envelope(key, _window(load_wh))
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert _loads(first) == [400.0, 400.0, 400.0]
    assert _loads(second) == [900.0, 900.0, 900.0]
