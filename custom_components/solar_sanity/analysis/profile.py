"""What a typical hour looks like, learned from the measurement window.

The outlook needs two expectations it cannot measure ahead of time: how much the
house will draw in each hour still to come, and — when no forecast provider is
configured — roughly when the sun will be up. Both come from the same place,
the hourly buckets the coordinator already keeps and backfills thirty days of
at setup, so a new installation has a profile on its first day.

Two clocks, on purpose. **Consumption is keyed by local hour**, because people
cook and sleep by the clock on the wall, and that clock jumps an hour twice a
year. **Production is keyed by UTC hour**, because the sun does not observe
daylight saving: keyed locally, the fortnight before a change would put
sunrise an hour out for the fortnight after it.

Pure, like the rest of ``analysis``. The local hour is injected, because
resolving it needs a time zone and this package deliberately has none.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .linalg import mean, median, percentile
from .model import Bucket

#: Days of history a profile is built from. Two weeks holds both kinds of
#: weekday and is short enough to follow the season: the load of an October
#: evening with the heating on is not the load of a September one.
PROFILE_DAYS = 14

#: Samples an hour-of-day needs before it is believed. Fewer than three and one
#: unusual evening — guests, an oven left on — is most of the average. An hour
#: below this makes the whole profile not ready; it is never filled with zero,
#: because a zero-consumption hour is the most flattering lie a projection of a
#: battery can tell.
MIN_SAMPLES_PER_HOUR = 3

#: Days of history the charging-rate ceiling is learned from. Longer than the
#: profile: the ceiling is a property of the hardware, not of the season, and
#: the hours that reach it are the sunny ones with an empty battery, which are
#: not every week.
RATE_DAYS = 30

#: Percentile of hourly charging energy taken as the ceiling. Not the maximum:
#: one hour that straddles a counter glitch would otherwise set it.
RATE_PERCENTILE = 99.0

#: Hours of charging needed before a ceiling is stated.
MIN_RATE_SAMPLES = 20

#: Charging beyond what the sun could explain, in Wh, before an hour is counted
#: as charged from the grid. Covers metering disagreement between a DC-side
#: production sensor and an AC-side load sensor.
GRID_CHARGE_MARGIN_WH = 100.0

#: Energy in an hour, in Wh, below which production, charging or grid import
#: counts as none. An idle inverter's meters report a few Wh of each.
QUIET_WH = 20.0

#: Discharge in an hour, in Wh, before the battery counts as carrying the house.
CARRYING_WH = 50.0

#: Hours the battery carried the house alone in the dark before an inverter's
#: own draw is stated: a night and a half.
MIN_STANDBY_HOURS = 24

#: Points below the target at which an hour counts as one the battery was
#: full in. A battery within three points of full is in its slow last stretch
#: or already there, and an inverter that cannot export then turns production
#: down to what the house uses. Measured production in that hour says nothing
#: about the sun.
FULL_BAND_PCT = 3.0

#: Days of forecast-and-production pairs needed before the forecast is scaled.
#: Fewer, and one stormy day the forecast missed would set the scale for every
#: sunny one.
MIN_SCALE_DAYS = 3

#: The scale is kept within these bounds. A ratio outside them is a forecast
#: for a different roof, or a production channel that is not the panels. It
#: says that something is mapped wrong, and correcting by it would hide that.
SCALE_BOUNDS = (0.1, 2.0)


@dataclass(frozen=True, slots=True)
class ForecastScale:
    """How far a provider's forecast runs from what this roof delivers.

    ``factor`` multiplies the forecast. ``hours`` and ``days`` are how much it
    rests on, carried for the diagnostics and the attributes.
    """

    factor: float
    hours: int
    days: int


@dataclass(frozen=True, slots=True)
class HourlyProfile:
    """A typical value for each of the 24 hours, in Wh.

    ``wh[h]`` is ``None`` when hour ``h`` has too few samples. ``samples`` is
    carried so diagnostics can show how much each figure rests on.
    """

    wh: tuple[float | None, ...]
    samples: tuple[int, ...]

    @property
    def ready(self) -> bool:
        """Every hour has a figure. A profile with a hole in it is not used."""
        return len(self.wh) == 24 and all(value is not None for value in self.wh)

    @property
    def days(self) -> int:
        """The weakest hour's sample count, which is roughly days of history."""
        return min(self.samples) if self.samples else 0

    def at(self, hour: int) -> float | None:
        return self.wh[hour % 24]


def hourly_profile(
    buckets: Sequence[Bucket],
    key: str,
    hour_of: Callable[[datetime], int],
    *,
    until: datetime,
    days: int = PROFILE_DAYS,
    typical: str = "mean",
) -> HourlyProfile:
    """The typical energy per hour-of-day for one channel.

    Only whole hours count: the first bucket after a restart covers part of an
    hour and would read as a quiet one. Only hours strictly before ``until`` are
    read, so a replay asking "what did the profile look like at 21:00 on the
    3rd" cannot see the 3rd's own night.

    ``typical`` is ``"mean"`` for consumption, because the projection adds hours
    up and only the mean preserves the total — a median would quietly drop the
    kettle and the oven from every evening. ``"median"`` suits production, where
    one overcast day should not move the shape of the sun.
    """
    since = until - timedelta(days=days)
    groups: list[list[float]] = [[] for _ in range(24)]
    for bucket in buckets:
        if bucket.seconds != 3600 or not (since <= bucket.start_utc < until):
            continue
        value = bucket.value(key)
        if value is None or not math.isfinite(value) or value < 0:
            continue
        groups[hour_of(bucket.start_utc) % 24].append(value)

    pick = median if typical == "median" else mean
    return HourlyProfile(
        wh=tuple(pick(group) if len(group) >= MIN_SAMPLES_PER_HOUR else None for group in groups),
        samples=tuple(len(group) for group in groups),
    )


def max_charge_w(
    buckets: Sequence[Bucket], key: str, *, until: datetime, days: int = RATE_DAYS
) -> float | None:
    """The fastest the battery has been seen to charge, as hourly energy.

    Without a ceiling the projection lets a four-kilowatt surplus fill a battery
    whose charger stops at two and a half, and announces "full at noon" for a
    battery that fills at three. ``None`` — no ceiling — until enough charging
    hours exist to say anything.
    """
    since = until - timedelta(days=days)
    values: list[float] = []
    for bucket in buckets:
        if bucket.seconds != 3600 or not (since <= bucket.start_utc < until):
            continue
        value = bucket.value(key)
        if value is not None and math.isfinite(value) and value > 0:
            values.append(value)
    if len(values) < MIN_RATE_SAMPLES:
        return None
    return percentile(values, RATE_PERCENTILE)


def grid_charged_hours(
    buckets: Sequence[Bucket],
    *,
    pv: str,
    load: str,
    charge: str,
    until: datetime,
    days: int = PROFILE_DAYS,
) -> int | None:
    """Hours in which the battery took in more than the sun had left over.

    Disclosure only. The projection deliberately does not count grid charging:
    it answers "if the grid were out, would the battery get me there?", which
    is the question somebody with frequent outages actually needs answered, and
    the conservative one for everybody else. But a projection that is pessimistic
    for a stated reason should state it, so this is counted and published.

    ``None`` when the channels needed to tell are not all present.
    """
    since = until - timedelta(days=days)
    seen = False
    count = 0
    for bucket in buckets:
        if bucket.seconds != 3600 or not (since <= bucket.start_utc < until):
            continue
        produced = bucket.value(pv)
        used = bucket.value(load)
        charged = bucket.value(charge)
        if produced is None or used is None or charged is None:
            continue
        seen = True
        if charged > max(produced - used, 0.0) + GRID_CHARGE_MARGIN_WH:
            count += 1
    return count if seen else None


def standby_w(
    buckets: Sequence[Bucket],
    *,
    pv: str,
    load: str,
    charge: str,
    discharge: str,
    grid: str,
    discharge_efficiency: float,
    until: datetime,
    days: int = PROFILE_DAYS,
) -> float | None:
    """The inverter's own draw, in W, on top of the house's.

    The house meter does not see the inverter running itself. On the reference
    installation in November 2026, over 366 dark hours with no sun and no grid,
    the battery delivered 125 W plus 1.03 times the house load. The projection
    left that out, and every night came out two to three points more
    optimistic than the battery really was.

    Measured, not configured: the median, over the hours the battery carried
    the house alone in the dark, of what it delivered beyond the load. It is
    stated on the house side of the discharge efficiency, like the load it
    is added to, so the projection divides both by the same figure. A battery
    metered on the AC side, where delivery and load agree, learns a negative
    figure, which clamps to zero and changes nothing.

    ``None`` when the channels are not all mapped or the battery has not
    carried the house alone for long enough to say.
    """
    since = until - timedelta(days=days)
    excess: list[float] = []
    for bucket in buckets:
        if bucket.seconds != 3600 or not (since <= bucket.start_utc < until):
            continue
        produced = bucket.value(pv)
        used = bucket.value(load)
        charged = bucket.value(charge)
        delivered = bucket.value(discharge)
        if produced is None or used is None or charged is None or delivered is None:
            continue
        imported = bucket.value(grid)
        if produced > QUIET_WH or charged > QUIET_WH or delivered < CARRYING_WH:
            continue
        if imported is not None and imported > QUIET_WH:
            continue
        excess.append(delivered * discharge_efficiency - used)
    if len(excess) < MIN_STANDBY_HOURS:
        return None
    middle = median(excess)
    return None if middle is None else max(middle, 0.0)


def forecast_scale(
    buckets: Sequence[Bucket],
    forecast: Mapping[datetime, float],
    *,
    soc_max: Mapping[datetime, float],
    full_pct: float,
    pv: str,
    grid: str,
    until: datetime,
    days: int = PROFILE_DAYS,
) -> ForecastScale | None:
    """What the panels delivered over what the forecast said, hour by hour.

    On the reference installation the Forecast.Solar forecast ran more than
    twice what the roof delivered, and the ratio held between 0.38 and 0.50
    through October and November 2026, measured with the archive re-keyed by
    period start (see ``_forecast_plan.by_period_start``). Unscaled, the
    outlook called a full battery on 13 of 27 mornings when the battery did
    not fill. Scaled, it did so on 2, and missed 4 days that did fill: wrong on
    6 days instead of 13.

    ``forecast`` is the forecast as it stood a day ahead, in Wh per UTC hour,
    because that is what a projection had in hand at the time. ``soc_max`` is
    the highest charge level in each UTC hour. Hours are left out when the
    battery was within ``FULL_BAND_PCT`` of ``full_pct``, when the grid was
    supplying the house, or when the charge level is not known: in any of
    them, production may have been turned down to what was used, and the sun
    was not the limit.
    """
    since = until - timedelta(days=days)
    by_hour = {bucket.start_utc: bucket for bucket in buckets if bucket.seconds == 3600}
    predicted = delivered = 0.0
    hours = 0
    dates: set[date] = set()
    for when, expected in forecast.items():
        if not (since <= when < until) or expected <= 0:
            continue
        bucket = by_hour.get(when)
        level = soc_max.get(when)
        if bucket is None or level is None or level >= full_pct - FULL_BAND_PCT:
            continue
        produced = bucket.value(pv)
        imported = bucket.value(grid)
        if produced is None or (imported is not None and imported > QUIET_WH):
            continue
        predicted += expected
        delivered += produced
        hours += 1
        dates.add(when.date())
    if len(dates) < MIN_SCALE_DAYS or predicted <= 0:
        return None
    low, high = SCALE_BOUNDS
    ratio = delivered / predicted
    if not low <= ratio <= high:
        return None
    return ForecastScale(factor=ratio, hours=hours, days=len(dates))
