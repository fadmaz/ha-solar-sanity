"""A covering hour is a measured energy comparison with a complete prefix."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from analysis.model import Bucket, BucketSource, Quality
from analysis.solar_coverage import HOUR, coverage_day, observe_coverage

START = datetime(2026, 10, 20, tzinfo=UTC)
END = START + 3 * HOUR
KEYS = ("pv", "load")


def measured(hour, pv=0, load=500, *, source=BucketSource.OWN_INTEGRAL, seconds=3600):
    return Bucket(
        hour,
        seconds,
        {"pv": pv, "load": load},
        dict.fromkeys(KEYS, Quality.OK),
        dict.fromkeys(KEYS, source),
    )


@pytest.mark.parametrize("expected_hour,relation", [(0, "later"), (1, "within"), (2, "earlier")])
def test_first_observed_hour_compares_with_the_frozen_boundary(expected_hour, relation):
    """PV must meet the house and the saved inverter estimate, including equality."""
    hour = START + HOUR
    buckets = {START: measured(START, 550), hour: measured(hour, 600)}
    result = observe_coverage(
        buckets,
        START,
        END,
        START + expected_hour * HOUR,
        standby_w=100,
        now=hour + HOUR + timedelta(minutes=15),
    )
    assert result["status"] == "observed"
    assert result["observed_start"] == hour.isoformat()
    assert result["observed_end"] == (hour + HOUR).isoformat()
    assert result["relation"] == relation


def test_zero_solar_cannot_cover_an_idle_house():
    """An entirely measured day can have no observed coverage, even at zero load."""
    buckets = {h: measured(h, load=0) for h in (START, START + HOUR, START + 2 * HOUR)}
    assert observe_coverage(
        buckets, START, END, START, standby_w=0, now=END + timedelta(minutes=15)
    ) == {"status": "not_observed"}


@pytest.mark.parametrize(
    "first", [None, measured(START, seconds=3500), measured(START, source=BucketSource.LTS_MEAN)]
)
def test_an_unproven_earlier_hour_blocks_the_first_claim(first):
    """A later covering hour cannot repair an earlier gap or a partial estimate."""
    hour = START + HOUR
    buckets = {hour: measured(hour, 900)}
    if first is not None:
        buckets[START] = first
    assert (
        observe_coverage(buckets, START, END, START, standby_w=100, now=END)["status"] == "pending"
    )
    assert observe_coverage(
        buckets, START, END, START, standby_w=100, now=END + timedelta(days=1)
    ) == {"status": "unavailable", "reason_code": "flow_incomplete"}


def test_a_covering_hour_waits_for_recorder_grace():
    """Even a ready-looking row cannot settle before its closing grace period."""
    result = observe_coverage(
        {START: measured(START, 600)},
        START,
        END,
        START,
        standby_w=100,
        now=START + HOUR + timedelta(minutes=14),
    )
    assert result == {"status": "pending"}


@pytest.mark.parametrize("month,day,hours", [(3, 7, 23), (10, 31, 25)])
def test_observation_uses_the_next_local_calendar_day(month, day, hours):
    """A clock change gives a different number of complete UTC hours."""
    zone = ZoneInfo("America/New_York")
    issued = datetime(2026, month, day, 20, tzinfo=zone).astimezone(UTC)
    start, end = coverage_day(issued, zone)
    assert (end - start) / HOUR == hours
    rows = {start + i * HOUR: measured(start + i * HOUR) for i in range(hours)}
    assert (
        observe_coverage(rows, start, end, start, standby_w=0, now=end + HOUR)["status"]
        == "not_observed"
    )


def test_a_fractional_offset_only_uses_whole_hours_inside_the_date():
    """The bucket straddling midnight cannot describe the next local day."""
    zone = ZoneInfo("Asia/Kathmandu")
    start, end = coverage_day(datetime(2026, 10, 19, 20, tzinfo=zone), zone)
    hour = start.replace(minute=0) + HOUR
    result = observe_coverage(
        {hour: measured(hour, 600)}, start, end, hour, standby_w=100, now=hour + 2 * HOUR
    )
    assert result["observed_start"] == hour.isoformat()
