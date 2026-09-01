"""Dropping the battery columns and fitting the loss model again.

``fit_loss_model`` refits without the battery whenever the battery pair is
refused, on the argument that columns fitted side by side share their errors: a
generation term standing next to a battery term that turned out to be explaining
something *other* than loss is carrying part of whatever that was.

That behaviour was documented in a comment and tested by nothing. The comment
claimed a house whose inverter really is DC-metered is "unaffected", which is the
safety property the whole guard depends on, and it had only ever been checked by
hand, once. This file makes both halves executable: the refit must kill a
generation term that only exists because something else is wrong, and must leave
a real one alone.

Written after the guard was accused of breaking a real installation and turned
out to be doing its job. That house fits +0.0403 with the battery columns and
-0.0742 without -- which is not the signature of a clean DC-metered inverter
(+0.0400 both ways) but of the contaminated one below (+0.0323 then -0.0631).
The accusation assumed the number it liked was the true one. These tests exist so
the next such accusation can be settled in a second rather than an afternoon.
"""

from __future__ import annotations

import pytest
from analysis.model import Answer, DeclaredTopology, LossModel
from analysis.residual import build_days
from analysis.topology import fit_loss_model, joint_loss_fit

from tests.synth import house
from tests.synth.adapt import specs_for, to_request

DECLARED = DeclaredTopology(
    has_battery=Answer.YES,
    grid_is_single_net_sensor=Answer.NO,
    load_covers_whole_house=Answer.YES,
)


def _fitted(series):
    specs = specs_for()
    days = build_days(to_request(series, declared=DECLARED).buckets, specs, LossModel())
    return days, specs


def _both_ways(series) -> tuple[float, float]:
    """``pv_dc`` as the joint fit sees it with the battery columns, and without."""
    days, specs = _fitted(series)
    with_battery = joint_loss_fit(days, specs) or {}
    without = joint_loss_fit(days, specs, with_battery=False) or {}
    return with_battery.get("pv_dc", 0.0), without.get("pv_dc", 0.0)


class TestTheSafetyProperty:
    """A real DC-metered inverter must survive the refit."""

    @pytest.mark.parametrize("seed", range(4))
    def test_a_dc_metered_inverter_reads_the_same_both_ways(self, seed: int) -> None:
        """The claim the guard rests on, now checked rather than asserted.

        A lossless battery puts nothing in its columns, so the pair is refused
        and the refit *does* run — which makes this the case that matters. If
        dropping the columns moved generation here, the guard would be trading
        one wrong answer for another.
        """
        series = house.measure_pv_dc(house.build(days=30, seed=seed), efficiency=0.96)

        with_battery, without = _both_ways(series)

        assert with_battery == pytest.approx(0.04, abs=0.002)
        assert without == pytest.approx(with_battery, abs=0.002)

    @pytest.mark.parametrize("seed", range(4))
    def test_and_the_shipped_model_keeps_it(self, seed: int) -> None:
        series = house.measure_pv_dc(house.build(days=30, seed=seed), efficiency=0.96)
        days, specs = _fitted(series)

        model = fit_loss_model(days, specs, None)

        assert model.established("pv_dc")
        assert model.pv_dc_gamma == pytest.approx(0.04, abs=0.002)


class TestWhatItExistsToKill:
    @pytest.mark.parametrize("seed", range(4))
    def test_a_generation_term_that_is_really_a_load_error_is_refused(self, seed: int) -> None:
        """The motivating case, from a real installation: a load CT reading 55%.

        The battery columns absorb the mis-scaled load, and generation picks up a
        spurious few percent beside them — small enough for the window to accept,
        which is exactly what makes it dangerous. Refit without them and it comes
        back negative, refused as it should be.
        """
        series = house.scale(
            house.measure_pv_dc(house.build(days=30, seed=seed), efficiency=0.96), "load", 0.55
        )

        with_battery, without = _both_ways(series)

        # Accepted on its own terms — this is the trap.
        assert 0.02 <= with_battery <= 0.15
        # And refused once the contaminated columns are gone.
        assert without < 0.02

    @pytest.mark.parametrize("seed", range(4))
    def test_so_nothing_is_subtracted_on_such_a_house(self, seed: int) -> None:
        series = house.scale(
            house.measure_pv_dc(house.build(days=30, seed=seed), efficiency=0.96), "load", 0.55
        )
        days, specs = _fitted(series)

        model = fit_loss_model(days, specs, None)

        assert model.fitted_terms == ()
        assert model.pv_dc_gamma == 0.0


class TestWhenTheRefitDoesNotRun:
    @pytest.mark.parametrize("seed", range(4))
    def test_an_accepted_battery_pair_keeps_its_columns(self, seed: int) -> None:
        """The refit is conditional on the pair being *refused*, and a
        DC-measured battery is not refused. Worth pinning, because the two fits
        genuinely disagree here (0.0400 against 0.0512) and the only reason that
        does not matter is that the second one is never consulted.
        """
        series = house.measure_battery_dc(
            house.measure_pv_dc(house.build(days=30, seed=seed), efficiency=0.96), efficiency=0.95
        )
        days, specs = _fitted(series)

        model = fit_loss_model(days, specs, None)

        assert model.established("battery_dc")
        assert model.established("pv_dc")
        assert model.pv_dc_gamma == pytest.approx(0.04, abs=0.003)
