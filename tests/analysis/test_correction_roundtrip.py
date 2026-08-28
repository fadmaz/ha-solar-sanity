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
from analysis.model import Answer, Correction, DeclaredTopology, Role, Status

from tests.synth import house
from tests.synth.adapt import extra_spec, specs_for, to_request

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
    if kind == "drop_channel":
        # A second generation sensor a few per cent off the first. Not close
        # enough to be indistinguishable from it, which would be the pair
        # finding and would rightly offer nothing.
        return clean.copy_with(pv_b=[value * 0.92 for value in clean.data["pv"]])
    raise AssertionError(f"unknown corruption {kind!r}")


def _specs(kind: str):
    """The channels this corruption needs mapped.

    Only the double-count adds one: it is the presence of a *second* generation
    sensor that makes the first redundant, and a channel nobody has mapped
    cannot be counted twice.
    """
    if kind == "drop_channel":
        return (*specs_for(), extra_spec("pv_b", Role.PV, "Solar B"))
    return specs_for()


def _request(kind: str, series: house.Series):
    return to_request(series, specs=_specs(kind), declared=DECLARED)


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


#: Every corruption whose fault the engine both names *and* offers a remedy for.
#:
#: ``drop_channel`` was absent for a long time and is not any more. Two channels
#: carrying the *same* energy score identically, the engine will not guess which
#: to blame, and it names the pair instead — correctly, and with no correction,
#: because dropping either would close the balance. But a second sensor reading
#: a few per cent off its partner is not that case: exactly one of them settles
#: the house, the engine knows which, and the remedy it offers can be round
#: tripped like any other.
@pytest.mark.parametrize(
    "kind", ["sign_flip", "sign_flip_pv", "scale_up", "scale_down", "drop_channel"]
)
@pytest.mark.parametrize("seed", range(3))
class TestTheRemedyIsRight:
    def test_the_fault_is_found_and_a_correction_offered(self, kind: str, seed: int) -> None:
        report = analyse(_request(kind, _broken(kind, seed)))

        assert report.finding is not None, f"{kind}/{seed}: nothing found to correct"
        assert report.finding.offered_correction is not None, (
            f"{kind}/{seed}: {report.finding.code} named a fault but offered no way forward"
        )

    def test_applying_it_closes_the_balance(self, kind: str, seed: int) -> None:
        """The whole point. A correction that does not restore the identity is
        not a correction, however confidently it was offered."""
        broken = _broken(kind, seed)
        report = analyse(_request(kind, broken))
        correction = report.finding.offered_correction

        after = analyse(_request(kind, _apply(broken, correction)))

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
        request = _request(kind, broken)
        correction = analyse(request).finding.offered_correction

        by_hand = analyse(_request(kind, _apply(broken, correction)))
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


class TestACorrectionThatOutlivesItsFault:
    """The failure mode the plan named and nothing implemented.

    A correction is an override on our own copy of a channel, and the user is
    told it is applied "so I can keep checking" — never that anything is fixed.
    So the sensor usually does get fixed eventually: the integration ships a
    polarity option, or a template gets rewritten. At that moment the override
    stops compensating for a fault and becomes one.

    Before this, the engine's answer to that was not a missed diagnosis but a
    wrong instruction. It reported "Battery charging is reporting backwards"
    about a sensor that was now correct, advised wrapping it in a template that
    negates it, and offered a *second* sign flip on top of the one already
    applied. `Code.CORRECTION_NOW_HARMFUL` had finished copy and was emitted by
    nothing at all.
    """

    FLIP = Correction(channel_key="battery_charge", kind="sign_flip")

    @staticmethod
    def _with(series: house.Series, corrections: tuple[Correction, ...]):
        request = to_request(series, declared=DECLARED)
        return analyse(replace(request, active_corrections=corrections))

    def test_while_the_sensor_is_still_broken_it_earns_its_keep(self) -> None:
        broken = house.invert(house.build(days=DAYS, seed=0), "battery_charge")

        report = self._with(broken, (self.FLIP,))

        assert report.status is Status.OK
        assert report.stale_corrections == ()

    def test_once_the_sensor_is_fixed_the_correction_is_named(self) -> None:
        report = self._with(house.build(days=DAYS, seed=0), (self.FLIP,))

        assert report.stale_corrections == ("battery_charge",)
        assert report.finding.code == Code.CORRECTION_NOW_HARMFUL
        assert "Battery charging" in report.finding.headline

    def test_it_does_not_offer_to_apply_yet_another_one(self) -> None:
        """How this went wrong in the first place. The remedy is to remove."""
        report = self._with(house.build(days=DAYS, seed=0), (self.FLIP,))

        assert report.finding.offered_correction is None
        assert "Remove" in report.finding.source_fix

    def test_it_is_asked_before_anything_else_is_blamed(self) -> None:
        """Every other stage reads buckets the correction has already altered.

        The screens run first and return first, so a check placed after them
        never ran at all — the engine reported the screen's verdict on data its
        own override had corrupted.
        """
        report = self._with(house.build(days=DAYS, seed=0), (self.FLIP,))

        assert report.finding.code != Code.CHANNEL_NEVER_POSITIVE

    def test_an_unrelated_fault_is_not_blamed_on_the_correction(self) -> None:
        """The guard. Removing it must actually be what makes things right.

        Here the flip is doing its job and something else is genuinely wrong, so
        dropping it would not make this house ok — and the real fault has to
        survive to be reported.
        """
        broken = house.invert(house.build(days=DAYS, seed=0), "battery_charge")
        also_stuck = house.freeze(broken, "load", from_hour=200)

        report = self._with(also_stuck, (self.FLIP,))

        assert report.stale_corrections == ()
        assert report.finding.code == Code.STUCK

    def test_no_corrections_means_nothing_to_say(self) -> None:
        report = self._with(house.build(days=DAYS, seed=0), ())

        assert report.stale_corrections == ()
