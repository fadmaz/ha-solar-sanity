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
    spec = next((s for s in specs if s.role is role), None)
    if spec is None:
        return None

    ratios: list[float] = []
    for day in days:
        for bucket, raw in zip(day.buckets, day.r, strict=True):
            value = bucket.value(spec.key)
            if value is None or value <= 0:
                continue
            ratios.append(raw / value)
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
    pv = next((s for s in specs if s.role is Role.PV), None)
    if pv is None:
        return None, 0.0

    discharge = next((s for s in specs if s.role is Role.BATTERY_DISCHARGE), None)
    if discharge is None:
        return None, 0.0

    load = next((s for s in specs if s.role is Role.LOAD), None)

    xs: list[float] = []
    ys: list[float] = []
    loads: list[float] = []
    for day in days:
        # Raw residual, for the same reason as _gamma_for_role: fitting against
        # a residual the previous model has already been subtracted from makes
        # the fit oscillate rather than converge.
        for bucket, raw in zip(day.buckets, day.r, strict=True):
            generation = bucket.value(pv.key)
            if generation is None or generation > NIGHT_MAX_PV_WH:
                continue
            flow = bucket.value(discharge.key)
            if flow is None:
                continue
            xs.append(flow)
            ys.append(raw)
            if load is not None and (drawn := bucket.value(load.key)) is not None:
                loads.append(drawn)

    if len(xs) < MIN_STANDBY_SAMPLES:
        return None, 0.0

    slope = theil_sen_slope(xs, ys)
    if slope is None:
        return None, 0.0
    intercept = theil_sen_intercept(xs, ys, slope)
    if intercept is None:
        return None, 0.0

    gamma = slope if DC_MEASUREMENT_WINDOW[0] <= slope <= DC_MEASUREMENT_WINDOW[1] else None
    return gamma, _plausible_standby(intercept, loads)


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


def unmetered_draw_w(days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...]) -> float | None:
    """A continuous draw too large to be an inverter idling, if there is one.

    Deliberately not folded into the loss model. Absorbing an arbitrary
    kilowatt-hours a day as "normal" would hide the very thing the user needs
    told — but staying silent about it, having measured it, is no better.
    """
    _, standby = _fit_night_terms(days, specs)
    if standby > 0.0:
        return None

    pv = next((s for s in specs if s.role is Role.PV), None)
    discharge = next((s for s in specs if s.role is Role.BATTERY_DISCHARGE), None)
    if pv is None or discharge is None:
        return None

    xs: list[float] = []
    ys: list[float] = []
    for day in days:
        for bucket, raw in zip(day.buckets, day.r, strict=True):
            generation = bucket.value(pv.key)
            flow = bucket.value(discharge.key)
            if generation is None or generation > NIGHT_MAX_PV_WH or flow is None:
                continue
            xs.append(flow)
            ys.append(raw)

    if len(xs) < MIN_STANDBY_SAMPLES:
        return None
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
