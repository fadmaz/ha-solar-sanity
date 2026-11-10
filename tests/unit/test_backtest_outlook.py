"""A missing night is not a successful prediction of its minimum."""

from datetime import UTC, datetime, timedelta

import pytest

from scripts.backtest_outlook import SocSeries, soc_from_diagnostics

START = datetime(2026, 11, 1, tzinfo=UTC)


def test_one_hour_cannot_grade_eight_hours_of_night() -> None:
    """The audit accepted a minimum despite seven unobserved hours."""
    soc = soc_from_diagnostics([{"start": START.isoformat(), "mean": 60, "min": 50, "max": 70}])
    assert soc.lowest_reading(START, START + timedelta(hours=8)) is None


def test_means_do_not_replace_missing_hourly_extremes() -> None:
    """A mean above reserve must not hide a missing minimum."""
    soc = soc_from_diagnostics(
        [{"start": (START + timedelta(hours=i)).isoformat(), "mean": 60} for i in range(3)]
    )
    assert not soc.covers(START, START + timedelta(hours=2))
    assert not soc.covers(START, START + timedelta(hours=2), extreme="max")


def test_a_complete_night_keeps_its_first_lowest_reading() -> None:
    """The end boundary belongs to the next hour and requires no extra row."""
    soc = SocSeries([], {START: 50, START + timedelta(hours=1): 35})
    assert soc.lowest_reading(START, START + timedelta(hours=2)) == (35, START + timedelta(hours=1))


@pytest.mark.parametrize("missing", [0, 2, 4])
def test_a_missing_morning_hour_cannot_be_called_a_failure_to_fill(missing: int) -> None:
    """Both a missing peak and a missing endpoint invalidate a morning."""
    maxima = {START + timedelta(hours=i): 80 for i in range(5) if i != missing}
    soc = SocSeries([], {}, maxima)
    assert not soc.covers(START, START + timedelta(hours=5), extreme="max")


def test_a_csv_gap_in_the_middle_invalidates_the_whole_night() -> None:
    """Valid endpoints alone used to let a six-hour recording outage pass."""
    soc = SocSeries([(START + timedelta(hours=i), 60) for i in (0, 1, 7, 8)])
    assert soc.lowest_reading(START, START + timedelta(hours=8)) is None


def test_csv_interpolation_preserves_the_two_hour_gap_limit() -> None:
    """The existing tolerance remains inclusive and both endpoints are needed."""
    soc = SocSeries([(START + timedelta(hours=i), 60 - i) for i in (0, 2, 4)])
    assert soc.covers(START, START + timedelta(hours=4))
    assert not soc.covers(START - timedelta(minutes=1), START + timedelta(hours=4))
    assert not soc.covers(START, START + timedelta(hours=4, minutes=1))
