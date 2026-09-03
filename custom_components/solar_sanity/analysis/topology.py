"""Working out the shape of the system, and whether the identity can close.

Principle: ask the user what the user certainly knows; infer what the user
certainly does not. People know whether they have a battery. They emphatically
do not know whether their PV sensor reads before or after the inverter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .faults import DC_MEASUREMENT_MAX_IQR, DC_MEASUREMENT_WINDOW
from .linalg import least_squares, median, project_out, theil_sen_intercept, theil_sen_slope
from .model import (
    Answer,
    Bucket,
    BucketSource,
    ChannelSpec,
    Coupling,
    DeclaredTopology,
    LossModel,
    Role,
    TopologyEstimate,
)
from .residual import DayResidual

#: Hours needed before the joint loss fit is trusted. Three free coefficients
#: against a residual that carries meter noise wants far more than three
#: equations — a week of complete hours, which is also roughly where the old
#: per-role estimator's own floor of fifty ratios sat.
MIN_JOINT_FIT_HOURS: Final = 168

#: Days of data before we will commit to a DC-vs-AC conclusion.
MIN_DAYS_FOR_COUPLING = 14

#: Generation at or below this counts as night, for fitting purposes.
NIGHT_MAX_PV_WH = 50.0

#: The largest share of generation that may be taken as conversion loss.
#:
#: A sensor before the inverter and a sensor reading high by the same factor are
#: not merely hard to tell apart — they are *the same series*. Dividing by an
#: efficiency and multiplying by its reciprocal produce identical numbers, so no
#: statistic of the data can favour one story. The engine already absorbed this
#: silently up to a tenth and reported "no problem found"; what it could not do
#: was say so.
#:
#: The bound is therefore not a discrimination — there is none to be had — but a
#: judgement about how much loss an inverter can plausibly account for. 0.15 is
#: a conversion efficiency of 0.85, below every inverter currently sold and with
#: room for the cabling and tracking losses a DC-side sensor also sees. Beyond
#: it, "your sensor reads before the inverter" stops being a credible reading of
#: the same number, and the engine goes back to saying it cannot explain the
#: difference — which it cannot.
#:
#: Whatever is taken here is said out loud. See `_loss_notes`: absorbing this
#: quietly is how a sensor reading a fifth high becomes "no problem found".
DC_PV_MAX_GAMMA = 0.15

#: How far the charge side may sit from the figure its discharge partner
#: implies, before the pair is not describing one battery.
#:
#: Two free columns can fit more than one column could, and some of what they
#: can fit is not loss. On a house whose export is unmapped the residual is
#: largest exactly when the battery is charging, and the fit will happily take
#: a 0.044 discharge coefficient to help explain it — enough to drop a real
#: `missing_export_channel` finding below the band and silence it. That is the
#: cost of the extra freedom, and this is what pays for it: the two directions
#: of one battery are locked together by its efficiency, so a pair that
#: describes no efficiency at all can be refused however small it is.
#:
#: Measured over 864 healthy corpus houses whose battery term is in range, the
#: worst disagreement is 0.107 — all of them `self_consumed` at 5% meter noise,
#: where charging absorbs the surplus and the charge column is nearly collinear
#: with generation. The spurious fit on an unmapped-export house sits at 1.02.
#: Nine and a half times apart; 0.35 is between them, three times clear of each.
DC_BATTERY_DIRECTION_TOLERANCE = 0.35

#: Widening the accepted band beyond `DC_MEASUREMENT_WINDOW` was measured and
#: *not* taken, and the reason is worth keeping.
#:
#: The two directions of one battery are locked to each other by its efficiency
#: — the charge coefficient must be `gamma / (1 - gamma)` — so the pair can be
#: checked rather than merely bounded, and on clean data the agreement is exact
#: at every efficiency from 0.95 to 0.75. That check would have justified
#: reaching down to a battery 75% efficient.
#:
#: It is not robust enough. Charging absorbs the surplus, so on a
#: self-consumption house the charge column and the generation column are
#: nearly collinear, and at the 5% meter noise this project already calls
#: healthy the fitted charge coefficient wanders by up to 0.093 in absolute
#: terms — far enough to change sign. The fault it would need to separate, a
#: discharge channel reading ten per cent low, sits at 0.125. A ratio of 1.3
#: between the noise and the signal is not a test.
#:
#: Done, and not by profiling. The parameter is now measured where the
#: collinear column does not exist at all — see `_dark_hours_battery`. The
#: constants below belong to that estimator; everything above still governs the
#: day fit, which remains the fallback when the dark hours cannot answer.

#: Dark hours averaged into one design point.
#:
#: This constant is the whole estimator, and both ends of it were measured. One
#: hour per point leaves the discharge column carrying its own meter noise into
#: a slope fitted against it, which reads the slope about 0.009 high at 5% —
#: larger than the fault it has to separate. A whole limb of night averages that
#: away but leaves two points a night, and a line through two points has no
#: residual left to be wrong about: a load booked to the wrong hour of the night
#: becomes a slope of 0.17 with nothing to contradict it, which is
#: `TestASurplusThatComesBackIsNotExport` absorbed rather than reported. Two
#: hours halves the noise and still leaves five or six blocks a night for the
#: line to fail against. At two hours that same house measures +0.0000 on clean
#: data, and 0.029 to 0.035 at 5% noise against an acceptance floor of 0.02.
NIGHT_BLOCK_HOURS: Final = 2

#: Blocks needed before the slope over them means anything. Roughly ten nights;
#: thirty days of the synthetic house gives 180, forty-five gives 270.
MIN_NIGHT_BLOCKS: Final = 40

#: The bracket the lock's root is looked for in, and how finely.
#:
#: ``h(g) = slope(u(g), y) - g`` falls monotonically in g, so halving finds its
#: single root without a convergence question. Sixteen halvings of a unit
#: bracket reach 1.5e-5, two orders below `MAX_GAMMA_HALF_WIDTH`. Measured
#: against a sixty-step heavily damped reference over 432 houses, the worst
#: disagreement is 0.0035, at a charge ratio of 1.5.
#:
#: A plain fixed point was built first and is not what ships: it two-cycles on
#: 18 of 378 configurations (0.145 against 0.1357, 0.2106 against 0.1992), which
#: makes the answer at any step cap arbitrary between two values. Damping removes
#: the cycle and costs more steps than bisection.
GAMMA_LOCK_BRACKET: Final = (-0.5, 0.5)
GAMMA_LOCK_STEPS: Final = 16

#: How much of the consumption column must survive the locked column and a
#: constant before the load screen is a test rather than an identity.
#:
#: After dark ``load = import + discharge - charge - r``, so on a house drawing
#: nothing from the grid and charging nothing, load is the residual's mirror
#: image and `_partner_share` returns its own input. Between that and a working
#: screen is a band where it merely loses power, and the loss is invisible in
#: the share itself: at independence 0.15 a healthy 30 kWh house scores -0.105
#: on the load screen while one whose consumption sensor reads ten per cent low
#: scores -0.005 — the fault on the *safer* side of the screen.
#:
#: Measured over 1,680 runs of that fault: below 0.18 it is absorbed on up to
#: 35% of houses, between 0.20 and 0.26 on a few per cent, at 0.28 and above on
#: none. Computed from the design columns alone — the residual never enters it —
#: so a fault cannot move it.
#:
#: It is a cliff. Healthy 30 kWh houses at a charge ratio of 0.286 sit at 0.209
#: to 0.243 and are refused; at 0.400 they sit at 0.293 to 0.332 and are taken.
#:
#: This is not only for the charging hours this estimator newly admits. The
#: estimator as it shipped at 0.26.0 absorbs the same fault on 18 of 600 houses
#: that never charge in the dark — all at 5% meter noise, all with batteries of
#: 15 kWh and up, independence 0.199 to 0.258. This refuses all eighteen.
MIN_LOAD_INDEPENDENCE: Final = 0.28

#: The band a DC-measured battery's loss fraction may occupy.
#:
#: Deliberately *not* `DC_MEASUREMENT_WINDOW`, which keeps its own meaning for
#: the generation term, for `infer`, and for the fallback. The two are guarded
#: by different things. A DC-side generation sensor and one reading high by the
#: same factor are the same series, so nothing but judgement bounds that term.
#: The battery term is measured on hours nothing else can reach, so it can
#: afford the extra room: 0.20 is a round trip of 0.80, below every hybrid
#: currently sold, and past it "your battery is metered on its DC side" stops
#: being a credible reading of the number.
DC_BATTERY_GAMMA_WINDOW: Final = (0.02, 0.20)

#: How wide the sign-test interval on the block slope may be before the dark
#: hours have not measured a gamma at all. A summer house sits at 0.0007 to
#: 0.0015; a 1.2 kWp house in December at 5% noise sits at 0.0055 to 0.0060 and
#: is refused, which is right — its battery moves a seventh of the energy and
#: its slope wanders from 0.032 to 0.074 on a truth of 0.050.
MAX_GAMMA_HALF_WIDTH: Final = 0.003

#: How far the day's implied charge coefficient may fall *short* of the one the
#: dark-hours gamma requires. One-sided on purpose: subtracting more than the
#: day contains is the failure; finding more than the lock explains is the fault
#: engine's business, and refusing on it would silence an unmapped-export
#: finding, which shows -0.60 to -1.12 here. Healthy houses sit in
#: [-0.012, +0.017]; a battery whose discharge is DC-metered and whose charge is
#: not sits at +0.042 to +0.152.
MAX_CHARGE_SHORTFALL: Final = 0.03

#: A day charging less than this has no charge coefficient to imply.
MIN_DAY_CHARGE_WH: Final = 500.0

#: Hours needed before a night fit means anything.
MIN_STANDBY_SAMPLES = 200

#: What an inverter's own idle draw can plausibly be. Outside this the number
#: is not absorbed as loss — it is reported, because a continuous unmetered
#: draw larger than an inverter idles at is something the user should know
#: about rather than something we should quietly subtract.
STANDBY_PLAUSIBLE_W = (10.0, 120.0)

#: How much of the night residual may move *with the consumption channel*
#: before the draw is not a constant draw at all.
#:
#: This replaces a cap on the draw as a share of night load, which was the right
#: idea measured the wrong way. It asked "is this small enough to be an inverter
#: idling", and the honest answer for a 250 W night load was a ceiling of about
#: 45 W — against an advertised band reaching 120 W, and against real hybrid
#: inverters that idle at 30 to 100 W. Worse, `add_standby` takes the draw out
#: of the metered load, so the denominator fell as the numerator rose and the
#: true ceiling was a sixth of night load rather than a fifth.
#:
#: The question it was really asking is whether the residual is *constant* or
#: *proportional to consumption*, and that can be asked directly. Fitted
#: together over night hours, a continuous draw puts everything in the flat term
#: and nothing in the load term; a consumption sensor reading low by a fraction
#: does the reverse. Measured at 5% meter noise: 0.0115 for a constant draw of
#: any size, against 0.111 for a load channel reading merely ten per cent low,
#: and 1.00 for one reading half. Ten times apart at their closest.
MAX_LOAD_PROPORTIONAL_SHARE = 0.04


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

    # Asked at setup, stored, and until now never read by anything.
    #
    # It belongs here and nowhere else: a load sensor covering part of the house
    # leaves the rest of the consumption outside every channel we have, so the
    # identity cannot close and saying it does is the one thing this function
    # exists to prevent. The owner has already told us so — we were simply not
    # listening, and were reporting their house as fully measured on their own
    # word that it is not.
    #
    # Deliberately the last branch. `check_closure` returns on first match, and
    # every branch above it describes a boundary that is open for a *different*
    # reason. In particular the export branch sets `unmeasured_export`, which is
    # what earns a house the restricted night-hours verdict; answering ahead of
    # it would return an open boundary with that flag unset and silently take
    # the only real verdict available away from every house with no export
    # meter — a strictly worse answer, arrived at by adding information.
    if declared.load_covers_whole_house is Answer.NO:
        return ClosureResult(
            Closure.OPEN,
            "Your consumption sensor does not cover the whole house, so whatever "
            "it misses will look like energy that went missing.",
        )

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


def _role_hourly(bucket: Bucket, keys: list[str]) -> float | None:
    """One role's total for one hour, or ``None`` if any of its channels is absent.

    Partial is not a total. A role carried by two sensors where one has a hole
    would otherwise contribute half of itself and be fitted against as though
    that were the whole thing.
    """
    parts = [value for key in keys if (value := bucket.value(key)) is not None]
    if len(parts) < len(keys):
        return None
    return sum(parts)


def joint_loss_fit(
    days: tuple[DayResidual, ...],
    specs: tuple[ChannelSpec, ...],
    *,
    with_battery: bool = True,
) -> dict[str, float] | None:
    """All three loss terms at once, by least squares.

    One at a time is biased, and on a DC-coupled hybrid it is badly biased. The
    terms overlap — generation and battery throughput rise together, and a
    continuous draw is present in both — so a median-of-ratios attributed to one
    carries a share of the others. Measured against a known 96%-efficient
    inverter the generation term reads 62% high, which is enough to push a
    healthy installation outside the window that would have absorbed it. Nothing
    is then subtracted at all, and the house reports "still looking" forever on
    a loss the model was built to explain.

    Fitted against the *raw* residual ``r``, never ``dr``, for the reason
    ``_gamma_for_role`` documented before it: ``dr`` already has the previous
    model subtracted, so fitting against it estimates the loss that remains
    rather than the loss that is there, and carried forward as the next run's
    prior that alternates between a full estimate and nearly zero.

    The flat column is fitted and its value discarded. It is here so that a
    continuous unmetered draw lands in it rather than being smeared across the
    two terms that are kept — the standby figure itself comes from the night
    fit, which sees the same draw without any generation confusing it.
    """
    roles = {
        "pv_dc": [spec.key for spec in specs if spec.role is Role.PV],
        # Charging and discharging are separate columns because they lose
        # separate amounts. Summed into one, the single coefficient that comes
        # back is a blend of the two, and a blend of 0.1000 and 0.1111 is
        # 0.1057 — which is outside the window that would have accepted the
        # 0.1000. The model was rejecting a loss its own bounds admit, on a
        # battery whose only sin was being 90% efficient.
        "battery_charge": [spec.key for spec in specs if spec.role is Role.BATTERY_CHARGE],
        "battery_discharge": [spec.key for spec in specs if spec.role is Role.BATTERY_DISCHARGE],
    }
    if not with_battery:
        roles = {"pv_dc": roles["pv_dc"]}
    present = [name for name, keys in roles.items() if keys]
    if not present:
        return None

    columns: dict[str, list[float]] = {name: [] for name in present}
    flat: list[float] = []
    target: list[float] = []
    for day in days:
        for bucket, raw in zip(day.buckets, day.r, strict=True):
            amounts = {name: _role_hourly(bucket, roles[name]) for name in present}
            if any(amount is None for amount in amounts.values()):
                continue
            for name, amount in amounts.items():
                # Magnitude, not the signed sum. Charging and discharging both
                # lose energy, and they carry opposite signs — summed, a battery
                # that cycles evenly cancels to nothing and the term it needs
                # cannot be seen at all.
                columns[name].append(abs(amount))  # type: ignore[arg-type]
            flat.append(1.0)
            target.append(raw)

    if len(target) < MIN_JOINT_FIT_HOURS:
        return None

    order = [*present, "flat"]
    solved = least_squares([*(columns[name] for name in present), flat], target)
    if solved is None:
        return None
    return dict(zip(order, solved, strict=True))


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
    # Called for the standby draw first, because `_charge_confirms` needs it.
    _night_gamma, standby = _fit_night_terms(days, specs)

    # Measured where the collinear column does not exist. See
    # `_dark_hours_battery`: this replaces fitting the two directions freely and
    # checking their agreement afterwards, which was never a test.
    dark_gamma, _dark_measured = _dark_hours_battery(days, specs)
    if dark_gamma is not None and not _charge_confirms(days, specs, dark_gamma, standby):
        dark_gamma = None

    joint = joint_loss_fit(days, specs) or {}
    if dark_gamma is not None:
        battery_dc = dark_gamma
        established.append("battery_dc")
    else:
        # Unchanged, and still the fallback. The dark hours are silent on a
        # December house at 1.2 kWp, where the battery moves a seventh of the
        # energy: most such scenarios at 5% noise are refused as imprecise and
        # handed straight back to this. Letting a *precise* refusal from the
        # dark hours veto the day fit as well was measured and not taken — it
        # cost two healthy houses their verdict to buy candour on a load CT
        # reading a tenth low, which stays "still looking" either way.
        battery_dc = _accepted_battery(joint, established)

    if battery_dc == 0.0 and any(
        spec.role in (Role.BATTERY_CHARGE, Role.BATTERY_DISCHARGE) for spec in specs
    ):
        # The pair described no battery, so refit without it before reading the
        # generation term. This is the same argument that made the fit joint in
        # the first place, one step further on: columns fitted side by side
        # share their errors, so a column fitted beside one that turned out to
        # be explaining something other than loss is carrying part of whatever
        # that was.
        #
        # It is not hypothetical. Two real battery banks beside a load CT
        # reading 55% put 0.53 into the discharge column — refused — and 0.0257
        # into generation, which the window accepts. That spurious 2.6% was
        # enough to break a tie and call the two banks a duplicate pair. Refit
        # without them and generation comes back at -0.068, refused as it should
        # be.
        #
        # The safety property is that a genuinely DC-metered inverter reads the
        # same both ways — 0.0400 either side, on a house whose battery is
        # lossless and whose pair is therefore refused, which is the only shape
        # in which this branch runs at all. That claim used to sit here as prose
        # checked once by hand; it is now `tests/analysis/test_loss_refit.py`,
        # along with the trap above.
        #
        # Read that file before concluding this branch has broken something. A
        # real installation was reported as its victim — +0.0403 with the columns
        # and -0.0742 without — and the pattern is not the clean one (+0.0400
        # both ways) but the contaminated one (+0.0323 then -0.0631). The
        # accusation assumed the likeable number was the true one. Divergence
        # here is the symptom this exists to find, not evidence against it.
        joint = joint_loss_fit(days, specs, with_battery=False) or joint

    def accepted(term: str, ceiling: float) -> float:
        # The floor is unchanged and still does the same job: below it there is
        # no term worth having. The ceiling is per-term, because the two terms
        # are guarded by different things — the battery pair by whether it
        # describes one efficiency, generation by nothing at all, since a sensor
        # before the inverter and a sensor reading high are the same series.
        value = joint.get(term)
        if value is None:
            return 0.0
        if not DC_MEASUREMENT_WINDOW[0] <= value <= ceiling:
            return 0.0
        established.append(term)
        return value

    pv_dc = accepted("pv_dc", DC_PV_MAX_GAMMA)

    # The bare night slope used to be taken here when generation had
    # independently shown as DC-measured. It is gone: that gate existed only
    # because the slope had no screens of its own, and `_dark_hours_battery`
    # now measures the same hours with them. It was also the branch through
    # which the refit's fabricated pv_dc opened a door for a gamma nothing had
    # checked.

    if standby > 0.0:
        established.append("standby")

    return LossModel(
        pv_dc_gamma=pv_dc,
        battery_dc_gamma=battery_dc,
        standby_w=standby,
        samples=len(days),
        fitted_terms=tuple(established),
    )


def _accepted_battery(joint: dict[str, float], established: list[str]) -> float:
    """The battery's DC loss, taken from the direction that defines it.

    A DC-measured battery does not lose the same fraction both ways. With ``e``
    the round-trip efficiency and the measured quantities on the right,

        residual = (1 - e) * discharge + ((1 - e) / e) * charge

    so the loss *fraction* is the discharge coefficient, and the charge side is
    the larger number implied by it. Fitting one coefficient against the sum of
    the two magnitudes returned neither: it returned a blend, and a blend is
    always above the smaller of the pair. At 90% efficiency the discharge
    coefficient is exactly 0.1000 — exactly what the window admits — while the
    blend is 0.1057, which it does not. The model was refusing a loss its own
    bounds accept, on the strength of a number that describes no physical
    quantity.

    The window is unchanged. This is not a widening; it is the same test asked
    of the right number.

    The pair is then checked against itself. Splitting one column into two buys
    the fit a degree of freedom it can spend on things that are not loss — on a
    house with unmapped export it will take a 0.044 discharge coefficient to
    help explain energy that is leaving, and silence the finding that would
    have named it. Requiring the charge side to be the number the discharge
    side implies costs a real battery nothing and refuses that outright.
    """
    charge = joint.get("battery_charge")
    discharge = joint.get("battery_discharge")
    if charge is None or discharge is None:
        return 0.0
    if not DC_MEASUREMENT_WINDOW[0] <= discharge <= DC_MEASUREMENT_WINDOW[1]:
        return 0.0
    if abs(charge - discharge / (1.0 - discharge)) > DC_BATTERY_DIRECTION_TOLERANCE:
        return 0.0

    established.append("battery_dc")
    return discharge


def _dark_blocks(
    days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...]
) -> tuple[list[float], list[float], list[float], list[float], list[float], float, float] | None:
    """Mean discharge, charge, raw residual, load and import per block of darkness.

    A day has two limbs of dark — the hours before its first light and the hours
    after its last — and each limb is chunked separately, so no design point is
    a mixture of the two ends of a day.

    Raw ``day.r``, never ``dr``, for the reason ``joint_loss_fit`` gives: a
    residual the previous model has already been subtracted from makes the fit
    estimate the loss that remains rather than the loss that is there.

    The charge column is returned rather than assumed absent. It used to be
    only a total, checked against a tolerance so that a house charging after
    dark could be refused; it is now a column the fit uses, because the two
    directions of one battery are one parameter. The two totals are kept
    because a reader still wants to know how much of each there was.
    """
    pv_keys = [spec.key for spec in specs if spec.role is Role.PV]
    if not pv_keys:
        return None
    discharge_keys = [spec.key for spec in specs if spec.role is Role.BATTERY_DISCHARGE]
    charge_keys = [spec.key for spec in specs if spec.role is Role.BATTERY_CHARGE]
    load_keys = [spec.key for spec in specs if spec.role is Role.LOAD]
    import_keys = [spec.key for spec in specs if spec.role is Role.GRID_IMPORT]

    xs: list[float] = []
    cs: list[float] = []
    ys: list[float] = []
    loads: list[float] = []
    imports: list[float] = []
    dark_charge = 0.0
    dark_discharge = 0.0
    for day in days:
        limbs: list[list[tuple[float, float, float, float, float]]] = [[], []]
        limb = 0
        for bucket, raw in zip(day.buckets, day.r, strict=True):
            generated = _role_total(bucket, pv_keys)
            if generated is None:
                continue
            if generated > NIGHT_MAX_PV_WH:
                limb = 1
                continue
            # A house with no battery is the degenerate case of the same line,
            # not one this cannot speak about: its column is all zeros and there
            # is simply no slope to find.
            out = 0.0 if not discharge_keys else _role_total(bucket, discharge_keys)
            into = 0.0 if not charge_keys else _role_total(bucket, charge_keys)
            drawn = 0.0 if not load_keys else _role_total(bucket, load_keys)
            taken = 0.0 if not import_keys else _role_total(bucket, import_keys)
            if out is None or into is None or drawn is None or taken is None:
                continue
            dark_charge += abs(into)
            dark_discharge += abs(out)
            limbs[limb].append((abs(out), abs(into), raw, drawn, taken))
        for hours in limbs:
            last = len(hours) - NIGHT_BLOCK_HOURS + 1
            for start in range(0, last, NIGHT_BLOCK_HOURS):
                block = hours[start : start + NIGHT_BLOCK_HOURS]
                size = float(len(block))
                xs.append(sum(point[0] for point in block) / size)
                cs.append(sum(point[1] for point in block) / size)
                ys.append(sum(point[2] for point in block) / size)
                loads.append(sum(point[3] for point in block) / size)
                imports.append(sum(point[4] for point in block) / size)
    if len(xs) < MIN_NIGHT_BLOCKS:
        return None
    return xs, cs, ys, loads, imports, dark_charge, dark_discharge


def _block_slope(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """Theil-Sen over the blocks, and half the sign-test interval on it.

    Every pair, not ``theil_sen_slope``'s strided sample: forty-five days is 270
    blocks and 36,315 pairs, which is six milliseconds, and the stride exists to
    bound a thousand-point problem this one does not have.

    The interval is the order statistic a median carries with it — the sorted
    slopes read at rank m/2 +/- 0.98*sqrt(m). The pairs share points, so it is
    not a confidence interval; it is a deterministic measure of how tightly the
    blocks agree about one line, and that is what decides whether the dark hours
    measured anything at all rather than how large it was.
    """
    slopes: list[float] = []
    for i in range(len(xs)):
        for j in range(i + 1, len(xs)):
            span = xs[j] - xs[i]
            if abs(span) < 1e-12:
                continue
            slopes.append((ys[j] - ys[i]) / span)
    if len(slopes) < 3:
        return None
    slopes.sort()
    centre = median(slopes)
    if centre is None:
        return None
    count = len(slopes)
    reach = 0.98 * (count**0.5)
    low = max(0, int(count / 2.0 - reach))
    high = min(count - 1, int(count / 2.0 + reach))
    return centre, (slopes[high] - slopes[low]) / 2.0


def _rms(values: list[float]) -> float:
    return (sum(value * value for value in values) / len(values)) ** 0.5


def _locked_column(discharge: list[float], charge: list[float], gamma: float) -> list[float]:
    """The one column a DC battery has, given a candidate loss fraction.

    The two directions are locked by a single efficiency — the charge
    coefficient must be ``gamma / (1 - gamma)`` — so
    ``gamma * discharge + gamma/(1-gamma) * charge`` is ``gamma`` times
    ``discharge + charge/(1-gamma)``, and there is one parameter rather than
    two. Clamped so the factor stays finite whatever slope the blocks produce;
    a gamma outside the bracket is refused by `DC_BATTERY_GAMMA_WINDOW` anyway,
    and where nothing charges in the dark this returns ``discharge`` for every
    gamma.
    """
    low, high = GAMMA_LOCK_BRACKET
    factor = 1.0 / (1.0 - max(low, min(high, gamma)))
    return [out + factor * into for out, into in zip(discharge, charge, strict=True)]


def _locked_slope(
    discharge: list[float], charge: list[float], residual: list[float]
) -> tuple[float, float] | None:
    """The gamma that is its own slope against the column it defines.

    A house that does not charge in the dark has no charge column, so the
    locked column is ``discharge`` for every gamma and the search would fit the
    same line sixteen times. Short-circuiting that is exact rather than an
    approximation, and it is why this costs nothing on the houses the estimator
    already answers.
    """
    if not any(into > 0.0 for into in charge):
        return _block_slope(discharge, residual)

    def excess(gamma: float) -> float | None:
        line = _block_slope(_locked_column(discharge, charge, gamma), residual)
        return None if line is None else line[0] - gamma

    low, high = GAMMA_LOCK_BRACKET
    at_low, at_high = excess(low), excess(high)
    # The root has to be bracketed. Refusing here rather than returning an
    # endpoint is the difference between "no loss fraction explains these
    # blocks" and a number that happens to sit where the search stopped.
    if at_low is None or at_high is None or at_low < 0.0 or at_high > 0.0:
        return None
    for _ in range(GAMMA_LOCK_STEPS):
        middle = (low + high) / 2.0
        here = excess(middle)
        if here is None:
            return None
        if here >= 0.0:
            low = middle
        else:
            high = middle
    return _block_slope(_locked_column(discharge, charge, (low + high) / 2.0), residual)


def _column_independence(column: list[float], partner: list[float]) -> float | None:
    """How much of ``partner`` the fitted column and a constant leave behind.

    A property of the design and of nothing else: the residual is not an input,
    so no fault can flatter it. Near zero means the partner is the fitted column
    plus an offset, and asking whether the residual moves with it is asking
    whether the residual moves with itself — which is the failure
    `MIN_LOAD_INDEPENDENCE` exists to refuse.
    """
    stripped = project_out([column, [1.0] * len(column)], partner)
    if stripped is None:
        return None
    scale = _rms(partner)
    if scale <= 1e-12:
        return None
    return _rms(stripped) / scale


def _partner_share(
    discharge: list[float], partner: list[float], residual: list[float]
) -> float | None:
    """How much of the dark residual moves with a channel that is not the battery.

    The same screen ``_load_proportional_share`` already applies to the standby
    term, asked of the slope and of one more channel. A consumption sensor
    reading a tenth low fits a dark-hours slope of 0.078 to 0.086 — squarely
    inside the band — and puts 0.136 into the load column; a grid import sensor
    reading a tenth low fits 0.024 to 0.033 and puts 0.116 into the import
    column. A real battery puts 0.022 into either at 5% noise. Without these two
    the band would be admitting two faults it has no other way to see.

    Both partners are removed by projection rather than fitted beside the
    battery column, so the number returned is the partner's own share of what
    the discharge column could not explain, whether or not the house has a
    battery column at all.
    """
    ones = [1.0] * len(partner)
    basis = [discharge, ones] if any(value != 0.0 for value in discharge) else [ones]
    stripped_partner = project_out(basis, partner)
    stripped_residual = project_out(basis, residual)
    if stripped_partner is None or stripped_residual is None:
        return None
    fitted = least_squares([stripped_partner], stripped_residual)
    return None if fitted is None else fitted[0]


def _dark_hours_battery(
    days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...]
) -> tuple[float | None, dict[str, float]]:
    """The battery's DC loss fraction, measured on every dark hour there is.

    The estimator this replaces assumed the charge column away: charging is the
    surplus, so after dark it is identically zero and the collinearity that
    defeats a day fit is absent. That premise is true of most houses and false
    of an ordinary one — a battery filled from the grid on a cheap overnight
    tariff — and such a house was refused outright.

    It does not have to be. The two directions are one parameter, so the charge
    hours carry information rather than contamination, and keeping them is what
    keeps the screens alive. Drop them instead and the retained hours satisfy
    ``load = discharge - residual`` identically: `_partner_share` returns -1 by
    construction and the import column's standard deviation is 0.000. That was
    measured on the reference installation, where the import share goes from
    undefined when filtered to -0.246 under the lock, and the load share from
    -0.993 — an identity — to +0.846, a measurement.

    The night correlation is why this works where a day fit cannot. Over
    daylight the charge column tracks generation at +0.48 to +0.70. After dark
    it tracks discharge at -0.53 to -0.26, because grid charging happens in the
    cheap hours when discharge is suppressed, so the two directions are not
    trading against each other but opposing.

    What is left to be careful about is not charge against discharge but load
    against discharge, which after dark runs 0.70 to 0.98. That is what
    `MIN_LOAD_INDEPENDENCE` bounds, and it is the only thing standing between
    this estimator and absorbing a consumption sensor reading ten per cent low.

    Returns ``(gamma, measured)``; ``gamma`` is ``None`` when the dark hours
    could not establish one, and ``measured`` is what they saw either way, so
    that "nothing could be established" keeps its numbers.
    """
    measured: dict[str, float] = {}
    samples = _dark_blocks(days, specs)
    if samples is None:
        return None, measured
    xs, cs, ys, loads, imports, dark_charge, dark_discharge = samples
    measured["dark_blocks"] = float(len(xs))
    measured["dark_charge_wh"] = dark_charge
    measured["dark_discharge_wh"] = dark_discharge

    line = _locked_slope(xs, cs, ys)
    if line is None:
        return None, measured
    gamma, half_width = line
    measured["dark_gamma"] = gamma
    measured["dark_gamma_half_width"] = half_width

    # The screens are asked of the column that was actually fitted, not of the
    # discharge column: on a charging house those are different vectors, and
    # the one the residual was explained by is the one a partner has to be
    # independent of.
    column = _locked_column(xs, cs, gamma)
    load_share = _partner_share(column, loads, ys)
    import_share = _partner_share(column, imports, ys)
    independence = _column_independence(column, loads)
    if load_share is not None:
        measured["dark_load_share"] = load_share
    if import_share is not None:
        measured["dark_import_share"] = import_share
    if independence is not None:
        measured["dark_load_independence"] = independence

    if half_width > MAX_GAMMA_HALF_WIDTH:
        return None, measured
    if not DC_BATTERY_GAMMA_WINDOW[0] <= gamma <= DC_BATTERY_GAMMA_WINDOW[1]:
        return None, measured
    if independence is None or independence < MIN_LOAD_INDEPENDENCE:
        return None, measured
    for share in (load_share, import_share):
        if share is None or abs(share) > MAX_LOAD_PROPORTIONAL_SHARE:
            return None, measured
    return gamma, measured


def _charge_confirms(
    days: tuple[DayResidual, ...],
    specs: tuple[ChannelSpec, ...],
    gamma: float,
    standby_w: float,
) -> bool:
    """Never subtract more charge-side loss than the day contains.

    Given gamma and the draw, everything in a day's total residual except the
    charge term is known, so what is left over divided by the day's charge is
    the coefficient the day implies. A median of per-day ratios, not a fitted
    column: generation is never a regressor here, so there is nothing for it to
    be collinear with.

    One-sided on purpose. A shortfall means the model is about to subtract
    energy the day has not got — a battery whose discharge is metered on the DC
    side and whose charge is not, which sits at +0.042 to +0.152 against a
    healthy +0.017. An *excess* means there is more unexplained energy than the
    lock accounts for, which is the fault engine's business and not a reason to
    distrust a gamma measured in the dark: an unmapped export path shows -0.60
    to -1.12 here, and refusing on that would silence the finding that names it.
    """
    discharge_keys = [spec.key for spec in specs if spec.role is Role.BATTERY_DISCHARGE]
    charge_keys = [spec.key for spec in specs if spec.role is Role.BATTERY_CHARGE]
    if not charge_keys:
        return True
    ratios: list[float] = []
    for day in days:
        residual = discharged = charged = hours = 0.0
        whole = True
        for bucket, raw in zip(day.buckets, day.r, strict=True):
            out = 0.0 if not discharge_keys else _role_total(bucket, discharge_keys)
            into = _role_total(bucket, charge_keys)
            if out is None or into is None:
                whole = False
                break
            residual += raw
            discharged += abs(out)
            charged += abs(into)
            hours += bucket.seconds / 3600.0
        if not whole or charged < MIN_DAY_CHARGE_WH:
            continue
        ratios.append((residual - gamma * discharged - standby_w * hours) / charged)
    if len(ratios) < 5:
        return True
    implied = median(ratios)
    if implied is None:
        return True
    return (gamma / (1.0 - gamma)) - implied <= MAX_CHARGE_SHORTFALL


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

    A house with no battery is the degenerate case of that same line rather than
    a house this cannot speak about: there is no throughput to vary with, so
    there is no slope, and the intercept is the whole of it. Requiring a
    discharge channel before looking meant a batteryless installation could
    never have its inverter's own draw absorbed — which a fortnight of December
    weather and ordinary meter noise is enough to turn into a permanent "still
    looking". Found by the clean corpus, at eight scenarios in three thousand.
    """
    samples = _night_samples(days, specs)
    if samples is None:
        return None, 0.0
    xs, ys, loads = samples

    line = _night_line(xs, ys)
    if line is None:
        return None, 0.0
    slope, intercept = line

    gamma = (
        slope
        if slope is not None and DC_MEASUREMENT_WINDOW[0] <= slope <= DC_MEASUREMENT_WINDOW[1]
        else None
    )
    return gamma, _plausible_standby(intercept, _load_proportional_share(xs, loads, ys))


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
    shape = _Shape(
        pv_keys=pv_keys,
        present=present,
        keys_for=keys_for,
        grid_keys=[key for role in _GRID_ROLES for key in keys_for.get(role, [])],
    )

    totals, residual, hours = _accumulate(days, shape, _ANY_HOUR)
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

    out.update(_split_by_the_grid(days, shape, wh))
    out.update(_split_by_provenance(days, shape, wh))
    out.update(_hours_with_no_supply(days, shape, wh))
    return out


def _split_by_provenance(
    days: tuple[DayResidual, ...],
    shape: _Shape,
    wh: Callable[[float], float],
) -> dict[str, float]:
    """The same ledger again, for hours we measured against hours we were told.

    An hourly mean cannot say whether the hour it describes was complete: an
    hour missing a third of its five-minute rows returns the average of the rest
    and is presented exactly like a whole one. So a power channel read that way
    can sit wrong while an energy counter beside it is exact. That produces a
    night that does not add up with nothing whatever wrong — and it is
    indistinguishable, in a total, from a sensor that genuinely under-reports.
    See `BucketSource` for why this is imputation rather than weighting.

    Our own integration is the control. It weights every reading by how long it
    stood, so if the deficit lives in the hours taken from statistics and the
    hours we integrated ourselves close, the fault is in the estimator rather
    than in the house. That is a question no amount of staring at one number
    can answer, and it answers itself given a few days of running.

    Emitted only once both kinds exist. A fresh installation is entirely
    backfilled, and a split with one empty half republishes the whole under a
    second name.
    """

    def measured(bucket: Bucket) -> bool:
        return all(
            bucket.source.get(key) is BucketSource.OWN_INTEGRAL
            for role in shape.present
            for key in shape.keys_for[role]
        )

    def told(bucket: Bucket) -> bool:
        return not measured(bucket)

    halves = {
        "night_measured": _accumulate(days, shape, measured),
        "night_from_statistics": _accumulate(days, shape, told),
    }
    if not all(hours for _, _, hours in halves.values()):
        return {}

    out: dict[str, float] = {}
    for prefix, (totals, residual, hours) in halves.items():
        out[f"{prefix}_hours"] = float(hours)
        out[f"{prefix}_residual_wh"] = wh(residual)
        for role, amount in totals.items():
            out[f"{prefix}_{role.key}_wh"] = wh(amount)
    return out


#: A channel below this in an hour has not meaningfully supplied anything.
SUPPLY_FLOOR_WH = 25.0

#: Consumption above this in an hour is real draw rather than a rounding edge.
DRAW_FLOOR_WH = 200.0


def _hours_with_no_supply(
    days: tuple[DayResidual, ...],
    shape: _Shape,
    wh: Callable[[float], float],
) -> dict[str, float]:
    """Night hours drawing real power with nothing measured supplying it.

    The one question a mis-scaled sensor and a blind one answer differently.
    Scaling a channel cannot rescue an hour where every source reads zero —
    there is nothing to multiply. So hours like these are proof that something
    stopped reporting rather than that something reports the wrong amount, and
    their absence is equally strong the other way.

    It matters because the two are indistinguishable in a total. A month whose
    night is short by 500 W looks the same whether every hour is short by 500 W
    or a fifth of the hours are short by everything, and those have different
    causes and different fixes.
    """
    sources = [role for role in shape.present if role.sign > 0]
    sinks = [role for role in shape.present if role.sign < 0]
    if not sources or not sinks:
        return {}

    hours = 0
    unexplained = 0.0
    for day in days:
        for bucket in day.buckets:
            if not _is_night(bucket, shape.pv_keys):
                continue
            supplied = sum(
                amount
                for role in sources
                if (amount := _role_total(bucket, shape.keys_for[role])) is not None
            )
            drawn = sum(
                amount
                for role in sinks
                if (amount := _role_total(bucket, shape.keys_for[role])) is not None
            )
            if supplied < SUPPLY_FLOOR_WH and drawn > DRAW_FLOOR_WH:
                hours += 1
                unexplained += drawn

    if not hours:
        return {"night_hours_with_no_supply": 0.0}
    return {
        "night_hours_with_no_supply": float(hours),
        "night_unsupplied_draw_wh": wh(unexplained),
    }


#: Roles that mean the grid was involved in an hour.
_GRID_ROLES = (Role.GRID_IMPORT, Role.GRID_EXPORT)

#: Below this in an hour, a grid channel has done nothing worth calling
#: involvement. Twenty-five watt-hours is the same "this is nothing" scale the
#: screens use, and far under any meter's own resolution over an hour.
GRID_QUIET_WH = 25.0


def _ANY_HOUR(_bucket: Bucket) -> bool:  # noqa: N802
    return True


@dataclass(frozen=True, slots=True)
class _Shape:
    """Which keys carry which role, worked out once and passed around.

    Exists so the three ledgers are demonstrably reading the same channels: a
    split whose halves counted different columns would still add up and still
    be wrong.
    """

    pv_keys: list[str]
    present: list[Role]
    keys_for: dict[Role, list[str]]
    grid_keys: list[str]


def _accumulate(
    days: tuple[DayResidual, ...],
    shape: _Shape,
    admit: Callable[[Bucket], bool],
) -> tuple[dict[Role, float], float, int]:
    """Role totals, residual and hour count over the night hours ``admit`` takes.

    One path for the whole night and for each half of the split, so the halves
    cannot drift from the total they are supposed to add up to.
    """
    totals = dict.fromkeys(shape.present, 0.0)
    residual = 0.0
    hours = 0
    for day in days:
        for bucket, raw in zip(day.buckets, day.r, strict=True):
            if not _is_night(bucket, shape.pv_keys) or not admit(bucket):
                continue
            amounts = {role: _role_total(bucket, shape.keys_for[role]) for role in shape.present}
            if any(amount is None for amount in amounts.values()):
                # Unreachable as the engine calls this: `build_days` admits a
                # bucket only when every balance channel reported, which is the
                # same set iterated here. Kept because the totals are only
                # comparable across roles if they cover identical hours, and
                # that is a contract with a caller rather than a property of
                # this function. Loosening `bucket_is_valid` makes it live.
                continue
            for role, amount in amounts.items():
                totals[role] += amount  # type: ignore[operator]
            residual += raw
            hours += 1
    return totals, residual, hours


def _split_by_the_grid(
    days: tuple[DayResidual, ...],
    shape: _Shape,
    wh: Callable[[float], float],
) -> dict[str, float]:
    """The same ledger again, for the hours the grid was in and the ones it was not.

    A whole-night total says how much is missing. It cannot say *when*, and the
    difference is the diagnosis. On the reference installation the night was
    short by 298 W on average — but in the hours the battery ran the house
    alone, generation, import and charging all exactly zero, the arithmetic
    closed to within 6% of discharge, which is what a DC-measured battery
    feeding an AC load should look like. The deficit lives entirely in the hours
    the grid was involved, and that points at one channel rather than at three.

    Finding that took diffing two diagnostics downloads by hand across a
    two-hour window. Nobody should have to.

    Emitted only when both halves have hours in them. With a grid that never
    rests, or one that never stirs, one half is the whole night and the other is
    empty — and republishing the same totals under a second name is how a reader
    comes to believe two numbers agreed when only one was ever computed.
    """
    if not shape.grid_keys:
        return {}

    def quiet(bucket: Bucket) -> bool:
        for key in shape.grid_keys:
            value = bucket.value(key)
            if value is None or abs(value) >= GRID_QUIET_WH:
                return False
        return True

    def active(bucket: Bucket) -> bool:
        return not quiet(bucket)

    out: dict[str, float] = {}
    halves = {
        "night_grid_quiet": _accumulate(days, shape, quiet),
        "night_grid_active": _accumulate(days, shape, active),
    }
    if not all(hours for _, _, hours in halves.values()):
        return {}

    for prefix, (totals, residual, hours) in halves.items():
        out[f"{prefix}_hours"] = float(hours)
        out[f"{prefix}_residual_wh"] = wh(residual)
        for role, amount in totals.items():
            out[f"{prefix}_{role.key}_wh"] = wh(amount)
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
    if not pv_keys:
        return None

    load_keys = [s.key for s in specs if s.role is Role.LOAD]

    xs: list[float] = []
    ys: list[float] = []
    loads: list[float] = []
    for day in days:
        for bucket, raw in zip(day.buckets, day.r, strict=True):
            if not _is_night(bucket, pv_keys):
                continue
            # No battery is not no data. A house without one still has night
            # hours, and they still measure a continuous draw — there is simply
            # no throughput for it to vary with, so every x is zero and the line
            # degenerates to its own intercept. `_night_line` handles that; what
            # must not happen is refusing to look, which is what returning
            # `None` here did to every batteryless installation.
            flow = 0.0 if not discharge_keys else _role_total(bucket, discharge_keys)
            if flow is None:
                continue
            xs.append(flow)
            ys.append(raw)
            if load_keys and (drawn := _role_total(bucket, load_keys)) is not None:
                loads.append(drawn)

    if len(xs) < MIN_STANDBY_SAMPLES:
        return None
    return xs, ys, loads


def _night_line(xs: list[float], ys: list[float]) -> tuple[float | None, float] | None:
    """Slope and intercept of the night residual against battery throughput.

    Returns ``(slope, intercept)``; the slope is ``None`` when there is none to
    find. That is not a failure — on a house with no battery every ``x`` is
    zero, and a line through points that all share an abscissa is exactly its
    own intercept. The conversion loss genuinely cannot be measured there
    because there is no conversion happening, while the continuous draw can be,
    and refusing both because one is absent is how a 25 W inverter supply went
    unabsorbed on every batteryless installation.

    Theil-Sen for the slope, the median for the degenerate intercept, so a
    handful of odd hours cannot drag either.
    """
    if not xs or all(x == 0.0 for x in xs):
        flat = median(ys)
        return None if flat is None else (None, flat)

    slope = theil_sen_slope(xs, ys)
    if slope is None:
        return None
    intercept = theil_sen_intercept(xs, ys, slope)
    return None if intercept is None else (slope, intercept)


def _load_proportional_share(
    discharge: list[float], loads: list[float], residual: list[float]
) -> float | None:
    """How much of the night residual moves with the consumption channel.

    The screen that decides whether a flat term is a flat term. A continuous
    draw is the same number every hour; a consumption sensor reading low by a
    fraction is a *share* of whatever the house happened to use, and the two
    are indistinguishable from the size of the residual alone. Fitted side by
    side they separate completely, because each lands in its own column.

    Least squares rather than Theil-Sen, and only as a screen: the value that
    gets accepted is still the robust one. This decides *whether* the term is a
    constant draw, not *how large* it is.
    """
    if not loads or len(loads) != len(residual):
        return None

    # A house with no battery has an all-zero discharge column, which is
    # collinear with nothing and makes the system singular — the solver refuses,
    # and refusing here would take the standby term away from exactly the
    # installations that most need it. There is no battery term to separate
    # there, so the column is simply not offered.
    columns = [loads, [1.0] * len(loads)]
    index = 0
    if any(value != 0.0 for value in discharge):
        columns.insert(0, discharge)
        index = 1

    fitted = least_squares(columns, residual)
    return None if fitted is None else fitted[index]


def _plausible_standby(intercept: float, load_share: float | None) -> float:
    """The intercept, if it can only be an inverter's own draw.

    Absolute bounds are not enough on their own: at 250 W of night load, a
    consumption sensor reading half produces a constant 125 W of residual,
    which sits comfortably inside any plausible idle range. What separates them
    is that a miscounted consumption channel scales with consumption and an
    inverter's own supply does not — which is a question about shape, and is
    now asked as one rather than inferred from magnitude.
    """
    if not (STANDBY_PLAUSIBLE_W[0] <= intercept <= STANDBY_PLAUSIBLE_W[1]):
        return 0.0
    if load_share is None or abs(load_share) > MAX_LOAD_PROPORTIONAL_SHARE:
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

    # The dark-hours estimator, for the same reason and on the same side of the
    # gate. It needs sixty two-hour blocks where `_night_samples` needs two
    # hundred hours, so it answers on houses this function used to leave
    # silent — and it shipped below the gate, which meant the reference
    # installation refused a gamma four different ways and published none of
    # them. Seven ways of declining are seven different problems.
    out.update(_dark_hours_battery(days, specs)[1])

    samples = _night_samples(days, specs)
    if samples is None:
        return out
    xs, ys, loads = samples
    out["night_hours"] = float(len(xs))

    line = _night_line(xs, ys)
    if line is not None:
        slope, intercept = line
        # Absent rather than zero when there is no battery to find a slope
        # against. Nought is a measurement, and this is the absence of one.
        if slope is not None:
            out["night_slope"] = slope
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

    line = _night_line(xs, ys)
    if line is None:
        return None
    _slope, intercept = line
    if intercept <= STANDBY_PLAUSIBLE_W[1]:
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
