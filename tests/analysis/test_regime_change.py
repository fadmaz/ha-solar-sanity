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
from analysis.regime import (
    MIN_REGIME_DAYS,
    STEP_RATIO,
    Cause,
    RegimeChange,
    attribute,
    find_latest_change,
    note_for,
)
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

    def test_it_offers_both_causes_and_asserts_neither(self) -> None:
        """The first version said "that is usually a settings change rather than
        a fault". That is a guess dressed as a fact, and on the only real
        installation this project has it looks like the wrong one: the owner
        changed nothing, and the balance says the battery had been cycling all
        along while its sensor under-reported it -- the day/night residual swing
        that used to be there collapsed on the very day the reported throughput
        jumped.

        A sensor that starts telling the truth and a setting that gets changed
        are indistinguishable from inside this window. Saying so is the honest
        move, and naming state of charge gives the reader the one check that
        separates them.
        """
        series = _scale_after(
            house.build(days=30, seed=10), 19, 5.0, "battery_charge", "battery_discharge"
        )
        days, specs = _days(series)
        change = find_latest_change(days, specs)
        assert change is not None

        note = note_for(change, Role.BATTERY_CHARGE, days_since=11, window=14)

        assert "usually" not in note
        assert "Either" in note
        # And it points at the one reading that can settle it, which comes from
        # the battery rather than from the meter that changed.
        assert "charge level" in note

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
        assert "battery charging" in report.reason
        assert "verdict" in report.reason

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


class TestWhichCause:
    """Telling a meter that started reporting from equipment that started doing.

    The energy balance cannot do this. A meter that under-reports is exactly the
    case where whatever it fails to say is still conserved and simply turns up as
    residual, so every column is implicated and none is decisive. An earlier
    attempt tried anyway and was withdrawn: the quantity it compared reduced
    algebraically to ``pv + import - load - export``, blind to the battery it
    claimed to measure.

    State of charge settles it because it is not in the balance. It comes from
    the battery management system, so a charge meter reading a fifth of the truth
    leaves it completely unmoved. These tests are all about that one property.
    """

    def _setup(self, seed: int = 1):
        series = _scale_after(
            house.build(days=30, seed=seed), 19, 5.0, "battery_charge", "battery_discharge"
        )
        days, specs = _days(series)
        change = find_latest_change(days, specs)
        assert change is not None, "the step itself was not seen"
        return days, specs, change

    def _soc(self, days, change, before: list[float], after: list[float]):
        """Daily swing percentages, cycled over each side of the boundary."""
        out = {}
        b = a = 0
        for day in days:
            if day.day < change.day:
                out[day.day] = before[b % len(before)]
                b += 1
            else:
                out[day.day] = after[a % len(after)]
                a += 1
        return out

    def test_an_unchanged_charge_level_means_the_meter_changed(self) -> None:
        """The reference installation's shape. Its meter's reported throughput
        stepped 5.4x on one day while its charge level went on swinging 42% a day
        before and 49% after -- so the battery was always doing this work."""
        days, specs, change = self._setup()
        soc = self._soc(days, change, [41.8], [49.0])

        assert attribute(days, change, specs, soc) is Cause.REPORTING

    def test_even_when_the_daily_swing_is_noisy(self) -> None:
        """A real battery's depth varies with weather and use. The reference
        installation ranges 17% to 65% within one unchanged regime, so a test
        built on tidy constants would prove nothing about it."""
        days, specs, change = self._setup(2)
        soc = self._soc(
            days, change, [17.2, 64.5, 41.8, 28.5, 59.4, 44.1], [33.5, 65.0, 47.0, 55.0, 39.0]
        )

        assert attribute(days, change, specs, soc) is Cause.REPORTING

    def test_a_charge_level_that_starts_swinging_means_the_battery_changed(self) -> None:
        """The opposite case, and the one the withdrawn version got wrong: a
        battery that genuinely begins cycling."""
        days, specs, change = self._setup(3)
        soc = self._soc(days, change, [4.0, 6.0, 5.0], [48.0, 55.0, 41.0])

        assert attribute(days, change, specs, soc) is Cause.BEHAVIOUR

    def test_and_one_that_stops(self) -> None:
        days, specs, change = self._setup(4)
        soc = self._soc(days, change, [50.0, 60.0, 45.0], [5.0, 4.0, 6.0])

        assert attribute(days, change, specs, soc) is Cause.BEHAVIOUR

    def test_without_a_charge_level_sensor_it_declines(self) -> None:
        """The common case: nobody has mapped one. The note then offers both
        causes, which is what shipped in 0.24.2."""
        days, specs, change = self._setup(5)

        assert attribute(days, change, specs, {}) is Cause.UNDETERMINED

    def test_an_ambiguous_ratio_is_left_alone(self) -> None:
        """Between "the same" and "different" there is a deliberate gap, and
        landing in it is an answer of its own."""
        days, specs, change = self._setup(6)
        soc = self._soc(days, change, [30.0], [50.0])  # 1.67x

        assert attribute(days, change, specs, soc) is Cause.UNDETERMINED

    def test_a_step_in_something_that_is_not_storage_is_not_guessed_at(self) -> None:
        """Charge level says nothing about a generation or grid channel."""
        days, specs, change = self._setup(7)
        forged = RegimeChange(day=change.day, channel_key="pv", before_wh=1000.0, after_wh=9000.0)
        soc = self._soc(days, forged, [41.8], [49.0])

        assert attribute(days, forged, specs, soc) is Cause.UNDETERMINED

    def test_too_few_days_of_charge_level_is_not_answered(self) -> None:
        """Partial coverage is ordinary -- a sensor added last week has history
        only from last week -- and two days a side is not a comparison."""
        days, specs, change = self._setup(8)
        full = self._soc(days, change, [41.8], [49.0])
        sparse = {d.day: v for d, v in ((d, full[d.day]) for d in days) if d.day >= change.day}

        assert attribute(days, change, specs, sparse) is Cause.UNDETERMINED

    def test_the_note_says_which_and_which_way_it_matters(self) -> None:
        _, _, change = self._setup(9)

        note = note_for(change, Role.BATTERY_CHARGE, 11, 14, Cause.REPORTING)

        assert "same work" in note
        assert "before that were wrong" in note
        # Says where the reading came from, because that is the whole argument.
        assert "from the battery itself" in note
        assert "Either" not in note

    def test_and_the_other_way_round(self) -> None:
        _, _, change = self._setup(10)

        note = note_for(change, Role.BATTERY_CHARGE, 11, 14, Cause.BEHAVIOUR)

        assert "different work" in note
        assert "not how it reports" in note
