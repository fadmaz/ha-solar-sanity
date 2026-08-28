"""S5 — a correction must actually correct.

Detection firing proves only that something is wrong. It says nothing about
whether the remedy offered alongside it is the *right* remedy. This suite closes
that loop: break a house, let the engine name the fault and offer its
correction, apply exactly what it offered, and analyse again. The second pass
has to come back clean.

Without this, a correction can be plausible, accepted, recorded, displayed in
``corrections_active`` — and wrong, or inert. That already happened once: the
grid screen offered ``reinterpret_as_net``, nothing implemented it, and every
individual piece looked correct in isolation.

Why it matters more than the detection tests: a correction is the only place
this integration changes a number on the user's behalf. Everything else it does
is read and report.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from analysis.engine import analyse
from analysis.faults import Code
from analysis.model import Answer, Correction, DeclaredTopology, Status

from tests.synth import house
from tests.synth.adapt import to_request

DECLARED = DeclaredTopology(
    has_battery=Answer.YES,
    grid_is_single_net_sensor=Answer.NO,
    load_covers_whole_house=Answer.YES,
)

DAYS = 21


def _broken(kind: str, seed: int) -> house.Series:
    """A house with exactly one categorical fault in it."""
    clean = house.build(days=DAYS, seed=seed)
    if kind == "sign_flip":
        return house.invert(clean, "battery_charge")
    if kind == "sign_flip_pv":
        return house.invert(clean, "pv")
    if kind == "scale_up":
        # The sensor publishes kW where the rest of the system publishes W.
        return house.scale(clean, "grid_import", 0.001)
    if kind == "scale_down":
        return house.scale(clean, "grid_import", 1000.0)
    raise AssertionError(f"unknown corruption {kind!r}")


def _apply(series: house.Series, correction: Correction) -> house.Series:
    """Do to the data what the engine promises the correction does.

    Deliberately reimplemented here from the *description* of the correction
    rather than by calling the engine's own applier. A test that reuses the
    implementation it is checking proves only that the code agrees with itself.
    """
    key = correction.channel_key
    values = list(series.data[key])
    if correction.kind == "sign_flip":
        values = [-v for v in values]
    elif correction.kind == "scale":
        assert correction.factor is not None, "a scale correction with no factor"
        values = [v * correction.factor for v in values]
    elif correction.kind == "drop_channel":
        values = [0.0] * len(values)
    else:
        raise AssertionError(f"no round trip written for {correction.kind!r}")
    return series.copy_with(**{key: values})


#: Every corruption whose fault the engine both names *and* offers a remedy
#: for. ``drop_channel`` is deliberately absent: the double-count it belongs
#: to is not currently reachable, because two channels carrying the same
#: energy produce two identically scored hypotheses and the engine will not
#: guess which of the pair to blame. That is the right instinct and the wrong
#: outcome — the code written to name the pair, DUPLICATE_CHANNEL, is emitted
#: by nothing at all. Tracked separately; adding it here now would assert a
#: remedy for a fault that never fires.
@pytest.mark.parametrize("kind", ["sign_flip", "sign_flip_pv", "scale_up", "scale_down"])
@pytest.mark.parametrize("seed", range(3))
class TestTheRemedyIsRight:
    def test_the_fault_is_found_and_a_correction_offered(self, kind: str, seed: int) -> None:
        report = analyse(to_request(_broken(kind, seed), declared=DECLARED))

        assert report.finding is not None, f"{kind}/{seed}: nothing found to correct"
        assert report.finding.offered_correction is not None, (
            f"{kind}/{seed}: {report.finding.code} named a fault but offered no way forward"
        )

    def test_applying_it_closes_the_balance(self, kind: str, seed: int) -> None:
        """The whole point. A correction that does not restore the identity is
        not a correction, however confidently it was offered."""
        broken = _broken(kind, seed)
        report = analyse(to_request(broken, declared=DECLARED))
        correction = report.finding.offered_correction

        after = analyse(to_request(_apply(broken, correction), declared=DECLARED))

        assert after.finding is None, (
            f"{kind}/{seed}: applying the offered correction left {after.finding.code}"
        )
        assert after.status is Status.OK, f"{kind}/{seed}: ended at {after.status}"

    def test_the_engine_applies_it_the_same_way(self, kind: str, seed: int) -> None:
        """Our reading of the correction and the engine's must agree.

        The two paths are independent: this suite transforms the raw series, the
        engine transforms its own buckets. If they ever diverge, one of them is
        lying to the user about what accepting the fix will do.
        """
        broken = _broken(kind, seed)
        request = to_request(broken, declared=DECLARED)
        correction = analyse(request).finding.offered_correction

        by_hand = analyse(to_request(_apply(broken, correction), declared=DECLARED))
        by_engine = analyse(replace(request, active_corrections=(correction,)))

        assert by_engine.finding is None, (
            f"{kind}/{seed}: the engine's own applier left {by_engine.finding.code}"
        )
        assert by_engine.status is by_hand.status


class TestACorrectionIsNotOfferedForEverything:
    """Statistical inferences get no one-click fix.

    Half-coverage is a real, nameable fault, and doubling the channel would even
    close the balance — but only if the guess about *why* it reads half is
    right. Getting that wrong writes a plausible number over a real measurement,
    which is worse than the fault it replaces.
    """

    def test_partial_coverage_names_the_fault_without_offering_a_fix(self) -> None:
        broken = house.halve(house.build(days=DAYS, seed=1), "load")
        report = analyse(to_request(broken, declared=DECLARED))

        assert report.finding is not None
        assert report.finding.code == Code.PARTIAL_COVERAGE
        assert report.finding.offered_correction is None
        assert report.finding.source_fix, "no fault may be raised without something to do"


class TestAnUnknownCorrectionCannotBeSilent:
    """The shape of the defect this suite exists because of.

    A correction whose kind nothing acts on leaves every bucket untouched. The
    balance does not move, the finding returns the next night, and the user has
    been told it was applied. Asserting the *effect* here — rather than that a
    branch was taken — is what makes this catch an inert kind rather than a
    misspelt one.
    """

    def test_a_correction_that_changes_nothing_would_be_visible(self) -> None:
        broken = _broken("sign_flip", 0)
        request = to_request(broken, declared=DECLARED)
        real = analyse(request).finding.offered_correction

        inert = Correction(channel_key=real.channel_key, kind="not_a_real_kind")
        after = analyse(replace(request, active_corrections=(inert,)))

        assert after.finding is not None, "an inert correction appeared to fix the house"
        assert after.finding.code == analyse(request).finding.code
