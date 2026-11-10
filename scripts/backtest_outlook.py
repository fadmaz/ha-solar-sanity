"""Replay the battery outlook against what a real battery actually did.

Every evening in the window, the projection is run exactly as the integration
would have run it then — profiles built only from the hours before that
moment, the forecast that existed a day ahead — and its answers are compared
with the charge level the battery really reached. The same is done each
morning for "full at" when forecast history is available.

The point is the one lesson the energy-balance check taught this project: a
number nobody can grade will be trusted until it is badly wrong. This grades
it, on the house that will use it, before anything is built on top of it.

Usage::

    python scripts/backtest_outlook.py diagnostics/config_entry-solar_sanity-XXXX.json \\
        --capacity 10 --tz Asia/Jerusalem

A diagnostics download from 0.28.0 or later carries everything needed:
``window``, ``soc_history`` and ``forecast_history``. An older one lacks the
last two; pass the charge level as a Home Assistant history export with
``--soc-csv`` and the night read-outs are graded against production history
instead of a forecast.

It only prints. Keep the input files out of git: they carry a real house's
entity ids and a month of its consumption (``diagnostics/`` is ignored).
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "custom_components" / "solar_sanity")]

from analysis.model import Bucket  # noqa: E402
from analysis.outlook import BatterySettings, build_steps, project  # noqa: E402
from analysis.profile import (  # noqa: E402
    GRID_CHARGE_MARGIN_WH,
    PROFILE_DAYS,
    forecast_scale,
    hourly_profile,
    max_charge_w,
    standby_w,
)

from tests.synth import diagnostics  # noqa: E402

#: Points further apart than this are not interpolated between: a charge level
#: missing for longer is unknown, not a straight line.
MAX_GAP = timedelta(hours=2)


@dataclass(frozen=True)
class Night:
    start: datetime
    soc: float
    predicted_lowest: float
    actual_lowest: float
    predicted_reserve: bool
    actual_reserve: bool
    takeover: datetime
    grid_charged: bool
    predicted_reserve_at: datetime | None = None
    actual_reserve_at: datetime | None = None

    @property
    def reserve_minutes(self) -> float | None:
        """Predicted minus actual time of reaching reserve; negative is early."""
        if self.predicted_reserve_at is None or self.actual_reserve_at is None:
            return None
        return (self.predicted_reserve_at - self.actual_reserve_at).total_seconds() / 60

    @property
    def error(self) -> float:
        return self.predicted_lowest - self.actual_lowest


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
        i = bisect_left(self.times, when)
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
        lo, hi = bisect_right(self.times, start), bisect_left(self.times, end)
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
        lo = max(0, bisect_right(self.times, start) - 1)
        hi = bisect_left(self.times, end) + 1
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
        lo, hi = bisect_right(self.times, start), bisect_left(self.times, end)
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


def soc_from_csv(path: Path) -> SocSeries:
    """A Home Assistant history export: ``entity_id,state,last_changed``.

    A fractional value stamped exactly on the hour is an hourly mean, and is
    moved to the middle of its hour; anything else is a raw state.
    """
    points = []
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                value = float(row["state"])
            except (TypeError, ValueError):
                continue
            when = datetime.fromisoformat(row["last_changed"].replace("Z", "+00:00"))
            on_the_hour = when.minute == 0 and when.second == 0 and when.microsecond == 0
            if on_the_hour and not value.is_integer():
                when += timedelta(minutes=30)
            points.append((when, value))
    return SocSeries(points)


def forecast_from_diagnostics(history: dict) -> dict[datetime, float] | None:
    """The first provider's day-ahead archive, in Wh per UTC hour."""
    for series in history.values():
        dayahead = (series or {}).get("dayahead")
        if dayahead:
            return {datetime.fromisoformat(k): v * 1000.0 for k, v in dayahead.items()}
    return None


def keyed_by_period_start(
    forecast: dict[datetime, float], before: datetime
) -> dict[datetime, float]:
    """Move every row archived before ``before`` back by one hour.

    Forecast.Solar stamps each value with the end of its period, and Home
    Assistant passed that through until 2026.11, so rows archived on an older
    release are an hour late. Rows from ``before`` on are left alone. This is
    an approximation at sunset, where the archive already folded two periods
    into one hour and the split cannot be recovered.

    ``before`` is the start of the first day whose day-ahead rows are keyed by
    period start. Read it off the archive rather than off the upgrade date: a
    day-ahead row is written up to two days ahead and never revised, so the
    series switches days after the release does. In the sunrise hour the
    shifted rows hold only the minutes before the hour, a fraction of a full
    sunrise hour, which makes the switch easy to see.
    """
    return {
        (when - timedelta(hours=1) if when < before else when): wh for when, wh in forecast.items()
    }


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


def effective_wh_per_pct(
    buckets: Sequence[Bucket], soc: SocSeries
) -> tuple[float, float, float] | None:
    """How much house load moves the charge level one point, measured after dark.

    Two estimates over the dark hours the battery carried alone. The ratio of
    total load to total drop is what the projection assumes, since it has no
    standby term; the straight-line fit ``drop = standby + load / k`` says
    whether a standby term would be worth adding. Against the configured
    capacity times 0.92, either says whether the projection's battery is the
    real one.

    Returns (ratio Wh per point, fitted Wh per point, standby points per hour).
    """
    xs: list[float] = []
    ys: list[float] = []
    for bucket in buckets:
        if bucket.seconds != 3600:
            continue
        pv, load = bucket.value("pv"), bucket.value("load")
        charge, grid = bucket.value("battery_charge"), bucket.value("grid_import")
        if pv is None or load is None or pv > 10 or (charge is not None and charge > 20):
            continue
        if grid is not None and grid > 20:
            continue
        begin, end = soc.at(bucket.start_utc), soc.at(bucket.start_utc + timedelta(hours=1))
        if begin is None or end is None:
            continue
        xs.append(load)
        ys.append(begin - end)
    if len(xs) < 24:
        return None
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx
    if slope <= 0 or sum(ys) <= 0:
        return None
    return sum(xs) / sum(ys), 1.0 / slope, my - slope * mx


#: Set from the command line: replay as 0.28.0 did, without the learned
#: standby and forecast scale, to compare.
RAW = False

#: Set from the command line: one factor for every day, as before 0.32.0.
ONE_FACTOR = False


def learned(buckets: Sequence[Bucket], battery: BatterySettings, now: datetime) -> BatterySettings:
    """The battery as the integration would have seen it at ``now``."""
    standby = None
    if not RAW:
        standby = standby_w(
            buckets,
            pv="pv",
            load="load",
            charge="battery_charge",
            discharge="battery_discharge",
            grid="grid_import",
            discharge_efficiency=battery.discharge_efficiency,
            until=now,
        )
    return BatterySettings(
        capacity_wh=battery.capacity_wh,
        reserve_pct=battery.reserve_pct,
        target_pct=battery.target_pct,
        max_charge_w=max_charge_w(buckets, "battery_charge", until=now),
        standby_w=0.0 if standby is None else standby,
    )


def scaled(
    buckets: Sequence[Bucket],
    forecast: dict[datetime, float] | None,
    soc: SocSeries,
    battery: BatterySettings,
    now: datetime,
    *,
    zone: ZoneInfo | None = None,
) -> dict[datetime, float] | None:
    """The forecast scaled as the integration would have scaled it at ``now``.

    Each local day by its own factor, learned mostly from the days whose
    forecast looked like it, as ``ForecastScale.for_day`` does. Here every
    day's total is its day-ahead total; the integration takes the current
    forecast for hours still to come, which this archive does not hold.
    """
    if forecast is None or RAW:
        return forecast
    hour = now.replace(minute=0, second=0, microsecond=0)
    since = hour - timedelta(days=PROFILE_DAYS)

    def day_of(when: datetime) -> date:
        return when.astimezone(zone).date() if zone is not None else when.date()

    scale = forecast_scale(
        buckets,
        forecast,
        soc_max={when: level for when, level in soc.maxima.items() if since <= when < hour},
        full_pct=battery.target_pct,
        pv="pv",
        grid="grid_import",
        until=hour,
        day_of=day_of,
    )
    if scale is None:
        return forecast
    if ONE_FACTOR:
        return {when: wh * scale.factor for when, wh in forecast.items()}
    totals: dict[date, float] = {}
    for when, wh in forecast.items():
        totals[day_of(when)] = totals.get(day_of(when), 0.0) + wh
    factors = {day: scale.for_day(total) for day, total in totals.items()}
    return {when: wh * factors[day_of(when)] for when, wh in forecast.items()}


def local_hour_of(zone: ZoneInfo) -> Callable[[datetime], int]:
    return lambda when: when.astimezone(zone).hour


def replay_nights(
    buckets: Sequence[Bucket],
    soc: SocSeries,
    forecast: dict[datetime, float] | None,
    *,
    battery: BatterySettings,
    zone: ZoneInfo,
    evening: int | Sequence[int],
) -> tuple[list[Night], dict[str, int]]:
    skipped = {"no_soc": 0, "not_ready": 0, "no_takeover": 0, "no_actual": 0, "incomplete_soc": 0}
    nights: list[Night] = []
    hours = [evening] if isinstance(evening, int) else list(evening)
    first = buckets[0].start_utc.astimezone(zone).date()
    last = buckets[-1].start_utc.astimezone(zone).date()
    local_hour = local_hour_of(zone)
    starts = []
    day = first
    while day <= last:
        starts += [
            datetime(day.year, day.month, day.day, hour, tzinfo=zone).astimezone(UTC)
            for hour in hours
        ]
        day += timedelta(days=1)
    for now in starts:
        start_soc = soc.at(now)
        if start_soc is None:
            skipped["no_soc"] += 1
            continue
        load = hourly_profile(buckets, "load", local_hour, until=now)
        pv = hourly_profile(buckets, "pv", lambda w: w.hour, until=now, typical="median")
        if not load.ready or (forecast is None and not pv.ready):
            skipped["not_ready"] += 1
            continue
        settings = learned(buckets, battery, now)
        steps = build_steps(
            now,
            load=load.wh,
            local_hour=local_hour,
            pv_forecast=scaled(buckets, forecast, soc, battery, now, zone=zone),
            pv_profile=None if forecast is not None else pv.wh,
        )
        if not steps:
            skipped["not_ready"] += 1
            continue
        outlook = project(start_soc, settings, steps, forecast_used=forecast is not None)
        if outlook.takeover_at is None or outlook.lowest_pct is None:
            skipped["no_takeover"] += 1
            continue
        if not soc.covers(now, outlook.takeover_at):
            skipped["incomplete_soc"] += 1
            continue
        lowest = soc.lowest_reading(now, outlook.takeover_at)
        if lowest is None:
            skipped["no_actual"] += 1
            continue
        actual, reached = lowest
        nights.append(
            Night(
                start=now,
                soc=start_soc,
                predicted_lowest=outlook.lowest_pct,
                actual_lowest=actual,
                predicted_reserve=outlook.reserve_at is not None,
                actual_reserve=actual <= battery.reserve_pct + 0.5,
                takeover=outlook.takeover_at,
                # Only charging before the lowest point disturbs it. An inverter
                # that hands the house to the grid at its cut-off and tops the
                # battery up from there has already shown its minimum.
                grid_charged=grid_charged(buckets, now, reached - timedelta(hours=1)),
                predicted_reserve_at=outlook.reserve_at,
                actual_reserve_at=soc.first_at_or_below(
                    battery.reserve_pct + 0.5, now, outlook.takeover_at
                ),
            )
        )
    return nights, skipped


def replay_mornings(
    buckets: Sequence[Bucket],
    soc: SocSeries,
    forecast: dict[datetime, float],
    *,
    battery: BatterySettings,
    zone: ZoneInfo,
    morning: int,
    skipped: dict[str, int] | None = None,
) -> list[tuple[datetime, datetime | None, datetime | None]]:
    """Predicted against actual "full at", one projection each morning."""
    out = []
    local_hour = local_hour_of(zone)
    day = buckets[0].start_utc.astimezone(zone).date()
    last = buckets[-1].start_utc.astimezone(zone).date()
    full_level = battery.target_pct - 1.0
    while day <= last:
        now = datetime(day.year, day.month, day.day, morning, tzinfo=zone).astimezone(UTC)
        day += timedelta(days=1)
        start_soc = soc.at(now)
        load = hourly_profile(buckets, "load", local_hour, until=now)
        if start_soc is None or not load.ready or start_soc >= full_level:
            continue
        steps = build_steps(
            now,
            load=load.wh,
            local_hour=local_hour,
            pv_forecast=scaled(buckets, forecast, soc, battery, now, zone=zone),
            pv_profile=None,
        )
        if not steps:
            continue
        outlook = project(start_soc, learned(buckets, battery, now), steps, forecast_used=True)
        dusk = now + timedelta(hours=14)
        if not soc.covers(now, dusk, extreme="max"):
            if skipped is not None:
                skipped["incomplete_soc"] = skipped.get("incomplete_soc", 0) + 1
            continue
        actual = soc.first_at_or_above(full_level, now, dusk)
        predicted = outlook.full_at if outlook.full_at and outlook.full_at <= dusk else None
        out.append((now, predicted, actual))
    return out


def forecast_lag(buckets: Sequence[Bucket], forecast: dict[datetime, float]) -> dict[int, float]:
    """Correlation of forecast with measured production at -1, 0 and +1 hours."""
    measured = {b.start_utc: b.value("pv") for b in buckets if b.seconds == 3600}
    out: dict[int, float] = {}
    for lag in (-1, 0, 1):
        pairs = [
            (f, measured.get(when + timedelta(hours=lag)))
            for when, f in forecast.items()
            if measured.get(when + timedelta(hours=lag)) is not None
        ]
        if len(pairs) < 24:
            continue
        xs, ys = zip(*pairs, strict=True)
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
        sy = math.sqrt(sum((y - my) ** 2 for y in ys))
        if sx and sy:
            out[lag] = sum((x - mx) * (y - my) for x, y in pairs) / (sx * sy)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("diagnostics", type=Path)
    parser.add_argument("--soc-csv", type=Path, help="charge level as a history export")
    parser.add_argument("--capacity", type=float, help="usable capacity in kWh")
    parser.add_argument("--reserve", type=float)
    parser.add_argument("--target", type=float)
    parser.add_argument("--tz", default="UTC", help="the installation's IANA time zone")
    parser.add_argument(
        "--evening",
        type=int,
        nargs="+",
        default=[20],
        help="local hours nights are projected from (several allowed)",
    )
    parser.add_argument("--morning", type=int, default=8, help="local hour mornings are projected")
    parser.add_argument("--since", help="ignore nights before this ISO date")
    parser.add_argument(
        "--period-end-before",
        help="ISO instant: day-ahead rows before it are Forecast.Solar period-end keys "
        "from Home Assistant older than 2026.11, and are moved back an hour",
    )
    parser.add_argument("--verbose", action="store_true", help="print every night")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="replay as 0.28.0 did: no learned standby, no forecast scale",
    )
    parser.add_argument(
        "--one-factor",
        action="store_true",
        help="scale every day by one factor, as before 0.32.0",
    )
    args = parser.parse_args()
    global RAW, ONE_FACTOR  # noqa: PLW0603
    RAW = args.raw
    ONE_FACTOR = args.one_factor

    data = diagnostics.load(args.diagnostics)
    data = data.get("data", data)
    buckets = diagnostics.buckets_from(data["window"])
    options = (data.get("entry") or {}).get("options") or {}

    capacity = args.capacity or options.get("battery_capacity_kwh")
    if not capacity:
        parser.error("no capacity: pass --capacity, or use a download that has one configured")
    battery = BatterySettings(
        capacity_wh=float(capacity) * 1000.0,
        reserve_pct=args.reserve or options.get("battery_reserve_pct") or 20.0,
        target_pct=args.target or options.get("battery_target_pct") or 100.0,
    )

    if args.soc_csv:
        soc = soc_from_csv(args.soc_csv)
    else:
        soc = soc_from_diagnostics(data.get("soc_history") or [])
    if not soc:
        parser.error("no charge-level history: pass --soc-csv for a download older than 0.28.0")

    forecast = forecast_from_diagnostics(data.get("forecast_history") or {})
    if forecast is not None and args.period_end_before:
        before = datetime.fromisoformat(args.period_end_before)
        if before.tzinfo is None:
            before = before.replace(tzinfo=UTC)
        forecast = keyed_by_period_start(forecast, before)
    zone = ZoneInfo(args.tz)

    print(f"window      {buckets[0].start_utc:%Y-%m-%d} .. {buckets[-1].start_utc:%Y-%m-%d}")
    print(
        f"charge      {soc.times[0]:%Y-%m-%d} .. {soc.times[-1]:%Y-%m-%d}, {len(soc.times)} points"
    )
    print(
        f"battery     {battery.capacity_wh / 1000:g} kWh, reserve {battery.reserve_pct:g}%, "
        f"target {battery.target_pct:g}%"
    )
    print(f"production  {'day-ahead forecast' if forecast else 'history (no forecast archive)'}")
    if forecast is not None and args.period_end_before:
        print(f"            rows before {args.period_end_before} moved back an hour")

    fitted = effective_wh_per_pct(buckets, soc)
    if fitted:
        ratio, per_pct, standby = fitted
        print(
            f"measured    {ratio:.0f} Wh of house load per point after dark "
            f"-> {ratio * 100 / 0.92 / 1000:.1f} kWh at 92%"
        )
        print(
            f"            fitted with a standby term: {per_pct:.0f} Wh per point, "
            f"standby {standby:+.2f} points/hour"
        )
        print(f"assumed     {battery.capacity_wh * 0.92 / 100:.0f} Wh per point")
    end = buckets[-1].start_utc + timedelta(hours=1)
    if not RAW:
        settings = learned(buckets, battery, end)
        print(f"standby     {settings.standby_w:.0f} W learned over the last {PROFILE_DAYS} days")

    if forecast:
        lags = forecast_lag(buckets, forecast)
        shown = ", ".join(f"{lag:+d} h: r={r:.3f}" for lag, r in sorted(lags.items()))
        print(f"lag         {shown}")
        if not RAW:
            now_scaled = scaled(buckets, forecast, soc, battery, end, zone=zone)
            today = end.astimezone(zone).date()
            shown = []
            for name, day in (("today", today), ("tomorrow", today + timedelta(days=1))):
                hours = [
                    w for w, wh in forecast.items() if wh > 0 and w.astimezone(zone).date() == day
                ]
                if now_scaled is not forecast and hours:
                    shown.append(f"{now_scaled[hours[0]] / forecast[hours[0]]:.2f} {name}")
            if shown:
                print(
                    f"scale       forecast x {', '.join(shown)}, by the kind of day, "
                    f"learned over the last {PROFILE_DAYS} days"
                )
            else:
                print("scale       none learned (too few hours with room in the battery)")

    nights, skipped = replay_nights(
        buckets, soc, forecast, battery=battery, zone=zone, evening=args.evening
    )
    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=zone)
        nights = [n for n in nights if n.start >= since]
    graded = [n for n in nights if not n.grid_charged]
    print()
    at = ", ".join(f"{hour}:00" for hour in args.evening)
    print(f"nights      {len(nights)} projections from {at}, skipped {skipped}")
    print(f"            {len(nights) - len(graded)} with grid charging, set aside")
    if args.verbose:
        for n in nights:
            mark = " grid" if n.grid_charged else ""
            print(
                f"  {n.start.astimezone(zone):%m-%d %H:%M}  from {n.soc:5.1f}%  "
                f"predicted {n.predicted_lowest:5.1f}%  actual {n.actual_lowest:5.1f}%  "
                f"error {n.error:+5.1f}"
                + (
                    f"  reserve {n.predicted_reserve_at.astimezone(zone):%H:%M}"
                    f" vs {n.actual_reserve_at.astimezone(zone):%H:%M}"
                    if n.reserve_minutes is not None
                    else ""
                )
                + mark
            )
    if graded:
        errors = [n.error for n in graded]
        absolute = sorted(abs(e) for e in errors)
        calls = sum(n.predicted_reserve == n.actual_reserve for n in graded)
        mean_error = sum(errors) / len(errors)
        mean_absolute = sum(absolute) / len(absolute)
        print(f"lowest      mean error {mean_error:+.1f} points (positive = optimistic)")
        print(f"            mean absolute {mean_absolute:.1f}, worst {absolute[-1]:.1f}")
        print(f"reserve     called right on {calls} of {len(graded)} nights")
        timed = sorted(n.reserve_minutes for n in graded if n.reserve_minutes is not None)
        if timed:
            mean_abs = sum(abs(m) for m in timed) / len(timed)
            print(
                f"reserve at  median error {timed[len(timed) // 2]:+.0f} min, mean absolute "
                f"{mean_abs:.0f} min over {len(timed)} (negative = predicted early)"
            )
        # Nights, not projections: three projections of one night are one piece
        # of evidence about it, and counting them three times would let a week
        # pass a gate meant to need a fortnight.
        distinct = len({n.start.astimezone(zone).date() for n in graded})
        print(f"distinct    {distinct} nights")
        gate = distinct >= 14 and mean_absolute <= 5.0 and calls / len(graded) >= 0.85
        verdict = "PASS" if gate else "NOT MET"
        print(f"gate        {verdict} (>= 14 nights, mean |error| <= 5, calls >= 85%)")

    if forecast:
        morning_skipped: dict[str, int] = {}
        mornings = replay_mornings(
            buckets,
            soc,
            forecast,
            battery=battery,
            zone=zone,
            morning=args.morning,
            skipped=morning_skipped,
        )
        both = [(p, a) for _, p, a in mornings if p and a]
        print()
        print(
            f"mornings    {len(mornings)} projected at {args.morning}:00, skipped {morning_skipped}"
        )
        if args.verbose:
            for now, p, a in mornings:
                shown_p = f"{p.astimezone(zone):%H:%M}" if p else "  -  "
                shown_a = f"{a.astimezone(zone):%H:%M}" if a else "  -  "
                print(f"  {now.astimezone(zone):%m-%d}  full predicted {shown_p}  actual {shown_a}")
        if both:
            minutes = sorted((p - a).total_seconds() / 60 for p, a in both)
            median = minutes[len(minutes) // 2]
            print(f"full at     median error {median:+.0f} min over {len(both)} days")
        missed = sum(1 for _, p, a in mornings if p and not a)
        unforeseen = sum(1 for _, p, a in mornings if a and not p)
        print(f"            predicted full, did not fill: {missed}")
        print(f"            filled, predicted not: {unforeseen}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
