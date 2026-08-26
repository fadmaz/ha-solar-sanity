"""Turning buckets into a residual, and deciding whether that residual matters.

The identity, per hour, in Wh:

    R = (pv + grid_import + battery_discharge)
      - (load + grid_export + battery_charge)

A perfect zero is *wrong*. Real systems lose energy — inverter conversion is
95-97% efficient, battery round-trip 85-95% — and on a DC-coupled hybrid those
losses fall inside the residual rather than outside it. So we fit what loss to
expect and subtract it before testing anything.

What makes this tractable is that the noise floor and the fault floor do not
overlap:

    meter accuracy, four channels in quadrature      4-5%
    unmodelled loss after fitting                    1-2%
    --- nothing lives here ---                       6-50%
    half-coverage CT                                 50%
    double counting                                  100%
    sign inversion                                   200%
    unit error                                       99900%

A threshold at 10-15% of throughput sits nowhere near either edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .linalg import median, safe_ratio
from .model import (
    Bucket,
    BucketSource,
    ChannelSpec,
    LossModel,
    Quality,
    Role,
)

#: Below this fraction of throughput we say nothing at all, ever.
CLEAN_HOURLY_PCT = 0.06
CLEAN_DAILY_PCT = 0.04

#: Above this we are permitted to start testing hypotheses. Between the two is
#: "watch": accumulate evidence, stay silent.
ACTIONABLE_HOURLY_PCT = 0.12
ACTIONABLE_DAILY_PCT = 0.10

#: Absolute floors. Without these a dull December day with 3 kWh of throughput
#: manufactures a 25% residual out of nothing.
CLEAN_HOURLY_FLOOR_WH = 60.0
CLEAN_DAILY_FLOOR_WH = 300.0
ACTIONABLE_HOURLY_FLOOR_WH = 150.0
ACTIONABLE_DAILY_FLOOR_WH = 800.0

#: Buckets derived from an arithmetic hourly mean over an event-reporting sensor
#: are biased, so their tolerance is widened and they cannot support certainty.
MEAN_SOURCE_TOLERANCE_FACTOR = 1.6

#: A day needs this many valid hours to be usable at all (>= 83% coverage).
MIN_VALID_BUCKETS_PER_DAY = 20

#: Below this there is not enough signal to attribute anything.
MIN_SIGNAL_WH = 3000.0


@dataclass(frozen=True, slots=True)
class DayResidual:
    """One day's worth of residual, already loss-corrected."""

    day: date
    buckets: tuple[Bucket, ...]
    r: tuple[float, ...]
    expected: tuple[float, ...]
    dr: tuple[float, ...]
    throughput: tuple[float, ...]
    band: str
    from_mean: bool

    @property
    def net(self) -> float:
        """Signed daily total. Near zero means an alternating (storage-like) shape."""
        return sum(self.dr)

    @property
    def gross(self) -> float:
        """Unsigned daily total — how much energy is in play at all."""
        return sum(abs(v) for v in self.dr)

    @property
    def total_throughput(self) -> float:
        return sum(self.throughput)

    @property
    def asymmetry(self) -> float | None:
        """``net / gross``, in [-1, 1].

        Near +/-1 means one-signed: something is consistently over- or
        under-counted. Near 0 means it alternates, which is what storage looks
        like. This single number does most of the disambiguation work.
        """
        return safe_ratio(self.net, self.gross)


def balance_keys(specs: tuple[ChannelSpec, ...]) -> tuple[str, ...]:
    """Channel keys that take part in the identity, in a stable order."""
    return tuple(s.key for s in specs if s.role.in_balance)


def bucket_is_valid(bucket: Bucket, keys: tuple[str, ...]) -> bool:
    """A bucket counts only if *every* balance channel has a trustworthy value.

    No imputation. A missing channel does not become zero; the hour is simply
    not used.
    """
    return all(bucket.value(k) is not None for k in keys)


def signed_sum(bucket: Bucket, specs: tuple[ChannelSpec, ...]) -> float:
    """Sources minus sinks, in Wh."""
    total = 0.0
    for spec in specs:
        if not spec.role.in_balance:
            continue
        value = bucket.value(spec.key)
        if value is None:
            continue
        total += spec.role.sign * value
    return total


def throughput(bucket: Bucket, specs: tuple[ChannelSpec, ...]) -> float:
    """Energy actually crossing the node this hour.

    The larger of the source side and the sink side. Normalising against this
    rather than against any single channel means a residual means the same
    thing on a 3 kWp system and a 20 kWp one.
    """
    sources = 0.0
    sinks = 0.0
    for spec in specs:
        if not spec.role.in_balance:
            continue
        value = bucket.value(spec.key)
        if value is None:
            continue
        if spec.role.sign > 0:
            sources += value
        else:
            sinks += value
    return max(sources, sinks)


def expected_loss(bucket: Bucket, specs: tuple[ChannelSpec, ...], loss: LossModel) -> float:
    """What residual a *correctly configured* system should show this hour.

    Three terms: conversion loss on generation measured before the inverter,
    round-trip loss on a battery measured on its DC side, and a flat standby
    draw that nothing meters.
    """
    if not loss.fitted:
        return 0.0

    total = 0.0
    for spec in specs:
        value = bucket.value(spec.key)
        if value is None:
            continue
        if spec.role is Role.PV:
            total += loss.pv_dc_gamma * value
        elif spec.role in (Role.BATTERY_CHARGE, Role.BATTERY_DISCHARGE):
            total += loss.battery_dc_gamma * value

    total += loss.standby_w * (bucket.seconds / 3600.0)
    return total


def _tolerance(base_pct: float, floor_wh: float, tp: float, from_mean: bool) -> float:
    tol = max(base_pct * tp, floor_wh)
    return tol * MEAN_SOURCE_TOLERANCE_FACTOR if from_mean else tol


def classify_day(day: DayResidual) -> str:
    """Return ``clean``, ``watch`` or ``actionable`` for one day."""
    tp = day.total_throughput
    deviation = abs(day.net)

    clean = _tolerance(CLEAN_DAILY_PCT, CLEAN_DAILY_FLOOR_WH, tp, day.from_mean)
    if deviation <= clean:
        return "clean"

    actionable = _tolerance(ACTIONABLE_DAILY_PCT, ACTIONABLE_DAILY_FLOOR_WH, tp, day.from_mean)
    if deviation > actionable:
        return "actionable"

    return "watch"


def build_days(
    buckets: tuple[Bucket, ...],
    specs: tuple[ChannelSpec, ...],
    loss: LossModel,
    utc_offset_hours: float = 0.0,
) -> tuple[DayResidual, ...]:
    """Group buckets into local days and compute each day's residual.

    Days with a DST transition are dropped entirely rather than special-cased:
    a 23- or 25-hour day breaks the standby term and there are only two a year.
    """
    keys = balance_keys(specs)
    if not keys:
        return ()

    offset = timedelta(hours=utc_offset_hours)
    grouped: dict[date, list[Bucket]] = {}

    for bucket in buckets:
        if bucket.is_dst_transition or bucket.seconds != 3600:
            continue
        if not bucket_is_valid(bucket, keys):
            continue
        local_day = (bucket.start_utc + offset).date()
        grouped.setdefault(local_day, []).append(bucket)

    days: list[DayResidual] = []
    for day in sorted(grouped):
        day_buckets = sorted(grouped[day], key=lambda b: b.start_utc)
        if len(day_buckets) < MIN_VALID_BUCKETS_PER_DAY:
            continue

        r = tuple(signed_sum(b, specs) for b in day_buckets)
        expected = tuple(expected_loss(b, specs, loss) for b in day_buckets)
        dr = tuple(a - e for a, e in zip(r, expected, strict=True))
        tp = tuple(throughput(b, specs) for b in day_buckets)
        from_mean = any(
            src is BucketSource.LTS_MEAN for b in day_buckets for src in b.source.values()
        ) or any(q is Quality.DERIVED_FROM_MEAN for b in day_buckets for q in b.quality.values())

        residual = DayResidual(
            day=day,
            buckets=tuple(day_buckets),
            r=r,
            expected=expected,
            dr=dr,
            throughput=tp,
            band="",
            from_mean=from_mean,
        )
        days.append(
            DayResidual(
                day=residual.day,
                buckets=residual.buckets,
                r=residual.r,
                expected=residual.expected,
                dr=residual.dr,
                throughput=residual.throughput,
                band=classify_day(residual),
                from_mean=residual.from_mean,
            )
        )

    return tuple(days)


def median_daily_abs_pct(days: tuple[DayResidual, ...]) -> float | None:
    """Typical daily residual as a percentage of throughput.

    Diagnostics only — never a headline. "Your residual is 12%" is a
    non-statement to a user; either we can name the fault or we say nothing.
    """
    ratios = []
    for day in days:
        ratio = safe_ratio(abs(day.net), day.total_throughput)
        if ratio is not None:
            ratios.append(ratio * 100.0)
    return median(ratios)


def total_abs_residual(days: tuple[DayResidual, ...]) -> float:
    return sum(day.gross for day in days)


def virtual_soc(day: DayResidual) -> tuple[float, ...]:
    """Cumulative residual through the day, in Wh.

    If an unmeasured battery is present this traces its state of charge: it
    climbs while there is surplus, plateaus when the battery fills, and drains
    overnight. That shape is what distinguishes a missing storage channel from
    a channel that is merely miscounted.
    """
    running = 0.0
    out: list[float] = []
    for value in day.dr:
        running += value
        out.append(running)
    return tuple(out)
