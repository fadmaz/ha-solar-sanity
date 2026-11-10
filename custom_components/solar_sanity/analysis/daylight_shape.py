"""Conservative daylight-shape experiment; never changes the daily correction."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .model import Bucket
from .profile import FULL_BAND_PCT, MIN_SCALE_DAYS, PROFILE_DAYS, QUIET_WH, ForecastScale, ScaleDay

SHAPE_REVISION = "daylight-thirds-v1"
# Promotion requires new issued observations, not a fit on the development house.
SHAPE_ENABLED = False


def daylight_thirds(
    points: Mapping[datetime, float],
    window: tuple[datetime, datetime] | None = None,
) -> dict[datetime, int] | None:
    """Explicit zero shoulders and continuous daylight prove the day's horizon.

    A truncated midday forecast can otherwise appear to be a complete short day.
    Providers without this evidence retain the current distribution.
    """
    if not points or any(not math.isfinite(v) or v < 0 for v in points.values()):
        return None
    positive = sorted(t for t, v in points.items() if v > 0)
    if not positive:
        return None
    first, last = positive[0], positive[-1]
    hour = timedelta(hours=1)
    if window is None:
        if points.get(first - hour) != 0 or points.get(last + hour) != 0:
            return None
    else:
        # A provider may omit dark hours. Independently calculated daylight
        # bounds can prove coverage without inventing zero shoulder readings.
        sunrise, sunset = window
        cursor = sunrise.replace(minute=0, second=0, microsecond=0)
        if sunset <= sunrise:
            return None
        while cursor < sunset:
            if cursor not in points:
                return None
            cursor += hour
    cursor = first
    while cursor <= last:
        if cursor not in points:
            return None
        cursor += hour
    span = (last + hour - first).total_seconds()
    return {t: min(2, int(3 * (t + hour / 2 - first).total_seconds() / span)) for t in positive}


@dataclass(frozen=True, slots=True)
class DaylightShape:
    thirds: tuple[ForecastScale, ...]

    @property
    def counts(self) -> tuple[int, ...]:
        return tuple(part.days for part in self.thirds)

    def distribute(
        self,
        corrected: Mapping[datetime, float],
        forecast_wh: float,
        window: tuple[datetime, datetime] | None = None,
    ) -> dict[datetime, float] | None:
        """Keep exactly the corrected day's total while moving its distribution."""
        bins = daylight_thirds(corrected, window)
        if bins is None:
            return None
        shaped = {
            t: value * self.thirds[bins[t]].for_day(forecast_wh) if t in bins else value
            for t, value in corrected.items()
        }
        total = sum(corrected.values())
        weighted = sum(shaped.values())
        if weighted <= 0:
            return None
        return {t: value * total / weighted for t, value in shaped.items()}


def learn_shape(
    buckets: Sequence[Bucket],
    forecast: Mapping[datetime, float],
    *,
    soc_max: Mapping[datetime, float],
    full_pct: float,
    until: datetime,
    day_of: Callable[[datetime], date],
    pv: str = "pv",
    grid: str = "grid_import",
    daylight: Mapping[date, tuple[datetime, datetime]] | None = None,
) -> DaylightShape | None:
    """Use previous local days only, the same similarity weights and prior as scale."""
    since = until - timedelta(days=PROFILE_DAYS)
    grouped: dict[date, dict[datetime, float]] = {}
    for when, value in forecast.items():
        if since <= when < until and day_of(when) < day_of(until):
            grouped.setdefault(day_of(when), {})[when] = value
    by_hour = {b.start_utc: b for b in buckets if b.seconds == 3600}
    taught: list[list[ScaleDay]] = [[], [], []]
    counts = [0, 0, 0]
    for day, points in grouped.items():
        bins = daylight_thirds(points, None if daylight is None else daylight.get(day))
        if bins is None:
            continue
        pairs = [[0.0, 0.0] for _ in range(3)]
        for when, part in bins.items():
            bucket = by_hour.get(when)
            level = soc_max.get(when)
            if bucket is None or level is None or level >= full_pct - FULL_BAND_PCT:
                continue
            produced, imported = bucket.value(pv), bucket.value(grid)
            if produced is None or produced < 0 or (imported is not None and imported > QUIET_WH):
                continue
            pairs[part][0] += points[when]
            pairs[part][1] += produced
            counts[part] += 1
        for part, (expected, produced) in enumerate(pairs):
            if expected > 0:
                taught[part].append(ScaleDay(sum(points.values()), expected, produced))
    if any(len(days) < MIN_SCALE_DAYS for days in taught):
        return None
    return DaylightShape(
        tuple(
            ForecastScale(
                sum(d.delivered_wh for d in days) / sum(d.predicted_wh for d in days),
                counts[part],
                len(days),
                tuple(days),
            )
            for part, days in enumerate(taught)
        )
    )
