"""A verdict for a house whose totals balance and whose hours do not.

"The numbers move around but not consistently enough to name" was being said,
forever, to an installation whose energy adds up to within 1.4% over a month.
That is not a hedge, it is wrong — and it was never going to stop, because
nothing about the house was going to change.

The property that makes it answerable is arithmetic rather than statistical. A
sensor reading the wrong amount is wrong in the same direction every hour, so a
day is out by the sum of its hours: the error accumulates and *cannot* cancel.
Measured across every fault this project can produce, the share surviving
aggregation to a day is **1.000** — to three decimals, on every seed. Anything
that cancels is therefore not that.

Three guards, because the first one alone is not enough:

``MAX_SURVIVING_SHARE``
    Most of the hourly discrepancy has to disappear by the end of the day.
``MAX_WINDOW_IMBALANCE``
    And what is left has to be small. Half a fault and half a timing artefact is
    not something to reassure anybody about.
``MAX_CHANNEL_RESEMBLANCE``
    And the residual must not simply *be* one of the channels. This is the guard
    that took looking for: a battery whose charge **and** discharge are both
    mis-scaled cancels better than the reference installation does, because the
    same energy comes back out as went in.
"""

from __future__ import annotations

import pytest
from analysis.engine import (
    MAX_CHANNEL_RESEMBLANCE,
    MAX_SURVIVING_SHARE,
    MAX_WINDOW_IMBALANCE,
    _adds_up_over_the_day,
    _daily_cancellation,
    _resembles_one_channel,
    analyse,
)
from analysis.model import Answer, DeclaredTopology, LossModel, Status
from analysis.residual import build_days
from analysis.topology import fit_loss_model

from tests.synth import house
from tests.synth.adapt import specs_for, to_request

DECLARED = DeclaredTopology(
    has_battery=Answer.YES,
    grid_is_single_net_sensor=Answer.NO,
    load_covers_whole_house=Answer.YES,
)

VERDICT = "Your energy adds up"


def _days(series):
    request = to_request(series, declared=DECLARED)
    provisional = build_days(request.buckets, request.specs, LossModel(), request.utc_offset_hours)
    loss = fit_loss_model(provisional, request.specs, None)
    return build_days(request.buckets, request.specs, loss, request.utc_offset_hours), request


def _mis_scaled_battery(series, factor: float):
    """Both directions wrong by the same factor.

    The adversary. What goes in comes back out, so every day nets to nearly
    nothing however wrong the readings are — which defeats any test built on
    cancellation alone.
    """
    return series.copy_with(
        battery_charge=[v * factor for v in series.data["battery_charge"]],
        battery_discharge=[v * factor for v in series.data["battery_discharge"]],
    )


class TestAFaultCannotCancel:
    """The arithmetic the whole verdict rests on."""

    @pytest.mark.parametrize(
        "corrupt",
        [
            pytest.param(lambda s: house.halve(s, "load"), id="load reads half"),
            pytest.param(lambda s: house.halve(s, "pv"), id="generation reads half"),
            pytest.param(lambda s: house.scale(s, "pv", 2.0), id="generation doubled"),
            pytest.param(lambda s: house.invert(s, "battery_discharge"), id="discharge inverted"),
            pytest.param(lambda s: house.scale(s, "grid_import", 1000.0), id="import in kilowatts"),
            pytest.param(lambda s: house.measure_pv_dc(s, 0.80), id="unabsorbed conversion loss"),
        ],
    )
    @pytest.mark.parametrize("seed", range(3))
    def test_every_fault_survives_the_day_entirely(self, corrupt, seed: int) -> None:
        """One-signed by construction, so the day is the sum of its hours."""
        days, _ = _days(corrupt(house.build(days=30, seed=seed)))

        surviving, _net, _gross = _daily_cancellation(days)

        assert surviving == pytest.approx(1.0, abs=0.01), surviving
        assert surviving > MAX_SURVIVING_SHARE


class TestTheBatteryThatCancelsBetterThanTheRealCase:
    """The trap, and the guard that closes it.

    A mis-scaled battery pair is a real fault that cancels *better* than the
    installation this verdict was built for — 0.05 against 0.14. No amount of
    cancellation can separate them, so something else has to.
    """

    @pytest.mark.parametrize("factor", [2.0, 0.5])
    @pytest.mark.parametrize("seed", range(3))
    def test_it_does_cancel_which_is_why_cancellation_is_not_enough(
        self, factor: float, seed: int
    ) -> None:
        days, _ = _days(_mis_scaled_battery(house.build(days=30, seed=seed), factor))

        surviving, _net, _gross = _daily_cancellation(days)

        assert surviving < MAX_SURVIVING_SHARE, (
            "if this ever stops cancelling, the guard below is no longer load-bearing "
            "and this whole class can go"
        )

    @pytest.mark.parametrize("factor", [2.0, 0.5])
    @pytest.mark.parametrize("seed", range(3))
    def test_the_residual_is_the_battery_and_is_recognised_as_such(
        self, factor: float, seed: int
    ) -> None:
        days, request = _days(_mis_scaled_battery(house.build(days=30, seed=seed), factor))

        assert _resembles_one_channel(days, request.specs)

    @pytest.mark.parametrize("factor", [2.0, 0.5])
    @pytest.mark.parametrize("seed", range(3))
    def test_and_so_it_is_never_told_its_energy_adds_up(self, factor: float, seed: int) -> None:
        """The outcome that matters. Reassurance here would be worse than
        silence, because silence at least invites a second look."""
        series = _mis_scaled_battery(house.build(days=30, seed=seed), factor)

        report = analyse(to_request(series, declared=DECLARED))

        assert not any(VERDICT in note for note in report.notes)


class TestWhatItTakesToEarnTheVerdict:
    @pytest.mark.parametrize("seed", [0, 4, 5, 6])
    def test_a_house_whose_hours_wobble_but_whose_month_balances(self, seed: int) -> None:
        """Meters noisy enough to leave the clean band, and a month that closes.

        Not the mechanism the reference installation has — its hours are
        mistimed rather than noisy — but the same shape, and the one this
        repository can build. Whichever the cause, the true thing to say is
        the same: the energy is all there, the hour it landed in is not
        reliable.

        The seeds are named rather than a range because whether a house leaves
        the clean band at all is weather-dependent — four of twelve do at this
        noise level. A house that stays clean never reaches this branch and is
        told nothing, which is correct and is the test below.
        """
        series = house.add_noise(house.build(days=30, seed=seed), 0.15, seed=seed + 77)

        report = analyse(to_request(series, declared=DECLARED))

        assert report.status is Status.OK
        assert any(VERDICT in note for note in report.notes)

    def test_a_quiet_house_is_told_nothing_of_the_sort(self) -> None:
        """It never reaches the branch, and there would be nothing to say if it
        did — a house with no discrepancy has no cancellation to explain."""
        report = analyse(to_request(house.build(days=30, seed=0), declared=DECLARED))

        assert report.status is Status.OK
        assert not any(VERDICT in note for note in report.notes)

    def test_half_cancelling_is_not_enough(self) -> None:
        """The middle of the range is where a wrong reassurance would live.

        A house where half the error cancels and half accumulates is half a
        fault. ``MAX_WINDOW_IMBALANCE`` is what refuses it: the surviving share
        alone puts it just inside the bar.
        """
        load = list(house.build(days=30, seed=0).data["load"])
        base = house.build(days=30, seed=0)
        moved = 0.0
        for hour in range(base.hours):
            if 9 <= hour % 24 <= 15:
                shift = min(1200.0, load[hour])
                load[hour] -= shift
                moved += shift
        dark = [hour for hour in range(base.hours) if hour % 24 in (0, 1, 2, 3)]
        for hour in dark:
            load[hour] += moved / len(dark)

        days, _ = _days(base.copy_with(load=load))
        _surviving, net, _gross = _daily_cancellation(days)
        throughput = sum(day.total_throughput for day in days)

        assert abs(net * 1000.0) > throughput * MAX_WINDOW_IMBALANCE
        assert _adds_up_over_the_day(days, specs_for()) is None


def test_the_thresholds_are_moats_rather_than_edges() -> None:
    """Every one of these sits in a gap that was measured, not chosen.

    Faults survive at 1.000 against a bar of 0.5; the widest canceller measured
    is 0.394. A mis-scaled channel resembles itself at 1.000 against a bar of
    0.7; the reference installation's worst role is 0.426.
    """
    assert MAX_SURVIVING_SHARE == 0.5
    assert MAX_CHANNEL_RESEMBLANCE == 0.7
    assert MAX_WINDOW_IMBALANCE == 0.025
