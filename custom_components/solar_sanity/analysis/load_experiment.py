"""Separate prospective pairs for the optional alternate-schedule candidate."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .load_schedule import LOAD_REVISION


def load_gate(
    records: Sequence[Mapping[str, Any]],
    context: str | None,
    baseline_context: str,
    baseline_revision: str,
    *,
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    """Only new complete sister pairs count, never the morning experiment shadow."""
    pairs = {}
    for base in records:
        shadow = base.get("load_shadow", {})
        if (
            context is not None
            and base.get("context") == baseline_context
            and base.get("model_revision") == baseline_revision
            and shadow.get("context") == context
            and shadow.get("model_revision") == LOAD_REVISION
            and shadow.get("baseline_revision") == baseline_revision
            and shadow.get("issued_at") == base.get("issued_at")
            and shadow.get("load_group") in (0, 1)
            and base.get("result", {}).get("status") == "graded"
            and shadow.get("result", {}).get("status") == "graded"
            and since <= datetime.fromisoformat(base["issued_at"]) < until
            and all(
                row.get("until") is not None
                and datetime.fromisoformat(row["until"]) <= until
                and (
                    row.get("graded_at") is None
                    or datetime.fromisoformat(row["graded_at"]) <= until
                )
                for row in (base, shadow)
            )
        ):
            pairs[base["id"]] = base
    nights = [r for r in pairs.values() if r["checkpoint"] == "evening"]
    mornings = [r for r in pairs.values() if r["checkpoint"] == "morning"]

    def count(rows, key, candidate=False):
        return sum((r["load_shadow"] if candidate else r)["result"][key] for r in rows)

    def mae(candidate=False):
        return (
            sum(abs((r["load_shadow"] if candidate else r)["result"]["error"]) for r in nights)
            / len(nights)
            if nights
            else None
        )

    before, after = mae(), mae(True)
    correct = count(nights, "reserve_correct", True)
    accuracy = correct * 100 / len(nights) if nights else None
    groups = {
        kind: [sum(r["load_shadow"]["load_group"] == group for r in rows) for group in (0, 1)]
        for kind, rows in (("night", nights), ("morning", mornings))
    }
    gates = {
        "new_nights": len(nights) >= 14,
        "new_mornings": len(mornings) >= 14,
        "each_group": all(n >= 3 for counts in groups.values() for n in counts),
        "night_error_reduced": before is not None and after is not None and after < before,
        "night_mae": after is not None and after <= 5,
        "reserve_errors_not_increased": correct >= count(nights, "reserve_correct"),
        "reserve_accuracy": accuracy is not None and accuracy >= 85,
        "false_fills_not_increased": count(mornings, "false_fill", True)
        <= count(mornings, "false_fill"),
        "missed_fills_not_increased": count(mornings, "missed_fill", True)
        <= count(mornings, "missed_fill"),
    }
    return {
        "revision": LOAD_REVISION,
        "active": False,
        "ready_for_review": all(gates.values()),
        "paired_nights": len(nights),
        "paired_mornings": len(mornings),
        "group_counts": groups,
        "baseline_night_mae": before,
        "candidate_night_mae": after,
        "candidate_reserve_accuracy": accuracy,
        "baseline_false_fills": count(mornings, "false_fill"),
        "candidate_false_fills": count(mornings, "false_fill", True),
        "baseline_missed_fills": count(mornings, "missed_fill"),
        "candidate_missed_fills": count(mornings, "missed_fill", True),
        "gates": gates,
    }
