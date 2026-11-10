"""One additional load over frozen projection inputs; published state is unchanged."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from .outlook import BatterySettings, Outlook, Step, project, simulate


@dataclass(frozen=True, slots=True)
class LoadComparison:
    """Two raw calculations on the same basis, without live-time hysteresis."""

    baseline: Outlook
    scenario: Outlook
    additional_wh: float
    baseline_uncovered_wh: float
    scenario_uncovered_wh: float
    applied_start: datetime
    applied_end: datetime


def compare_load(
    soc_pct: float,
    battery: BatterySettings,
    steps: Sequence[Step],
    *,
    forecast_used: bool,
    power_w: float,
    duration_minutes: int,
    start_at: datetime,
    now: datetime,
) -> LoadComparison:
    """Split exact boundaries and add only the interval still in the future."""
    if not math.isfinite(power_w) or power_w < 0:
        raise ValueError("Additional power must be a finite non-negative number.")
    if (
        isinstance(duration_minutes, bool)
        or not isinstance(duration_minutes, int)
        or not 1 <= duration_minutes <= 1440
    ):
        raise ValueError("Duration must be between one minute and 24 hours.")
    if start_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("The start time must include a time zone.")
    # Duration is elapsed time even when the local clock skips or repeats.
    start_at, now = start_at.astimezone(UTC), now.astimezone(UTC)
    if not steps or steps[0].start > now or steps[-1].end <= now:
        raise ValueError("No current projection is available.")
    end = start_at + timedelta(minutes=duration_minutes)
    if end <= now:
        raise ValueError("This scenario has finished.")
    if end > steps[-1].end:
        raise ValueError("The complete scenario must fit inside the projection horizon.")
    begin = max(start_at, now, steps[0].start)
    additional = power_w * ((end - begin).total_seconds() / 3600)
    if not math.isfinite(additional):
        raise ValueError("Additional energy is outside the supported numeric range.")
    changed = []
    for step in steps:
        boundaries = sorted(
            {step.start, step.end, *(t for t in (begin, end) if step.start < t < step.end)}
        )
        for a, b in pairwise(boundaries):
            hours = (b - a).total_seconds() / 3600
            share = hours / step.hours
            extra = power_w * hours if begin <= a < end else 0.0
            changed.append(
                Step(
                    a,
                    hours,
                    step.pv_wh * share,
                    step.load_wh * share + extra,
                    step.daylight_wh * share,
                )
            )
    # Zero extra load reproduces the baseline exactly, including ties.
    chosen = steps if power_w == 0 else tuple(changed)
    baseline = project(soc_pct, battery, steps, forecast_used=forecast_used)
    scenario = (
        baseline if power_w == 0 else project(soc_pct, battery, chosen, forecast_used=forecast_used)
    )
    return LoadComparison(
        baseline,
        scenario,
        additional,
        sum(simulate(soc_pct, battery, steps).shortfall_wh),
        sum(simulate(soc_pct, battery, chosen).shortfall_wh),
        begin,
        end,
    )
