"""Bounded presentation of issued observations, without exposing replay inputs."""

from collections.abc import Mapping, Sequence
from typing import Any

OBSERVATION_REASONS = {
    "soc_incomplete": "Battery charge observations are incomplete.",
    "flow_incomplete": "Energy flow observations are incomplete.",
    "grid_charging": "Grid charging prevents a solar-only comparison.",
    "already_full": "The battery was already full at issue time.",
    "context_changed": "The setup changed during the observation window.",
    "history_read_failed": "The observation history could not be read.",
    "offline_checkpoint": "Home Assistant was offline at the checkpoint.",
    "missed_checkpoint": "No valid prediction was published within five minutes.",
    "no_night": "The prediction did not cover a complete upcoming night.",
    "no_forecast": "The morning prediction had no solar forecast.",
}


def history_rows(
    records: Sequence[Mapping[str, Any]], since: str, current_context: str
) -> list[dict[str, Any]]:
    """Copy only display fields; a missed checkpoint never acquires an issue time."""
    result = []
    for record in sorted(records, key=lambda r: r["issued_at"], reverse=True):
        local_date = record["id"].split(":", 1)[0]
        if local_date < since:
            continue
        outcome = record["result"]
        missed = outcome["status"] == "missed"
        code = outcome.get("reason_code")
        result.append(
            {
                "id": record["id"],
                "local_date": local_date,
                "checkpoint": record["checkpoint"],
                "issued_at": None if missed else record["issued_at"],
                "scheduled_at": record["issued_at"] if missed else None,
                "until": record.get("until"),
                "time_zone": record.get("time_zone"),
                "model_revision": record.get("model_revision"),
                "previous_setup": record["context"] != current_context,
                "settings": {
                    key: record.get("settings", {}).get(key)
                    for key in ("capacity_kwh", "reserve_pct", "target_pct")
                },
                "published": {
                    key: record.get("published", {}).get(key)
                    for key in ("lowest_pct", "reserve_at", "full_at")
                },
                "result": {
                    key: outcome.get(key)
                    for key in (
                        "status",
                        "reason_code",
                        "actual_lowest",
                        "error",
                        "reserve_correct",
                        "fill_correct",
                        "false_fill",
                        "missed_fill",
                        "actual_filled",
                        "actual_full_at",
                        "timing_resolution",
                        "time_error_minutes",
                    )
                },
                "reason": OBSERVATION_REASONS.get(code, code.replace("_", " ") if code else None),
            }
        )
        if len(result) == 180:
            break
    return result
