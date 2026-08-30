"""S1b — the clean corpus. A CI gate, not a test.

``test_clean.py`` asserts silence on one house wearing several hats. This
asserts it across the cross product of everything that changes what the engine
*does*: five topologies, five loss profiles, two seasons, three noise levels,
and sensors that drop out. Three thousand healthy installations.

Topology is the axis this was built for. ``house.build`` has always emitted the
same six channels, so ``check_closure`` returned ``CLOSED`` on every clean
scenario the project ever ran, and the open-boundary path — the one an
installation with no export meter is reported through, which is the reference
installation — had never been exercised by a clean-house test at all.

Two things are asserted, and the second is the one that was missing:

**No healthy house is ever blamed.** A single false "your sensor is broken" on a
working installation loses that owner permanently.

**Every healthy house gets an answer.** Silence and a verdict are not the same
thing. The old gate accepted ``INVESTIGATING`` as quiet, which let a healthy
house be told *"the numbers move around but not consistently enough to name"*
forever and counted that as a pass — while the numbers were not, in fact, moving
around. That is the failure this project exists to fix, so a corpus that could
not see it was guarding the wrong property.
"""

from __future__ import annotations

import pytest
from analysis import topology
from analysis.engine import analyse
from analysis.linalg import least_squares
from analysis.model import Answer, DeclaredTopology, LossModel, Role, Status
from analysis.residual import build_days

from tests.synth import corpus, house
from tests.synth.adapt import to_request

FAST = tuple(corpus.cases(corpus.FAST_SEEDS))
FULL = tuple(corpus.cases(corpus.FULL_SEEDS))


def _label(case: corpus.Case) -> str:
    return case.label


def _assert_healthy(case: corpus.Case) -> None:
    report = analyse(corpus.build(case))

    assert report.finding is None, (
        f"{case.label}: a healthy installation was blamed — "
        f"{report.finding.code}: {report.finding.headline}"
    )
    assert report.identity_fails is False, (
        f"{case.label}: healthy house marked as failing its own arithmetic "
        f"(status {report.status.value}, residual "
        f"{report.residual.median_daily_abs_pct:.2f}%)"
    )
    assert report.status is Status.OK, (
        f"{case.label}: no verdict — status {report.status.value}, residual "
        f"{report.residual.median_daily_abs_pct:.2f}%, reason {report.reason!r}"
    )


@pytest.mark.parametrize("topology", corpus.TOPOLOGIES, ids=lambda t: t.name)
def test_every_topology_describes_a_house_that_can_exist(topology: corpus.Topology) -> None:
    """Energy is conserved, hour by hour, before any loss is applied.

    Asserting silence about a house that cannot exist proves nothing about the
    engine, and this project has already shipped six scenarios that did exactly
    that: ``split_arrays`` replaced the generation curve with a smoothed copy of
    itself and adjusted nothing else, leaving up to 1,121 Wh an hour
    unaccounted for. Every generator is checked rather than trusted.
    """
    worst = max(
        corpus.balance_error(case)
        for case in corpus.cases(corpus.FAST_SEEDS)
        if case.topology == topology.name
    )

    assert worst < 1e-6, f"{topology.name}: identity misses by up to {worst:.3f} Wh an hour"


@pytest.mark.parametrize("case", FAST, ids=_label)
def test_a_healthy_house_is_answered_and_never_blamed(case: corpus.Case) -> None:
    """The gate that runs on every pull request.

    Two seeds rather than ten, which still covers every combination of every
    axis — the seeds vary weather and usage, not the shape of the installation.
    """
    _assert_healthy(case)


@pytest.mark.slow
@pytest.mark.parametrize("case", FULL, ids=_label)
def test_the_full_corpus_is_answered_and_never_blamed(case: corpus.Case) -> None:
    """The same thing at ten seeds, on main and the weekly schedule.

    Worth its three minutes because weather is the axis a false positive hides
    in: a threshold that is wrong for one spell of cloud in ten shows up here and
    nowhere else.
    """
    _assert_healthy(case)


DECLARED = DeclaredTopology(
    has_battery=Answer.YES,
    grid_is_single_net_sensor=Answer.NO,
    load_covers_whole_house=Answer.YES,
)


def _dc_battery(efficiency: float, *, seed: int, kwp: float = 6.0):
    return analyse(
        to_request(
            house.measure_battery_dc(house.build(days=30, seed=seed, kwp=kwp), efficiency),
            declared=DECLARED,
        )
    )


class TestTheBoundaryOfWhatCanBeAbsorbed:
    """Where a healthy house stops getting an answer at all.

    The corpus above deliberately stays inside the region the loss model can
    absorb, because a gate has to be green to be worth anything. This class is
    the other half of that honesty: it records, with numbers, where the engine
    gives up on an installation that has nothing wrong with it.

    A battery metered on its DC side is an ordinary hybrid, and its round-trip
    efficiency is an ordinary property of the hardware. Somewhere below 0.91 the
    verdict stops arriving — not gradually, but at a cliff — and the owner is
    told "the numbers move around but not consistently enough to name" for as
    long as the installation stands. These tests exist so that the day someone
    fixes it, the failure tells them the boundary moved rather than letting it
    move unnoticed.
    """

    @pytest.mark.parametrize("efficiency", [0.98, 0.96, 0.94, 0.92, 0.91])
    @pytest.mark.parametrize("seed", range(4))
    def test_down_to_here_the_loss_is_absorbed(self, efficiency: float, seed: int) -> None:
        report = _dc_battery(efficiency, seed=seed)

        assert report.status is Status.OK, (
            f"eff={efficiency} seed={seed}: {report.status.value} at "
            f"{report.residual.median_daily_abs_pct:.2f}%"
        )

    @pytest.mark.parametrize("efficiency", [0.90, 0.88, 0.85])
    @pytest.mark.parametrize("seed", range(4))
    def test_below_it_a_healthy_house_gets_no_verdict(self, efficiency: float, seed: int) -> None:
        """A known limitation, pinned. **This failing is good news.**

        If this test starts failing because the status became ``OK``, the
        boundary has been fixed and this expectation should move down with it.
        What must never happen is the status becoming ``FAULT_FOUND``: that
        would be the engine blaming a working installation for the efficiency
        of its own battery, which is the one outcome worse than silence.
        """
        report = _dc_battery(efficiency, seed=seed)

        assert report.finding is None, (
            f"eff={efficiency} seed={seed}: a healthy DC battery was blamed — {report.finding.code}"
        )
        assert report.status is Status.INVESTIGATING, (
            f"eff={efficiency} seed={seed}: expected the known dead zone, got "
            f"{report.status.value} — if this is now OK, the boundary moved and "
            f"this test should say so"
        )

    def test_the_estimator_cannot_produce_the_value_its_own_window_accepts(self) -> None:
        """The mechanism, so the fix is obvious to whoever picks this up.

        A battery metered on the DC side does not lose the same fraction in both
        directions. Working it through, with ``e`` the round-trip efficiency and
        the measured quantities on the right::

            residual = (1 - e) * discharge + ((1 - e) / e) * charge

        Two different coefficients, and the charge one is always the larger.
        ``joint_loss_fit`` fits a single coefficient against ``|charge| +
        |discharge|``, so what it recovers is a blend of the two — necessarily
        above the smaller.

        At ``e = 0.90`` the discharge coefficient is exactly 0.1000, which is
        exactly ``DC_MEASUREMENT_WINDOW``'s upper bound and therefore acceptable.
        The blend is 0.1057, which is not. So the model rejects a loss its own
        window admits, subtracts nothing at all, and a 5.5% residual is left for
        the bands to worry about.

        Fitting the two columns separately recovers both exactly — checked here,
        not asserted — and their ratio is a physical signature a fault would not
        satisfy: ``charge_coefficient == discharge_coefficient / (1 - discharge_coefficient)``.
        """
        efficiency = 0.90
        request = to_request(
            house.measure_battery_dc(house.build(days=30, seed=0), efficiency),
            declared=DECLARED,
        )
        days = build_days(request.buckets, request.specs, LossModel(), request.utc_offset_hours)

        charge_keys = [s.key for s in request.specs if s.role is Role.BATTERY_CHARGE]
        discharge_keys = [s.key for s in request.specs if s.role is Role.BATTERY_DISCHARGE]
        charge: list[float] = []
        discharge: list[float] = []
        together: list[float] = []
        flat: list[float] = []
        target: list[float] = []
        for day in days:
            for bucket, raw in zip(day.buckets, day.r, strict=True):
                into = topology._role_total(bucket, charge_keys)
                out = topology._role_total(bucket, discharge_keys)
                if into is None or out is None:
                    continue
                charge.append(abs(into))
                discharge.append(abs(out))
                together.append(abs(into) + abs(out))
                flat.append(1.0)
                target.append(raw)

        separately = least_squares([discharge, charge, flat], target)
        blended = least_squares([together, flat], target)
        assert separately is not None and blended is not None

        # Both true coefficients, recovered exactly.
        assert separately[0] == pytest.approx(1 - efficiency, abs=1e-3)
        assert separately[1] == pytest.approx((1 - efficiency) / efficiency, abs=1e-3)

        # The blend sits above the window that would have accepted the real one.
        low, high = topology.DC_MEASUREMENT_WINDOW
        assert low <= separately[0] <= high, "the physical coefficient is acceptable"
        assert blended[0] > high, "yet the blend of it with the charge side is not"

        # Which is why nothing is subtracted, and the house is left unexplained.
        assert topology.fit_loss_model(days, request.specs, None).fitted_terms == ()
