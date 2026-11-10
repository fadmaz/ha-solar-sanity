"""Input comparisons over the future shared by two valid published projections."""

from dataclasses import dataclass
from datetime import datetime

from .outlook import Step


@dataclass(frozen=True, slots=True)
class InputSnapshot:
    """Only the inputs needed for an in-memory comparison; no source entity IDs."""

    context: str
    at: datetime
    soc_pct: float
    steps: tuple[Step, ...]
    scale: float | None
    scale_today: float | None
    scale_tomorrow: float | None
    max_charge_w: float | None
    standby_w: float | None


def _energy(steps: tuple[Step, ...], start: datetime, end: datetime, key: str) -> float:
    return (
        sum(
            getattr(step, key)
            * max(0.0, (min(end, step.end) - max(start, step.start)).total_seconds())
            / (step.hours * 3600)
            for step in steps
        )
        / 1000
    )


class InputChanges:
    """Keep two snapshots at most; invalid or different contexts start a new pair."""

    def __init__(self) -> None:
        self.previous: InputSnapshot | None = None
        self.current: InputSnapshot | None = None

    def publish(self, current: InputSnapshot | None) -> dict:
        previous = self.current
        self.previous, self.current = None, current
        if current is None:
            return {"status": "unavailable", "reason_code": "projection_unavailable"}
        if (
            previous is None
            or previous.context != current.context
            or previous.at >= current.at
            or not previous.steps
            or not current.steps
        ):
            return {"status": "starting", "reason_code": "waiting_for_comparison"}
        start = max(previous.at, current.at, previous.steps[0].start, current.steps[0].start)
        end = min(previous.steps[-1].end, current.steps[-1].end)
        if end <= start:
            return {"status": "starting", "reason_code": "no_common_horizon"}
        self.previous = previous
        fields = []

        def add(key, before, after, unit, digits):
            # Missing estimates are never converted to zero or a numeric change.
            if before is None or after is None:
                return
            before, after = round(before, digits), round(after, digits)
            if before != after:
                fields.append({"key": key, "previous": before, "current": after, "unit": unit})

        add("soc_pct", previous.soc_pct, current.soc_pct, "%", 3)
        for key, attr in (("solar_energy", "pv_wh"), ("load_energy", "load_wh")):
            add(
                key,
                _energy(previous.steps, start, end, attr),
                _energy(current.steps, start, end, attr),
                "kWh",
                6,
            )
        for key in ("scale", "scale_today", "scale_tomorrow", "max_charge_w", "standby_w"):
            add(
                key,
                getattr(previous, key),
                getattr(current, key),
                "W" if key.endswith("_w") else "",
                4,
            )
        return {
            "status": "ok",
            "previous_at": previous.at.isoformat(),
            "current_at": current.at.isoformat(),
            "common_start": start.isoformat(),
            "common_end": end.isoformat(),
            "fields": fields,
        }
