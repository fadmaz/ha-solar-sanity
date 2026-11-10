"""Calendar-day training and independent prospective pairs for optional day groups."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from analysis.load_experiment import load_gate
from analysis.load_schedule import LOAD_REVISION, GroupProfile, group_steps, learn_groups
from analysis.model import Bucket, BucketSource, Quality
from analysis.outlook import Step

NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)


def bucket(at, value=100):
    return Bucket(
        at, 3600, {"load": value}, {"load": Quality.OK}, {"load": BucketSource.OWN_INTEGRAL}
    )


def local(when):
    return when.date(), when.hour


def test_each_installation_learns_its_selected_day_group_from_previous_dates_only():
    """The same history can describe different routines without a global weekend."""
    rows = [
        bucket(
            NOW.replace(hour=6) - timedelta(days=i),
            200 if (NOW - timedelta(days=i)).weekday() == 5 else 100,
        )
        for i in range(1, 29)
    ]
    rows += [
        bucket(NOW.replace(hour=6), 99999),
        bucket(NOW + timedelta(days=1), 99999),
        bucket(NOW.replace(hour=6) - timedelta(days=29), 99999),
    ]
    first = learn_groups(rows, "load", ["sat"], local, until=NOW)
    second = learn_groups(rows, "load", ["sun"], local, until=NOW)
    assert first.wh[1][6] == 200
    assert first.dates[1][6] == 4
    assert first.wh[0][6] == 100
    assert second.wh[1][6] == 100
    assert second.wh[0][6] > 100


def test_three_different_dates_are_required_and_zero_is_a_valid_reading():
    """Repeated rows and missing counters cannot manufacture three quiet days."""
    rows = [bucket(NOW - timedelta(days=7 * i), 0) for i in (1, 2)]
    rows += rows
    rows += [replace(bucket(NOW - timedelta(days=21)), quality={"load": Quality.MISSING})]
    assert learn_groups(rows, "load", [], local, until=NOW).wh[0][12] is None
    rows += [bucket(NOW - timedelta(days=21), 0)]
    # Use distinct valid timestamps; an actual measurement window is unique.
    rows = [b for b in rows if b.value("load") is not None]
    result = learn_groups(rows, "load", [], local, until=NOW)
    assert result.wh[0][12] == 0
    assert result.dates[0][12] == 3


@pytest.mark.parametrize("parts", [(2026, 3, 8), (2026, 11, 1)])
def test_clock_change_dates_do_not_create_extra_training_days_or_zero_hours(parts):
    """A missing hour is absent; a repeated hour is averaged within one date."""
    zone = ZoneInfo("America/New_York")
    today = datetime(*parts, tzinfo=zone)
    start, end = today.astimezone(UTC), (today + timedelta(days=1)).astimezone(UTC)
    rows = [
        bucket(start + timedelta(hours=i), 100 + i)
        for i in range(int((end - start).total_seconds() / 3600))
    ]

    def wall(t):
        return t.astimezone(zone).date(), t.astimezone(zone).hour

    result = learn_groups(rows, "load", ["sun"], wall, until=end)
    assert max(result.dates[1]) == 1
    assert all(v is None for v in result.wh[1])
    assert result.dates[1][2] == (0 if parts[1] == 3 else 1)
    assert result.dates[1][1] == 1


def test_group_steps_follow_both_dates_and_preserve_the_measured_anchor_and_solar():
    """An evening crossing into an alternate day needs two different load rates."""
    start = datetime(2026, 11, 13, 23, 30, tzinfo=UTC)
    series = (Step(start, 0.5, 20, 80, 30), Step(start + timedelta(minutes=30), 1, 50, 100, 50))
    profile = GroupProfile(((100,) * 24, (200,) * 24), ((3,) * 24, (3,) * 24))
    changed = group_steps(series, profile, ["sat"], local, anchored_first=True)
    assert changed[0] is series[0]
    assert changed[1].load_wh == 200
    assert [(s.pv_wh, s.daylight_wh, s.hours) for s in changed] == [
        (s.pv_wh, s.daylight_wh, s.hours) for s in series
    ]
    missing = replace(profile, wh=(profile.wh[0], (None,) * 24))
    assert group_steps(series, missing, ["sat"], local, anchored_first=True) is None


def test_repeated_hour_has_one_dates_weight_after_training_becomes_ready():
    """Two fall-back observations cannot outweigh the two other calendar dates."""
    zone = ZoneInfo("America/New_York")
    first = datetime(2026, 11, 1, 1, tzinfo=zone)
    rows = [bucket(first.astimezone(UTC), 100), bucket(first.replace(fold=1).astimezone(UTC), 300)]
    rows += [
        bucket(datetime(2026, 10, day, 1, tzinfo=zone).astimezone(UTC), value)
        for day, value in ((31, 400), (30, 600))
    ]

    def wall(t):
        return t.astimezone(zone).date(), t.astimezone(zone).hour

    profile = learn_groups(rows, "load", [], wall, until=datetime(2026, 11, 2, tzinfo=zone))
    assert profile.wh[0][1] == 400
    assert profile.dates[0][1] == 3


def pair(day, kind, group, *, error=2, candidate_error=1):
    at = NOW - timedelta(days=day)
    result = {
        "status": "graded",
        "error": error,
        "reserve_correct": True,
        "false_fill": False,
        "missed_fill": False,
    }
    row = {
        "id": f"{at.date()}:{kind}",
        "issued_at": at.isoformat(),
        "until": (at + timedelta(hours=1)).isoformat(),
        "graded_at": (at + timedelta(hours=2)).isoformat(),
        "checkpoint": kind,
        "model_revision": "baseline",
        "context": "production",
        "result": result,
    }
    shadow = deepcopy(row)
    shadow.update(
        context="trial",
        model_revision=LOAD_REVISION,
        baseline_revision="baseline",
        load_group=group,
    )
    shadow["result"]["error"] = candidate_error
    row["load_shadow"] = shadow
    row["shadow"] = {"unrelated_morning_experiment": True}
    return row


def gate(rows, **kw):
    return load_gate(
        rows,
        kw.get("context", "trial"),
        "production",
        "baseline",
        since=NOW - timedelta(days=40),
        until=NOW,
    )


def pairs():
    return [
        pair(day, kind, int(day % 7 == 0 or day % 7 == 1))
        for day in range(1, 15)
        for kind in ("morning", "evening")
    ]


def test_promotion_requires_fourteen_new_complete_pairs_and_both_groups():
    """A strong overall average cannot stand in for evidence from both routines."""
    rows = pairs()
    assert gate(rows)["ready_for_review"]
    assert not gate(rows[:-2])["ready_for_review"]
    for row in rows:
        row["load_shadow"]["load_group"] = 0
    assert not gate(rows)["gates"]["each_group"]


@pytest.mark.parametrize(
    "defect",
    [
        "reserve",
        "false_fill",
        "missed_fill",
        "mae_equal",
        "mae_high",
        "old_context",
        "old_revision",
        "old_issue",
        "future_result",
        "pending",
    ],
)
def test_any_failed_accuracy_or_provenance_gate_blocks_promotion(defect):
    """No increase in either kind of fill error or reserve mistakes is accepted."""
    rows = pairs()
    for row in rows:
        shadow = row["load_shadow"]
        if defect == "reserve":
            shadow["result"]["reserve_correct"] = False
        if defect == "false_fill":
            shadow["result"]["false_fill"] = True
        if defect == "missed_fill":
            shadow["result"]["missed_fill"] = True
        if defect == "mae_equal":
            shadow["result"]["error"] = row["result"]["error"]
        if defect == "mae_high":
            shadow["result"]["error"] = 6
        if defect == "old_context":
            shadow["context"] = "retired"
        if defect == "old_revision":
            shadow["baseline_revision"] = "old"
        if defect == "old_issue":
            shadow["issued_at"] = (NOW - timedelta(days=90)).isoformat()
        if defect == "future_result":
            shadow["graded_at"] = (NOW + timedelta(days=1)).isoformat()
        if defect == "pending":
            shadow["result"]["status"] = "pending"
    assert not gate(rows)["ready_for_review"]


def test_morning_shadow_never_counts_as_an_alternate_schedule_comparison():
    """The existing daylight experiment and interval evidence remain independent."""
    rows = pairs()
    for row in rows:
        row["shadow"] = row.pop("load_shadow")
    assert gate(rows)["paired_nights"] == 0
    assert gate(rows, context=None)["paired_mornings"] == 0
