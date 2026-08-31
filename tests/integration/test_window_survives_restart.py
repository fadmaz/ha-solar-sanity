"""The hours this integration measured itself must survive a restart.

Until now they did not. ``_buckets`` was a plain list initialised empty, so every
restart refilled the window from Home Assistant's long-term statistics — a
different measurement of the same hours, and a worse one. An hourly arithmetic
mean over a sensor that reports on change over-weights the busy part of the
hour; this integration weights every reading by how long it actually stood.

So the engine was handed the weaker figure for hours it had already measured
properly, *and* told to trust it less on top: ``MEAN_SOURCE_TOLERANCE_FACTOR``
widens the actionable band from a tenth to a sixth for mean-derived hours. One
reference installation showed 55 hours of its own against 3,580 backfilled.

The mechanism is ordering, and it already existed. ``async_setup_entry`` calls
``async_restore`` before ``_async_backfill``, and ``ingest_backfill`` skips any
hour already present — "our own measurement is preferred where we have it". All
that was missing was putting the window in the file.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.analysis.model import Bucket, BucketSource, Quality
from custom_components.solar_sanity.const import STORAGE_KEY_STATE, STORAGE_VERSION

KEYS = ("pv", "load", "grid_import", "battery_charge", "battery_discharge")


def _measured(start: datetime, hours: int) -> list[Bucket]:
    """Hours shaped as our own integrator produces them."""
    return [
        Bucket(
            start_utc=start + timedelta(hours=hour),
            seconds=3600,
            wh=dict.fromkeys(KEYS, 100.0 + hour),
            quality=dict.fromkeys(KEYS, Quality.OK),
            source=dict.fromkeys(KEYS, BucketSource.OWN_INTEGRAL),
            local_date=(start + timedelta(hours=hour)).date(),
            is_dst_transition=False,
        )
        for hour in range(hours)
    ]


async def _coordinator(hass: HomeAssistant, entry: MockConfigEntry):
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry.runtime_data.coordinator


async def test_a_saved_window_comes_back_with_its_provenance(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """The point of the whole exercise.

    Provenance is not decoration: it decides whether the band is a tenth or a
    sixth. An hour restored as ``LTS_MEAN`` when we measured it ourselves is a
    quieter engine on better data.
    """
    start = datetime(2026, 8, 1, tzinfo=UTC)
    coordinator = await _coordinator(hass, entry)
    coordinator._buckets = _measured(start, 12)

    snapshot = coordinator.window_snapshot()
    restored = coordinator._buckets_from_snapshot(snapshot)

    assert len(restored) == 12
    assert [b.start_utc for b in restored] == [b.start_utc for b in coordinator._buckets]
    assert all(
        source is BucketSource.OWN_INTEGRAL for b in restored for source in b.source.values()
    )
    assert all(q is Quality.OK for b in restored for q in b.quality.values())


async def test_a_hole_comes_back_a_hole(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """``None`` must not return as zero. It is the distinction the engine is
    built around — an hour with a hole in it is not an hour with less energy."""
    start = datetime(2026, 8, 1, tzinfo=UTC)
    coordinator = await _coordinator(hass, entry)
    holed = _measured(start, 3)
    holed[1] = Bucket(
        start_utc=holed[1].start_utc,
        seconds=3600,
        wh={**holed[1].wh, "pv": None},
        quality={**holed[1].quality, "pv": Quality.MISSING},
        source=holed[1].source,
        local_date=holed[1].local_date,
        is_dst_transition=False,
    )
    coordinator._buckets = holed

    restored = coordinator._buckets_from_snapshot(coordinator.window_snapshot())

    assert restored[1].wh["pv"] is None
    assert restored[1].quality["pv"] is Quality.MISSING


async def test_a_partial_hour_stays_partial(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """``build_days`` drops anything not exactly 3600 s, so the seconds column
    is what keeps the first hour after a restart out of the arithmetic."""
    start = datetime(2026, 8, 1, tzinfo=UTC)
    coordinator = await _coordinator(hass, entry)
    partial = _measured(start, 2)
    partial[0] = Bucket(
        start_utc=partial[0].start_utc,
        seconds=1800,
        wh=partial[0].wh,
        quality=partial[0].quality,
        source=partial[0].source,
        local_date=partial[0].local_date,
        is_dst_transition=False,
    )
    coordinator._buckets = partial

    restored = coordinator._buckets_from_snapshot(coordinator.window_snapshot())

    assert restored[0].seconds == 1800


async def test_the_window_is_read_back_at_setup(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """End to end: a window in the file is a window in the coordinator.

    Seeded rather than round-tripped through a real save, because
    ``async_delay_save`` writes thirty seconds later and this is a test about
    reading.
    """
    start = datetime(2026, 8, 1, tzinfo=UTC)
    key = f"{STORAGE_KEY_STATE}.{entry.entry_id}"
    rows = [
        [
            (start + timedelta(hours=hour)).isoformat(),
            3600,
            [100.0 + hour] * len(KEYS),
            "O" * len(KEYS),
            "i" * len(KEYS),
        ]
        for hour in range(6)
    ]
    hass_storage[key] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": key,
        "data": {"loss_model": None, "window": {"keys": list(KEYS), "rows": rows}},
    }

    coordinator = await _coordinator(hass, entry)

    assert len(coordinator._buckets) >= 6
    ours = [b for b in coordinator._buckets if b.start_utc >= start]
    assert all(
        source is BucketSource.OWN_INTEGRAL for b in ours[:6] for source in b.source.values()
    )


class TestItRefusesRatherThanGuesses:
    """Losing the saved window costs a restart's worth of history. Trusting a
    damaged one costs a wrong answer with no way to tell which it was."""

    async def _coordinator(self, hass, entry):
        return await _coordinator(hass, entry)

    async def test_an_unknown_code_restores_nothing(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
    ) -> None:
        """Silently mapping an unrecognised code to OK is how a file written by
        something else becomes a window that looks measured and is not."""
        coordinator = await self._coordinator(hass, entry)
        snapshot = coordinator.window_snapshot()
        snapshot["keys"] = ["pv"]
        snapshot["rows"] = [["2026-08-01T00:00:00+00:00", 3600, [1.0], "Z", "i"]]

        assert coordinator._buckets_from_snapshot(snapshot) == []

    async def test_a_row_whose_codes_do_not_match_its_channels_restores_nothing(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
    ) -> None:
        coordinator = await self._coordinator(hass, entry)
        snapshot = coordinator.window_snapshot()
        snapshot["keys"] = ["pv", "load"]
        snapshot["rows"] = [["2026-08-01T00:00:00+00:00", 3600, [1.0, 2.0], "O", "ii"]]

        assert coordinator._buckets_from_snapshot(snapshot) == []

    async def test_a_file_with_no_window_is_simply_no_window(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
    ) -> None:
        """An installation upgrading from a release before this one has no
        window in its file, and that is not an error."""
        coordinator = await self._coordinator(hass, entry)

        assert coordinator._buckets_from_snapshot(None) == []
        assert coordinator._buckets_from_snapshot({}) == []
