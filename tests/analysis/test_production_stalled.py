"""A roof that stopped producing during hours it normally produces.

Not a residual fault. The arithmetic can be perfect while this happens, because
a tripped string is *correctly* reported as zero by a sensor working exactly as
it should — every other check in this package asks whether the numbers agree
with each other, and this one asks whether the roof is doing anything, which is
the question its owner actually has.

Four conditions, each removing a way of being wrong, and the tests below are
organised around them: a typical hour taken from the installation's own history
rather than from a sun position, a run rather than a single hour, the rest of
the system alive to prove it was being watched, and a reading that exists at all.
"""

from __future__ import annotations

import pytest
from analysis.faults import Code
from analysis.model import Answer, DeclaredTopology, LossModel, Quality
from analysis.residual import build_days
from analysis.screen import (
    STALL_MIN_DAYS,
    STALL_MIN_RUN_HOURS,
    screen_production_stalled,
)

from tests.synth import house
from tests.synth.adapt import specs_for, to_request

DECLARED = DeclaredTopology(
    has_battery=Answer.YES,
    grid_is_single_net_sensor=Answer.NO,
    load_covers_whole_house=Answer.YES,
)


def _buckets(series):
    request = to_request(series, declared=DECLARED)
    return request.buckets, request.specs


def _stall(series, *, hours: range, days: range):
    """Generation flat at zero for a run of hours, on some days.

    Everything else is left alone, which is the point: the house carries on
    reporting and only the roof stops. That is what a tripped string looks like
    and what a broker outage does not.
    """
    pv = list(series.data["pv"])
    for day in days:
        for hour in hours:
            index = day * 24 + hour
            if index < len(pv):
                pv[index] = 0.0
    return series.copy_with(pv=pv)


class TestItFires:
    def test_a_roof_that_stops_for_a_morning_is_reported(self) -> None:
        series = _stall(house.build(days=30, seed=0), hours=range(8, 14), days=range(20, 30))

        hits = screen_production_stalled(*_buckets(series))

        assert hits, "a roof producing nothing for six hours a day went unreported"
        assert hits[0].code == Code.PRODUCTION_STALLED
        assert hits[0].fields["hours"] >= STALL_MIN_RUN_HOURS

    def test_it_names_the_generation_channel(self) -> None:
        series = _stall(house.build(days=30, seed=0), hours=range(8, 14), days=range(20, 30))

        hits = screen_production_stalled(*_buckets(series))

        assert hits[0].channel_keys == ("pv",)

    def test_the_figures_it_reports_are_the_ones_it_measured(self) -> None:
        """The copy quotes all three, so all three have to mean something."""
        series = _stall(house.build(days=30, seed=0), hours=range(9, 15), days=range(25, 30))

        fields = screen_production_stalled(*_buckets(series))[0].fields

        assert fields["hours"] >= STALL_MIN_RUN_HOURS
        assert fields["count"] >= fields["hours"]
        assert fields["days"] >= STALL_MIN_DAYS


class TestItStaysQuiet:
    @pytest.mark.parametrize("seed", range(4))
    def test_a_healthy_roof_is_never_accused(self, seed: int) -> None:
        assert screen_production_stalled(*_buckets(house.build(days=30, seed=seed))) == []

    @pytest.mark.parametrize("seed", range(3))
    def test_a_cloudy_day_is_not_a_stall(self, seed: int) -> None:
        """One flat hour is weather. Requiring a run is what separates them
        without having to model weather at all."""
        series = _stall(house.build(days=30, seed=seed), hours=range(11, 12), days=range(30))

        assert screen_production_stalled(*_buckets(series)) == []

    def test_a_short_history_says_nothing(self) -> None:
        """Below a fortnight the 'typical' hour is one season's weather, and a
        run of cloud would teach it that noon produces nothing."""
        series = _stall(house.build(days=10, seed=0), hours=range(8, 16), days=range(10))

        assert screen_production_stalled(*_buckets(series)) == []

    def test_a_house_that_stopped_reporting_entirely_is_not_a_stall(self) -> None:
        """The difference between a fault and an outage.

        If nothing else moved either, the house was not being watched and
        generation is not what stopped. Reporting that as a stall sends somebody
        up a ladder because their broker was down.
        """
        series = house.build(days=30, seed=0)
        data = {key: list(values) for key, values in series.data.items()}
        for day in range(20, 30):
            for hour in range(8, 16):
                index = day * 24 + hour
                for values in data.values():
                    if index < len(values):
                        values[index] = 0.0
        silent = series.copy_with(**data)

        assert screen_production_stalled(*_buckets(silent)) == []

    def test_an_unreadable_hour_is_not_a_zero(self) -> None:
        """``MISSING`` says nothing about production. The same distinction the
        whole engine is built on, applied here."""
        series = house.build(days=30, seed=0)
        request = to_request(
            series,
            specs=specs_for(),
            declared=DECLARED,
            missing={"pv": {day * 24 + hour for day in range(20, 30) for hour in range(8, 16)}},
        )

        assert screen_production_stalled(request.buckets, request.specs) == []

    def test_a_house_with_no_generation_mapped_is_not_asked(self) -> None:
        keys = ("grid_export", "grid_import", "battery_charge", "battery_discharge", "load")
        request = to_request(
            house.drop(house.build(days=30, seed=0), "pv"),
            specs=specs_for(keys),
            declared=DECLARED,
        )

        assert screen_production_stalled(request.buckets, request.specs) == []


def test_the_daylight_predicate_comes_from_this_roof_and_not_an_almanac() -> None:
    """A roof behind a hill is dark at nine whatever the sun is doing.

    Checked by moving the array's whole day later: the hours it now produces in
    are different, and a stall in those hours is still found while the same
    stall in the hours it *used* to produce in is not.
    """
    base = house.build(days=30, seed=0)
    pv = list(base.data["pv"])
    shifted = [0.0] * 6 + pv[:-6]
    late = base.copy_with(pv=shifted)

    in_its_own_daylight = _stall(late, hours=range(15, 21), days=range(20, 30))
    outside_it = _stall(late, hours=range(4, 10), days=range(20, 30))

    assert screen_production_stalled(*_buckets(in_its_own_daylight))
    assert screen_production_stalled(*_buckets(outside_it)) == []


def test_it_does_not_disturb_the_residual() -> None:
    """A stalled roof is a fact about the roof. The arithmetic is untouched, and
    a house whose numbers still balance must not gain a residual because of it."""
    series = _stall(house.build(days=30, seed=0), hours=range(8, 14), days=range(20, 30))
    request = to_request(series, declared=DECLARED)

    days = build_days(request.buckets, request.specs, LossModel(), request.utc_offset_hours)

    assert days
    assert all(bucket.quality["pv"] is Quality.OK for day in days for bucket in day.buckets)
