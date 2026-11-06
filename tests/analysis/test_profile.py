"""Typical hours, learned from the buckets.

The profile feeds every hour of the projection, so the two things it must never
do are the two that flatter a battery: read a missing hour as a quiet one, and
let the night being projected leak into the history it was projected from.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from analysis.model import Bucket, BucketSource, Quality
from analysis.profile import (
    MIN_SAMPLES_PER_HOUR,
    grid_charged_hours,
    hourly_profile,
    max_charge_w,
)

START = datetime(2026, 6, 1, tzinfo=UTC)


def bucket(start: datetime, seconds: int = 3600, **wh: float | None) -> Bucket:
    return Bucket(
        start_utc=start,
        seconds=seconds,
        wh=dict(wh),
        quality={k: (Quality.OK if v is not None else Quality.MISSING) for k, v in wh.items()},
        source=dict.fromkeys(wh, BucketSource.OWN_INTEGRAL),
    )


def days_of(days: int, **per_hour) -> list[Bucket]:
    """``days`` days of buckets; each keyword is a function of the UTC hour."""
    out: list[Bucket] = []
    for h in range(days * 24):
        start = START + timedelta(hours=h)
        out.append(bucket(start, **{k: f(start.hour) for k, f in per_hour.items()}))
    return out


def utc_hour(when: datetime) -> int:
    return when.hour


class TestTheProfile:
    def test_each_hour_is_the_mean_of_its_days(self) -> None:
        buckets = days_of(4, load=lambda h: 100.0 * h)
        profile = hourly_profile(buckets, "load", utc_hour, until=START + timedelta(days=4))

        assert profile.ready
        assert profile.wh[5] == pytest.approx(500.0)
        assert profile.days == 4

    def test_the_mean_keeps_the_oven_in(self) -> None:
        """Three quiet evenings and one with the oven on. A median would drop
        it; the projection adds hours up, so the mean is the one that is right."""
        buckets = days_of(4, load=lambda h: 400.0)
        oven = buckets[3 * 24 + 19]
        buckets[3 * 24 + 19] = bucket(oven.start_utc, load=2400.0)
        profile = hourly_profile(buckets, "load", utc_hour, until=START + timedelta(days=4))

        assert profile.wh[19] == pytest.approx(900.0)

    def test_too_few_days_is_not_ready_and_never_zero(self) -> None:
        buckets = days_of(MIN_SAMPLES_PER_HOUR - 1, load=lambda h: 300.0)
        profile = hourly_profile(buckets, "load", utc_hour, until=START + timedelta(days=10))

        assert not profile.ready
        assert all(value is None for value in profile.wh)

    def test_a_missing_reading_is_absent_not_quiet(self) -> None:
        buckets = days_of(3, load=lambda h: 300.0)
        buckets[24 + 2] = bucket(buckets[24 + 2].start_utc, load=None)
        profile = hourly_profile(buckets, "load", utc_hour, until=START + timedelta(days=3))

        assert profile.wh[2] is None
        assert profile.samples[2] == 2
        assert not profile.ready

    def test_a_partial_hour_is_not_a_quiet_one(self) -> None:
        """The first bucket after a restart covers part of an hour."""
        buckets = days_of(3, load=lambda h: 300.0)
        buckets[0] = bucket(START, seconds=900, load=75.0)
        profile = hourly_profile(buckets, "load", utc_hour, until=START + timedelta(days=3))

        assert profile.samples[0] == 2

    def test_the_future_cannot_leak_into_the_past(self) -> None:
        """A replay asking for the profile at noon on day 3 must not see day 3's
        afternoon, or the backtest would grade the projection on its own answer."""
        buckets = days_of(5, load=lambda h: 300.0)
        until = START + timedelta(days=3, hours=12)
        profile = hourly_profile(buckets, "load", utc_hour, until=until)

        # Days 0-2 whole, plus day 3's morning — and nothing from 12:00 on.
        assert profile.samples[11] == 4
        assert profile.samples[12] == 3
        assert profile.samples[23] == 3

    def test_only_the_last_fortnight_counts(self) -> None:
        buckets = days_of(30, load=lambda h: 300.0)
        profile = hourly_profile(buckets, "load", utc_hour, until=START + timedelta(days=30))
        assert profile.days == 14

    def test_production_takes_the_median(self) -> None:
        buckets = days_of(3, pv=lambda h: 1000.0)
        buckets[12] = bucket(buckets[12].start_utc, pv=100.0)
        profile = hourly_profile(
            buckets, "pv", utc_hour, until=START + timedelta(days=3), typical="median"
        )
        assert profile.wh[12] == pytest.approx(1000.0)


class TestTheChargingCeiling:
    def test_the_ceiling_is_the_battery_at_its_busiest(self) -> None:
        buckets = days_of(30, battery_charge=lambda h: 2500.0 if 9 <= h < 12 else 0.0)
        ceiling = max_charge_w(buckets, "battery_charge", until=START + timedelta(days=30))
        assert ceiling == pytest.approx(2500.0)

    def test_no_ceiling_without_enough_charging(self) -> None:
        buckets = days_of(3, battery_charge=lambda h: 2000.0 if h == 10 else 0.0)
        assert max_charge_w(buckets, "battery_charge", until=START + timedelta(days=3)) is None


class TestGridChargingIsCounted:
    def test_charging_the_sun_cannot_explain_is_counted(self) -> None:
        buckets = days_of(
            2,
            pv=lambda h: 0.0,
            load=lambda h: 300.0,
            battery_charge=lambda h: 1500.0 if h in (2, 3) else 0.0,
        )
        count = grid_charged_hours(
            buckets,
            pv="pv",
            load="load",
            charge="battery_charge",
            until=START + timedelta(days=2),
        )
        assert count == 4

    def test_solar_charging_is_not(self) -> None:
        buckets = days_of(
            2,
            pv=lambda h: 3000.0 if 9 <= h < 15 else 0.0,
            load=lambda h: 500.0,
            battery_charge=lambda h: 2400.0 if 9 <= h < 15 else 0.0,
        )
        count = grid_charged_hours(
            buckets,
            pv="pv",
            load="load",
            charge="battery_charge",
            until=START + timedelta(days=2),
        )
        assert count == 0

    def test_unknown_without_the_channels(self) -> None:
        buckets = days_of(2, load=lambda h: 300.0)
        assert (
            grid_charged_hours(
                buckets,
                pv="pv",
                load="load",
                charge="battery_charge",
                until=START + timedelta(days=2),
            )
            is None
        )
