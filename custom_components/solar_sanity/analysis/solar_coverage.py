"""Observed hourly coverage alongside an immutable evening prediction."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Any

from .model import Bucket, BucketSource, Quality

HOUR = timedelta(hours=1)
RECORDER_GRACE = timedelta(minutes=15)


def coverage_day(issued: datetime, zone: tzinfo) -> tuple[datetime, datetime]:
    """The next local date, expressed as UTC bounds for recorder buckets."""
    day = issued.astimezone(zone).date() + timedelta(days=1)
    start = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    return start, end


def _measured_energy(bucket: Bucket, key: str) -> float | None:
    # Recorder means can omit part of an hour. Only the integration's own
    # closed measurements attest to coverage for both channels.
    if bucket.quality.get(key) != Quality.OK or bucket.source.get(key) != BucketSource.OWN_INTEGRAL:
        return None
    value = bucket.value(key)
    return value if value is not None and math.isfinite(value) and value >= 0 else None


def observe_coverage(
    buckets: Mapping[datetime, Bucket],
    start: datetime,
    end: datetime,
    expected: datetime,
    *,
    standby_w: float,
    now: datetime,
) -> dict[str, Any]:
    """Find the first covering hour only when the measured prefix is complete."""
    hour = start.replace(minute=0, second=0, microsecond=0)
    if hour < start:
        hour += HOUR
    if hour + HOUR > end:
        return {"status": "unavailable", "reason_code": "no_complete_hours"}
    while hour + HOUR <= end:
        close = hour + HOUR
        if now < close + RECORDER_GRACE:
            return {"status": "pending"}
        bucket = buckets.get(hour)
        pv = load = None
        if bucket is not None and bucket.seconds == 3600:
            pv, load = _measured_energy(bucket, "pv"), _measured_energy(bucket, "load")
        if pv is None or load is None:
            if now < end + timedelta(days=1):
                return {"status": "pending", "reason_code": "flow_incomplete"}
            return {"status": "unavailable", "reason_code": "flow_incomplete"}
        if pv > 0 and pv >= load + standby_w:
            relation = "later" if expected < hour else "earlier" if expected >= close else "within"
            return {
                "status": "observed",
                "observed_start": hour.isoformat(),
                "observed_end": close.isoformat(),
                "relation": relation,
                "late_soc_status": "pending" if relation == "later" else "not_applicable",
            }
        hour = close
    return {"status": "not_observed"}
