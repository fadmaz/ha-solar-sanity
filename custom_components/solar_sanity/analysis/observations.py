"""Observation coverage shared by live grading and historical replay.

No Home Assistant, clock or database: missing readings stay missing.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timedelta

from .model import Bucket
from .profile import GRID_CHARGE_MARGIN_WH

#: The existing replay tolerance, preserved when live grading was added.
MAX_GAP = timedelta(hours=2)


def _left(values: Sequence[datetime], value: datetime) -> int:
    lo, hi = 0, len(values)
    while lo < hi:
        mid = (lo + hi) // 2
        if values[mid] < value:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _right(values: Sequence[datetime], value: datetime) -> int:
    lo, hi = 0, len(values)
    while lo < hi:
        mid = (lo + hi) // 2
        if values[mid] <= value:
            lo = mid + 1
        else:
            hi = mid
    return lo


class SocSeries:
    """A charge level as points in time, read by interpolation.

    Hourly means are placed at the middle of their hour, raw states at the
    moment they were reported. Both kinds appear in one export: Home Assistant
    keeps raw states for about ten days and hourly statistics after that.

    ``minima`` and ``maxima`` are each hour's extremes, keyed by the hour's
    start, where known. Grading uses them in preference to the means: a battery
    that touches its reserve at 01:10 and is charged from the grid from 01:30
    has an hourly *mean* a point above the reserve, and graded on means that
    night reads as one the projection wrongly said would reach it.
    """

    def __init__(
        self,
        points: Sequence[tuple[datetime, float]],
        minima: dict[datetime, float] | None = None,
        maxima: dict[datetime, float] | None = None,
    ) -> None:
        ordered = sorted(points)
        self.times = [when for when, _ in ordered]
        self.values = [value for _, value in ordered]
        self.minima = minima or {}
        self.maxima = maxima or {}
        self.hourly_extremes = minima is not None or maxima is not None

    def __bool__(self) -> bool:
        return bool(self.times)

    def at(self, when: datetime) -> float | None:
        i = _left(self.times, when)
        if i < len(self.times) and self.times[i] == when:
            return self.values[i]
        if i == 0 or i == len(self.times):
            return None
        before, after = self.times[i - 1], self.times[i]
        if after - before > MAX_GAP:
            return None
        share = (when - before) / (after - before)
        return self.values[i - 1] + share * (self.values[i] - self.values[i - 1])

    def lowest(self, start: datetime, end: datetime) -> tuple[float, datetime] | None:
        """The lowest level between two moments, and when it was first reached."""
        if not self.covers(start, end):
            return None
        first, last = self.at(start), self.at(end)
        if first is None or last is None:
            return None
        lo, hi = _right(self.times, start), _left(self.times, end)
        candidates = [(first, start), *zip(self.values[lo:hi], self.times[lo:hi], strict=True)]
        candidates.append((last, end))
        return min(candidates, key=lambda pair: (pair[0], pair[1]))

    def lowest_reading(self, start: datetime, end: datetime) -> tuple[float, datetime] | None:
        """The lowest reading in the hours from ``start`` up to ``end``.

        An incomplete night is not evidence that the battery stayed above reserve.
        """
        hours = _hours(start, end)
        if not self.covers(start, end):
            return None
        if not self.hourly_extremes:
            return self.lowest(start, end)
        return min((self.minima[hour], hour) for hour in hours)

    def covers(self, start: datetime, end: datetime, *, extreme: str = "min") -> bool:
        """Whether the entire interval can be graded, not just its surviving rows.

        The audit accepted eight hours of night from one hourly minimum. Raw CSV
        points need both endpoints and every internal gap within MAX_GAP instead.
        """
        if end <= start:
            return False
        if self.hourly_extremes:
            values = self.maxima if extreme == "max" else self.minima
            return all(h in values and math.isfinite(values[h]) for h in _hours(start, end))
        if self.at(start) is None or self.at(end) is None:
            return False
        lo = max(0, _right(self.times, start) - 1)
        hi = _left(self.times, end) + 1
        return all(
            after - before <= MAX_GAP
            for before, after in zip(self.times[lo:hi], self.times[lo + 1 : hi], strict=False)
        )

    def first_at_or_above(self, level: float, start: datetime, end: datetime) -> datetime | None:
        """When the level was first reached, to the hour where extremes exist."""
        if not self.covers(start, end, extreme="max"):
            return None
        if self.hourly_extremes:
            hour = next(
                (h for h in _hours(start, end) if self.maxima.get(h, -math.inf) >= level), None
            )
            return None if hour is None else max(hour + timedelta(minutes=30), start)
        return self._first_crossing(level, start, end, rising=True)

    def first_at_or_below(self, level: float, start: datetime, end: datetime) -> datetime | None:
        if not self.covers(start, end):
            return None
        if self.hourly_extremes:
            hour = next(
                (h for h in _hours(start, end) if self.minima.get(h, math.inf) <= level), None
            )
            return None if hour is None else max(hour + timedelta(minutes=30), start)
        return self._first_crossing(level, start, end, rising=False)

    def _first_crossing(
        self, level: float, start: datetime, end: datetime, *, rising: bool
    ) -> datetime | None:
        def past(value: float) -> bool:
            return value >= level if rising else value <= level

        previous_time, previous = start, self.at(start)
        if previous is None:
            return None
        if past(previous):
            return start
        lo, hi = _right(self.times, start), _left(self.times, end)
        for when, value in zip(self.times[lo:hi], self.values[lo:hi], strict=True):
            if past(value):
                if when - previous_time > MAX_GAP or value == previous:
                    return when
                share = (level - previous) / (value - previous)
                return previous_time + share * (when - previous_time)
            previous_time, previous = when, value
        return None


def _hours(start: datetime, end: datetime) -> list[datetime]:
    """Hour starts from the one containing ``start`` up to before ``end``."""
    hour = start.replace(minute=0, second=0, microsecond=0)
    out = []
    while hour < end:
        out.append(hour)
        hour += timedelta(hours=1)
    return out


def soc_from_diagnostics(rows: list[dict]) -> SocSeries:
    points = []
    minima: dict[datetime, float] = {}
    maxima: dict[datetime, float] = {}
    for row in rows:
        mean = row.get("mean")
        if mean is None:
            continue
        start = datetime.fromisoformat(row["start"])
        points.append((start + timedelta(minutes=30), float(mean)))
        if row.get("min") is not None:
            minima[start] = float(row["min"])
        if row.get("max") is not None:
            maxima[start] = float(row["max"])
    return SocSeries(points, minima, maxima)


def grid_charged(buckets: Sequence[Bucket], start: datetime, end: datetime) -> bool:
    for bucket in buckets:
        if not (start <= bucket.start_utc < end):
            continue
        pv, load, charge = (bucket.value(k) for k in ("pv", "load", "battery_charge"))
        if pv is None or load is None or charge is None:
            continue
        if charge > max(pv - load, 0.0) + GRID_CHARGE_MARGIN_WH:
            return True
    return False
