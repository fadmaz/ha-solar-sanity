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

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any, Literal

#: An hourly point: when it starts (UTC, on the hour) and its energy in kWh.
type Point = tuple[datetime, float]
type ForecastHorizon = Literal["battery", "today", "tomorrow"]

FORECAST_SOURCE_REASONS = {
    "provider_unavailable": "The forecast provider is unavailable.",
    "capture_unavailable": "The latest forecast capture did not return usable data.",
    "forecast_stale": "The solar forecast has not been refreshed within three hours.",
    "forecast_horizon_missing": "The forecast does not cover this period.",
    "waiting_for_capture": "Waiting for a valid forecast capture.",
    "no_provider": "No solar forecast provider is selected.",
}


@dataclass(frozen=True, slots=True)
class ForecastSelection:
    provider_id: str | None = None
    captured_at: datetime | None = None
    reason_code: str | None = None


def forecast_is_fresh(captured: datetime | None, now: datetime, max_age: timedelta) -> bool:
    """One age rule for the projection and all figures derived from a capture."""
    return captured is not None and now - captured <= max_age


def by_period_start(wh_hours: Mapping[str, Any]) -> dict[str, Any]:
    """A ``wh_hours`` map keyed by the end of each period, re-keyed by its start.

    Forecast.Solar stamps each value with the moment its period *ends*: "the
    value is always for the period from last timestamp to the timestamp in the
    key". Home Assistant passed those keys through unchanged until 2026.11
    (core PR 188790). Read as period starts, every hour landed one hour late,
    and the short sunset period was folded into the hour before it. On the
    reference installation that put several kilowatt-hours into the hour the
    sun set in.

    This is core's correction, so both sides of 2026.11 agree: each value moves
    to the previous timestamp, and a zero at midnight is the start of a new day
    rather than a period that ended there. The midnight test is made in the
    key's own offset, as core makes it. Keys are ordered by instant, not as
    text, because around a daylight-saving change the offsets in one payload
    differ. The original key strings are kept, so nothing downstream sees a
    format it has not seen before.
    """
    stamped: list[tuple[datetime, str, Any]] = []
    for raw, value in wh_hours.items():
        try:
            when = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        stamped.append((when, str(raw), value))
    stamped.sort(key=lambda item: item[0] if item[0].tzinfo else item[0].replace(tzinfo=UTC))

    out: dict[str, Any] = {}
    for (_, start, _), (end, _, value) in pairwise(stamped):
        if value == 0 and (end.hour, end.minute, end.second) == (0, 0, 0):
            continue
        out[start] = value
    return out


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
