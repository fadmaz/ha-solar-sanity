"""Working out the shape of the system, and whether the identity can close.

Principle: ask the user what the user certainly knows; infer what the user
certainly does not. People know whether they have a battery. They emphatically
do not know whether their PV sensor reads before or after the inverter.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .faults import DC_MEASUREMENT_MAX_IQR, DC_MEASUREMENT_WINDOW
from .linalg import median
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

#: Night hours used to fit the standby term. PV must be zero and the battery
#: essentially idle, or we are fitting something else.
STANDBY_MAX_BATTERY_WH = 50.0
MIN_STANDBY_SAMPLES = 200
STANDBY_PLAUSIBLE_W = (10.0, 120.0)


class Closure(Enum):
    """Whether every energy path across the house boundary is measured."""

    CLOSED = "closed"
    OPEN = "open"
    NOT_CHECKABLE = "not_checkable"


@dataclass(frozen=True, slots=True)
class ClosureResult:
    state: Closure
    reason: str = ""


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

    if not battery_mapped:
        # Battery absent or unknown: still run the storage probe as a falsifier.
        return ClosureResult(Closure.OPEN, "No battery channel mapped.")

    return ClosureResult(Closure.CLOSED)


def _gamma_for_role(
    days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...], role: Role
) -> float | None:
    """Median ratio of residual to a role's contribution, across all hours."""
    spec = next((s for s in specs if s.role is role), None)
    if spec is None:
        return None

    ratios: list[float] = []
    for day in days:
        for bucket, dr in zip(day.buckets, day.dr, strict=True):
            value = bucket.value(spec.key)
            if value is None or value <= 0:
                continue
            ratios.append(dr / value)
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

    pv_gamma = _gamma_for_role(days, specs, Role.PV)
    pv_dc = 0.0
    if pv_gamma is not None and DC_MEASUREMENT_WINDOW[0] <= pv_gamma <= DC_MEASUREMENT_WINDOW[1]:
        pv_dc = pv_gamma

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

    standby = _fit_standby(days, specs)

    return LossModel(
        pv_dc_gamma=pv_dc,
        battery_dc_gamma=battery_dc,
        standby_w=standby,
        samples=len(days),
    )


def _fit_standby(days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...]) -> float:
    """Flat draw that nothing meters, fitted on clean night hours only."""
    pv = next((s for s in specs if s.role is Role.PV), None)
    if pv is None:
        return 0.0

    battery_keys = [s.key for s in specs if s.role in (Role.BATTERY_CHARGE, Role.BATTERY_DISCHARGE)]

    samples: list[float] = []
    for day in days:
        for bucket, dr in zip(day.buckets, day.dr, strict=True):
            generation = bucket.value(pv.key)
            if generation is None or generation > 0:
                continue
            throughput = sum(v for k in battery_keys if (v := bucket.value(k)) is not None)
            if throughput > STANDBY_MAX_BATTERY_WH:
                continue
            samples.append(dr / (bucket.seconds / 3600.0))

    if len(samples) < MIN_STANDBY_SAMPLES:
        return 0.0

    fitted = median(samples)
    if fitted is None:
        return 0.0
    if not (STANDBY_PLAUSIBLE_W[0] <= fitted <= STANDBY_PLAUSIBLE_W[1]):
        return 0.0
    return fitted


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

    battery_dc = loss.battery_dc_gamma > 0.0 if loss.fitted else None

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
