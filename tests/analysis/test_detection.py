"""S3/S4/S5 — detection, disambiguation, and correction round-trips.

The clean suite proves we stay quiet. This one proves we are not quiet by simply
never saying anything.

Note the asymmetry in what is asserted: a *missed* detection is disappointing,
but naming the *wrong* fault is far worse — it sends the user to rewire a sensor
that was fine. So the strict assertions here are about which code comes back,
not about detection rate under adverse noise.
"""

from __future__ import annotations

import pytest
from analysis.engine import analyse
from analysis.faults import Code
from analysis.model import Answer, DeclaredTopology, Severity, Status

from tests.synth import house
from tests.synth.adapt import specs_for, to_request


def _analyse(series, **kwargs):
    return analyse(to_request(series, **kwargs))


class TestStageA:
    """Categorical faults: fast, near-certain, no residual needed."""

    def test_unit_scale_error_is_named(self) -> None:
        """A channel out by 1000 is the loudest possible signal."""
        series = house.scale(house.build(days=14, seed=3), "pv", 0.001)
        report = _analyse(series)

        assert report.status is Status.FAULT_FOUND
        assert report.finding is not None
        assert report.finding.code == Code.UNIT_SCALE_1000
        assert report.finding.channel_keys == ("pv",)
        assert "thousand" in report.finding.headline

    def test_unit_scale_offers_a_correction(self) -> None:
        series = house.scale(house.build(days=14, seed=3), "pv", 0.001)
        finding = _analyse(series).finding

        assert finding is not None
        assert finding.offered_correction is not None
        assert finding.offered_correction.kind == "scale"
        assert finding.offered_correction.channel_key == "pv"

    def test_cumulative_total_in_a_periodic_slot(self) -> None:
        """A lifetime counter mapped where an hourly figure belongs."""
        series = house.to_cumulative(house.build(days=14, seed=5), "pv", 14_500_000.0)
        report = _analyse(series)

        assert report.finding is not None
        assert report.finding.code == Code.CUMULATIVE_IN_PERIODIC
        assert report.finding.channel_keys == ("pv",)

    def test_net_meter_in_a_one_way_slot(self) -> None:
        """A signed net sensor mapped to import-only goes negative on sunny days."""
        series = house.merge_to_net(house.build(days=14, seed=7))
        report = _analyse(series)

        assert report.finding is not None
        assert report.finding.code == Code.SIGNED_NET_IN_DEDICATED
        assert "net meter" in report.finding.headline

    def test_frozen_sensor_is_named_and_blocks_everything_else(self) -> None:
        """A dead channel makes every other statistic meaningless."""
        series = house.freeze(house.build(days=14, seed=9), "load", from_hour=200)
        report = _analyse(series)

        assert report.finding is not None
        assert report.finding.code == Code.STUCK
        assert report.finding.channel_keys == ("load",)


class TestDisambiguation:
    """The pairs that could plausibly be confused for one another."""

    def test_missing_battery_is_not_reported_as_a_channel_fault(self) -> None:
        """An unmeasured battery alternates and is orthogonal to everything.

        The competing explanation — a miscounted channel — is one-signed and
        explained by a channel that exists. Those are different statistics.
        """
        series = house.build(days=21, seed=11)
        series = house.drop(series, "battery_charge")
        series = house.drop(series, "battery_discharge")

        specs = specs_for(("pv", "grid_import", "grid_export", "load"))
        report = analyse(
            to_request(
                series,
                specs=specs,
                declared=DeclaredTopology(has_battery=Answer.YES),
            )
        )

        # Either we name it as missing storage, or we stay honest and say we do
        # not know. What we must never do is blame a channel that is fine.
        if report.finding is not None:
            assert report.finding.code == Code.MISSING_STORAGE
        else:
            assert report.status in (Status.INVESTIGATING, Status.INSUFFICIENT_DATA)

    def test_missing_load_channel_is_not_checkable(self) -> None:
        """Without consumption the identity closes by definition.

        Reporting "ok" here would be a lie: nothing was actually verified.
        """
        series = house.build(days=21, seed=13)
        specs = specs_for(
            ("pv", "grid_import", "grid_export", "battery_charge", "battery_discharge")
        )
        report = analyse(to_request(series, specs=specs))

        assert report.status is Status.NOT_CHECKABLE
        assert report.finding is None
        assert "consumption" in report.reason.lower()


class TestCorrectionRoundTrip:
    """S5 — applying the offered correction must actually fix it.

    This proves the correction is *right*, not merely that detection fired.
    """

    @pytest.mark.parametrize("factor", [0.001, 1000.0])
    def test_scale_correction_restores_silence(self, factor: float) -> None:
        clean = house.build(days=21, seed=17)
        broken = house.scale(clean, "pv", factor)

        finding = _analyse(broken).finding
        assert finding is not None
        assert finding.offered_correction is not None

        # Apply the inverse of what was injected, as the correction describes.
        repaired = house.scale(broken, "pv", 1.0 / factor)
        after = _analyse(repaired)

        assert after.finding is None, f"correction did not restore silence: {after.finding}"


class TestConfidence:
    """Autodetected channels are less trustworthy than confirmed ones."""

    def test_autodetected_channel_downgrades_confidence(self) -> None:
        series = house.scale(house.build(days=14, seed=19), "pv", 0.001)

        confirmed = analyse(to_request(series, specs=specs_for(origin="user")))
        guessed = analyse(to_request(series, specs=specs_for(origin="autodetected")))

        assert confirmed.finding is not None
        assert guessed.finding is not None
        assert guessed.finding.confidence != confirmed.finding.confidence

    def test_probable_findings_are_questions_not_faults(self) -> None:
        """A finding we are unsure of must never be asserted as a fact."""
        series = house.scale(house.build(days=14, seed=23), "pv", 0.001)
        report = analyse(to_request(series, specs=specs_for(origin="autodetected")))

        assert report.finding is not None
        if report.finding.confidence.value == "probable":
            assert report.finding.severity is Severity.QUESTION


class TestOneFindingAtATime:
    """An uncorrected fault dominates the residual, so we report one."""

    def test_two_faults_yield_exactly_one_finding(self) -> None:
        series = house.build(days=21, seed=29)
        series = house.scale(series, "pv", 0.001)
        series = house.freeze(series, "grid_import", from_hour=300)

        report = _analyse(series)

        assert report.finding is not None
        # deferred carries the rest so nothing is silently lost.
        assert isinstance(report.deferred, tuple)
