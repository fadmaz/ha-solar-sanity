"""Paired, chronologically issued evidence for promoting the morning candidate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .daylight_shape import SHAPE_REVISION


def shape_gate(records: Sequence[Mapping[str, Any]], context: str) -> dict[str, Any]:
    """Compare only complete pairs from this exact configuration and revision."""
    pairs = [
        r
        for r in records
        if r.get("context") == context
        and r.get("result", {}).get("status") == "graded"
        and r.get("shadow", {}).get("model_revision") == SHAPE_REVISION
        and r.get("shadow", {}).get("context") == context
        and r.get("shadow", {}).get("issued_at") == r.get("issued_at")
        and r.get("shadow", {}).get("result", {}).get("status") == "graded"
    ]
    pairs.sort(key=lambda r: datetime.fromisoformat(r["issued_at"]))
    mornings = [r for r in pairs if r["checkpoint"] == "morning"]
    nights = [r for r in pairs if r["checkpoint"] == "evening"]

    def count(rows, key, shadow=False):
        return sum((r["shadow"] if shadow else r)["result"][key] for r in rows)

    def mae(shadow=False):
        return (
            (
                sum(abs((r["shadow"] if shadow else r)["result"]["error"]) for r in nights)
                / len(nights)
            )
            if nights
            else None
        )

    baseline_mae, candidate_mae = mae(), mae(True)
    correct = count(nights, "reserve_correct", True)
    accuracy = 100 * correct / len(nights) if nights else None
    gates = {
        "new_mornings": len(mornings) >= 14,
        "complete_nights": len(nights) >= 14,
        "false_fills_not_increased": count(mornings, "false_fill", True)
        <= count(mornings, "false_fill"),
        "missed_fills_reduced": count(mornings, "missed_fill", True)
        < count(mornings, "missed_fill"),
        "night_error_not_increased": candidate_mae is not None
        and baseline_mae is not None
        and candidate_mae <= baseline_mae,
        "reserve_errors_not_increased": correct >= count(nights, "reserve_correct"),
        "night_accuracy": accuracy is not None and accuracy >= 85,
        "night_mae": candidate_mae is not None and candidate_mae <= 5,
    }
    return {
        "revision": SHAPE_REVISION,
        "active": False,
        "ready_for_review": all(gates.values()),
        "paired_mornings": len(mornings),
        "paired_nights": len(nights),
        "baseline_night_mae": baseline_mae,
        "candidate_night_mae": candidate_mae,
        "candidate_reserve_accuracy": accuracy,
        "baseline_false_fills": count(mornings, "false_fill"),
        "candidate_false_fills": count(mornings, "false_fill", True),
        "baseline_missed_fills": count(mornings, "missed_fill"),
        "candidate_missed_fills": count(mornings, "missed_fill", True),
        "gates": gates,
    }
