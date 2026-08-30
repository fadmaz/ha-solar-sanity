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
from analysis.engine import analyse
from analysis.model import Status

from tests.synth import corpus

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
