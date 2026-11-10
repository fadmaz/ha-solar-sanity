"""Evening intervals use frozen past residuals and held-out future outcomes."""

from datetime import UTC, datetime, timedelta

from analysis.calibration import INTERVAL_REVISION, interval_view, issue_interval, quantile

START = datetime(2026, 1, 1, 20, tzinfo=UTC)


def row(day, error=0.0):
    issue = START + timedelta(days=day)
    return {
        "id": f"{day}:evening",
        "checkpoint": "evening",
        "context": "same",
        "model_revision": "baseline",
        "issued_at": issue.isoformat(),
        "until": (issue + timedelta(hours=12)).isoformat(),
        "graded_at": (issue + timedelta(hours=13)).isoformat(),
        "published": {"lowest_pct": 50},
        "result": {"status": "graded", "error": error, "actual_lowest": 50 - error},
    }


def test_signed_quantiles_have_the_correct_direction_and_exclude_the_current_night():
    """Optimistic errors must lower the actual-charge range rather than lift it."""
    past = [row(i, 10) for i in range(30)]
    current = row(30, -1000)
    interval = issue_interval([*past, current], current)
    assert (interval["lower"], interval["upper"]) == (40, 40)
    assert interval["calibration_count"] == 30
    assert not interval["publishable"]
    assert interval["reason_code"] == "interval_needs_validation"
    assert quantile([0, 10], 0.1) == 1


def test_no_interval_is_published_until_thirty_calibration_and_fourteen_later_nights():
    """In-sample coverage is not evidence that an 80% interval works prospectively."""
    past = [row(i, (-1) ** i * 5) for i in range(30)]
    for day in range(30, 44):
        current = row(day, 0)
        current["interval"] = issue_interval(past, current)
        assert not current["interval"]["publishable"]
        past.append(current)
    current = row(44)
    interval = issue_interval(past, current)
    assert interval["publishable"]
    assert interval["validation_count"] == 14
    assert interval["measured_coverage"] == 1
    assert interval["revision"] == INTERVAL_REVISION
    current["interval"] = interval
    assert interval_view(current, START + timedelta(days=44, hours=1))["lower"] is not None
    expired = interval_view(current, START + timedelta(days=44, hours=12))
    assert expired["lower"] is None and expired["upper"] is None
    assert expired["reason_code"] == "interval_expired"


def test_calibration_is_capped_and_separated_from_future_reads_or_other_contexts():
    """A delayed recorder read cannot retroactively improve an issued interval."""
    past = [row(i) for i in range(100)]
    past[-1]["graded_at"] = (START + timedelta(days=200)).isoformat()
    current = row(100)
    interval = issue_interval(past, current)
    assert interval["calibration_count"] == 60
    assert "99:evening" not in interval["calibration_ids"]
    current["context"] = "changed"
    assert issue_interval(past, current)["calibration_count"] == 0
    current["context"] = "same"
    current["model_revision"] = "new-model"
    assert issue_interval(past, current)["calibration_count"] == 0


def test_failed_held_out_coverage_keeps_limits_unknown():
    """The sample threshold alone does not authorize a poorly calibrated band."""
    past = [row(i) for i in range(44)]
    for item in past[30:]:
        item["interval"] = {
            "revision": INTERVAL_REVISION,
            "lower": 0,
            "upper": 1,
            "calibration_count": 30,
        }
    current = row(44)
    current["interval"] = issue_interval(past, current)
    view = interval_view(current, START + timedelta(days=44, hours=1))
    assert view["reason_code"] == "interval_coverage_low"
    assert view["lower"] is None


def test_old_snapshots_never_gain_a_retrospectively_reconstructed_interval():
    """A forecast issued before interval recording remains explicitly unknown."""
    assert interval_view(row(0), START)["reason_code"] == "interval_not_recorded"
