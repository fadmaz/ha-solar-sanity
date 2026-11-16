"""Evidence for using an unscaled day-ahead forecast in manual planning."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from math import isfinite

from .forecast import FORECAST_MIN_DAYS, ForecastDay, eligible, estimate

FIT_DAYS = 45
VALIDATION_DAYS = 7
# Manual planning gates. The shift test uses the same observed hours for every
# alignment, so moving the forecast cannot improve the score by dropping a peak.
MAX_HOURLY_ERROR_PCT = 20.0
MAX_SHIFT_ADVANTAGE_PCT = 5.0
HOUR = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class ForecastFit:
    reason: str
    comparable_days: int = 0
    training_days: int = 0
    validation_days: int = 0
    compared_hours: int = 0
    hourly_error_pct: float | None = None
    exclusions: dict[str, int] = field(default_factory=dict)

    @property
    def eligible(self) -> bool:
        return self.reason == "validated"


def evaluate_fit(
    forecast: Mapping[datetime, float],
    actual: Mapping[datetime, float],
    daylight: Mapping[date, tuple[datetime, ...]],
    *,
    uncurtailed: set[datetime] | None = None,
    curtailed: frozenset[datetime] = frozenset(),
) -> ForecastFit:
    """Use disjoint local days. Never learn from an incomplete daylight curve."""
    examples: dict[date, tuple[datetime, ...]] = {}
    totals: list[ForecastDay] = []
    excluded: Counter[str] = Counter()
    for day, hours in sorted(daylight.items()):
        if not hours:
            excluded["daylight_unavailable"] += 1
        elif not all(_valid(forecast, hour) for hour in hours):
            excluded["forecast_incomplete"] += 1
        elif not all(_valid(actual, hour) for hour in hours):
            excluded["measurement_incomplete"] += 1
        elif not curtailed.isdisjoint(hours):
            excluded["confirmed_curtailment"] += 1
        elif uncurtailed is not None and not uncurtailed.issuperset(hours):
            excluded["curtailment_not_ruled_out"] += 1
        else:
            examples[day] = hours
            totals.append(
                ForecastDay(
                    day, sum(forecast[hour] for hour in hours), sum(actual[hour] for hour in hours)
                )
            )
    if len(totals) < FORECAST_MIN_DAYS + VALIDATION_DAYS:
        return ForecastFit(
            "insufficient_days", comparable_days=len(totals), exclusions=dict(excluded)
        )

    validation = totals[-VALIDATION_DAYS:]
    training = totals[:-VALIDATION_DAYS]
    scored = estimate(training)
    excluded["low_forecast_energy"] += len(training) - len(eligible(training))
    counts = {
        "comparable_days": len(totals),
        "training_days": scored.days,
        "validation_days": len(validation),
        "exclusions": dict(+excluded),
    }
    if not scored.neutral:
        reason = (
            "insufficient_days"
            if "forecast_bias" not in scored.measurements
            else "non_neutral_daily_score"
        )
        return ForecastFit(reason, **counts)

    hours = [hour for day in validation for hour in examples[day.day]]
    total_actual = sum(actual[hour] for hour in hours)
    if total_actual <= 0:
        return ForecastFit("insufficient_production", **counts)
    error = _error_pct(forecast, actual, hours)
    # Only use adjacent hours from the same day's complete daylight window.
    interior = [hour for day in validation for hour in examples[day.day][1:-1]]
    if not interior or sum(actual[hour] for hour in interior) <= 0:
        reason = "alignment_unavailable"
    elif (
        _error_pct(forecast, actual, interior)
        - min(
            _error_pct(forecast, actual, interior, shift=-HOUR),
            _error_pct(forecast, actual, interior, shift=HOUR),
        )
        > MAX_SHIFT_ADVANTAGE_PCT
    ):
        reason = "hourly_misalignment"
    elif error > MAX_HOURLY_ERROR_PCT:
        reason = "hourly_error_high"
    else:
        reason = "validated"
    return ForecastFit(reason, compared_hours=len(hours), hourly_error_pct=error, **counts)


def _valid(values: Mapping[datetime, float], hour: datetime) -> bool:
    value = values.get(hour)
    return value is not None and isfinite(value) and value >= 0


def _error_pct(
    forecast: Mapping[datetime, float],
    actual: Mapping[datetime, float],
    hours: list[datetime],
    *,
    shift: timedelta = timedelta(),
) -> float:
    error = sum(abs(actual[hour] - forecast[hour + shift]) for hour in hours)
    return 100 * error / sum(actual[hour] for hour in hours)
