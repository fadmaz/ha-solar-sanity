"""Working out the shape of the system, and whether the identity can close.

Principle: ask the user what the user certainly knows; infer what the user
certainly does not. People know whether they have a battery. They emphatically
do not know whether their PV sensor reads before or after the inverter.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .faults import DC_MEASUREMENT_MAX_IQR, DC_MEASUREMENT_WINDOW
from .linalg import median, theil_sen_intercept, theil_sen_slope
from .model import (
    Answer,
    Bucket,
    ChannelSpec,
    Coupling,
    DeclaredTopology,
    LossModel,
    Role,
    TopologyEstimate,
)
from .residual import DayResidual

#: Days of data before we will commit to a DC-vs-AC conclusion.
MIN_DAYS_FOR_COUPLING = 14

#: Generation at or below this counts as night, for fitting purposes.
NIGHT_MAX_PV_WH = 50.0

#: Hours needed before a night fit means anything.
MIN_STANDBY_SAMPLES = 200

#: What an inverter's own idle draw can plausibly be. Outside this the number
#: is not absorbed as loss — it is reported, because a continuous unmetered
#: draw larger than an inverter idles at is something the user should know
#: about rather than something we should quietly subtract.
STANDBY_PLAUSIBLE_W = (10.0, 120.0)

#: ...and how large it may be relative to what the house draws at night. An
#: inverter's own supply is a small fraction of the load it is serving. A
#: consumption sensor reading half looks exactly like a constant draw when the
#: night load is low, and without this the fit absorbs the fault as loss and
#: the engine goes quiet about it.
STANDBY_MAX_SHARE_OF_LOAD = 0.20


class Closure(Enum):
    """Whether every energy path across the house boundary is measured."""

    CLOSED = "closed"
    OPEN = "open"
    NOT_CHECKABLE = "not_checkable"


@dataclass(frozen=True, slots=True)
class ClosureResult:
    state: Closure
    reason: str = ""
    #: Open specifically because nothing measures energy leaving the house.
    #: Worth distinguishing: it is the one open boundary that can be worked
    #: around, because there are hours in which nothing *can* leave.
    unmeasured_export: bool = False


def check_closure(specs: tuple[ChannelSpec, ...], declared: DeclaredTopology) -> ClosureResult:
    """Can the identity say anything at all about this configuration?

    The load channel is the one that cannot be derived. With generation, grid
    and battery but no load, ``load`` is *definitionally* whatever closes the
    equation — the residual is identically zero and the check is vacuous. That
    is worth saying out loud rather than silently reporting a perfect score.
    """
    roles = {spec.role for spec in specs}

    if Role.LOAD not in roles:
        return ClosureResult(
            Closure.NOT_CHECKABLE,
            "No consumption sensor is mapped. Without one the balance closes by "
            "definition and cannot tell you anything.",
        )

    if Role.PV not in roles:
        return ClosureResult(
            Closure.NOT_CHECKABLE,
            "No generation sensor is mapped.",
        )

    has_grid = Role.GRID_IMPORT in roles or Role.GRID_EXPORT in roles
    if not has_grid:
        return ClosureResult(
            Closure.NOT_CHECKABLE,
            "No grid sensor is mapped.",
        )

    battery_mapped = Role.BATTERY_CHARGE in roles or Role.BATTERY_DISCHARGE in roles
    if declared.has_battery is Answer.YES and not battery_mapped:
        return ClosureResult(
            Closure.OPEN,
            "You told us there is a battery but nothing measures it.",
        )

    # Export is a real path across the boundary, and an import-only mapping does
    # not measure it. Treating that as closed asserts every path is accounted
    # for on a system that is provably sending energy the other way — and the
    # unmeasured export then arrives in the residual, one-signed and large,
    # looking exactly like a fault.
    #
    # A single signed meter is the exception: it carries both directions in one
    # channel, so import alone is complete.
    if Role.GRID_EXPORT not in roles and declared.grid_is_single_net_sensor is not Answer.YES:
        return ClosureResult(
            Closure.OPEN,
            "Nothing measures energy leaving the house. Anything exported will "
            "look like generation that went missing.",
            unmeasured_export=True,
        )

    if not battery_mapped:
        # Battery absent or unknown: still run the storage probe as a falsifier.
        return ClosureResult(Closure.OPEN, "No battery channel mapped.")

    return ClosureResult(Closure.CLOSED)


def _gamma_for_role(
    days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...], role: Role
) -> float | None:
    """Median ratio of residual to a role's contribution, across all hours.

    Measured against the *raw* residual ``r``, never the loss-corrected ``dr``.
    The distinction is the whole stability of the fit: ``dr`` already has the
    previous model subtracted, so fitting against it estimates the loss that
    remains rather than the loss that is there. Carried forward as the next
    run's prior, that alternates — a full estimate, then near zero, then a full
    estimate again — and the reported status oscillates with it on every
    refresh. Against ``r`` the fit is idempotent.
    """
    keys = [spec.key for spec in specs if spec.role is role]
    if not keys:
        return None

    ratios: list[float] = []
    for day in days:
        for bucket, raw in zip(day.buckets, day.r, strict=True):
            # The whole role, not the first channel carrying it. Measuring one
            # of two generation sensors made this depend on which the user
            # happened to map first — so a house with a second array, or with a
            # channel temporarily set aside, got a different loss model and a
            # different verdict from the same data in a different order. It also
            # made the DC-loss term collapse whenever the channel it had picked
            # was the one being examined, which is silence on exactly the
            # topology this project was built against.
            parts = [value for key in keys if (value := bucket.value(key)) is not None]
            if len(parts) < len(keys):
                continue
            total = sum(parts)
            if total <= 0:
                continue
            ratios.append(raw / total)
    if len(ratios) < 50:
        return None
    return median(ratios)


def fit_loss_model(
    days: tuple[DayResidual, ...],
    specs: tuple[ChannelSpec, ...],
    prior: LossModel | None,
) -> LossModel:
    """Estimate genuine loss so it can be subtracted before any fault test.

    Deliberately conservative: a gamma outside the DC-measurement window is not
    folded into the model, because that is a fault's territory and absorbing it
    here would hide exactly what we are looking for.
    """
    if len(days) < 3:
        return prior or LossModel()

    established: list[str] = []

    pv_gamma = _gamma_for_role(days, specs, Role.PV)
    pv_dc = 0.0
    if pv_gamma is not None and DC_MEASUREMENT_WINDOW[0] <= pv_gamma <= DC_MEASUREMENT_WINDOW[1]:
        pv_dc = pv_gamma
        established.append("pv_dc")

    charge_gamma = _gamma_for_role(days, specs, Role.BATTERY_CHARGE)
    discharge_gamma = _gamma_for_role(days, specs, Role.BATTERY_DISCHARGE)
    battery_dc = 0.0
    if charge_gamma is not None and discharge_gamma is not None:
        # The tell for a DC-measured battery is that gamma is positive on *both*
        # directions simultaneously. No other fault does that.
        both = [charge_gamma, discharge_gamma]
        if all(DC_MEASUREMENT_WINDOW[0] <= g <= DC_MEASUREMENT_WINDOW[1] for g in both):
            fitted = median(both)
            if fitted is not None:
                battery_dc = fitted
                established.append("battery_dc")

    night_gamma, standby = _fit_night_terms(days, specs)

    # The both-directions test needs daylight, and daylight is exactly what an
    # open boundary makes unusable. Falling back to the discharge-only slope is
    # safe only once generation has independently been shown to be measured on
    # the DC side, because on such a system a DC-measured battery is the
    # expected topology rather than a coincidence that happens to look like one.
    if battery_dc == 0.0 and night_gamma is not None and "pv_dc" in established:
        battery_dc = night_gamma
        established.append("battery_dc")

    if standby > 0.0:
        established.append("standby")

    return LossModel(
        pv_dc_gamma=pv_dc,
        battery_dc_gamma=battery_dc,
        standby_w=standby,
        samples=len(days),
        fitted_terms=tuple(established),
    )


def _fit_night_terms(
    days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...]
) -> tuple[float | None, float]:
    """Battery conversion loss and continuous unmetered draw, fitted together.

    Returns ``(battery_gamma, standby_w)``; the gamma is ``None`` when it could
    not be established.

    These two have to be fitted jointly or neither can be fitted at all. The
    standby term used to be estimated from night hours in which the battery was
    *also* idle — and on a house whose battery carries the load overnight, which
    is most houses with a battery, those hours do not exist. So the term that
    exists to absorb an inverter's own draw could never be measured on exactly
    the systems that have one, and its energy landed in the residual instead.

    At night, generation is zero and the residual is a straight line in battery
    throughput: the slope is the conversion loss, the intercept is whatever
    is drawn regardless. Theil-Sen for both, so a handful of odd hours cannot
    drag either one.
    """
    samples = _night_samples(days, specs)
    if samples is None:
        return None, 0.0
    xs, ys, loads = samples

    slope = theil_sen_slope(xs, ys)
    if slope is None:
        return None, 0.0
    intercept = theil_sen_intercept(xs, ys, slope)
    if intercept is None:
        return None, 0.0

    gamma = slope if DC_MEASUREMENT_WINDOW[0] <= slope <= DC_MEASUREMENT_WINDOW[1] else None
    return gamma, _plausible_standby(intercept, loads)


def _is_night(bucket: Bucket, pv_keys: list[str]) -> bool:
    """Whether nothing was generating in this hour.

    The single definition, used by the fit and by the ledger below. If those two
    disagreed by even an hour, the totals reported to explain a fit would not be
    the totals the fit saw, and the discrepancy would look like a fault in the
    data rather than in this file.
    """
    generation = _role_total(bucket, pv_keys)
    return generation is not None and generation <= NIGHT_MAX_PV_WH


def _role_total(bucket: Bucket, keys: list[str]) -> float | None:
    """The role's whole contribution, or ``None`` if any part is missing."""
    parts = [value for key in keys if (value := bucket.value(key)) is not None]
    return sum(parts) if len(parts) == len(keys) else None


def night_ledger(days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...]) -> dict[str, float]:
    """Every channel's night total, over hours where all of them were readable.

    Medians were what this reported before, and medians do not compose: knowing
    the middle hour of load and the middle hour of discharge says nothing about
    whether the two reconcile, because they are different hours. Totals over one
    agreed set of hours do reconcile, exactly, which turns "the numbers do not
    add up at night" from a summary into an arithmetic anyone can check a line at
    a time.

    ``night_ledger_hours`` is how many hours the totals cover, which is what
    turns the gap into a rate — a shortfall of 35,100 Wh over 390 hours is 90 W
    drawn continuously, and that is the number worth knowing.

    It is *not* a coverage signal. An earlier version of this docstring said a
    ledger count far below ``night_hours`` would mean coverage rather than
    physics, and that cannot happen: ``build_days`` has already discarded any
    bucket missing a balance channel, so by the time an hour reaches here every
    channel has reported. The two counts are equal on every input, and advice to
    compare them sent a reader looking for a signal that does not exist.
    """
    pv_keys = [spec.key for spec in specs if spec.role is Role.PV]
    if not pv_keys:
        return {}

    roles = [role for role in Role if role.in_balance]
    keys_for = {role: [spec.key for spec in specs if spec.role is role] for role in roles}
    present = [role for role in roles if keys_for[role]]

    totals = dict.fromkeys(present, 0.0)
    residual = 0.0
    hours = 0
    for day in days:
        for bucket, raw in zip(day.buckets, day.r, strict=True):
            if not _is_night(bucket, pv_keys):
                continue
            amounts = {role: _role_total(bucket, keys_for[role]) for role in present}
            if any(amount is None for amount in amounts.values()):
                # Unreachable as the engine calls this: `build_days` admits a
                # bucket only when every balance channel reported, which is the
                # same set iterated here. Kept because the totals below are only
                # comparable across roles if they cover identical hours, and
                # that is a contract with a caller rather than a property of
                # this function. Loosening `bucket_is_valid` makes it live.
                continue
            for role, amount in amounts.items():
                totals[role] += amount  # type: ignore[operator]
            residual += raw
            hours += 1

    if not hours:
        return {}

    # Rounded to the milliwatt-hour, far below anything a meter resolves, and
    # here for two other reasons. It keeps a total that cancels to nothing from
    # being reported as 2.6e-21, which reads as broken rather than as zero. And
    # it makes the result independent of the order the channels were mapped in:
    # the sums are the same either way, but floating point addition is not
    # associative, so without this the last digits move.
    def wh(value: float) -> float:
        return round(value, 3)

    # `night_total_residual_wh` is the whole identity: summing the signed role
    # totals gives the same figure reassociated, to the last bit on every input
    # tried. This used to publish that sum a second time as
    # `night_sources_minus_sinks_wh`, described as a check on the first — two
    # names for one number, in a file whose reader is trying to work out which
    # number to believe. One name.
    out: dict[str, float] = {
        "night_ledger_hours": float(hours),
        "night_total_residual_wh": wh(residual),
    }
    for role, amount in totals.items():
        out[f"night_total_{role.key}_wh"] = wh(amount)
    return out


def _night_samples(
    days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...]
) -> tuple[list[float], list[float], list[float]] | None:
    """Battery throughput, raw residual and load, over hours with no generation.

    Raw residual, for the same reason as ``_gamma_for_role``: fitting against a
    residual the previous model has already been subtracted from makes the fit
    oscillate rather than converge.
    """
    # Whole roles, not their first channel. Night is when *nothing* is
    # generating, so an installation with a second array had hours counted as
    # night while half its roof was still producing — and which half depended on
    # the order the two were mapped in.
    pv_keys = [s.key for s in specs if s.role is Role.PV]
    discharge_keys = [s.key for s in specs if s.role is Role.BATTERY_DISCHARGE]
    if not pv_keys or not discharge_keys:
        return None

    load_keys = [s.key for s in specs if s.role is Role.LOAD]

    xs: list[float] = []
    ys: list[float] = []
    loads: list[float] = []
    for day in days:
        for bucket, raw in zip(day.buckets, day.r, strict=True):
            if not _is_night(bucket, pv_keys):
                continue
            flow = _role_total(bucket, discharge_keys)
            if flow is None:
                continue
            xs.append(flow)
            ys.append(raw)
            if load_keys and (drawn := _role_total(bucket, load_keys)) is not None:
                loads.append(drawn)

    if len(xs) < MIN_STANDBY_SAMPLES:
        return None
    return xs, ys, loads


def _plausible_standby(intercept: float, night_loads: list[float]) -> float:
    """The intercept, if it can only be an inverter's own draw.

    Absolute bounds are not enough on their own: at 250 W of night load, a
    consumption sensor reading half produces a constant 125 W of residual,
    which sits comfortably inside any plausible idle range. The share test is
    what separates them — an inverter's supply is a small part of what it
    serves, and half a house is not.
    """
    if not (STANDBY_PLAUSIBLE_W[0] <= intercept <= STANDBY_PLAUSIBLE_W[1]):
        return 0.0
    typical = median(night_loads)
    if typical is None or typical <= 0:
        return 0.0
    if intercept > typical * STANDBY_MAX_SHARE_OF_LOAD:
        return 0.0
    return intercept


def night_fit_raw(
    days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...]
) -> dict[str, float]:
    """What the night fit measured, before anything was accepted or rejected.

    Exists so that "nothing could be established" is a statement with numbers
    behind it. A slope of 0.25 and a slope of -0.02 both leave the term at 0.0
    and both report the same empty tuple, and they are completely different
    problems.
    """
    # The ledger first, and outside the gate below. It answers a different
    # question from the fit — not "what did the night slope measure" but "what
    # did each channel actually total" — and it needs far less to answer it.
    # `_night_samples` wants a discharge channel and two hundred night hours, so
    # a house with no battery got nothing at all, and every install got nothing
    # for its first fortnight. That fortnight is precisely when somebody is
    # looking at "still looking" and wanting to know why.
    out: dict[str, float] = dict(night_ledger(days, specs))

    samples = _night_samples(days, specs)
    if samples is None:
        return out
    xs, ys, loads = samples
    out["night_hours"] = float(len(xs))

    slope = theil_sen_slope(xs, ys)
    if slope is not None:
        out["night_slope"] = slope
        intercept = theil_sen_intercept(xs, ys, slope)
        if intercept is not None:
            out["night_intercept_w"] = intercept

    typical = median(loads)
    if typical is not None:
        out["median_night_load_w"] = typical
    discharge = median(xs)
    if discharge is not None:
        out["median_night_discharge_wh"] = discharge
    residual = median(ys)
    if residual is not None:
        out["median_night_residual_wh"] = residual
    return out


def unmetered_draw_w(days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...]) -> float | None:
    """A continuous draw too large to be an inverter idling, if there is one.

    Deliberately not folded into the loss model. Absorbing an arbitrary
    kilowatt-hours a day as "normal" would hide the very thing the user needs
    told — but staying silent about it, having measured it, is no better.
    """
    _, standby = _fit_night_terms(days, specs)
    if standby > 0.0:
        return None

    samples = _night_samples(days, specs)
    if samples is None:
        return None
    xs, ys, _loads = samples

    slope = theil_sen_slope(xs, ys)
    if slope is None:
        return None
    intercept = theil_sen_intercept(xs, ys, slope)
    if intercept is None or intercept <= STANDBY_PLAUSIBLE_W[1]:
        return None
    return intercept


def infer(
    days: tuple[DayResidual, ...],
    specs: tuple[ChannelSpec, ...],
    declared: DeclaredTopology,
    loss: LossModel,
) -> TopologyEstimate:
    """Best current understanding of the system's shape."""
    notes: list[str] = []

    pv_dc: bool | None = None
    if len(days) >= MIN_DAYS_FOR_COUPLING:
        gamma = _gamma_for_role(days, specs, Role.PV)
        if gamma is not None:
            if DC_MEASUREMENT_WINDOW[0] <= gamma <= DC_MEASUREMENT_WINDOW[1]:
                pv_dc = True
                notes.append(
                    f"Generation appears to be measured before the inverter "
                    f"({gamma * 100:.0f}% conversion loss)."
                )
            elif abs(gamma) < DC_MEASUREMENT_MAX_IQR:
                pv_dc = False

    # Only claim a direction for a term the fit actually established. A
    # rejected term also leaves the gamma at 0.0, and reporting that as
    # "measured on the AC side" states as fact something never measured.
    battery_dc = loss.battery_dc_gamma > 0.0 if loss.established("battery_dc") else None

    coupling = Coupling.UNKNOWN
    if pv_dc is True or battery_dc is True:
        coupling = Coupling.DC_COUPLED
    elif pv_dc is False and battery_dc is False:
        coupling = Coupling.AC_COUPLED

    grid_is_net: bool | None = None
    if declared.grid_is_single_net_sensor is Answer.YES:
        grid_is_net = True
    elif declared.grid_is_single_net_sensor is Answer.NO:
        grid_is_net = False

    return TopologyEstimate(
        coupling=coupling,
        pv_measured_dc=pv_dc,
        battery_measured_dc=battery_dc,
        grid_is_net=grid_is_net,
        notes=tuple(notes),
    )
