"""Fitting the two terms that only night hours can measure.

The standby term used to be estimated from night hours in which the battery was
*also* idle. On a house whose battery carries the load overnight — which is most
houses that have one — those hours do not exist, so the term that exists to
absorb an inverter's own draw could never be measured on the systems that have
one, and its energy stayed in the residual looking like a fault.

Fitting it jointly with the battery's conversion loss is what makes both
possible: at night the residual is a straight line in battery throughput, and
the slope and intercept are the two numbers wanted.
"""

from __future__ import annotations

import pytest
from analysis.model import Answer, DeclaredTopology, LossModel
from analysis.residual import (
    ACTIONABLE_DAILY_FLOOR_WH,
    CLEAN_DAILY_FLOOR_WH,
    build_days,
    classify_day,
)
from analysis.topology import STANDBY_PLAUSIBLE_W, fit_loss_model, unmetered_draw_w

from tests.synth import house
from tests.synth.adapt import specs_for, to_request

DECLARED = DeclaredTopology(
    has_battery=Answer.YES,
    grid_is_single_net_sensor=Answer.NO,
    load_covers_whole_house=Answer.YES,
)


def _days(series):
    specs = specs_for()
    request = to_request(series, declared=DECLARED)
    return build_days(request.buckets, specs, LossModel()), specs


class TestJointNightFit:
    @pytest.mark.parametrize("seed", range(4))
    def test_a_clean_house_fits_nothing(self, seed: int) -> None:
        """No term may be invented where there is no loss to find."""
        days, specs = _days(house.build(days=30, seed=seed))
        model = fit_loss_model(days, specs, None)

        assert model.fitted_terms == ()
        assert model.standby_w == 0.0

    @pytest.mark.parametrize("seed", range(4))
    def test_an_inverter_idle_draw_is_recovered(self, seed: int) -> None:
        days, specs = _days(house.add_standby(house.build(days=30, seed=seed), 25.0))
        model = fit_loss_model(days, specs, None)

        assert model.established("standby")
        assert model.standby_w == pytest.approx(25.0, abs=2.0)

    @pytest.mark.parametrize("seed", range(4))
    def test_a_dc_measured_battery_is_recovered(self, seed: int) -> None:
        """The pair the old fit could only find in daylight."""
        series = house.measure_battery_dc(
            house.measure_pv_dc(house.build(days=30, seed=seed), 0.96), 0.95
        )
        days, specs = _days(series)
        model = fit_loss_model(days, specs, None)

        assert model.established("battery_dc")
        assert model.battery_dc_gamma == pytest.approx(0.05, abs=0.01)


class TestStandbyIsNotAHidingPlace:
    """The guard against absorbing a fault as loss."""

    @pytest.mark.parametrize("seed", range(4))
    def test_a_halved_consumption_sensor_is_not_absorbed(self, seed: int) -> None:
        """At 250 W of night load, half the house is a plausible-looking 125 W.

        Absolute bounds cannot separate those. The share of the load it would
        have to represent can: an inverter's supply is a small part of what it
        serves, and half a house is not.
        """
        days, specs = _days(house.halve(house.build(days=30, seed=seed), "load"))
        model = fit_loss_model(days, specs, None)

        assert model.standby_w == 0.0, "a halved load sensor was absorbed as standby"

    def test_the_absorbed_amount_stays_inside_its_bounds(self) -> None:
        days, specs = _days(house.add_standby(house.build(days=30, seed=1), 25.0))
        model = fit_loss_model(days, specs, None)

        assert STANDBY_PLAUSIBLE_W[0] <= model.standby_w <= STANDBY_PLAUSIBLE_W[1]


class TestUnmeteredDraw:
    """Measured, reported, never subtracted."""

    @pytest.mark.parametrize("seed", range(4))
    def test_a_large_continuous_draw_is_reported(self, seed: int) -> None:
        days, specs = _days(house.add_standby(house.build(days=30, seed=seed), 200.0))

        assert unmetered_draw_w(days, specs) == pytest.approx(200.0, abs=15.0)

    @pytest.mark.parametrize("seed", range(4))
    def test_it_is_not_also_subtracted(self, seed: int) -> None:
        """Absorbing a kilowatt-hour a day as normal would hide the thing."""
        days, specs = _days(house.add_standby(house.build(days=30, seed=seed), 200.0))
        model = fit_loss_model(days, specs, None)

        assert model.standby_w == 0.0

    @pytest.mark.parametrize("seed", range(4))
    def test_an_ordinary_idle_draw_is_not_reported_as_one(self, seed: int) -> None:
        days, specs = _days(house.add_standby(house.build(days=30, seed=seed), 25.0))

        assert unmetered_draw_w(days, specs) is None

    @pytest.mark.parametrize("seed", range(4))
    def test_a_clean_house_reports_none(self, seed: int) -> None:
        days, specs = _days(house.build(days=30, seed=seed))

        assert unmetered_draw_w(days, specs) is None


class TestPartialDayFloor:
    """An absolute floor stated per day has to be prorated for a shorter one."""

    @staticmethod
    def _day(hours: int, residual_wh: float, throughput_wh: float):
        days, _ = _days(house.build(days=3, seed=1))
        template = days[0]
        per_hour = residual_wh / hours
        return type(template)(
            day=template.day,
            buckets=template.buckets[:hours],
            r=tuple(per_hour for _ in range(hours)),
            expected=tuple(0.0 for _ in range(hours)),
            dr=tuple(per_hour for _ in range(hours)),
            throughput=tuple(throughput_wh / hours for _ in range(hours)),
            band="",
            from_mean=False,
        )

    def test_a_full_day_below_the_floor_is_clean(self) -> None:
        day = self._day(24, CLEAN_DAILY_FLOOR_WH - 10, 1000.0)

        assert classify_day(day) == "clean"

    def test_the_same_energy_in_a_third_of_a_day_is_not(self) -> None:
        """Eleven hours judged against a whole day's floor reads as clean.

        That is what let an eleven-hour window run a quarter out every night for
        a month and still be called quiet.
        """
        day = self._day(8, CLEAN_DAILY_FLOOR_WH - 10, 1000.0)

        assert classify_day(day) != "clean"

    def test_a_partial_day_can_reach_actionable(self) -> None:
        day = self._day(8, ACTIONABLE_DAILY_FLOOR_WH * 0.5, 1000.0)

        assert classify_day(day) == "actionable"
