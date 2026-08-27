"""Deciding which forecast hours to archive, with no I/O in sight.

A provider revises its forecast all day. The rolling archive keeps the latest
revision for every hour, which makes it idempotent and useless for scoring: by
the time an hour has passed, what is stored for it was issued minutes before,
not the day before. Scoring that would flatter every provider equally.

The day-ahead archive answers the other question, and the rule that makes it an
answer rather than a hope is that an hour is written once, at real lead time,
and never revised. This module is that rule on its own, so it can be tested
without a recorder — the same reason ``_match`` exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta

#: An hourly point: when it starts (UTC, on the hour) and its energy in kWh.
type Point = tuple[datetime, float]


def eligible(points: list[Point], now: datetime, min_lead_hours: int) -> list[Point]:
    """Points still far enough ahead to be a forecast rather than a nowcast."""
    horizon = now + timedelta(hours=min_lead_hours)
    return [(when, value) for when, value in points if when >= horizon]


def dayahead_write_plan(
    points: list[Point],
    existing: dict[datetime, float],
    now: datetime,
    min_lead_hours: int,
) -> list[Point]:
    """The rows to write to the day-ahead archive, in ascending order.

    Empty when nothing is due — which is the common case, since most captures
    see only hours that are already recorded or already too close.

    Two properties this has to hold, and they pull against each other:

    * **An hour keeps the value it was first given.** Anything else and the
      archive drifts towards being a nowcast again, one revision at a time.
    * **The running total must never go backwards.** Capture stops sometimes —
      restarts, downtime, an inverter that publishes late — and a gap can open
      *behind* the newest row. Appending into that gap without replaying what
      follows leaves ``sum`` decreasing at the join, which is the one thing the
      recorder's contract does not permit.

    So new hours are merged in, existing values win, and everything from the
    earliest new hour onwards is rewritten together.
    """
    fresh = [
        (when, value)
        for when, value in eligible(points, now, min_lead_hours)
        if when not in existing
    ]
    if not fresh:
        return []

    earliest = min(when for when, _ in fresh)
    merged: dict[datetime, float] = {
        when: value for when, value in existing.items() if when >= earliest
    }
    for when, value in fresh:
        merged.setdefault(when, value)

    return sorted(merged.items())


def running_totals(points: list[Point], resume: float) -> list[tuple[datetime, float, float]]:
    """``(start, state, sum)`` for each point, carrying a running total.

    Separated from the write purely so the arithmetic is visible to a test. The
    running total is bookkeeping the recorder's contract requires; it is never
    what scoring reads.
    """
    out: list[tuple[datetime, float, float]] = []
    total = resume
    for when, value in points:
        total += value
        out.append((when, value, total))
    return out
