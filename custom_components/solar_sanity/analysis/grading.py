"""Grade a frozen issued prediction, never a forecast reconstructed afterwards."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from .model import Bucket
from .observations import SocSeries, grid_charged, observation_hours


def flow_coverage(buckets: Sequence[Bucket], start: datetime, end: datetime) -> bool:
    """Missing flow cannot attest that the grid did not charge the battery."""
    hours = {b.start_utc: b for b in buckets if b.seconds == 3600}
    return all(
        hour in hours
        and all(hours[hour].value(key) is not None for key in ("pv", "load", "battery_charge"))
        for hour in observation_hours(start, end)
    )


def grade(
    record: Mapping[str, Any],
    soc: SocSeries,
    buckets: Sequence[Bucket],
    *,
    full_observed_at: datetime | None = None,
) -> dict[str, Any]:
    """One settled outcome. Hourly extremes never claim minute precision."""
    start = datetime.fromisoformat(record["issued_at"])
    end = datetime.fromisoformat(record["until"])
    kind = record["checkpoint"]
    extreme = "min" if kind == "evening" else "max"
    if not soc.covers(start, end, extreme=extreme):
        return {"status": "excluded", "reason_code": "soc_incomplete"}
    if not flow_coverage(buckets, start, end):
        return {"status": "excluded", "reason_code": "flow_incomplete"}
    if kind == "evening":
        reading = soc.lowest_reading(start, end)
        if reading is None:
            return {"status": "excluded", "reason_code": "soc_incomplete"}
        actual, at = reading
        # Preserve the replay's measured cut-off allowance: charging after the
        # minimum must not discard a battery that already demonstrated it.
        if grid_charged(buckets, start, at - timedelta(hours=1)):
            return {"status": "excluded", "reason_code": "grid_charging"}
        reserve = record["settings"]["reserve_pct"]
        return {
            "status": "graded",
            "actual_lowest": actual,
            "error": record["published"]["lowest_pct"] - actual,
            "reserve_correct": bool(record["published"]["reserve_at"]) == (actual <= reserve + 0.5),
        }
    level = record["settings"]["target_pct"] - 1.0
    if record["soc_pct"] >= level:
        return {"status": "excluded", "reason_code": "already_full"}
    actual_at = soc.first_at_or_above(level, start, end)
    # For a morning, grid charging before full makes the solar-only call
    # ungradable, even if the battery subsequently reaches its target.
    grid_end = end if actual_at is None else actual_at
    if grid_charged(buckets, start, grid_end):
        return {"status": "excluded", "reason_code": "grid_charging"}
    raw_prediction = record["published"].get("full_at")
    predicted_at = datetime.fromisoformat(raw_prediction) if raw_prediction else None
    predicted = predicted_at is not None and start <= predicted_at <= end
    actual = actual_at is not None
    result: dict[str, Any] = {
        "status": "graded",
        "fill_correct": predicted == actual,
        "false_fill": predicted and not actual,
        "missed_fill": actual and not predicted,
        "actual_filled": actual,
        "actual_full_at": None,
        "timing_resolution": "hour",
        "time_error_minutes": None,
    }
    if actual and full_observed_at is not None and start <= full_observed_at < end:
        result["actual_full_at"] = full_observed_at.isoformat()
        result["timing_resolution"] = "state"
        if predicted and predicted_at is not None:
            result["time_error_minutes"] = (predicted_at - full_observed_at).total_seconds() / 60
    return result


def summarize(
    records: Sequence[Mapping[str, Any]], context: str, since: datetime
) -> dict[str, Any]:
    """Descriptive results with explicit denominators, never a guessed zero."""
    selected = [
        r
        for r in records
        if r.get("context") == context and datetime.fromisoformat(r["issued_at"]) >= since
    ]
    graded = [r for r in selected if (r.get("result") or {}).get("status") == "graded"]
    nights = [r["result"] for r in graded if r["checkpoint"] == "evening"]
    mornings = [r["result"] for r in graded if r["checkpoint"] == "morning"]
    errors = [float(r["error"]) for r in nights if math.isfinite(r["error"])]
    timed = [r["time_error_minutes"] for r in mornings if r.get("time_error_minutes") is not None]
    excluded: dict[str, int] = {}
    for record in selected:
        result = record.get("result") or {}
        if result.get("status") in ("excluded", "missed"):
            reason = result.get("reason_code", "unknown")
            excluded[reason] = excluded.get(reason, 0) + 1
    return {
        "nights": len(nights),
        "mornings": len(mornings),
        "timed_mornings": len(timed),
        "night_lowest_mae": sum(abs(v) for v in errors) / len(errors) if errors else None,
        "reserve_call_accuracy": 100 * sum(r["reserve_correct"] for r in nights) / len(nights)
        if nights
        else None,
        "morning_fill_accuracy": 100 * sum(r["fill_correct"] for r in mornings) / len(mornings)
        if mornings
        else None,
        "fill_time_mae": sum(abs(v) for v in timed) / len(timed) if timed else None,
        "false_fill": sum(r["false_fill"] for r in mornings),
        "missed_fill": sum(r["missed_fill"] for r in mornings),
        "excluded": excluded,
        "pending": sum((r.get("result") or {}).get("status") == "pending" for r in selected),
    }
