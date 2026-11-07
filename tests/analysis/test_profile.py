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
    FULL_BAND_PCT,
    MIN_SAMPLES_PER_HOUR,
    MIN_STANDBY_HOURS,
    forecast_scale,
    grid_charged_hours,
    hourly_profile,
    max_charge_w,
    standby_w,
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


CHANNELS = {
    "pv": "pv",
    "load": "load",
    "charge": "battery_charge",
    "discharge": "battery_discharge",
    "grid": "grid_import",
}


def nights_of(days: int, *, load: float, delivered: float, **extra) -> list[Bucket]:
    """Dark from 18:00 to 06:00 UTC, the battery carrying the house alone."""

    def dark(h: int) -> bool:
        return h >= 18 or h < 6

    return days_of(
        days,
        pv=lambda h: 0.0 if dark(h) else 3000.0,
        load=lambda h: load,
        battery_charge=lambda h: 0.0 if dark(h) else 1500.0,
        battery_discharge=lambda h: delivered if dark(h) else 0.0,
        **extra,
    )


class TestTheInvertersOwnDraw:
    def test_what_the_battery_gives_beyond_the_house_is_learned(self) -> None:
        """Delivering 600 Wh to a 452 W house at 0.92 leaves 600 x 0.92 - 452 =
        100 W the house meter never saw."""
        buckets = nights_of(3, load=452.0, delivered=600.0, grid_import=lambda h: 0.0)
        learned = standby_w(
            buckets, **CHANNELS, discharge_efficiency=0.92, until=START + timedelta(days=3)
        )
        assert learned == pytest.approx(100.0)

    def test_a_battery_metered_on_the_house_side_learns_nothing(self) -> None:
        """Delivery equal to the load is an AC-side meter, not an idle inverter."""
        buckets = nights_of(3, load=500.0, delivered=500.0)
        learned = standby_w(
            buckets, **CHANNELS, discharge_efficiency=0.92, until=START + timedelta(days=3)
        )
        assert learned == 0.0

    def test_hours_the_grid_helped_are_not_the_battery_alone(self) -> None:
        """The grid carrying part of the house makes delivery look small, and
        would teach a negative draw. Those hours are left out, which here
        leaves none."""
        buckets = nights_of(3, load=900.0, delivered=500.0, grid_import=lambda h: 500.0)
        learned = standby_w(
            buckets, **CHANNELS, discharge_efficiency=0.92, until=START + timedelta(days=3)
        )
        assert learned is None

    def test_one_night_is_not_enough(self) -> None:
        buckets = nights_of(1, load=452.0, delivered=600.0)
        assert MIN_STANDBY_HOURS > 12
        assert (
            standby_w(
                buckets, **CHANNELS, discharge_efficiency=0.92, until=START + timedelta(days=1)
            )
            is None
        )

    def test_unknown_without_a_discharge_channel(self) -> None:
        buckets = days_of(3, pv=lambda h: 0.0, load=lambda h: 400.0, battery_charge=lambda h: 0.0)
        assert (
            standby_w(
                buckets, **CHANNELS, discharge_efficiency=0.92, until=START + timedelta(days=3)
            )
            is None
        )


def sunny(hour: int) -> float:
    return 2000.0 if 8 <= hour < 16 else 0.0


def forecast_of(days: int) -> dict[datetime, float]:
    out = {}
    for h in range(days * 24):
        when = START + timedelta(hours=h)
        if sunny(when.hour) > 0:
            out[when] = sunny(when.hour)
    return out


def soc_of(days: int, level=lambda h: 60.0) -> dict[datetime, float]:
    hours = (START + timedelta(hours=h) for h in range(days * 24))
    return {when: level(when.hour) for when in hours}


def scale_of(buckets: list[Bucket], days: int, **kw):
    kw.setdefault("soc_max", soc_of(days))
    kw.setdefault("full_pct", 100.0)
    return forecast_scale(
        buckets,
        forecast_of(max(days, 5)),
        pv="pv",
        grid="grid_import",
        until=START + timedelta(days=days),
        **kw,
    )


class TestTheForecastScale:
    def test_the_scale_is_what_the_roof_delivered_over_what_was_forecast(self) -> None:
        scale = scale_of(days_of(4, pv=lambda h: sunny(h) / 2), 4)

        assert scale is not None
        assert scale.factor == pytest.approx(0.5)
        assert (scale.days, scale.hours) == (4, 32)

    def test_hours_with_a_full_battery_teach_nothing(self) -> None:
        """From 13:00 the battery is full and the inverter turns production down
        to the 300 W the house uses. Counted, those afternoons would say the sun
        is weaker than it is."""
        buckets = days_of(4, pv=lambda h: 300.0 if 13 <= h < 16 else sunny(h) / 2)
        scale = scale_of(buckets, 4, soc_max=soc_of(4, lambda h: 100.0 if h >= 13 else 60.0))

        assert scale is not None
        assert scale.factor == pytest.approx(0.5)
        assert scale.hours == 4 * 5

    def test_full_is_judged_against_the_target(self) -> None:
        """A battery set to stop at 80% is full at 80%."""
        buckets = days_of(4, pv=lambda h: 300.0 if h >= 13 else sunny(h) / 2)
        level = soc_of(4, lambda h: 80.0 - FULL_BAND_PCT if h >= 13 else 60.0)
        scale = scale_of(buckets, 4, soc_max=level, full_pct=80.0)

        assert scale is not None
        assert scale.factor == pytest.approx(0.5)

    def test_an_hour_without_a_charge_level_is_not_assumed_to_have_room(self) -> None:
        assert scale_of(days_of(4, pv=lambda h: sunny(h) / 2), 4, soc_max={}) is None

    def test_hours_the_grid_supplied_are_left_out(self) -> None:
        buckets = days_of(
            4,
            pv=lambda h: 100.0 if h < 12 else sunny(h) / 2,
            grid_import=lambda h: 800.0 if h < 12 else 0.0,
        )
        scale = scale_of(buckets, 4)

        assert scale is not None
        assert scale.factor == pytest.approx(0.5)

    def test_two_days_are_not_enough(self) -> None:
        assert scale_of(days_of(2, pv=lambda h: sunny(h) / 2), 2) is None

    def test_a_scale_out_of_bounds_is_not_believed(self) -> None:
        """A roof delivering ten times its forecast is a forecast for another
        roof, or a production channel that is not the panels."""
        assert scale_of(days_of(4, pv=lambda h: sunny(h) * 10), 4) is None

    def test_the_day_being_projected_cannot_teach_its_own_scale(self) -> None:
        """The fifth day delivered nothing; asked as of its start, it is unseen."""
        buckets = days_of(5, pv=lambda h: sunny(h) / 2)
        for i in range(4 * 24, 5 * 24):
            buckets[i] = bucket(buckets[i].start_utc, pv=0.0)
        scale = scale_of(buckets, 4, soc_max=soc_of(5))

        assert scale is not None
        assert scale.factor == pytest.approx(0.5)
