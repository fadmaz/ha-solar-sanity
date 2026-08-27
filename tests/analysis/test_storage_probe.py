"""A battery nobody measures, which the engine could describe but never reach.

Two independent reasons it was unreachable, and fixing either alone changed
nothing:

* it had no residual model, so ``explained`` was zero on every input and it
  failed the first gate;
* it was gated behind the daily bands, which ask how far a day's residual runs
  in one direction — and a store borrows in the afternoon and repays at night,
  so its net is near zero however much energy is moving.

The second is the interesting one. The band was not too strict; it was measuring
the wrong quantity for this shape, and no threshold change would have helped.
"""

from __future__ import annotations

import pytest
from analysis.engine import analyse
from analysis.faults import Code
from analysis.hypotheses import looks_like_storage
from analysis.model import Answer, DeclaredTopology, LossModel
from analysis.residual import build_days

from tests.synth import house
from tests.synth.adapt import specs_for, to_request

#: Everything but the battery, on a house that has one.
UNMAPPED = ("pv", "grid_import", "grid_export", "load")

DECLARED = DeclaredTopology(has_battery=Answer.YES, load_covers_whole_house=Answer.YES)
UNSURE = DeclaredTopology(load_covers_whole_house=Answer.YES)


def _report(series, declared=DECLARED):
    return analyse(to_request(series, specs=specs_for(UNMAPPED), declared=declared))


def _self_stored(series):
    """The same house with no store at all, and the identity still closing.

    The control has to be a house that genuinely never stores anything, not one
    whose battery is merely unmeasured — that second thing is the fault case.
    """
    load = [
        base + charged - discharged
        for base, charged, discharged in zip(
            series.data["load"],
            series.data["battery_charge"],
            series.data["battery_discharge"],
            strict=True,
        )
    ]
    return series.copy_with(
        load=load,
        battery_charge=[0.0] * series.hours,
        battery_discharge=[0.0] * series.hours,
    )


class TestItIsNamed:
    @pytest.mark.parametrize("seed", range(10))
    def test_nothing_else_is_ever_named(self, seed: int) -> None:
        """Silence is allowed. Blaming a channel for the missing one is not."""
        report = _report(house.build(days=30, seed=seed))

        if report.finding is not None:
            assert report.finding.code == Code.MISSING_STORAGE, (
                f"seed={seed}: an unmeasured battery was attributed to "
                f"{report.finding.code} — {report.finding.headline}"
            )

    def test_it_is_usually_named(self) -> None:
        found = sum(
            1
            for seed in range(10)
            if (f := _report(house.build(days=30, seed=seed)).finding) is not None
            and f.code == Code.MISSING_STORAGE
        )

        assert found >= 8, f"only {found}/10 unmeasured batteries were described"

    def test_the_finding_renders(self) -> None:
        """The gate a hand-declared field list cannot provide.

        The copy asked for ``daily`` while the probe supplied ``daily_kwh``, so
        rendering raised the moment this hypothesis won — and it could not win,
        so nothing ever found out. Driving the engine to actually emit it is the
        only check that could not be fooled by a wrong declaration elsewhere.
        """
        report = _report(house.build(days=30, seed=0))

        assert report.finding is not None
        assert "{" not in report.finding.detail
        assert "kWh a day" in report.finding.detail
        assert report.finding.source_fix

    def test_it_offers_no_correction(self) -> None:
        """There is nothing to override — the fix is to map the sensors."""
        report = _report(house.build(days=30, seed=0))

        assert report.finding.offered_correction is None


class TestItIsNotInvented:
    """The control, at three noise levels."""

    @pytest.mark.parametrize("noise_pct", [0.0, 0.03, 0.05])
    @pytest.mark.parametrize("seed", range(6))
    def test_a_house_that_stores_nothing_is_silent(self, seed: int, noise_pct: float) -> None:
        series = _self_stored(house.build(days=30, seed=seed))
        series.assert_closes(tolerance=1e-6)
        if noise_pct:
            series = house.add_noise(series, noise_pct, seed=seed + 400)
        report = _report(series, declared=UNSURE)

        assert report.finding is None, (
            f"seed={seed} noise={noise_pct}: invented a battery "
            f"({report.finding.code}) on a house with none"
        )

    def test_a_mapped_battery_is_never_reported_as_missing(self) -> None:
        report = analyse(to_request(house.build(days=30, seed=1), declared=DECLARED))

        if report.finding is not None:
            assert report.finding.code != Code.MISSING_STORAGE


class TestOneDaysShape:
    """What counts as a day that traces a battery."""

    @staticmethod
    def _day(series, index=2):
        specs = specs_for(UNMAPPED)
        request = to_request(series, specs=specs, declared=DECLARED)
        return build_days(request.buckets, specs, LossModel())[index]

    def test_a_day_that_closes_is_storage(self) -> None:
        day = self._day(house.build(days=5, seed=2))

        assert looks_like_storage(day, capacity_wh=10000.0)

    def test_the_first_day_of_a_window_need_not_close(self) -> None:
        """And is correctly refused rather than special-cased.

        A window opens with the battery at whatever charge it happened to hold,
        so its first day ends somewhere else entirely. Nothing needs to know
        that: the day simply does not look like a store, and is not counted as
        one. There are thirty of them and one is cheap.
        """
        day = self._day(house.build(days=5, seed=2), index=0)

        assert not looks_like_storage(day, capacity_wh=10000.0)

    def test_a_capacity_nothing_like_the_swing_is_not(self) -> None:
        """A system does not have a different battery on Tuesday."""
        day = self._day(house.build(days=5, seed=2))

        assert not looks_like_storage(day, capacity_wh=500.0)
        assert not looks_like_storage(day, capacity_wh=500000.0)

    def test_a_day_that_does_not_come_back_is_not(self) -> None:
        """Energy that goes into a battery comes out again.

        A trace that ends far from where it started is something accumulating,
        which is a different and worse finding than a store.
        """
        series = _self_stored(house.build(days=5, seed=2))
        drifting = series.copy_with(
            load=[value - 400.0 for value in series.data["load"]],
        )
        day = self._day(drifting)

        assert not looks_like_storage(day, capacity_wh=10000.0)
