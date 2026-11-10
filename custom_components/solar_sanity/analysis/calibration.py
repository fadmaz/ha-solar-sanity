"""Frozen empirical intervals for issued evening forecasts, never the live outlook."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

INTERVAL_REVISION = "evening-error-quantiles-v1"
CALIBRATION_MIN = 30
CALIBRATION_MAX = 60
VALIDATION_MIN = 14
TARGET_COVERAGE = 0.8


def quantile(values: Sequence[float], fraction: float) -> float:
    """Linearly interpolated empirical quantile; caller supplies finite errors."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    left = int(position)
    right = min(left + 1, len(ordered) - 1)
    return ordered[left] + (ordered[right] - ordered[left]) * (position - left)


def _known_before(row: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    """Only observations actually graded before this prediction was issued."""
    try:
        issued = datetime.fromisoformat(current["issued_at"])
        return (
            row.get("context") == current.get("context")
            and row.get("model_revision") == current.get("model_revision")
            and row.get("checkpoint") == "evening"
            and row.get("result", {}).get("status") == "graded"
            and datetime.fromisoformat(row["issued_at"]) < issued
            and datetime.fromisoformat(row["until"]) <= issued
            and datetime.fromisoformat(row["graded_at"]) <= issued
        )
    except (KeyError, TypeError, ValueError):
        return False


def issue_interval(
    records: Sequence[Mapping[str, Any]], current: Mapping[str, Any]
) -> dict[str, Any]:
    """Freeze calibration and prior held-out coverage; this night's result is absent."""
    past = sorted(
        (r for r in records if _known_before(r, current)),
        key=lambda r: datetime.fromisoformat(r["issued_at"]),
    )
    calibrated = [r for r in past if math.isfinite(r["result"]["error"])][-CALIBRATION_MAX:]
    errors = [float(r["result"]["error"]) for r in calibrated]
    validation = [
        r
        for r in past
        if r.get("interval", {}).get("revision") == INTERVAL_REVISION
        and r["interval"].get("lower") is not None
        and r["interval"].get("calibration_count", 0) >= CALIBRATION_MIN
    ][-VALIDATION_MIN:]
    covered = sum(
        r["interval"]["lower"] <= r["result"]["actual_lowest"] <= r["interval"]["upper"]
        for r in validation
    )
    coverage = covered / len(validation) if validation else None
    result = {
        "revision": INTERVAL_REVISION,
        "target_coverage": TARGET_COVERAGE,
        "calibration_count": len(errors),
        "validation_count": len(validation),
        "measured_coverage": coverage,
        "lower": None,
        "upper": None,
        "publishable": False,
        "reason_code": "interval_needs_calibration",
    }
    if len(errors) < CALIBRATION_MIN:
        return result
    prediction = current.get("published", {}).get("lowest_pct")
    if prediction is None or not math.isfinite(prediction):
        result["reason_code"] = "interval_no_prediction"
        return result
    # Errors are prediction minus actual: subtract the upper error for the
    # lower actual bound, not the other way round. Clamp to physical percentages.
    result.update(
        lower=max(0.0, min(100.0, prediction - quantile(errors, 0.9))),
        upper=max(0.0, min(100.0, prediction - quantile(errors, 0.1))),
        calibration_ids=[r["id"] for r in calibrated],
        reason_code="interval_needs_validation",
    )
    if len(validation) >= VALIDATION_MIN:
        if coverage is not None and coverage >= TARGET_COVERAGE:
            result.update(publishable=True, reason_code=None)
        else:
            result["reason_code"] = "interval_coverage_low"
    return result


def interval_view(record: Mapping[str, Any] | None, now: datetime) -> dict[str, Any]:
    """Public limits expire at the frozen night boundary, with an explicit reason."""
    if record is None:
        return {"lower": None, "upper": None, "reason_code": "interval_waiting_evening"}
    result = dict(record.get("interval") or {})
    if not result:
        result["reason_code"] = "interval_not_recorded"
    elif result.get("revision") != INTERVAL_REVISION:
        result["reason_code"] = "interval_revision_unknown"
    result.update(
        issued_at=record["issued_at"],
        until=record.get("until"),
        model_revision=record.get("model_revision"),
    )
    if now < datetime.fromisoformat(record["issued_at"]):
        result["reason_code"] = "interval_not_yet_issued"
    elif not record.get("until"):
        result["reason_code"] = "interval_no_prediction"
    elif now >= datetime.fromisoformat(record["until"]):
        result["reason_code"] = "interval_expired"
    if result.get("reason_code") or not result.get("publishable"):
        result.update(lower=None, upper=None)
    return result
