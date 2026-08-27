"""Stage A: categorical, per-channel checks.

These are facts about a channel, not statistical inferences from a residual — a
sensor either goes negative or it does not, is monotone or is not, exceeds a
physical bound or does not. That makes them fast (1-3 days rather than 5-14) and
near-certain.

They also run *first* and short-circuit everything else: a frozen or
wrong-by-1000 channel makes every residual meaningless, so there is no point
attributing anything until it is fixed.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from .faults import Code
from .linalg import median, percentile, safe_ratio
from .model import Bucket, ChannelSpec, Confidence, LiveSnapshot, Role

#: Plausible upper bounds for a residential installation, in W. A channel that
#: sits far outside these is not measuring what it claims to.
PLAUSIBLE_PEAK_W: dict[Role, tuple[float, float]] = {
    Role.PV: (200.0, 30000.0),
    Role.LOAD: (300.0, 30000.0),
    Role.GRID_IMPORT: (100.0, 60000.0),
    Role.GRID_EXPORT: (100.0, 60000.0),
    Role.BATTERY_CHARGE: (100.0, 30000.0),
    Role.BATTERY_DISCHARGE: (100.0, 30000.0),
}

#: How far off the quorum's order of magnitude counts as a scale error.
SCALE_FACTOR_LOW = 300.0
SCALE_FACTOR_HIGH = 3000.0

#: Channels must agree within this factor to form a quorum we can measure against.
QUORUM_SPREAD = 20.0

STUCK_MIN_HOURS = 6
STALE_MULTIPLE = 30.0

#: A channel is only "goes negative" if it does so substantially and repeatedly.
NEGATIVE_MIN_WH = 25.0
NEGATIVE_MIN_FRACTION = 0.005
NEGATIVE_MIN_DAYS = 3

#: Simultaneous opposing flow must be seen this often before we believe it.
SIMULTANEOUS_MIN_W = 200.0
SIMULTANEOUS_MIN_COUNT = 50

CUMULATIVE_MONOTONE_FRACTION = 0.95
CUMULATIVE_MIN_RATIO = 20.0


@dataclass(frozen=True, slots=True)
class ScreenHit:
    """A categorical finding, before it is rendered into user-facing copy."""

    code: str
    channel_keys: tuple[str, ...]
    confidence: Confidence
    correction_kind: str | None
    fields: dict[str, float | str]


def _series(buckets: tuple[Bucket, ...], key: str) -> list[float]:
    return [v for b in buckets if (v := b.value(key)) is not None]


def _raw_series(buckets: tuple[Bucket, ...], key: str) -> list[float]:
    """Values ignoring quality — needed to see negatives on an otherwise-OK channel."""
    return [v for b in buckets if (v := b.wh.get(key)) is not None]


def screen_stuck(buckets: tuple[Bucket, ...], specs: tuple[ChannelSpec, ...]) -> list[ScreenHit]:
    """A channel frozen at a non-zero value while others move.

    Three guards, all needed to avoid firing on healthy systems:

    * **The stuck value must be non-zero.** Generation reads exactly zero every
      night, export reads zero on a dull day, and a battery at rest reads zero
      for hours. A channel legitimately idle reads zero; a channel whose sensor
      has died reports its last real value forever.
    * **The run must be long** — longer than any plausible quiet spell.
    * **The channel must have varied earlier**, so we know it is capable of
      moving at all.
    """
    hits: list[ScreenHit] = []
    ordered = tuple(sorted(buckets, key=lambda b: b.start_utc))
    if len(ordered) < STUCK_MIN_HOURS * 2:
        return hits

    for spec in specs:
        values = _series(ordered, spec.key)
        if len(values) < STUCK_MIN_HOURS * 2:
            continue

        tail = values[-STUCK_MIN_HOURS:]
        if len({round(v, 6) for v in tail}) != 1:
            continue

        frozen_at = tail[0]
        if abs(frozen_at) < 1e-6:
            # Idle, not broken.
            continue

        earlier = values[:-STUCK_MIN_HOURS]
        if len({round(v, 6) for v in earlier}) < 3:
            # Never varied, so freezing tells us nothing new.
            continue

        others_moved = any(
            len({round(v, 6) for v in _series(ordered, other.key)[-STUCK_MIN_HOURS:]}) > 1
            for other in specs
            if other.key != spec.key
        )
        if not others_moved:
            continue

        hits.append(
            ScreenHit(
                code=Code.STUCK,
                channel_keys=(spec.key,),
                confidence=Confidence.CERTAIN,
                correction_kind=None,
                fields={
                    "name": spec.friendly_name,
                    "observed": frozen_at,
                    "hours": float(len(tail)),
                },
            )
        )
    return hits


def screen_unit_scale(
    buckets: tuple[Bucket, ...], specs: tuple[ChannelSpec, ...]
) -> list[ScreenHit]:
    """Odd-one-out on order of magnitude.

    A scale error preserves the *shape* of a series perfectly — the curve still
    looks like solar, the load still looks lumpy — which is exactly what
    distinguishes it from a dead or garbage sensor. So we compare peaks, not
    shapes, and require a quorum of channels that agree with each other before
    calling any one of them the outlier.
    """
    hits: list[ScreenHit] = []
    peaks: dict[str, float] = {}
    for spec in specs:
        values = _series(buckets, spec.key)
        if len(values) < 12:
            continue
        peak = percentile(values, 99)
        if peak is not None and peak > 0:
            peaks[spec.key] = peak

    if len(peaks) < 3:
        return hits

    reference = median(list(peaks.values()))
    if reference is None or reference <= 0:
        return hits

    quorum = [
        p for p in peaks.values() if reference / QUORUM_SPREAD <= p <= reference * QUORUM_SPREAD
    ]
    if len(quorum) < 2:
        return hits

    quorum_level = median(quorum)
    if quorum_level is None or quorum_level <= 0:
        return hits

    for spec in specs:
        peak = peaks.get(spec.key)
        if peak is None:
            continue
        ratio = safe_ratio(peak, quorum_level)
        if ratio is None:
            continue

        too_big = SCALE_FACTOR_LOW <= ratio <= SCALE_FACTOR_HIGH
        too_small = SCALE_FACTOR_LOW <= (1.0 / ratio) <= SCALE_FACTOR_HIGH
        if not (too_big or too_small):
            continue

        # Second, independent check: does the peak sit outside what is physically
        # plausible for this role? Both tests must agree.
        bounds = PLAUSIBLE_PEAK_W.get(spec.role)
        if bounds is not None and bounds[0] <= peak <= bounds[1]:
            continue

        hits.append(
            ScreenHit(
                code=Code.UNIT_SCALE_1000,
                channel_keys=(spec.key,),
                confidence=Confidence.CERTAIN,
                correction_kind="scale",
                fields={
                    "name": spec.friendly_name,
                    "observed": peak,
                    "expected": quorum_level,
                },
            )
        )
    return hits


def screen_cumulative(
    buckets: tuple[Bucket, ...], specs: tuple[ChannelSpec, ...]
) -> list[ScreenHit]:
    """A lifetime total mapped into a periodic slot.

    Three conjunctive tests: the series only ever increases, its magnitude
    dwarfs its own first difference, and that first difference is itself a
    plausible hourly figure. The third is what makes this certain rather than
    merely suspicious — we have proved the *correct* interpretation, not just
    rejected the wrong one.
    """
    hits: list[ScreenHit] = []
    ordered = sorted(buckets, key=lambda b: b.start_utc)

    for spec in specs:
        values = _series(tuple(ordered), spec.key)
        if len(values) < 24:
            continue

        pairs = list(pairwise(values))
        non_decreasing = sum(1 for a, b in pairs if b >= a - 1e-9)
        if safe_ratio(non_decreasing, len(pairs)) is None:
            continue
        if non_decreasing / len(pairs) < CUMULATIVE_MONOTONE_FRACTION:
            continue

        # Only *positive* increments describe the underlying rate. A solar
        # counter adds nothing between dusk and dawn, so more than half of all
        # hourly diffs are exactly zero and their median would be zero too —
        # which would silently disable this whole check.
        increments = [b - a for a, b in pairs if b > a]
        if len(increments) < 12:
            continue
        typical = median(increments)
        if typical is None or typical <= 0:
            continue

        # A lifetime total dwarfs its own increment by hundreds of days.
        level = median(values)
        if level is None:
            continue
        ratio = safe_ratio(level, typical)
        if ratio is None or ratio < CUMULATIVE_MIN_RATIO:
            continue

        # Third test, and the one that makes this certain rather than merely
        # suspicious: the implied daily figure must itself be plausible. That
        # proves the correct interpretation, not just that the current one is wrong.
        implied_daily_wh = typical * len(increments) / max(1, len(pairs) / 24)
        if not (500.0 <= implied_daily_wh <= 200_000.0):
            continue

        hits.append(
            ScreenHit(
                code=Code.CUMULATIVE_IN_PERIODIC,
                channel_keys=(spec.key,),
                confidence=Confidence.CERTAIN,
                # No correction offered: the coordinator already differences
                # cumulative energy sensors, and a button that does nothing is
                # worse than no button on a product selling trustworthiness.
                correction_kind=None,
                fields={
                    "name": spec.friendly_name,
                    "observed": level,
                    "daily": typical * 24.0 / 1000.0,
                },
            )
        )
    return hits


#: One-directional roles whose values must be magnitudes, and the copy that
#: applies when they are not. PV and LOAD are deliberately absent: a generation
#: sensor reading slightly below zero overnight is an offset, not a net meter,
#: and there is no net slot to redirect either of them to — so naming them here
#: would buy a false-positive surface and offer nothing to do about it.
_MAGNITUDE_ROLES: dict[Role, str] = {
    Role.GRID_IMPORT: Code.SIGNED_NET_IN_DEDICATED,
    Role.GRID_EXPORT: Code.SIGNED_NET_IN_DEDICATED,
    Role.BATTERY_CHARGE: Code.SIGNED_NET_BATTERY,
    Role.BATTERY_DISCHARGE: Code.SIGNED_NET_BATTERY,
}


def screen_signed_net(
    buckets: tuple[Bucket, ...], specs: tuple[ChannelSpec, ...]
) -> list[ScreenHit]:
    """A net meter mapped into a one-way slot.

    Requires sustained, repeated negatives — not a single noisy sample, and not
    a CT drifting a few watts below zero at 3am.

    This is decidable from a couple of days of ordinary hours: no statistics, no
    gamma, no waiting for the residual to stabilise. It used to cover the grid
    roles only, which left the case it was written for — a battery published as
    one signed figure and mapped to the charging slot — to fall through to the
    inferential stage, where it cannot be named because the sign has already
    been absorbed into the arithmetic.
    """
    hits: list[ScreenHit] = []
    ordered = sorted(buckets, key=lambda b: b.start_utc)

    for spec in specs:
        code = _MAGNITUDE_ROLES.get(spec.role)
        if code is None:
            continue

        values = _raw_series(tuple(ordered), spec.key)
        if len(values) < 48:
            continue

        negatives = [v for v in values if v <= -NEGATIVE_MIN_WH]
        if not negatives:
            continue
        if len(negatives) / len(values) < NEGATIVE_MIN_FRACTION:
            continue

        days_with_negatives = {
            b.start_utc.date()
            for b in ordered
            if (v := b.wh.get(spec.key)) is not None and v <= -NEGATIVE_MIN_WH
        }
        if len(days_with_negatives) < NEGATIVE_MIN_DAYS:
            continue

        hits.append(
            ScreenHit(
                code=code,
                channel_keys=(spec.key,),
                confidence=Confidence.CERTAIN,
                # Only the grid has a net slot to be reinterpreted into. For a
                # battery the fix is a remap, and offering an internal override
                # that silently drops half the channel would be worse than
                # saying nothing.
                correction_kind=(
                    "reinterpret_as_net" if code == Code.SIGNED_NET_IN_DEDICATED else None
                ),
                fields={"name": spec.friendly_name},
            )
        )
    return hits


def screen_simultaneous_flow(
    snapshots: tuple[LiveSnapshot, ...], specs: tuple[ChannelSpec, ...]
) -> list[ScreenHit]:
    """Import and export — or charge and discharge — both flowing at once.

    Physically impossible at a single connection point, and invisible in hourly
    aggregates because it averages out inside the bucket. This is the entire
    reason the live tier exists.
    """
    hits: list[ScreenHit] = []
    pairs = (
        (Role.GRID_IMPORT, Role.GRID_EXPORT),
        (Role.BATTERY_CHARGE, Role.BATTERY_DISCHARGE),
    )

    for role_a, role_b in pairs:
        spec_a = next((s for s in specs if s.role is role_a), None)
        spec_b = next((s for s in specs if s.role is role_b), None)
        if spec_a is None or spec_b is None:
            continue

        count = 0
        days: set[object] = set()
        for snap in snapshots:
            a = snap.watts.get(spec_a.key)
            b = snap.watts.get(spec_b.key)
            if a is None or b is None:
                continue
            if a > SIMULTANEOUS_MIN_W and b > SIMULTANEOUS_MIN_W:
                count += 1
                days.add(snap.taken_utc.date())

        if count >= SIMULTANEOUS_MIN_COUNT and len(days) >= 3:
            hits.append(
                ScreenHit(
                    code=Code.SIMULTANEOUS_FLOW,
                    channel_keys=(spec_a.key, spec_b.key),
                    confidence=Confidence.CERTAIN,
                    correction_kind=None,
                    fields={
                        "name": spec_a.friendly_name,
                        "other": spec_b.friendly_name,
                        "count": float(count),
                        "days": float(len(days)),
                    },
                )
            )
    return hits


def run_all(
    buckets: tuple[Bucket, ...],
    specs: tuple[ChannelSpec, ...],
    snapshots: tuple[LiveSnapshot, ...],
) -> list[ScreenHit]:
    """Every Stage A check, in precedence order.

    Cumulative runs before stuck, and that ordering is load-bearing. A lifetime
    total stops increasing overnight — generation adds nothing between dusk and
    dawn — so a running counter looks frozen for six hours every single day.
    "Stuck" would be a true observation and the wrong diagnosis; the specific
    explanation must win over the general one.

    Beyond that, a genuinely frozen channel short-circuits everything else,
    because no residual computed against a dead sensor means anything.
    """
    cumulative = screen_cumulative(buckets, specs)
    if cumulative:
        return cumulative

    stuck = screen_stuck(buckets, specs)
    if stuck:
        return stuck

    # Signed before unit-scale, and short-circuiting, for the same reason:
    # screen_unit_scale measures a channel against the others' order of
    # magnitude, and a channel whose values cancel around zero reads as a
    # thousandfold error. That finding ships with a one-click correction that
    # would multiply the channel by 1000 — a wrong answer with a destructive
    # button attached is worse than a slower right one.
    signed = screen_signed_net(buckets, specs)
    if signed:
        return signed

    hits: list[ScreenHit] = []
    hits.extend(screen_unit_scale(buckets, specs))
    hits.extend(screen_simultaneous_flow(snapshots, specs))
    return hits
