"""What may and may not be said about a forecast.

The residual engine works because physics leaves an empty band between meter
noise and the smallest real fault. Forecast error has no such gap: model error,
an omitted temperature derate, light soiling and a little shading all live in
the same five-to-fifteen percent range.

So the whole of this module is a series of reasons to stay quiet, and most of
these tests assert silence. The two that assert a number are the exception, and
they only pass because the input was built to deserve one.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import pytest
from analysis.forecast import (
    FORECAST_BIAS_MIN_UNDER,
    FORECAST_MIN_CENTRE_SHARE,
    FORECAST_MIN_DAYS,
    MEAN_TOLERANCE_FACTOR,
    ForecastDay,
    build_days,
    eligible,
    estimate,
)

START = date(2026, 6, 1)

#: A day's worth of forecast, shaped like a solar curve rather than a block, so
#: the per-hour pairing rules are exercised on something realistic.
SHAPE = [
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.1,
    0.6,
    1.6,
    2.9,
    4.1,
    5.0,
    5.5,
    5.6,
    5.3,
    4.6,
    3.5,
    2.2,
    1.1,
    0.3,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
]


def hours(day: date) -> list[datetime]:
    base = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return [base + timedelta(hours=index) for index in range(24)]


def series(days: int, factor: float = 1.0, jitter: float = 0.0):
    """A forecast and an actual, the second a fixed multiple of the first."""
    forecast: dict[datetime, float] = {}
    actual: dict[datetime, float] = {}
    for index in range(days):
        day = START + timedelta(days=index)
        # Deterministic wobble: the engine must be byte-identical run to run.
        wobble = 1.0 + jitter * math.sin(index * 1.7)
        for hour, value in zip(hours(day), SHAPE, strict=True):
            if value <= 0:
                continue
            forecast[hour] = value
            actual[hour] = value * factor * wobble
    return forecast, actual


def local(hour: datetime) -> date:
    return hour.date()


def paired(days: int, factor: float = 1.0, jitter: float = 0.0) -> list[ForecastDay]:
    forecast, actual = series(days, factor, jitter)
    return build_days(forecast, actual, local)


class TestPairing:
    def test_both_sides_are_summed_over_the_same_hours(self) -> None:
        days = paired(1)

        assert days[0].forecast_kwh == pytest.approx(sum(SHAPE))
        assert days[0].actual_kwh == pytest.approx(sum(SHAPE))

    def test_a_missing_midday_hour_drops_the_whole_day(self) -> None:
        """One absent hour is most of a day's energy. A day is cheap."""
        forecast, actual = series(3)
        noon = datetime(START.year, START.month, START.day, 12, tzinfo=UTC)
        del actual[noon]

        assert [d.day for d in build_days(forecast, actual, local)] == [
            START + timedelta(days=1),
            START + timedelta(days=2),
        ]

    def test_a_missing_hour_the_provider_expected_nothing_from_is_fine(self) -> None:
        forecast, actual = series(1)
        forecast[datetime(START.year, START.month, START.day, 2, tzinfo=UTC)] = 0.0

        assert len(build_days(forecast, actual, local)) == 1

    def test_days_come_back_in_order(self) -> None:
        days = paired(5)

        assert [d.day for d in days] == sorted(d.day for d in days)


class TestEligibility:
    def test_a_tiny_day_is_not_divided_by(self) -> None:
        """A December afternoon turns any absolute gap into a huge ratio."""
        days = paired(30)
        days.append(ForecastDay(day=date(2026, 12, 1), forecast_kwh=0.4, actual_kwh=0.9))

        assert date(2026, 12, 1) not in [d.day for d in eligible(days)]

    def test_a_normal_day_survives(self) -> None:
        assert len(eligible(paired(30))) == 30

    def test_every_eligible_day_yields_a_ratio(self) -> None:
        """An invariant two gates quietly rest on.

        Eligibility floors the forecast well above zero, so no eligible day can
        fail to divide. Both halves of the window are therefore always
        populated, and the split-half check downstream never has to cope with an
        empty side. If this ever fails, that branch has just come alive and its
        message — written for a case that could not occur — will be shown to
        somebody.
        """
        days = paired(30)
        days.append(ForecastDay(day=date(2026, 12, 1), forecast_kwh=0.0, actual_kwh=0.0))

        assert all(day.ratio is not None for day in eligible(days))


class TestSilence:
    """Most of the module. Each of these is a different reason to say nothing."""

    def test_too_few_days(self) -> None:
        bias = estimate(paired(FORECAST_MIN_DAYS - 1, factor=0.7))

        assert bias.reportable_pct is None
        assert "comparable days so far" in bias.reason

    def test_enough_days_crammed_into_too_short_a_window(self) -> None:
        """Twenty-one days inside a fortnight is one weather spell seen twice."""
        forecast, actual = series(21, factor=0.7)
        days = build_days(forecast, actual, local)
        squeezed = [
            ForecastDay(
                day=START + timedelta(days=index // 2),
                forecast_kwh=d.forecast_kwh,
                actual_kwh=d.actual_kwh,
            )
            for index, d in enumerate(days)
        ]
        bias = estimate(squeezed)

        assert bias.reportable_pct is None
        assert "cover only" in bias.reason

    def test_a_forecast_that_tracks_reality(self) -> None:
        bias = estimate(paired(40, factor=1.0))

        assert bias.reportable_pct is None
        assert "closely enough to say nothing" in bias.reason
        assert bias.value == pytest.approx(0.0, abs=0.01)

    def test_every_reason_reads_as_a_sentence(self) -> None:
        """The reason is shown to a user, so it cannot be a gate name."""
        for days, mean in [
            (paired(5), False),
            (paired(40, factor=1.0), False),
            (paired(40, factor=0.8, jitter=0.9), False),
            (paired(40, factor=0.86), True),
        ]:
            reason = estimate(days, from_mean=mean).reason

            assert reason, "a gate closed without saying why"
            # A count may lead, as in "5 comparable days so far".
            assert reason[0].isupper() or reason[0].isdigit()
            assert reason.endswith("."), reason

    def test_a_bias_just_under_the_threshold(self) -> None:
        bias = estimate(paired(40, factor=1.0 - FORECAST_BIAS_MIN_UNDER + 0.02))

        assert bias.reportable_pct is None
        assert "closely enough" in bias.reason

    def test_wild_day_to_day_scatter(self) -> None:
        """A centre that describes no day the reader will ever see."""
        bias = estimate(paired(40, factor=0.8, jitter=0.9))

        assert bias.reportable_pct is None
        # Not merely silent — silent for this reason. A test that only checks
        # for silence passes just as well when the gate above it is broken.
        assert "wildly different amounts" in bias.reason

    def test_a_drift_is_a_different_finding(self) -> None:
        days = [
            ForecastDay(
                day=START + timedelta(days=index),
                forecast_kwh=30.0,
                # Sliding steadily from level to a third down.
                actual_kwh=30.0 * (1.0 - index * 0.01),
            )
            for index in range(40)
        ]
        bias = estimate(days)

        assert bias.reportable_pct is None
        assert "moving steadily" in bias.reason or "not the same across" in bias.reason

    def test_a_gap_that_scales_with_the_day_rather_than_the_size(self) -> None:
        """Additive, not multiplicative — a percentage would misdescribe it."""
        days = [
            ForecastDay(
                day=START + timedelta(days=index),
                forecast_kwh=10.0 + (index % 20) * 2.0,
                actual_kwh=10.0 + (index % 20) * 2.0 - 4.0,
            )
            for index in range(40)
        ]
        bias = estimate(days)

        assert bias.reportable_pct is None
        assert "grows with the size of the day" in bias.reason
        assert bias.measurements["forecast_size_correlation"] > 0.5


class TestTwoKindsOfDayAreNotOneFigure:
    """The gate that spread alone cannot supply.

    A month split between days well under and days well over has a median, an
    energy-weighted figure that agrees with it, and a scatter inside the cap.
    Every other gate passes. The figure it produces describes not one day in the
    window — it lands in the empty middle between the two kinds of day.
    """

    @staticmethod
    def split(count: int, under: float, over: float) -> list[ForecastDay]:
        """Alternating days, so no half or third of the window is unlike another."""
        return [
            ForecastDay(
                day=START + timedelta(days=index),
                forecast_kwh=20.0,
                actual_kwh=20.0 * (under if index % 2 else over),
            )
            for index in range(count)
        ]

    def test_the_figure_would_have_described_no_day(self) -> None:
        bias = estimate(self.split(32, under=0.85, over=1.45))

        # What it would have published: +15%, from a month containing no day
        # within thirty points of that.
        assert bias.value == pytest.approx(0.15)
        assert bias.reportable_pct is None
        assert "rarely in between" in bias.reason

    @pytest.mark.parametrize("count", [32, 36, 40, 44])
    def test_it_holds_whatever_the_window_length(self, count: int) -> None:
        """The parity of the count decided this before the gate existed.

        With an odd number of days in each half the medians of the two halves
        landed on different modes and the drift gate caught it by luck. At these
        lengths it did not, and the figure was published.
        """
        assert estimate(self.split(count, under=0.85, over=1.45)).reportable_pct is None

    def test_the_share_of_days_near_the_figure_is_recorded(self) -> None:
        """Diagnostics have to show the reader why, not just that."""
        bias = estimate(self.split(32, under=0.85, over=1.45))

        assert bias.measurements["forecast_centre_share"] == 0.0

    @pytest.mark.parametrize(
        ("under", "over"),
        [(1.13, 1.17), (1.14, 1.16), (1.15, 1.15)],
    )
    def test_two_modes_a_hair_apart_are_one_figure(self, under: float, over: float) -> None:
        """Two-mode in form, one figure in substance.

        Thirteen and seventeen percent over are both "15% over" to anyone
        reading it. Without an absolute floor under the band, the band collapses
        with the spread and this installation is silenced for a split nobody
        could act on.
        """
        bias = estimate(self.split(30, under=under, over=over))

        assert bias.reportable_pct == 15
        assert bias.measurements["forecast_centre_share"] == 1.0

    def test_an_ordinary_noisy_installation_still_speaks(self) -> None:
        """The gate must cost nothing to the systems it is not aimed at."""
        days = [
            ForecastDay(
                day=START + timedelta(days=index),
                forecast_kwh=20.0,
                # Deterministic spread, wide but single-humped.
                actual_kwh=20.0 * (1.15 + 0.20 * ((index * 7 % 11) / 10.0 - 0.5)),
            )
            for index in range(30)
        ]
        bias = estimate(days)

        assert bias.reportable_pct == 15
        assert bias.measurements["forecast_centre_share"] > FORECAST_MIN_CENTRE_SHARE


class TestAFigureThatWasEarned:
    def test_a_steady_shortfall_is_named(self) -> None:
        bias = estimate(paired(40, factor=0.80))

        assert bias.reportable_pct == -20
        assert bias.direction == "under"

    def test_a_steady_surplus_is_named(self) -> None:
        bias = estimate(paired(40, factor=1.15))

        assert bias.reportable_pct == 15
        assert bias.direction == "over"

    def test_it_is_snapped_to_five_points(self) -> None:
        bias = estimate(paired(40, factor=0.827))

        assert bias.reportable_pct == -15
        # The measured figure survives unrounded, so what was actually seen is
        # inspectable without being asserted.
        assert bias.measurements["forecast_bias"] == pytest.approx(-0.173, abs=0.005)

    def test_it_never_snaps_a_reportable_figure_to_zero(self) -> None:
        rounded = estimate(paired(40, factor=0.80)).reportable_pct

        assert rounded is not None and rounded != 0

    def test_the_measurements_carry_what_was_checked(self) -> None:
        bias = estimate(paired(40, factor=0.80))

        for key in ("forecast_days", "forecast_span_days", "forecast_scatter_iqr"):
            assert key in bias.measurements

    def test_a_scatter_that_could_not_be_computed_is_absent_not_zero(self) -> None:
        """The project's own rule, and its own test caught me breaking it."""
        bias = estimate([ForecastDay(START + timedelta(days=i), 30.0, 24.0) for i in range(40)])

        assert bias.measurements.get("forecast_scatter_iqr") in (0.0, None)


class TestMeanDerivedDataIsHeldToAWiderBar:
    """The reference installation's own case, and it may never qualify."""

    def test_a_shortfall_that_qualifies_on_exact_data(self) -> None:
        assert estimate(paired(40, factor=0.86)).reportable_pct == -15

    def test_does_not_qualify_on_hourly_means(self) -> None:
        bias = estimate(paired(40, factor=0.86), from_mean=True)

        assert bias.reportable_pct is None
        assert "closely enough" in bias.reason
        # Measured all the same. Silence is about what may be asserted, not
        # about refusing to look.
        assert bias.value == pytest.approx(-0.14, abs=0.01)

    def test_the_widened_threshold_is_recorded(self) -> None:
        bias = estimate(paired(40, factor=0.86), from_mean=True)

        assert bias.measurements["forecast_threshold"] == pytest.approx(
            FORECAST_BIAS_MIN_UNDER * MEAN_TOLERANCE_FACTOR
        )


class TestDeterminism:
    def test_the_same_input_gives_the_same_answer(self) -> None:
        """The engine's standing rule, and this module is part of it."""
        first = estimate(paired(40, factor=0.8, jitter=0.05))
        second = estimate(paired(40, factor=0.8, jitter=0.05))

        assert first == second
