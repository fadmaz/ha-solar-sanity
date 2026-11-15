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
    highest_minimum_soc_pct: float | None = None


@dataclass(frozen=True, slots=True)
class SolarLoadTiming:
    start: datetime
    end: datetime
    baseline_grid_wh: float
    scenario_grid_wh: float

    @property
    def additional_grid_wh(self) -> float:
        return max(0.0, self.scenario_grid_wh - self.baseline_grid_wh)


@dataclass(frozen=True, slots=True)
class SolarLoadPlan:
    recommendation: SolarLoadTiming | None = None
    run_now: SolarLoadTiming | None = None
    reason_code: str | None = None
    lowest_extra_grid_wh: float | None = None


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


def _future_steps(steps: Sequence[Step], now: datetime) -> tuple[Step, ...]:
    """Begin from measured charge at now, excluding elapsed forecast energy."""
    if now.utcoffset() is None or not steps or steps[0].start > now:
        raise ValueError("No current projection is available.")
    now = now.astimezone(UTC)
    future = []
    cursor = now
    for original in steps:
        if original.end <= cursor:
            continue
        if original.start > cursor or (future and original.start != cursor):
            raise ValueError("The outlook has a gap in its future horizon.")
        step = original
        if original.start < cursor:
            hours = (original.end - cursor).total_seconds() / 3600
            share = hours / original.hours
            step = Step(
                cursor,
                hours,
                original.pv_wh * share,
                original.load_wh * share,
                original.daylight_wh * share,
            )
        future.append(step)
        cursor = step.end
    if not future:
        raise ValueError("No current projection is available.")
    return tuple(future)


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
    steps = _future_steps(steps, now)
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


def time_load_now(
    soc_pct: float,
    battery: BatterySettings,
    steps: Sequence[Step],
    *,
    power_w: float,
    duration_minutes: int,
    now: datetime,
) -> LoadTiming:
    """Simulate a whole run through the forecast horizon, including later reserve crossings."""
    end = now + _duration(power_w, duration_minutes)
    steps = _future_steps(steps, now)
    if end > steps[-1].end or not math.isfinite(power_w * (duration_minutes / 60)):
        raise ValueError("The complete load must fit inside a current outlook.")
    baseline = simulate(soc_pct, battery, steps)
    run = simulate(soc_pct, battery, _with_load(steps, power_w, now, end))
    return _timing(now, end, run, sum(baseline.shortfall_wh))


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
    minimum_soc_pct: float | None = None,
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
    steps = _future_steps(steps, now)
    if deadline - first < duration:
        return LoadPlan(reason_code="window_too_short")
    if deadline > steps[-1].end:
        return LoadPlan(reason_code="beyond_horizon")

    baseline = simulate(soc_pct, battery, steps)
    baseline_grid_wh = sum(baseline.shortfall_wh)
    candidates = []
    run_now = None
    highest_minimum = None
    for start in _start_options(first, deadline - duration):
        end = start + duration
        run = simulate(soc_pct, battery, _with_load(steps, power_w, start, end))
        if run.reserve_at is None or start == now:
            timing = _timing(start, end, run, baseline_grid_wh)
            if start == now:
                run_now = timing
            if run.reserve_at is None:
                minimum = timing.minimum_soc_pct
                if highest_minimum is None or minimum > highest_minimum:
                    highest_minimum = minimum
                if minimum_soc_pct is None or minimum >= minimum_soc_pct:
                    candidates.append(timing)
    if not candidates:
        if highest_minimum is not None:
            reason = (
                "baseline_below_planning_minimum"
                if minimum_soc_pct is not None and min(baseline.soc) < minimum_soc_pct
                else "planning_minimum_not_met"
            )
        else:
            reason = (
                "baseline_reaches_reserve" if baseline.reserve_at else "all_times_reach_reserve"
            )
        return LoadPlan(
            run_now=run_now, reason_code=reason, highest_minimum_soc_pct=highest_minimum
        )
    # Disregard floating-point noise from splitting an otherwise identical step.
    best = min(
        candidates,
        key=lambda t: (round(t.additional_grid_wh, 6), -round(t.minimum_soc_pct, 6), t.start),
    )
    return LoadPlan(best, run_now)


def solar_load_timing(
    steps: Sequence[Step], *, power_w: float, start: datetime, end: datetime
) -> SolarLoadTiming:
    """Compare grid deficits during exactly the part of the run being considered."""
    if not steps or end <= start or start < steps[0].start or end > steps[-1].end:
        raise ValueError("The load must fit within the current forecast.")
    baseline, scenario = 0.0, 0.0
    covered_until = start
    for step in steps:
        begin, finish = max(start, step.start), min(end, step.end)
        if begin >= finish:
            continue
        if begin != covered_until:
            raise ValueError("The load crosses an hour without complete planning data.")
        covered_until = finish
        hours = (finish - begin).total_seconds() / 3600
        net = (step.load_wh - step.pv_wh) * (hours / step.hours)
        baseline += max(0.0, net)
        scenario += max(0.0, net + power_w * hours)
    if covered_until != end or not math.isfinite(scenario):
        raise ValueError("The load crosses an hour without complete planning data.")
    return SolarLoadTiming(start, end, baseline, scenario)


def compare_solar_load(
    steps: Sequence[Step],
    *,
    power_w: float,
    duration_minutes: int,
    start_at: datetime,
    now: datetime,
) -> SolarLoadTiming:
    duration = _duration(power_w, duration_minutes)
    start = start_at.astimezone(UTC)
    return solar_load_timing(
        steps, power_w=power_w, start=max(start, now.astimezone(UTC)), end=start + duration
    )


def find_solar_load_time(
    steps: Sequence[Step],
    *,
    power_w: float,
    duration_minutes: int,
    earliest_start: datetime,
    finish_by: datetime,
    now: datetime,
    max_grid_wh: float | None,
) -> SolarLoadPlan:
    duration = _duration(power_w, duration_minutes)
    now = now.astimezone(UTC)
    first, deadline = max(earliest_start.astimezone(UTC), now), finish_by.astimezone(UTC)
    if deadline - first < duration:
        return SolarLoadPlan(reason_code="window_too_short")
    timings = [
        solar_load_timing(steps, power_w=power_w, start=start, end=start + duration)
        for start in _start_options(first, deadline - duration)
    ]
    best = min(timings, key=lambda item: (round(item.additional_grid_wh, 6), item.start))
    run_now = timings[0] if first == now else None
    lowest = best.additional_grid_wh
    if not meets_grid_limit(lowest, max_grid_wh):
        return SolarLoadPlan(
            run_now=run_now, reason_code="grid_limit_not_met", lowest_extra_grid_wh=lowest
        )
    return SolarLoadPlan(best, run_now)


def meets_grid_limit(additional_grid_wh: float, max_grid_wh: float | None) -> bool:
    return max_grid_wh is None or round(additional_grid_wh, 6) <= round(max_grid_wh, 6)
