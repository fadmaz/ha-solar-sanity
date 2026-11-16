"""Hourly evidence must distinguish roof fit from coincident daily totals."""

from datetime import UTC, datetime, timedelta

import pytest
from analysis.forecast_fit import evaluate_fit


def _series():
    start = datetime(2026, 8, 1, 12, tzinfo=UTC)
    forecast, daylight = {}, {}
    for day in range(40):
        noon = start + timedelta(days=day)
        hours = tuple(noon + timedelta(hours=index) for index in range(4))
        daylight[noon.date()] = hours
        forecast.update(
            (hour, scale * (1 + day % 5 / 4))
            for hour, scale in zip(hours, (1, 3, 2, 1), strict=True)
        )
    return forecast, daylight


def test_matching_forecast_passes_on_the_seven_later_days():
    """The daily scorer is neutral, and the held-out curve also fits."""
    forecast, daylight = _series()
    result = evaluate_fit(forecast, forecast, daylight)
    assert result.eligible
    assert result.training_days == 33
    assert result.validation_days == 7
    assert result.compared_hours == 28
    assert result.hourly_error_pct == 0


def test_delayed_production_with_equal_daily_totals_fails():
    """Moving the peak leaves daily bias at zero and moves the best load time."""
    forecast, daylight = _series()
    actual = {
        hour: forecast[previous]
        for hours in daylight.values()
        for hour, previous in zip(hours, (hours[-1], *hours[:-1]), strict=True)
    }
    result = evaluate_fit(forecast, actual, daylight)
    assert result.reason == "hourly_misalignment"
    assert result.hourly_error_pct == pytest.approx(400 / 7)


def test_an_unstable_later_week_cannot_teach_the_neutral_factor():
    """The last seven usable dates are evaluated after fitting the training days."""
    forecast, daylight = _series()
    validation = {hour for day in list(daylight.values())[-7:] for hour in day}
    actual = {hour: value * (2 if hour in validation else 1) for hour, value in forecast.items()}
    result = evaluate_fit(forecast, actual, daylight)
    assert result.reason == "hourly_error_high"
    assert result.hourly_error_pct == 50


def test_incomplete_and_curtailed_days_do_not_count_as_zero_production():
    """Fixed-limit days need affirmative evidence for every daylight hour."""
    forecast, daylight = _series()
    actual = dict(forecast)
    actual.pop(next(iter(actual)))
    reduced = list(forecast)[4]
    unverified = list(forecast)[8]
    result = evaluate_fit(
        forecast,
        actual,
        daylight,
        uncurtailed=set(forecast) - {reduced, unverified},
        curtailed=frozenset({reduced}),
    )
    assert result.eligible
    assert result.comparable_days == 37
    assert result.exclusions == {
        "measurement_incomplete": 1,
        "confirmed_curtailment": 1,
        "curtailment_not_ruled_out": 1,
    }
    assert evaluate_fit(forecast, forecast, daylight, uncurtailed=set()).comparable_days == 0
