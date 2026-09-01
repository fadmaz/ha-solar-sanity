"""Finding the day the installation stopped being the same installation.

Written against the reference installation, where the defect was found: its
battery's daily throughput stepped from about 6 kWh to about 31 kWh on one day
while generation and consumption barely moved, and every statistic computed over
the pooled month then described neither the house before nor the house after.

The risk this carries is the mirror of the one it fixes. Firing wrongly
withholds a verdict from a healthy house for a fortnight, so most of what is
here is about *not* firing: on seasonal drift, on a noisy channel, on a house
that simply has busy days and quiet ones.
"""

from __future__ import annotations

import pytest
from analysis.model import Answer, DeclaredTopology, LossModel, Role
from analysis.regime import MIN_REGIME_DAYS, STEP_RATIO, find_latest_change, note_for
from analysis.residual import build_days

from tests.synth import house
from tests.synth.adapt import specs_for, to_request

DECLARED = DeclaredTopology(
    has_battery=Answer.YES,
    grid_is_single_net_sensor=Answer.NO,
    load_covers_whole_house=Answer.YES,
)


def _days(series):
    specs = specs_for()
    return build_days(to_request(series, declared=DECLARED).buckets, specs, LossModel()), specs


def _scale_after(series, day_index: int, factor: float, *channels: str):
    """Multiply some channels by ``factor`` from ``day_index`` onwards.

    A blunt instrument on purpose. The detector is not being asked whether the
    result is physically coherent — it is being asked whether it can see that
    the last N days are not like the first ones, which is exactly the question
    it will be asked on a house whose owner changed a setting.
    """
    cut = day_index * 24
    changed = {}
    for key in channels:
        values = list(series.data[key])
        changed[key] = [v * factor if i >= cut else v for i, v in enumerate(values)]
    return series.copy_with(**changed)


class TestItFires:
    def test_the_reference_installation_shape(self) -> None:
        """A battery that starts working five times harder, two thirds of the
        way through the window. This is the case that exists."""
        series = _scale_after(
            house.build(days=30, seed=1), 19, 5.0, "battery_charge", "battery_discharge"
        )
        days, specs = _days(series)

        change = find_latest_change(days, specs)

        assert change is not None
        assert change.day == days[19].day
        assert change.channel_key in ("battery_charge", "battery_discharge")
        assert change.factor == pytest.approx(5.0, rel=0.35)

    def test_it_also_sees_a_channel_that_goes_quiet(self) -> None:
        """The step has no preferred direction. A battery taken out of service
        is as much a different installation as one put into it."""
        series = _scale_after(
            house.build(days=30, seed=2), 20, 0.1, "battery_charge", "battery_discharge"
        )
        days, specs = _days(series)

        change = find_latest_change(days, specs)

        assert change is not None
        assert change.day == days[20].day

    def test_the_most_recent_change_wins_not_the_largest(self) -> None:
        """Two changes leave three regimes, and only the newest describes the
        system as it is now. Taking the biggest step would analyse a period that
        has itself already been superseded.

        Both boundaries have to be *clean* splits for this to be a real choice,
        so the three regimes ascend: each is clear of everything before it. The
        first attempt at this test stepped up then partly back down, which
        leaves only one clean split -- the detector picked it, correctly, and
        the test was wrong rather than the code.
        """
        series = house.build(days=30, seed=3)
        series = _scale_after(series, 10, 5.0, "battery_charge", "battery_discharge")
        series = _scale_after(series, 22, 5.0, "battery_charge", "battery_discharge")
        days, specs = _days(series)

        change = find_latest_change(days, specs)

        assert change is not None
        assert change.day == days[22].day


class TestItStaysQuiet:
    @pytest.mark.parametrize("seed", range(6))
    def test_an_unchanged_house_is_left_alone(self, seed: int) -> None:
        """The one that matters most. Every day of the corpus passes through
        here, and a false positive costs a healthy house its verdict."""
        days, specs = _days(house.build(days=30, seed=seed))

        assert find_latest_change(days, specs) is None

    @pytest.mark.parametrize("seed", range(4))
    def test_seasonal_drift_is_not_a_step(self, seed: int) -> None:
        """Generation falling steadily across a month is the single most common
        way a channel's throughput ends the window smaller than it started, and
        it is not a change of installation."""
        series = house.build(days=30, seed=seed)
        pv = list(series.data["pv"])
        faded = [v * (1.0 - 0.4 * (i / len(pv))) for i, v in enumerate(pv)]
        days, specs = _days(series.copy_with(pv=faded))

        assert find_latest_change(days, specs) is None

    def test_a_step_smaller_than_the_margin_is_ignored(self) -> None:
        """Just under ``STEP_RATIO`` must not fire, or the threshold is
        decorative."""
        series = _scale_after(
            house.build(days=30, seed=4),
            18,
            STEP_RATIO * 0.8,
            "battery_charge",
            "battery_discharge",
        )
        days, specs = _days(series)

        assert find_latest_change(days, specs) is None

    def test_a_change_too_near_the_edge_is_not_yet_a_regime(self) -> None:
        """Two days is not a regime, it is a weekend. Firing here would refuse a
        verdict every time somebody had visitors."""
        series = _scale_after(
            house.build(days=30, seed=5),
            30 - (MIN_REGIME_DAYS - 1),
            8.0,
            "battery_charge",
            "battery_discharge",
        )
        days, specs = _days(series)

        assert find_latest_change(days, specs) is None

    def test_a_short_window_cannot_have_two_regimes(self) -> None:
        days, specs = _days(house.build(days=2 * MIN_REGIME_DAYS - 1, seed=6))

        assert find_latest_change(days, specs) is None


class TestWhatTheUserIsTold:
    def test_it_names_the_channel_the_date_and_the_wait(self) -> None:
        """ "Something changed" is not something anybody can check. The owner
        usually knows what they did, at which point this stops being a warning
        and becomes a confirmation."""
        series = _scale_after(
            house.build(days=30, seed=7), 19, 5.0, "battery_charge", "battery_discharge"
        )
        days, specs = _days(series)
        change = find_latest_change(days, specs)
        assert change is not None

        note = note_for(change, Role.BATTERY_CHARGE, days_since=11, window=14)

        assert "battery charging" in note
        assert f"{change.day.day}" in note
        assert "3 days" in note
        assert "settings change" in note

    def test_it_names_the_role_and_never_an_entity_id(self) -> None:
        """The first version passed `spec.friendly_name`, and on the reference
        installation that produced "your
        sensor.siseli_inverter_1_..._battery_charge_energy started moving
        roughly 6 times more energy per day" -- `_friendly_name` falls back to
        the entity id when a state carries no friendly_name attribute, which is
        ordinary on an MQTT-bridged inverter.

        Diagnostics do not carry friendly names at all, so a note built from one
        cannot survive a replay either, and the replay reproducing the verdict
        *including its notes* is the only non-synthetic test this project owns.
        `_generation_name` in engine.py had already written that down.
        """
        series = _scale_after(
            house.build(days=30, seed=9), 19, 5.0, "battery_charge", "battery_discharge"
        )
        days, specs = _days(series)
        change = find_latest_change(days, specs)
        assert change is not None

        note = note_for(change, Role.BATTERY_CHARGE, days_since=11, window=14)

        assert "sensor." not in note
        assert "battery charging" in note

    def test_it_does_not_promise_a_verdict_that_has_already_arrived(self) -> None:
        series = _scale_after(
            house.build(days=30, seed=8), 19, 5.0, "battery_charge", "battery_discharge"
        )
        days, specs = _days(series)
        change = find_latest_change(days, specs)
        assert change is not None

        assert "tomorrow" in note_for(change, Role.BATTERY_CHARGE, days_since=14, window=14)


class TestTheEngineActsOnIt:
    """The detector is only worth having if the report changes because of it."""

    def _report(self, series):
        from analysis.engine import analyse

        return analyse(to_request(series, declared=DECLARED))

    def test_a_verdict_is_refused_while_the_new_regime_is_young(self) -> None:
        """Eleven days of evidence cannot answer a question that needs
        fourteen. Saying so costs the user a wait; the alternative costs them an
        answer about a house that no longer exists, which is what shipped.
        """
        from analysis.model import Status

        series = _scale_after(
            house.build(days=30, seed=11), 19, 5.0, "battery_charge", "battery_discharge"
        )

        report = self._report(series)

        assert report.status is Status.INSUFFICIENT_DATA
        assert report.finding is None
        assert "settings change" in report.reason

    def test_the_residual_is_measured_over_the_new_regime_only(self) -> None:
        """An average across the change describes neither side of it."""
        series = _scale_after(
            house.build(days=30, seed=12), 19, 5.0, "battery_charge", "battery_discharge"
        )

        report = self._report(series)

        assert report.residual is not None
        assert report.residual.valid_days == 11

    def test_an_unchanged_house_still_reaches_its_verdict(self) -> None:
        """The guard against the guard. Thirty ordinary days must be unaffected,
        or this has traded one silent wrong answer for a silent refusal."""
        from analysis.model import Status

        report = self._report(house.build(days=30, seed=13))

        assert report.status is not Status.INSUFFICIENT_DATA
