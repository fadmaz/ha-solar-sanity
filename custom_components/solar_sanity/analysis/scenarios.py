"""One additional load over frozen projection inputs; published state is unchanged."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from .outlook import BatterySettings, Outlook, Run, Step, project, simulate


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


@dataclass(frozen=True, slots=True)
class LoadTiming:
    """The whole-horizon impact of one complete run."""

    start: datetime
    end: datetime
    minimum_soc_pct: float
    additional_grid_wh: float
    reserve_at: datetime | None


@dataclass(frozen=True, slots=True)
class LoadPlan:
    recommendation: LoadTiming | None = None
    run_now: LoadTiming | None = None
    reason_code: str | None = None


def _duration(power_w: float, duration_minutes: int) -> timedelta:
    if not math.isfinite(power_w) or power_w < 0:
        raise ValueError("Additional power must be a finite non-negative number.")
    if (
        isinstance(duration_minutes, bool)
        or not isinstance(duration_minutes, int)
        or not 1 <= duration_minutes <= 1440
    ):
        raise ValueError("Duration must be between one minute and 24 hours.")
    return timedelta(minutes=duration_minutes)


def _with_load(
    steps: Sequence[Step], power_w: float, begin: datetime, end: datetime
) -> Sequence[Step]:
    if power_w == 0:
        return steps
    changed = []
    for step in steps:
        if step.end <= begin or step.start >= end:
            changed.append(step)
            continue
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
    return tuple(changed)


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
    duration = _duration(power_w, duration_minutes)
    if start_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("The start time must include a time zone.")
    # Duration is elapsed time even when the local clock skips or repeats.
    start_at, now = start_at.astimezone(UTC), now.astimezone(UTC)
    if not steps or steps[0].start > now or steps[-1].end <= now:
        raise ValueError("No current projection is available.")
    end = start_at + duration
    if end <= now:
        raise ValueError("This scenario has finished.")
    if end > steps[-1].end:
        raise ValueError("The complete scenario must fit inside the projection horizon.")
    begin = max(start_at, now, steps[0].start)
    additional = power_w * ((end - begin).total_seconds() / 3600)
    if not math.isfinite(additional):
        raise ValueError("Additional energy is outside the supported numeric range.")
    # Zero extra load reproduces the baseline exactly, including ties.
    chosen = _with_load(steps, power_w, begin, end)
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


def _start_options(first: datetime, last: datetime) -> tuple[datetime, ...]:
    """Check both exact endpoints as well as UTC quarter hours between them."""
    quarter = timedelta(minutes=15)
    start = first.replace(minute=first.minute // 15 * 15, second=0, microsecond=0) + quarter
    options = [first]
    while start < last:
        options.append(start)
        start += quarter
    if last > first:
        options.append(last)
    return tuple(options)


def _timing(start: datetime, end: datetime, run: Run, baseline_grid_wh: float) -> LoadTiming:
    additional_grid_wh = sum(run.shortfall_wh) - baseline_grid_wh
    if not math.isfinite(additional_grid_wh):
        raise ValueError("Additional energy is outside the supported numeric range.")
    return LoadTiming(start, end, min(run.soc), max(0.0, additional_grid_wh), run.reserve_at)


def find_best_time(
    soc_pct: float,
    battery: BatterySettings,
    steps: Sequence[Step],
    *,
    power_w: float,
    duration_minutes: int,
    earliest_start: datetime,
    finish_by: datetime,
    now: datetime,
) -> LoadPlan:
    """Rank complete runs against one frozen outlook and its reserve."""
    duration = _duration(power_w, duration_minutes)
    if not math.isfinite(power_w * (duration_minutes / 60)):
        raise ValueError("Additional energy is outside the supported numeric range.")
    if any(t.utcoffset() is None for t in (earliest_start, finish_by, now)):
        raise ValueError("Planning times must include a time zone.")
    now = now.astimezone(UTC)
    first = max(earliest_start.astimezone(UTC), now)
    deadline = finish_by.astimezone(UTC)
    if not steps or steps[0].start > now or steps[-1].end <= now:
        raise ValueError("No current projection is available.")
    if deadline - first < duration:
        return LoadPlan(reason_code="window_too_short")
    if deadline > steps[-1].end:
        return LoadPlan(reason_code="beyond_horizon")

    baseline = simulate(soc_pct, battery, steps)
    baseline_grid_wh = sum(baseline.shortfall_wh)
    candidates = []
    run_now = None
    for start in _start_options(first, deadline - duration):
        end = start + duration
        run = simulate(soc_pct, battery, _with_load(steps, power_w, start, end))
        if run.reserve_at is None or start == now:
            timing = _timing(start, end, run, baseline_grid_wh)
            if start == now:
                run_now = timing
            if run.reserve_at is None:
                candidates.append(timing)
    if not candidates:
        reason = "baseline_reaches_reserve" if baseline.reserve_at else "all_times_reach_reserve"
        return LoadPlan(run_now=run_now, reason_code=reason)
    # Disregard floating-point noise from splitting an otherwise identical step.
    best = min(
        candidates,
        key=lambda t: (round(t.additional_grid_wh, 6), -round(t.minimum_soc_pct, 6), t.start),
    )
    return LoadPlan(best, run_now)
