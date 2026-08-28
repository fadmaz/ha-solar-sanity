"""Scoring a forecast against what actually happened.

This is a different problem from the rest of the engine, and the difference is
worth stating before any of the arithmetic.

The residual check works because physics leaves an empty band: meter noise
stops around five percent, the smallest real fault starts around fifty, and
nothing lives in between. **Forecast error has no such gap.** A provider's model
error, an omitted temperature derate, light soiling and a little shading all sit
in the same five-to-fifteen percent range. So no forecast figure may ever be a
fault, no matter how large or how stable — at most it is a note, and the copy
must say plainly that it cannot tell a forecast running high apart from an array
producing less than it could.

What that leaves is still worth having: a number, with a stated horizon, that
holds still long enough to mean something. Everything here exists to decide
whether such a number has been earned, and to stay quiet otherwise.

Pure, like the rest of ``analysis``: no clock, no zone database, no Home
Assistant. The mapping from an hour to the local day it belongs to is injected,
because resolving it needs a time zone and this package deliberately has none.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime

from .linalg import iqr, median, pearson, safe_ratio

#: Below this a day is too small to divide by. A December afternoon of two
#: kilowatt-hours turns any absolute discrepancy into an enormous ratio.
FORECAST_MIN_DAY_KWH = 2.0

#: ...and a day must also be a reasonable share of a typical one in the window,
#: for the same reason applied relatively.
FORECAST_MIN_DAY_SHARE = 0.25

#: Qualifying days before any figure is stated, and the calendar span they must
#: cover. Both are needed: twenty-one days inside one fortnight is one weather
#: regime seen twice, not three weeks of evidence.
FORECAST_MIN_DAYS = 21
FORECAST_MIN_SPAN_DAYS = 28

#: Qualifying days required in each third of the span. Weather regimes run three
#: to seven days, so thirty days is six to ten independent episodes and a figure
#: can otherwise rest almost entirely on one of them.
FORECAST_MIN_DAYS_PER_THIRD = 5

#: How far the median-of-ratios and the energy-weighted figure may differ. They
#: answer the same question two ways; when they disagree the bias is not
#: multiplicative — it is additive, or one day dominates — and the right move is
#: silence rather than picking whichever looks better.
FORECAST_AGREEMENT = 0.05

#: Deliberately asymmetric. "Produced less than forecast" is the claim that
#: sends somebody onto a roof, and the one confound that only ever pushes in
#: that direction — an omitted temperature derate — is not measurable here.
FORECAST_BIAS_MIN_OVER = 0.08
FORECAST_BIAS_MIN_UNDER = 0.12

#: Widening applied when generation is derived from hourly means, matching what
#: the residual bands already do for the same data. On an installation whose PV
#: is mean-backed this lifts the under threshold to nineteen percent, and such
#: an installation may simply never qualify. That is the correct outcome.
MEAN_TOLERANCE_FACTOR = 1.6

#: Stability. A centre that describes no day the reader will ever see is worse
#: than no centre, because quoting it invites them to expect it tomorrow.
FORECAST_MAX_SCATTER = 0.60
FORECAST_MAX_CORRELATION = 0.5
FORECAST_SPLIT_TOLERANCE = 0.05
FORECAST_SPLIT_SHARE = 0.4

#: Reported to the nearest five points. "About ten percent under" is true and
#: the same length as the false version with a decimal place on it.
FORECAST_ROUNDING = 5


@dataclass(frozen=True, slots=True)
class ForecastDay:
    """One local day where a forecast and a measurement can be compared."""

    day: date
    forecast_kwh: float
    actual_kwh: float

    @property
    def ratio(self) -> float | None:
        """Actual over forecast. ``None`` when there is nothing to divide by."""
        return safe_ratio(self.actual_kwh, self.forecast_kwh)


@dataclass(frozen=True, slots=True)
class Bias:
    """What can and cannot be said about a provider, and why."""

    days: int
    #: Signed, unrounded. Negative means the system produced less than forecast.
    #: Present whenever it could be computed, even when it may not be stated —
    #: measuring and asserting are different things.
    value: float | None = None
    #: The figure to show, snapped to five points. ``None`` means stay quiet.
    reportable_pct: int | None = None
    reason: str = ""
    measurements: dict[str, float] = field(default_factory=dict)

    @property
    def direction(self) -> str:
        """ "over", "under" or "level" — never a sign for a reader to decode."""
        if self.reportable_pct is None or self.reportable_pct == 0:
            return "level"
        return "under" if self.reportable_pct < 0 else "over"


def build_days(
    forecast: Mapping[datetime, float],
    actual: Mapping[datetime, float],
    local_date: Callable[[datetime], date],
) -> list[ForecastDay]:
    """Pair a forecast with a measurement, day by day.

    Both sides are summed over exactly the same hours, and a day is kept only
    when every hour the provider expected something from has a measurement
    beside it. One absent midday hour is most of a day's energy, and a day is
    cheap where a fabricated bias is not.
    """
    wanted: dict[date, list[datetime]] = {}
    for hour in forecast:
        wanted.setdefault(local_date(hour), []).append(hour)

    days: list[ForecastDay] = []
    for day in sorted(wanted):
        hours = wanted[day]
        if any(forecast[hour] > 0 and hour not in actual for hour in hours):
            continue
        paired = [hour for hour in hours if hour in actual]
        if not paired:
            continue
        days.append(
            ForecastDay(
                day=day,
                forecast_kwh=sum(forecast[hour] for hour in paired),
                actual_kwh=sum(actual[hour] for hour in paired),
            )
        )
    return days


def eligible(days: list[ForecastDay]) -> list[ForecastDay]:
    """Days large enough for a ratio to mean anything."""
    forecasts = [day.forecast_kwh for day in days if day.forecast_kwh > 0]
    typical = median(forecasts)
    if typical is None:
        return []
    floor = max(FORECAST_MIN_DAY_KWH, typical * FORECAST_MIN_DAY_SHARE)
    return [day for day in days if day.forecast_kwh >= floor]


def estimate(days: list[ForecastDay], *, from_mean: bool = False) -> Bias:
    """Whether a bias has been earned, and what it is.

    Every gate is conjunctive and every one of them ends in silence. The order
    is deliberate: cheap structural checks first, so the reason given back names
    the first thing actually missing rather than the last thing tested.
    """
    usable = eligible(days)
    ratios = [r for day in usable if (r := day.ratio) is not None]
    errors = [r - 1.0 for r in ratios]

    if len(usable) < FORECAST_MIN_DAYS:
        return Bias(
            days=len(usable),
            reason=(f"{len(usable)} comparable days so far; a figure needs {FORECAST_MIN_DAYS}."),
        )

    span = (usable[-1].day - usable[0].day).days + 1
    if span < FORECAST_MIN_SPAN_DAYS:
        return Bias(
            days=len(usable),
            reason=(
                f"Those {len(usable)} days cover only {span}. Weather runs in "
                "spells, so a short window is one spell seen repeatedly."
            ),
        )

    centre = median(ratios)
    if centre is None:
        return Bias(days=len(usable), reason="No comparable days.")
    value = centre - 1.0

    weighted = safe_ratio(
        sum(day.actual_kwh for day in usable), sum(day.forecast_kwh for day in usable)
    )
    measured: dict[str, float] = {
        "forecast_days": float(len(usable)),
        "forecast_span_days": float(span),
        "forecast_bias": value,
    }
    # Absent rather than zero. A scatter that could not be computed and a
    # scatter of nothing are different facts, and this project has a test that
    # says so.
    if (scatter := iqr(errors)) is not None:
        measured["forecast_scatter_iqr"] = scatter
    if weighted is not None:
        measured["forecast_bias_energy_weighted"] = weighted - 1.0

    fails = _instability(usable, ratios, errors, value=value, weighted=weighted, measured=measured)
    if fails:
        return Bias(days=len(usable), value=value, reason=fails, measurements=measured)

    threshold = FORECAST_BIAS_MIN_UNDER if value < 0 else FORECAST_BIAS_MIN_OVER
    if from_mean:
        threshold *= MEAN_TOLERANCE_FACTOR
    measured["forecast_threshold"] = threshold

    if abs(value) < threshold:
        return Bias(
            days=len(usable),
            value=value,
            reason="The forecast tracks what happens closely enough to say nothing.",
            measurements=measured,
        )

    return Bias(
        days=len(usable),
        value=value,
        reportable_pct=_snap(value),
        measurements=measured,
    )


def _instability(
    days: list[ForecastDay],
    ratios: list[float],
    errors: list[float],
    *,
    value: float,
    weighted: float | None,
    measured: dict[str, float],
) -> str:
    """The first reason this figure would not hold still, or an empty string."""
    if weighted is None or abs(value - (weighted - 1.0)) > FORECAST_AGREEMENT:
        return (
            "The typical day and the whole month disagree, so the difference is "
            "not a steady proportion and no single figure describes it."
        )

    scatter = iqr(errors)
    if scatter is not None and scatter > FORECAST_MAX_SCATTER:
        return (
            "The forecast is off by wildly different amounts from day to day. An "
            "average of that describes no day you will actually see."
        )

    half = len(days) // 2
    first = median([r - 1.0 for r in ratios[:half]])
    second = median([r - 1.0 for r in ratios[half:]])
    if first is None or second is None:
        return "Not enough of the window to check whether the figure holds still."
    measured["forecast_bias_first_half"] = first
    measured["forecast_bias_second_half"] = second
    allowed = max(FORECAST_SPLIT_TOLERANCE, FORECAST_SPLIT_SHARE * abs(value))
    if (first < 0) != (second < 0) or abs(first - second) > allowed:
        return (
            "The difference is not the same across the window, so it is "
            "drifting rather than settled."
        )

    thirds = _thirds(days)
    if any(len(part) < FORECAST_MIN_DAYS_PER_THIRD for part in thirds):
        return (
            "The comparable days are bunched into part of the window rather than spread across it."
        )
    signs = {
        (m := median([r for day in part if (r := day.ratio) is not None])) is not None and m < 1.0
        for part in thirds
    }
    if len(signs) > 1:
        return "The forecast runs high in one part of the window and low in another."

    size = pearson([day.forecast_kwh for day in days], errors)
    if size is not None:
        measured["forecast_size_correlation"] = size
        if abs(size) > FORECAST_MAX_CORRELATION:
            return (
                "The gap grows with the size of the day rather than staying a "
                "proportion of it, so a percentage would misdescribe it."
            )

    drift = pearson([float(index) for index in range(len(errors))], errors)
    if drift is not None:
        measured["forecast_drift_correlation"] = drift
        if abs(drift) > FORECAST_MAX_CORRELATION:
            return (
                "The gap is moving steadily in one direction. That is a change "
                "worth understanding on its own, not a level to quote."
            )

    return ""


def _thirds(days: list[ForecastDay]) -> tuple[list[ForecastDay], ...]:
    """Split by *date*, not by count, so a gap shows up as a gap."""
    if not days:
        return ([], [], [])
    start = days[0].day
    span = (days[-1].day - start).days + 1
    edge = max(1, span // 3)
    return (
        [d for d in days if (d.day - start).days < edge],
        [d for d in days if edge <= (d.day - start).days < edge * 2],
        [d for d in days if (d.day - start).days >= edge * 2],
    )


def _snap(value: float) -> int:
    """To the nearest five points, and never to zero from a reportable figure."""
    points = value * 100.0
    snapped = int(round(points / FORECAST_ROUNDING) * FORECAST_ROUNDING)
    if snapped == 0:
        return -FORECAST_ROUNDING if points < 0 else FORECAST_ROUNDING
    return snapped
