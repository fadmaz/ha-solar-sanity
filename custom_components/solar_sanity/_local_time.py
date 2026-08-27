"""Which local day an hour belongs to, and whether that day is a strange length.

Both answers need a time zone, which is exactly what the analysis package does
not have — it holds no clock and no zone database, and that is what keeps
``analyse`` byte-identical for identical input. So the resolution happens here,
at the edge, and the answer travels inwards as data on the bucket.

A single UTC offset applied across a window is not a substitute. It is right for
most of the year and wrong for half of any window containing a daylight-saving
change, and where it is wrong it is wrong by a whole calendar day for every hour
near local midnight — quietly moving a night's energy into the neighbouring day
on precisely the days the fits are already least trustworthy.

Kept free of Home Assistant imports so the rule can be tested against real zone
data without one, the same reason ``_match`` and ``_forecast_plan`` exist.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo


def local_day(when_utc: datetime, zone: tzinfo | None) -> tuple[date | None, bool]:
    """Return ``(local date, whether that day is not 24 hours long)``.

    ``(None, False)`` when there is no zone to resolve against — an unknown day
    is left unknown rather than guessed at, and the caller falls back to
    whatever it was doing before.
    """
    if zone is None:
        return None, False

    try:
        day = when_utc.astimezone(zone).date()
    except (ValueError, OverflowError, OSError):
        return None, False

    return day, not _is_whole_day(day, zone)


def _is_whole_day(day: date, zone: tzinfo) -> bool:
    """Whether local midnight to local midnight spans exactly twenty-four hours.

    Measured rather than looked up, so this needs to know no transition rule —
    a spring-forward day comes out at 23 hours and an autumn one at 25.

    Both ends are converted to UTC before subtracting, and that is the whole
    trick. Subtracting two aware datetimes that carry the *same* zone gives the
    wall-clock difference, not the elapsed one: midnight to midnight across a
    transition reads as exactly 24 hours, and the day it exists to catch is the
    one it would quietly wave through.
    """
    try:
        start = datetime.combine(day, time.min, tzinfo=zone)
        following = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
        elapsed = following.astimezone(UTC) - start.astimezone(UTC)
    except (ValueError, OverflowError, OSError):
        # Nothing is known about the day, so nothing is claimed about it. Saying
        # "not a transition" here would be a guess dressed as a measurement.
        return True

    return round(elapsed.total_seconds()) == 24 * 3600
