"""The first hour after a restart is not a whole hour, and must not claim to be.

Solar Sanity integrates power on the sensor's own state changes, so a restart at
half past leaves it holding thirty minutes of a sixty-minute bucket. Claiming
the full hour applies a full hour of standby draw to half an hour of energy and
silently degrades the day — and it is not rare, it is every restart on every
installation.

``build_days`` drops anything not exactly 3600 seconds, so a bucket that tells
the truth about its own length is discarded rather than believed. That is the
whole mechanism: ``seconds`` is not decoration, it is the field that keeps a
partial hour out of the arithmetic.

``_close_bucket`` is called directly here with explicit times rather than driven
by moving the clock. The survey that proposed this test warned that crossing an
hour boundary needs both a frozen clock and the bucket timer fired, and that
``accumulate`` and the power tracker read the time from different places — which
is a recipe for a test that fails on a slow CI runner for reasons unrelated to
the thing it checks. The arithmetic under test takes its times as arguments, so
it can simply be given them.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _hour() -> datetime:
    return dt_util.utcnow().replace(minute=0, second=0, microsecond=0)


async def _coordinator(hass: HomeAssistant, entry: MockConfigEntry):
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data.coordinator


async def test_a_bucket_started_late_reports_only_the_time_it_covered(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """Restarted at half past: thirty minutes, and it says thirty minutes."""
    coordinator = await _coordinator(hass, entry)
    start = _hour()
    coordinator._first_sample_at = start + timedelta(minutes=30)
    before = len(coordinator._buckets)

    coordinator._close_bucket(start, start + timedelta(hours=1))

    assert len(coordinator._buckets) == before + 1
    assert coordinator._buckets[-1].seconds == 1800


async def test_a_whole_hour_is_still_a_whole_hour(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """The control. Without it the test above passes on a coordinator that
    reports every bucket short, which would discard every hour there is."""
    coordinator = await _coordinator(hass, entry)
    start = _hour()
    coordinator._first_sample_at = start - timedelta(hours=3)

    coordinator._close_bucket(start, start + timedelta(hours=1))

    assert coordinator._buckets[-1].seconds == 3600


async def test_a_first_sample_before_the_hour_does_not_stretch_it(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """``max(start, started)`` is doing real work.

    Integration that began before this hour still only contributes this hour to
    this bucket. Without the clamp a coordinator running for three hours would
    stamp the next bucket at 10,800 seconds, which ``build_days`` would drop —
    turning a correctly running installation into one with no complete hours at
    all.
    """
    coordinator = await _coordinator(hass, entry)
    start = _hour()
    coordinator._first_sample_at = start - timedelta(hours=3)

    coordinator._close_bucket(start, start + timedelta(hours=1))

    assert coordinator._buckets[-1].seconds == 3600


async def test_the_bucket_is_stamped_as_our_own_measurement(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """Provenance decides the tolerance band, so it cannot be guessed at.

    Hours this integration measured itself are held to 10%; hours derived from
    an hourly mean are held to 16%, because an arithmetic mean over an
    event-reporting sensor cannot say whether its hour was complete. A bucket we
    integrated must never be stamped as anything else.
    """
    from custom_components.solar_sanity.analysis.model import BucketSource

    coordinator = await _coordinator(hass, entry)
    start = _hour()

    coordinator._close_bucket(start, start + timedelta(hours=1))

    sources = set(coordinator._buckets[-1].source.values())
    assert sources == {BucketSource.OWN_INTEGRAL}
