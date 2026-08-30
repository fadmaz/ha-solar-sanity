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

from .faults import DC_MEASUREMENT_WINDOW, SNAP_TABLE, Code, Snap
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
from .topology import NIGHT_MAX_PV_WH

#: A channel needs this many large-magnitude hours before its gamma means much.
#: The unit is *upper-quartile* hours, not hours — ``estimate_gamma`` divides by
#: the channel's own magnitude, so small hours produce enormous ratios and are
#: excluded before the median is taken.
MIN_RATIO_SAMPLES = 40

#: ...which means roughly four times that many valid hours have to exist, since
#: the upper quartile is a quarter of them. Stated here because the two numbers
#: drifted apart once already: the engine let attribution start at five days
#: while this floor could not be met before seven, so every install spent two
#: days being told no explanation was convincing when none had been generated.
MIN_HOURS_FOR_SNAP = MIN_RATIO_SAMPLES * 4

#: ...which is this many days, at a full day of hours. Derived rather than
#: written down a second time. The two were written down separately once, drifted
#: to five and seven, and every installation then spent two days being told no
#: explanation was convincing when in fact none had been generated.
MIN_DAYS_FOR_SNAP = -(-MIN_HOURS_FOR_SNAP // 24)

#: Unmeasured export: how much of the squared residual must fall in hours where
#: generation exceeds consumption. Export cannot happen at any other time, so
#: this is the whole discrimination.
EXPORT_MIN_SURPLUS_SHARE = 0.85
#: ...and how quiet the remaining hours must be, relative to surplus hours.
EXPORT_MAX_DEFICIT_RATIO = 0.25
#: Below this many surplus hours there is nothing to conclude from.
EXPORT_MIN_SURPLUS_HOURS = 40

#: ...and how quiet the *lit* deficit hours must be, which is a sharper question
#: than the one above and the reason this constant exists.
#:
#: The deficit bucket is mostly night, and night is silent under every
#: explanation, so averaging over all of it dilutes the one comparison that
#: discriminates. An hour with the sun up and consumption still ahead of
#: generation is the hour that separates the two stories: nothing can be
#: exported in it, so unmeasured export claims *exactly zero* there — while a
#: loss proportional to generation is present in every generating hour, because
#: that is what proportional to generation means.
#:
#: Measured. A house that genuinely exports unmeasured sits at 0.0000 to 0.0033,
#: and that upper end is with a DC-metered inverter at 0.80 stacked on top of
#: the real export — both stories true at once, which is the case this must not
#: suppress. A house that never exports a single watt-hour, carrying only a DC
#: metering loss the window refused to absorb, sits at 0.0338 to 0.2170. Ten
#: times apart at their closest, and this sits between them.
EXPORT_MAX_LIT_DEFICIT_RATIO = 0.01

#: Below this many lit deficit hours the discrimination above cannot be made at
#: all, and an accusation is withheld rather than made without it.
EXPORT_MIN_LIT_DEFICIT_HOURS = 20

#: The largest share of generation a *metering* loss can account for.
#:
#: Being loud in the lit deficit hours is not on its own a reason to stay quiet,
#: because two very different things are loud there. A generation sensor reading
#: high by a conversion loss contributes a small fraction of generation — a
#: quarter at the very worst, since an inverter below 75% efficient is not a
#: product anybody sells. A roof whose entire output is exported unmeasured
#: contributes *all* of it. Measured: 0.15 to 0.25 for DC metering at 0.85 down
#: to 0.75, against 0.99 to 1.00 for a rented roof serving none of its own load.
#:
#: So the veto asks for both — loud in hours export cannot reach, *and*
#: proportional at a rate only a metering loss could produce. 0.35 sits far
#: above any real inverter and far below a roof that exports everything.
#:
#: This band is deliberately much wider than `DC_MEASUREMENT_WINDOW`, which
#: decides whether to *subtract* the loss. Subtracting changes the user's
#: numbers and needs confidence; declining to accuse them needs only doubt, and
#: it is the accusation that does the damage if it is wrong.
MAX_GENERATION_LOSS_COEFFICIENT = 0.35

#: Fraction of the residual a hypothesis must account for.
MIN_EXPLAINED = 0.80

#: How far ahead of the runner-up a categorical hypothesis must be...
MARGIN_CATEGORICAL = 0.15
#: ...and a free-parameter one, which overfits more easily.
MARGIN_FREE_PARAMETER = 0.25

#: The estimate must be stable across days, not merely right on average.
MAX_GAMMA_CV = 0.15

MIN_DAYS_SUPPORTING = 4

#: Days of evidence before a *structural* hypothesis may be named — a battery or
#: an export path nobody measures. These need shape, not per-channel arithmetic,
#: so they are answerable from far less data than the snap table.
MIN_DAYS_EVALUATED = 5

#: Storage-shape thresholds for a missing battery.
STORAGE_MAX_ASYMMETRY = 0.30
STORAGE_MAX_ORTHOGONAL_EXPLAINED = 0.50
STORAGE_FLAT_TOP_MAX_CV = 0.15
STORAGE_MIN_CAPACITY_WH = 1500.0
STORAGE_MAX_CAPACITY_WH = 60000.0

#: How far one day's trace may be from the fitted capacity and still be the
#: same battery. Wide, because a battery is not cycled fully every day — but
#: bounded, because a day that swings ten times the capacity is not this.
STORAGE_DAY_MIN_SHARE = 0.35
STORAGE_DAY_MAX_SHARE = 2.0

#: How far a day's trace may end from where it started, as a share of its own
#: swing. Energy that goes into a battery comes out again; a day that does not
#: close is something accumulating, and that is a different finding.
STORAGE_DAY_MAX_DRIFT = 0.35

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
        # Zero, not len(pairs). The third element is counted in upper-quartile
        # hours on the success path, and returning a different unit from the
        # failure paths made the caller's own sample check unreadable.
        return None, None, 0

    magnitudes = [abs(u) for u, _ in pairs]
    cutoff = percentile(magnitudes, 75)
    if cutoff is None or cutoff <= 0:
        return None, None, 0

    ratios = [
        ratio for u, dr in pairs if abs(u) >= cutoff and (ratio := safe_ratio(dr, u)) is not None
    ]
    if len(ratios) < 3:
        return None, None, len(ratios)

    return median(ratios), iqr(ratios), len(ratios)


def _role_key(role: Role) -> str | None:
    spec = next((s for s in _SPEC_STUBS.values() if s.role is role), None)
    return spec.key if spec else None


def surplus_mask(day: DayResidual) -> tuple[bool, ...]:
    """Which hours of a day had generation exceeding consumption.

    Export can only leave the house in these hours. Any hypothesis about
    unmeasured export therefore claims nothing at all about the others, and
    that restriction is what stops it explaining an arbitrary residual.
    """
    pv_key = _role_key(Role.PV)
    load_key = _role_key(Role.LOAD)
    if pv_key is None or load_key is None:
        return tuple(False for _ in day.buckets)

    out: list[bool] = []
    for bucket in day.buckets:
        generation = bucket.value(pv_key)
        consumption = bucket.value(load_key)
        out.append(generation is not None and consumption is not None and generation > consumption)
    return tuple(out)


def looks_like_storage(day: DayResidual, capacity_wh: float) -> bool:
    """Whether one day's residual traces a battery charging and discharging.

    Two properties, both required. The trace has to swing about as far as the
    fitted capacity — a system does not have a different battery on Tuesday. And
    it has to come back: energy that goes into a battery comes out again, so a
    day ending far from where it started is not storage, it is something
    accumulating, which is a different and worse finding.
    """
    soc = virtual_soc(day)
    if not soc:
        return False

    span = max(soc) - min(soc)
    if span <= 0:
        return False
    if not (capacity_wh * STORAGE_DAY_MIN_SHARE <= span <= capacity_wh * STORAGE_DAY_MAX_SHARE):
        return False

    return abs(soc[-1]) <= span * STORAGE_DAY_MAX_DRIFT


def _per_day_gamma(days: tuple[DayResidual, ...], spec: ChannelSpec) -> list[float]:
    out: list[float] = []
    for day in days:
        gamma, _, _ = estimate_gamma((day,), spec)
        if gamma is not None:
            out.append(gamma)
    return out


def residual_after(days: tuple[DayResidual, ...], hyp: Hypothesis) -> list[float]:
    """The residual that would remain if this hypothesis were true and corrected."""
    if hyp.code == Code.MISSING_EXPORT:
        # Unmeasured export absorbs the residual in surplus hours and claims
        # nothing about the rest. Modelling it as "explains everything" would
        # make it fit any residual at all and it would win every time.
        out: list[float] = []
        for day in days:
            for surplus, value in zip(surplus_mask(day), day.dr, strict=True):
                out.append(0.0 if surplus and value > 0 else value)
        return out

    if hyp.code == Code.MISSING_STORAGE:
        # A battery nobody measures explains the days that trace one, and says
        # nothing about the rest. Without a model at all this hypothesis scored
        # zero explained on every input and could never clear the first gate, so
        # a user with an unmapped battery was told the numbers did not add up
        # and never told why.
        capacity = (hyp.extra or {}).get("capacity_wh")
        out: list[float] = []
        for day in days:
            explained = capacity is not None and looks_like_storage(day, capacity)
            out.extend(0.0 if explained else value for value in day.dr)
        return out

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
        out.extend(generate_structural(days, specs))

    return out


def generate_structural(
    days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...]
) -> list[Hypothesis]:
    """The probes that ask about an unmeasured path rather than a bad channel.

    Separate because they are reached differently. A miscounted channel makes
    the residual run one way, day after day, which is what the daily bands
    measure. An unmeasured battery does the opposite: it takes energy in the
    afternoon and gives it back at night, so the day's *net* residual is near
    zero by construction and no band will ever call it actionable. Gating these
    behind the bands made them unreachable on exactly the systems they describe.
    """
    _SIGNS.clear()
    for spec in specs:
        _SIGNS[spec.key] = spec.role.sign

    out: list[Hypothesis] = []
    storage = _storage_hypothesis(days, specs)
    if storage is not None:
        out.append(storage)
    export = _missing_export_hypothesis(days, specs)
    if export is not None:
        out.append(export)
    return out


def _missing_export_hypothesis(
    days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...]
) -> Hypothesis | None:
    """Is the unexplained energy leaving the house when there is a surplus?

    A house with no export sensor is not a rare misconfiguration — plenty of
    inverters expose import and not export, and the Energy Dashboard is happy
    without one. The residual it produces is large, one-signed and daily, which
    is exactly what an ordinary fault looks like from the outside. The one thing
    that separates them is *when*: export cannot happen while consumption
    exceeds generation, and a miscounted channel does not care what time it is.

    That is true, and it was not being asked carefully enough. A miscounted
    channel does not care what time it is, but a *generation-proportional* loss
    does — it is largest exactly when generation is largest, which is exactly
    when there is a surplus. So a DC-metered inverter whose loss the window
    declined to absorb produced a residual with the same daily shape as
    unmeasured export, and this said HIGH confidence about a house measured to
    export precisely zero watt-hours in a month.

    The hours that tell them apart are the lit ones with no surplus: the sun is
    up, consumption is still ahead of generation, and nothing can leave. Export
    claims nothing there by construction. A loss proportional to generation is
    present there in proportion to generation, because that is what it is.
    Night is silent under both stories, and night is most of the deficit bucket,
    which is why averaging over the whole of it hid the difference.
    """
    if any(spec.role is Role.GRID_EXPORT for spec in specs):
        return None

    surplus_sq = 0.0
    deficit_sq = 0.0
    lit_deficit_sq = 0.0
    lit_cross = 0.0
    lit_generation_sq = 0.0
    surplus_hours = 0
    deficit_hours = 0
    lit_deficit_hours = 0

    pv_key = _role_key(Role.PV)
    for day in days:
        for bucket, surplus, value in zip(day.buckets, surplus_mask(day), day.dr, strict=True):
            if surplus:
                surplus_hours += 1
                surplus_sq += value * value
                continue
            deficit_hours += 1
            deficit_sq += value * value
            generation = bucket.value(pv_key) if pv_key is not None else None
            if generation is not None and generation > NIGHT_MAX_PV_WH:
                lit_deficit_hours += 1
                lit_deficit_sq += value * value
                lit_cross += value * generation
                lit_generation_sq += generation * generation

    if surplus_hours < EXPORT_MIN_SURPLUS_HOURS or deficit_hours < EXPORT_MIN_SURPLUS_HOURS:
        return None

    total_sq = surplus_sq + deficit_sq
    if total_sq <= 0:
        return None
    if surplus_sq / total_sq < EXPORT_MIN_SURPLUS_SHARE:
        return None

    # Per-hour, not per-total: a day with three times as many deficit hours
    # would otherwise pass the share test on arithmetic alone.
    per_surplus = surplus_sq / surplus_hours
    per_deficit = deficit_sq / deficit_hours
    if per_surplus <= 0 or per_deficit / per_surplus > EXPORT_MAX_DEFICIT_RATIO:
        return None

    # The lit hours with no surplus, where export claims nothing and a
    # generation-proportional loss claims a great deal. Without enough of them
    # the two stories cannot be told apart, and this says nothing rather than
    # saying something confident it has no way to check.
    if lit_deficit_hours < EXPORT_MIN_LIT_DEFICIT_HOURS:
        return None

    if lit_deficit_sq / lit_deficit_hours / per_surplus > EXPORT_MAX_LIT_DEFICIT_RATIO:
        # Loud where export cannot reach. That alone is not grounds for silence:
        # a rented roof exporting its whole output is loud there too, and
        # telling its owner nothing leaves the field to "generation is counted
        # twice", whose remedy is to delete the one sensor that was telling the
        # truth. What separates them is the rate.
        loss = lit_cross / lit_generation_sq if lit_generation_sq > 0 else 0.0
        if DC_MEASUREMENT_WINDOW[0] <= loss <= MAX_GENERATION_LOSS_COEFFICIENT:
            return None

    # Energy going out unmeasured makes the residual run positive. The other
    # sign is a different problem entirely and must not borrow this copy.
    asymmetries = [a for day in days if (a := day.asymmetry) is not None]
    if not asymmetries:
        return None
    if median(asymmetries) is None or median(asymmetries) < ONE_SIDED_ASYMMETRY:
        return None

    return Hypothesis(
        code=Code.MISSING_EXPORT,
        channel_keys=(),
        a=None,
        gamma=None,
        gamma_iqr=None,
        confidence=Confidence.HIGH,
        correction_kind=None,
        has_free_parameter=True,
    )


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
        # Named for the template that renders it. It carried "daily_kwh" while
        # the copy asked for "daily", which raised the moment this hypothesis
        # won — and it could not win, so nothing ever found out.
        extra={"capacity_wh": capacity, "daily": capacity / 1000.0},
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


#: Names for the gates, so a caller can ask *which* one closed rather than only
#: whether one did. Nothing else in the engine should be re-deriving them.
GATE_EXPLAINED = "explained"
GATE_MARGIN = "margin"
GATE_DAYS_SUPPORTING = "days_supporting"
GATE_DAYS_EVALUATED = "days_evaluated"
GATE_GAMMA_CV = "gamma_cv"


def gate_failures(hyp: Hypothesis, days_evaluated: int) -> frozenset[str]:
    """Which gates this hypothesis fails, by name.

    Split out from ``passes_gates`` because one caller needs to know that the
    *only* thing standing in the way is the margin — a hypothesis that is right
    about everything except being distinguishable from its runner-up. Deriving
    that anywhere else would mean a second copy of the gate logic, and a second
    copy is how the two come to disagree.
    """
    failed: set[str] = set()
    # Written the way round that rejects a NaN rather than waving it through: a
    # single non-finite reading makes `explained` NaN, and `NaN < 0.8` is false.
    if not hyp.explained >= MIN_EXPLAINED:
        failed.add(GATE_EXPLAINED)
    if not hyp.margin >= hyp.required_margin:
        failed.add(GATE_MARGIN)
    if hyp.days_supporting < MIN_DAYS_SUPPORTING:
        failed.add(GATE_DAYS_SUPPORTING)
    if days_evaluated < days_needed(hyp):
        failed.add(GATE_DAYS_EVALUATED)
    if hyp.gamma_cv is not None and not hyp.gamma_cv <= MAX_GAMMA_CV:
        failed.add(GATE_GAMMA_CV)
    return frozenset(failed)


def passes_gates(hyp: Hypothesis, days_evaluated: int) -> bool:
    """All seven gates. Failing any one means silence.

    The day floor is not one number. A snap-table hypothesis rests on a gamma
    estimated from the upper quartile of hours, so it needs a week; a structural
    one rests on the shape of the residual and is answerable from five days.
    Holding both to the longer floor would delay the findings that are ready,
    and holding both to the shorter one would claim a floor the arithmetic
    cannot meet.

    The NaN handling lives in ``gate_failures``, which this defers to so the two
    cannot drift.
    """
    return not gate_failures(hyp, days_evaluated)


def days_needed(hyp: Hypothesis) -> int:
    """Days of evidence this kind of hypothesis needs before it may be named."""
    return MIN_DAYS_FOR_SNAP if hyp.channel_keys else MIN_DAYS_EVALUATED
