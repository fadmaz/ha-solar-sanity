"""Optional day-group consumption learned from previous local calendar dates."""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from .model import Bucket
from .outlook import Step

LOAD_REVISION = "alternate-days-v1"
# A month follows the owner's weekly routine without counting today's outcome.
LOAD_DAYS = 28
# A repeated clock hour is one local date, never a second training day.
MIN_LOCAL_DATES = 3
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True, slots=True)
class GroupProfile:
    wh: tuple[tuple[float | None, ...], ...]
    dates: tuple[tuple[int, ...], ...]


def learn_groups(
    buckets: Sequence[Bucket],
    key: str,
    alternate_days: Sequence[str],
    local: Callable[[datetime], tuple[date, int]],
    *,
    until: datetime,
) -> GroupProfile:
    """Each valid local date has equal weight, including a repeated clock hour."""
    today = local(until)[0]
    since = today - timedelta(days=LOAD_DAYS)
    selected = set(alternate_days)
    groups: list[list[dict[date, list[float]]]] = [[{} for _ in range(24)] for _ in range(2)]
    seen = set()
    for bucket in buckets:
        if bucket.seconds != 3600 or bucket.start_utc + timedelta(hours=1) > until:
            continue
        day, hour = local(bucket.start_utc)
        if not since <= day < today or bucket.start_utc in seen:
            continue
        seen.add(bucket.start_utc)
        value = bucket.value(key)
        if value is None or not math.isfinite(value) or value < 0:
            continue
        group = int(WEEKDAYS[day.weekday()] in selected)
        groups[group][hour].setdefault(day, []).append(value)
    means, counts = [], []
    for group in groups:
        means.append(
            tuple(
                sum(sum(values) / len(values) for values in hour.values()) / len(hour)
                if len(hour) >= MIN_LOCAL_DATES
                else None
                for hour in group
            )
        )
        counts.append(tuple(len(hour) for hour in group))
    return GroupProfile(tuple(means), tuple(counts))


def group_steps(
    steps: Sequence[Step],
    profile: GroupProfile,
    alternate_days: Sequence[str],
    local: Callable[[datetime], tuple[date, int]],
    *,
    anchored_first: bool,
) -> tuple[Step, ...] | None:
    """Keep the measured anchor and fall back if any required hour lacks evidence."""
    selected = set(alternate_days)
    result = []
    for index, step in enumerate(steps):
        if index == 0 and anchored_first:
            result.append(step)
            continue
        day, hour = local(step.start)
        value = profile.wh[int(WEEKDAYS[day.weekday()] in selected)][hour]
        if value is None:
            return None
        result.append(replace(step, load_wh=value * step.hours))
    return tuple(result)
