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

#: The slot on the other side of the same flow.
_OPPOSITE_ROLE: dict[Role, Role] = {
    Role.GRID_IMPORT: Role.GRID_EXPORT,
    Role.GRID_EXPORT: Role.GRID_IMPORT,
    Role.BATTERY_CHARGE: Role.BATTERY_DISCHARGE,
    Role.BATTERY_DISCHARGE: Role.BATTERY_CHARGE,
}

#: Energy below which the opposite channel is not really carrying anything —
#: an empty slot, or one whose sensor reports a flat zero.
_OPPOSITE_MIN_WH = 100.0

#: How much of the house a channel must account for before its sign is worth
#: arguing about, as a share of the median in-balance channel.
#:
#: Relative rather than absolute, because a 3 kW flat and a 20 kW farm disagree
#: about what a large number is. Measured across the synthetic house: real
#: channels run from 46% to 235% of the median, while an idle export slot
#: drifting thirty watt-hours below zero once a day comes to 0.30% — nine times
#: below the smallest real channel, and sixteen times above the drift. Without
#: it, that unused sensor was told it was wired backwards.
_MATERIAL_SHARE = 0.05


def _channel_total(buckets: tuple[Bucket, ...], key: str) -> float:
    return sum(abs(v) for b in buckets if (v := b.wh.get(key)) is not None)


def _is_material(buckets: tuple[Bucket, ...], specs: tuple[ChannelSpec, ...], key: str) -> bool:
    """Whether this channel carries enough of the house to diagnose."""
    totals = [_channel_total(buckets, spec.key) for spec in specs if spec.role.in_balance]
    typical = median(totals)
    if typical is None or typical <= 0:
        return False
    return _channel_total(buckets, key) >= typical * _MATERIAL_SHARE


def _carries_energy(
    buckets: tuple[Bucket, ...], specs: tuple[ChannelSpec, ...], role: Role
) -> bool:
    """Whether any channel in this role reports material energy in the window."""
    for spec in specs:
        if spec.role is not role:
            continue
        total = sum(abs(v) for b in buckets if (v := b.wh.get(spec.key)) is not None)
        if total >= _OPPOSITE_MIN_WH:
            return True
    return False


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
        # The net-meter question only applies to the four roles with an opposite
        # slot; the backwards question applies to anything in the balance, PV
        # and load included. Neither of those can physically flow in reverse,
        # and an inverted one leaves a residual of a hundred per cent or more
        # that nothing else in the engine will ever name.
        code = _MAGNITUDE_ROLES.get(spec.role)
        if code is None and not spec.role.in_balance:
            continue

        values = _raw_series(tuple(ordered), spec.key)
        if len(values) < 48:
            continue

        negatives = [v for v in values if v <= -NEGATIVE_MIN_WH]
        if not negatives:
            continue
        if not _is_material(tuple(ordered), specs, spec.key):
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

        # Never positive at all is not a net meter — it is a sensor wired or
        # published backwards, and that has a different name and a one-click
        # fix. The inferential stage would reach the same verdict for most
        # channels, but not for one that is idle in more than three quarters of
        # hours: battery charging runs a few hours a day, so the upper-quartile
        # cutoff its gamma estimate needs is zero and no estimate is ever
        # produced. Without this the commonest battery mis-mapping there is
        # would go unnamed for good.
        if not any(v >= NEGATIVE_MIN_WH for v in values):
            hits.append(
                ScreenHit(
                    code=Code.CHANNEL_NEVER_POSITIVE,
                    channel_keys=(spec.key,),
                    confidence=Confidence.CERTAIN,
                    correction_kind="sign_flip",
                    fields={"name": spec.friendly_name},
                )
            )
            continue

        # A signed sensor alone in its pair is not a fault. Import carries +1
        # and export -1, so one channel reporting `import - export` contributes
        # exactly what the two would have contributed separately: the identity
        # closes to floating-point noise, and the setup screen tells the user to
        # configure it this way in as many words. Firing here reported a fault
        # on a house that had done exactly what it was asked, and pointed the
        # fix at a slot that does not exist.
        #
        # What is a fault is the same energy arriving twice — a signed sensor in
        # one slot while the other slot is also carrying. Then the negatives
        # duplicate what the opposite channel already reports.
        opposite = _OPPOSITE_ROLE.get(spec.role)
        if code is None or opposite is None:
            continue
        if not _carries_energy(tuple(ordered), specs, opposite):
            continue

        hits.append(
            ScreenHit(
                code=code,
                channel_keys=(spec.key,),
                confidence=Confidence.CERTAIN,
                # No internal override. The fix is to unmap one of the two
                # sensors, which is a configuration change we must not make on
                # somebody's behalf — and the override previously offered here,
                # "reinterpret_as_net", was implemented nowhere: accepting it
                # recorded a correction, counted it in `corrections_active`, and
                # changed not one number.
                correction_kind=None,
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


#: Hours a day must have been generating, historically, before their emptiness
#: today means anything.
#:
#: Taken from the installation's own median for that hour of the day rather than
#: from a sun position. A roof behind a hill is dark at nine whatever the
#: almanac says, and a sun-elevation rule would call every winter morning a
#: stall on exactly the systems where mornings are worth least.
STALL_MIN_TYPICAL_WH = 200.0

#: How many days of history before the median means anything.
#:
#: Below this the "typical" hour is a handful of days of one season's weather,
#: and a fortnight of cloud would teach it that noon produces nothing.
STALL_MIN_DAYS = 14

#: Consecutive stalled hours before this is reported.
#:
#: One empty hour is a cloud. The shortest thing worth telling somebody about is
#: a morning that never started, and requiring a run is what separates the two
#: without needing to model weather at all.
STALL_MIN_RUN_HOURS = 3

#: How much of its typical output an hour must fall below to count as stalled.
#:
#: Not zero. An inverter that has tripped still reports its own standby draw on
#: some installations, and a string that has gone offline on a two-string array
#: leaves the other one producing.
STALL_MAX_SHARE_OF_TYPICAL = 0.05


def screen_production_stalled(
    buckets: tuple[Bucket, ...], specs: tuple[ChannelSpec, ...]
) -> list[ScreenHit]:
    """Generation that stopped during hours this roof normally produces.

    Not a residual fault — the arithmetic can be perfect while this happens,
    because a tripped string is *correctly* reported as zero by a sensor that is
    working exactly as it should. Every other check in this package asks whether
    the numbers agree with each other. This one asks whether the roof is doing
    anything, which is the question its owner actually has.

    Four conditions, and each removes a way of being wrong:

    *A typical hour.* The daylight predicate comes from the installation's own
    median production for that hour of the day, not from a sun position. A roof
    behind a hill is dark at nine whatever the almanac says.

    *A run.* One empty hour is a cloud. Three consecutive is a morning that
    never started.

    *The rest of the system alive.* If nothing else reported either, the house
    was not being watched and generation is not what stopped. That is the
    difference between a fault and an outage, and reporting the second as the
    first is how somebody spends an afternoon on the roof for nothing.

    *A reading that exists.* An hour whose generation is ``MISSING`` says
    nothing about production. ``value`` returns ``None`` for those and they are
    skipped rather than counted as zero — the same distinction the whole engine
    is built on.
    """
    pv_keys = [spec.key for spec in specs if spec.role is Role.PV]
    if not pv_keys:
        return []

    # Counted off `start_utc`, not `local_date`. Screens run on the raw request
    # and `local_date` is filled in later by `build_days` — reading it here gave
    # an empty set and a screen that could never fire, which is how the first
    # version of this passed every refusal test and none of the firing ones.
    #
    # A UTC day rather than a local one is the right resolution for the question
    # being asked, which is only "is there enough history for a median to mean
    # anything".
    days = {bucket.start_utc.date() for bucket in buckets}
    if len(days) < STALL_MIN_DAYS:
        return []

    by_hour: dict[int, list[float]] = {}
    for bucket in buckets:
        generated = _role_sum(bucket, pv_keys)
        if generated is not None:
            by_hour.setdefault(bucket.start_utc.hour, []).append(generated)

    typical = {
        hour: value
        for hour, values in by_hour.items()
        if (value := median(values)) is not None and value >= STALL_MIN_TYPICAL_WH
    }
    if not typical:
        return []

    run = 0
    longest = 0
    stalled_hours = 0
    for bucket in sorted(buckets, key=lambda b: b.start_utc):
        expected = typical.get(bucket.start_utc.hour)
        generated = _role_sum(bucket, pv_keys)
        if expected is None or generated is None or not _rest_alive(bucket, pv_keys):
            run = 0
            continue
        if generated <= expected * STALL_MAX_SHARE_OF_TYPICAL:
            run += 1
            stalled_hours += 1
            longest = max(longest, run)
        else:
            run = 0

    if longest < STALL_MIN_RUN_HOURS:
        return []

    return [
        ScreenHit(
            code=Code.PRODUCTION_STALLED,
            channel_keys=tuple(pv_keys),
            confidence=Confidence.HIGH,
            correction_kind=None,
            fields={
                "hours": float(longest),
                "count": float(stalled_hours),
                "days": float(len(days)),
            },
        )
    ]


def _role_sum(bucket: Bucket, keys: list[str]) -> float | None:
    """A role's total, or ``None`` if any part of it is unreadable."""
    parts = [value for key in keys if (value := bucket.value(key)) is not None]
    return sum(parts) if len(parts) == len(keys) else None


def _rest_alive(bucket: Bucket, pv_keys: list[str]) -> bool:
    """Whether anything other than generation reported movement this hour.

    A house where nothing at all moved was not being watched. Generation reading
    zero then says nothing about the roof, and saying it does is how somebody
    goes up a ladder because their broker was down.
    """
    return any(value not in (None, 0.0) for key, value in bucket.wh.items() if key not in pv_keys)


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
    # Last, and not short-circuiting. A stalled string is a fact about the roof
    # rather than about the data, so it must not pre-empt a mis-scaled channel —
    # and a channel wrong by a thousand makes every "typical hour" above wrong
    # by the same factor, which would produce this finding on a house whose only
    # problem is a unit.
    hits.extend(screen_production_stalled(buckets, specs))
    return hits
