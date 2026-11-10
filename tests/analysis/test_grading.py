"""Issued forecasts are scored only against complete, independent observations."""

from datetime import UTC, datetime, timedelta

import pytest
from analysis.grading import grade, summarize
from analysis.model import Bucket, BucketSource, Quality
from analysis.observations import SocSeries

START = datetime(2026, 11, 1, 20, tzinfo=UTC)


def record(kind="evening"):
    return {
        "issued_at": START.isoformat(),
        "until": (START + timedelta(hours=3)).isoformat(),
        "checkpoint": kind,
        "context": "one",
        "soc_pct": 60,
        "settings": {"reserve_pct": 35, "target_pct": 100},
        "published": {"lowest_pct": 40, "reserve_at": None, "full_at": None},
    }


def observations(levels=(55, 45, 38), *, maximum=False):
    extremes = {START + timedelta(hours=i): value for i, value in enumerate(levels)}
    return SocSeries([], {} if maximum else extremes, extremes if maximum else {})


def buckets():
    keys = ("pv", "load", "battery_charge")
    return [
        Bucket(
            START + timedelta(hours=i),
            3600,
            {"pv": 0, "load": 500, "battery_charge": 0},
            dict.fromkeys(keys, Quality.OK),
            dict.fromkeys(keys, BucketSource.OWN_INTEGRAL),
        )
        for i in range(3)
    ]


def test_the_frozen_published_minimum_is_graded():
    """The next morning's recomputation must never replace the evening promise."""
    result = grade(record(), observations(), buckets())
    assert result["error"] == 2
    assert result["reserve_correct"] is True


@pytest.mark.parametrize("gap", [0, 1, 2])
def test_every_missing_hour_excludes_the_night(gap):
    """A surviving minimum is not a complete night."""
    soc = observations()
    del soc.minima[START + timedelta(hours=gap)]
    assert grade(record(), soc, buckets())["reason_code"] == "soc_incomplete"


def test_missing_battery_flow_cannot_attest_to_no_grid_charge():
    """A missing channel cannot be silently interpreted as zero."""
    flow = buckets()
    flow[1].wh["battery_charge"] = None
    assert grade(record(), observations(), flow)["reason_code"] == "flow_incomplete"


def test_grid_charging_before_the_minimum_excludes_it():
    """Grid energy is outside the battery-alone prediction."""
    flow = buckets()
    flow[0].wh["battery_charge"] = 1500
    assert grade(record(), observations(), flow)["reason_code"] == "grid_charging"


def test_grid_charging_after_the_minimum_does_not_erase_it():
    """A battery that already reached its minimum demonstrated that outcome."""
    flow = buckets()
    flow[2].wh["battery_charge"] = 1500
    assert grade(record(), observations((35, 38, 45)), flow)["status"] == "graded"


def test_hourly_maxima_never_claim_minute_precision():
    """An hourly maximum proves full happened, not at which minute."""
    prediction = record("morning")
    prediction["published"]["full_at"] = (START + timedelta(hours=1)).isoformat()
    outcome = grade(prediction, observations((80, 100, 100), maximum=True), buckets())
    assert outcome["fill_correct"] is True
    assert outcome["time_error_minutes"] is None
    assert outcome["timing_resolution"] == "hour"


def test_recorded_crossings_can_grade_timing():
    """The exact history is optional; classification and timing have separate counts."""
    prediction = record("morning")
    prediction["published"]["full_at"] = (START + timedelta(hours=1)).isoformat()
    outcome = grade(
        prediction,
        observations((80, 100, 100), maximum=True),
        buckets(),
        full_observed_at=START + timedelta(hours=1, minutes=10),
    )
    assert outcome["time_error_minutes"] == -10


def test_grid_charging_cannot_make_a_solar_fill_call_correct():
    """The morning rule excludes an externally filled battery."""
    flow = buckets()
    flow[0].wh["battery_charge"] = 1500
    outcome = grade(record("morning"), observations((80, 100, 100), maximum=True), flow)
    assert outcome["reason_code"] == "grid_charging"


def test_an_empty_summary_is_unknown_and_contexts_stay_separate():
    """No samples and old settings must not produce an accuracy of zero or one hundred."""
    prediction = record()
    prediction["result"] = grade(prediction, observations(), buckets())
    assert summarize([prediction], "other", START)["night_lowest_mae"] is None
    result = summarize([prediction], "one", START)
    assert result["night_lowest_mae"] == 2
    assert result["nights"] == 1
