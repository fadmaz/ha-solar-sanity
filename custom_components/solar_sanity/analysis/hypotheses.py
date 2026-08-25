"""Estimating gamma per channel, and deciding whether any estimate is a fault.

Generation is permissive and scoring is strict. We would rather produce ten
candidate explanations and reject all of them than name one we are not sure of.

The gates are conjunctive and there are seven of them. Every one has to pass.
The target is fewer than one false finding per two hundred installations per
year, and that budget is what dictates the strictness — a single wrong "your
sensor is broken" on a healthy system loses the user permanently.
"""

from __future__ import annotations

from dataclasses import dataclass

from .faults import SNAP_TABLE, Code, Snap
from .linalg import (
    coefficient_of_variation,
    iqr,
    median,
    percentile,
    safe_ratio,
    sum_squares,
)
from .model import ChannelSpec, Confidence, Role
from .residual import DayResidual, virtual_soc

#: A channel needs this many large-magnitude hours before its gamma means much.
MIN_RATIO_SAMPLES = 40

#: Fraction of the residual a hypothesis must account for.
MIN_EXPLAINED = 0.80

#: How far ahead of the runner-up a categorical hypothesis must be...
MARGIN_CATEGORICAL = 0.15
#: ...and a free-parameter one, which overfits more easily.
MARGIN_FREE_PARAMETER = 0.25

#: The estimate must be stable across days, not merely right on average.
MAX_GAMMA_CV = 0.15

MIN_DAYS_SUPPORTING = 4
MIN_DAYS_EVALUATED = 5

#: Storage-shape thresholds for a missing battery.
STORAGE_MAX_ASYMMETRY = 0.30
STORAGE_MAX_ORTHOGONAL_EXPLAINED = 0.50
STORAGE_FLAT_TOP_MAX_CV = 0.15
STORAGE_MIN_CAPACITY_WH = 1500.0
STORAGE_MAX_CAPACITY_WH = 60000.0

#: One-signed threshold: above this the residual is consistently in one direction.
ONE_SIDED_ASYMMETRY = 0.85


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """One candidate explanation for the residual."""

    code: str
    channel_keys: tuple[str, ...]
    a: float | None
    gamma: float | None
    gamma_iqr: float | None
    confidence: Confidence
    correction_kind: str | None
    has_free_parameter: bool
    explained: float = 0.0
    margin: float = 0.0
    days_supporting: int = 0
    gamma_cv: float | None = None
    extra: dict[str, float] | None = None

    @property
    def required_margin(self) -> float:
        return MARGIN_FREE_PARAMETER if self.has_free_parameter else MARGIN_CATEGORICAL


def _channel_units(days: tuple[DayResidual, ...], spec: ChannelSpec) -> list[tuple[float, float]]:
    """Return ``(u, dr)`` pairs where ``u = sign * value`` for one channel."""
    pairs: list[tuple[float, float]] = []
    for day in days:
        for bucket, dr in zip(day.buckets, day.dr, strict=True):
            value = bucket.value(spec.key)
            if value is None:
                continue
            pairs.append((spec.role.sign * value, dr))
    return pairs


def estimate_gamma(
    days: tuple[DayResidual, ...], spec: ChannelSpec
) -> tuple[float | None, float | None, int]:
    """Robust gamma for one channel: the median of ``dr / u`` over large hours.

    Restricting to the upper quartile of magnitude matters — dividing by a tiny
    ``u`` produces enormous ratios that swamp the median with noise.
    """
    pairs = _channel_units(days, spec)
    if len(pairs) < MIN_RATIO_SAMPLES:
        return None, None, len(pairs)

    magnitudes = [abs(u) for u, _ in pairs]
    cutoff = percentile(magnitudes, 75)
    if cutoff is None or cutoff <= 0:
        return None, None, len(pairs)

    ratios = [
        ratio for u, dr in pairs if abs(u) >= cutoff and (ratio := safe_ratio(dr, u)) is not None
    ]
    if len(ratios) < 3:
        return None, None, len(ratios)

    return median(ratios), iqr(ratios), len(ratios)


def _per_day_gamma(days: tuple[DayResidual, ...], spec: ChannelSpec) -> list[float]:
    out: list[float] = []
    for day in days:
        gamma, _, _ = estimate_gamma((day,), spec)
        if gamma is not None:
            out.append(gamma)
    return out


def residual_after(days: tuple[DayResidual, ...], hyp: Hypothesis) -> list[float]:
    """The residual that would remain if this hypothesis were true and corrected."""
    if hyp.a is None or not hyp.channel_keys:
        return [v for day in days for v in day.dr]

    key = hyp.channel_keys[0]
    out: list[float] = []
    for day in days:
        for bucket, dr in zip(day.buckets, day.dr, strict=True):
            value = bucket.value(key)
            if value is None:
                out.append(dr)
                continue
            # gamma = 1 - a, and the residual contribution is gamma * u.
            # Correcting the channel removes exactly that contribution.
            sign = _sign_for(days, key)
            u = sign * value
            out.append(dr - (1.0 - hyp.a) * u)
    return out


_SIGN_CACHE_KEY = "__sign__"


def _sign_for(days: tuple[DayResidual, ...], key: str) -> int:
    """Recover a channel's sign without threading specs everywhere."""
    del days
    return _SIGNS.get(key, 1)


#: Populated by :func:`generate` so ``residual_after`` can stay cheap.
_SIGNS: dict[str, int] = {}


def _wins_on_day(day: DayResidual, hyp: Hypothesis) -> bool:
    before = sum_squares(day.dr)
    after = sum_squares(residual_after((day,), hyp))
    return after < before * 0.5


def generate(
    days: tuple[DayResidual, ...],
    specs: tuple[ChannelSpec, ...],
    closure_open: bool,
) -> list[Hypothesis]:
    """Produce every candidate worth scoring. Permissive by design."""
    _SIGNS.clear()
    for spec in specs:
        _SIGNS[spec.key] = spec.role.sign

    out: list[Hypothesis] = []

    for spec in specs:
        if not spec.role.in_balance:
            continue
        gamma, gamma_iqr, samples = estimate_gamma(days, spec)
        if gamma is None or gamma_iqr is None or samples < MIN_RATIO_SAMPLES:
            continue
        for snap in SNAP_TABLE:
            if not _snap_applies(snap, spec, gamma, gamma_iqr):
                continue
            out.append(
                Hypothesis(
                    code=snap.code,
                    channel_keys=(spec.key,),
                    a=snap.a,
                    gamma=gamma,
                    gamma_iqr=gamma_iqr,
                    confidence=snap.confidence,
                    correction_kind=snap.correction_kind,
                    has_free_parameter=False,
                )
            )

    if closure_open:
        storage = _storage_hypothesis(days, specs)
        if storage is not None:
            out.append(storage)

    return out


def _snap_applies(snap: Snap, spec: ChannelSpec, gamma: float, gamma_iqr: float) -> bool:
    if not (snap.low <= gamma <= snap.high):
        return False
    if gamma_iqr > snap.max_iqr:
        return False
    return not (
        snap.bidirectional_only
        and spec.role
        not in (
            Role.BATTERY_CHARGE,
            Role.BATTERY_DISCHARGE,
            Role.GRID_IMPORT,
            Role.GRID_EXPORT,
        )
    )


def _storage_hypothesis(
    days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...]
) -> Hypothesis | None:
    """Is the unexplained energy shaped like a battery nobody measures?

    Four tests, all required. Storage returns what it takes (low asymmetry), is
    not explained by anything configured (orthogonality), stops at the same
    level on every sunny day (flat top), and is a plausible size.
    """
    asymmetries = [a for day in days if (a := day.asymmetry) is not None]
    if not asymmetries:
        return None
    if any(abs(a) > STORAGE_MAX_ASYMMETRY for a in asymmetries):
        return None

    peaks: list[float] = []
    ranges: list[float] = []
    for day in days:
        soc = virtual_soc(day)
        if not soc:
            continue
        peaks.append(max(soc))
        ranges.append(max(soc) - min(soc))

    if len(peaks) < 3:
        return None

    peak_cv = coefficient_of_variation(peaks)
    if peak_cv is None or peak_cv > STORAGE_FLAT_TOP_MAX_CV:
        return None

    capacity = median(ranges)
    if capacity is None:
        return None
    if not (STORAGE_MIN_CAPACITY_WH <= capacity <= STORAGE_MAX_CAPACITY_WH):
        return None

    if any(s.role in (Role.BATTERY_CHARGE, Role.BATTERY_DISCHARGE) for s in specs):
        # A battery *is* configured, so an alternating residual is more likely a
        # fault on that channel than a missing one. Let the snap table win.
        return None

    return Hypothesis(
        code=Code.MISSING_STORAGE,
        channel_keys=(),
        a=None,
        gamma=None,
        gamma_iqr=None,
        confidence=Confidence.HIGH,
        correction_kind=None,
        has_free_parameter=True,
        extra={"capacity_wh": capacity, "daily_kwh": capacity / 1000.0},
    )


def score(days: tuple[DayResidual, ...], candidates: list[Hypothesis]) -> list[Hypothesis]:
    """Attach explained fraction, stability and per-day support to each candidate."""
    baseline = sum_squares([v for day in days for v in day.dr])
    if baseline <= 0:
        return []

    scored: list[Hypothesis] = []
    for hyp in candidates:
        after = sum_squares(residual_after(days, hyp))
        explained = 1.0 - (after / baseline)
        supporting = sum(1 for day in days if _wins_on_day(day, hyp))

        cv: float | None = None
        if hyp.channel_keys:
            per_day = _per_day_gamma(days, _spec_stub(hyp.channel_keys[0]))
            if len(per_day) >= 2:
                cv = coefficient_of_variation(per_day)

        scored.append(
            Hypothesis(
                code=hyp.code,
                channel_keys=hyp.channel_keys,
                a=hyp.a,
                gamma=hyp.gamma,
                gamma_iqr=hyp.gamma_iqr,
                confidence=hyp.confidence,
                correction_kind=hyp.correction_kind,
                has_free_parameter=hyp.has_free_parameter,
                explained=explained,
                days_supporting=supporting,
                gamma_cv=cv,
                extra=hyp.extra,
            )
        )

    scored.sort(key=lambda h: h.explained, reverse=True)
    for index, hyp in enumerate(scored):
        runner_up = scored[index + 1].explained if index + 1 < len(scored) else 0.0
        scored[index] = Hypothesis(
            code=hyp.code,
            channel_keys=hyp.channel_keys,
            a=hyp.a,
            gamma=hyp.gamma,
            gamma_iqr=hyp.gamma_iqr,
            confidence=hyp.confidence,
            correction_kind=hyp.correction_kind,
            has_free_parameter=hyp.has_free_parameter,
            explained=hyp.explained,
            margin=hyp.explained - runner_up,
            days_supporting=hyp.days_supporting,
            gamma_cv=hyp.gamma_cv,
            extra=hyp.extra,
        )
    return scored


_SPEC_STUBS: dict[str, ChannelSpec] = {}


def register_specs(specs: tuple[ChannelSpec, ...]) -> None:
    _SPEC_STUBS.clear()
    for spec in specs:
        _SPEC_STUBS[spec.key] = spec


def _spec_stub(key: str) -> ChannelSpec:
    return _SPEC_STUBS[key]


def passes_gates(hyp: Hypothesis, days_evaluated: int) -> bool:
    """All seven gates. Failing any one means silence."""
    if hyp.explained < MIN_EXPLAINED:
        return False
    if hyp.margin < hyp.required_margin:
        return False
    if hyp.days_supporting < MIN_DAYS_SUPPORTING:
        return False
    if days_evaluated < MIN_DAYS_EVALUATED:
        return False
    return not (hyp.gamma_cv is not None and hyp.gamma_cv > MAX_GAMMA_CV)
