"""The battery projection, against answers worked out by hand.

Every expected value in this file was computed on paper before the code ran,
from the definitions in ``analysis/outlook.py`` — not read back from the
implementation. A projection is only useful if it is right in the cases simple
enough to check, because those are the only cases anybody can check.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from analysis.outlook import (
    PUBLISH_STEP,
    BatterySettings,
    Step,
    build_steps,
    night_window,
    project,
    round_time,
    safe_average_load,
    settle,
    settle_full,
    settle_reserve,
    simulate,
)

MIDNIGHT = datetime(2026, 6, 1, tzinfo=UTC)


def flat(start: datetime, hours: int, *, pv: float = 0.0, load: float = 0.0) -> list[Step]:
    """``hours`` whole steps of constant production and consumption, in Wh."""
    return [Step(start + timedelta(hours=i), 1.0, pv, load, pv) for i in range(hours)]


def day_shape(hour: int) -> float:
    """Production in Wh for a UTC hour: zero at night, 4 kW at noon."""
    if hour < 6 or hour >= 18:
        return 0.0
    return 4000.0 * (1.0 - abs(hour + 0.5 - 12.0) / 6.0)


def utc_hour(when: datetime) -> int:
    return when.hour


class TestTheArithmeticIsExact:
    """The three cases a person can check with a pencil."""

    def test_a_constant_night_load_reaches_reserve_when_the_energy_runs_out(self) -> None:
        """80% of 10 kWh down to 20% is 6 kWh in the battery, 5.4 kWh to the
        house at 90%; at 500 W that is 10.8 hours."""
        battery = BatterySettings(10_000, 20, 100, discharge_efficiency=0.9)
        run = simulate(80, battery, flat(MIDNIGHT, 12, load=500))

        assert run.reserve_at == MIDNIGHT + timedelta(hours=10.8)
        assert round_time(run.reserve_at) == MIDNIGHT + timedelta(hours=10, minutes=50)

    def test_the_safe_load_is_the_energy_spread_over_the_night(self) -> None:
        """5.4 kWh over twelve hours is 450 W, and the search finds it."""
        battery = BatterySettings(10_000, 20, 100, discharge_efficiency=0.9)
        safe = safe_average_load(80, battery, flat(MIDNIGHT, 12, load=500))

        assert safe == pytest.approx(450.0, abs=1.0)

    def test_a_surplus_fills_the_battery_and_spills_the_rest(self) -> None:
        """From 90% of 10 kWh, 2 kWh/h with no losses: 99% is 900 Wh away, so
        27 minutes; the battery takes 1 kWh to 100% and the other 1 kWh spills."""
        battery = BatterySettings(10_000, 20, 100, charge_efficiency=1.0)
        run = simulate(90, battery, flat(MIDNIGHT, 1, pv=2000))

        assert run.full_at == MIDNIGHT + timedelta(minutes=27)
        assert run.soc[-1] == pytest.approx(100.0)
        assert run.spilled_wh[0] == pytest.approx(1000.0)

    def test_reserve_holds_and_the_rest_is_shortfall(self) -> None:
        """25% to 20% of 10 kWh is 500 Wh; 1 kW for an hour is 1 kWh. Half an
        hour in, the battery stops at reserve and 500 Wh is not covered."""
        battery = BatterySettings(10_000, 20, 100, discharge_efficiency=1.0)
        run = simulate(25, battery, flat(MIDNIGHT, 1, load=1000))

        assert run.reserve_at == MIDNIGHT + timedelta(minutes=30)
        assert run.soc[-1] == pytest.approx(20.0)
        assert run.shortfall_wh[0] == pytest.approx(500.0)

    def test_charging_stops_at_the_target_not_at_a_hundred(self) -> None:
        """An inverter set to stop at 90% spills everything past 90%."""
        battery = BatterySettings(10_000, 20, 90, charge_efficiency=1.0)
        run = simulate(80, battery, flat(MIDNIGHT, 1, pv=3000))

        assert run.soc[-1] == pytest.approx(90.0)
        assert run.spilled_wh[0] == pytest.approx(2000.0)
        # Full at 89%: 900 Wh at 3 kWh/h.
        assert run.full_at == MIDNIGHT + timedelta(minutes=18)

    def test_the_charging_ceiling_delays_full(self) -> None:
        """A 4 kWh surplus into a charger that stops at 1 kW fills 800 Wh in
        48 minutes, not 12, and spills the difference."""
        battery = BatterySettings(10_000, 20, 100, charge_efficiency=1.0, max_charge_w=1000)
        run = simulate(91, battery, flat(MIDNIGHT, 1, pv=4000))

        assert run.full_at == MIDNIGHT + timedelta(minutes=48)
        assert run.spilled_wh[0] == pytest.approx(3100.0)

    def test_the_inverters_own_draw_drains_the_battery_too(self) -> None:
        """A 400 W house and an inverter drawing 100 W of its own is the 500 W
        of the first case: reserve after 10.8 hours, not the 13.5 the house
        alone would give. Left out, every night on the reference installation
        came out two to three points better than it was."""
        battery = BatterySettings(10_000, 20, 100, discharge_efficiency=0.9, standby_w=100)
        run = simulate(80, battery, flat(MIDNIGHT, 12, load=400))

        assert run.reserve_at == MIDNIGHT + timedelta(hours=10.8)

    def test_the_safe_load_is_what_the_house_may_draw_on_top_of_the_inverter(self) -> None:
        """450 W over twelve hours, of which the inverter takes 100: the house
        may draw 350."""
        battery = BatterySettings(10_000, 20, 100, discharge_efficiency=0.9, standby_w=100)
        safe = safe_average_load(80, battery, flat(MIDNIGHT, 12, load=500))

        assert safe == pytest.approx(350.0, abs=1.0)

    def test_the_inverters_own_draw_is_paid_from_a_surplus_first(self) -> None:
        """2 kWh/h of surplus with 200 W of standby charges 1.8 kWh: from 90%
        of 10 kWh, 99% is 900 Wh away, so 30 minutes."""
        battery = BatterySettings(10_000, 20, 100, charge_efficiency=1.0, standby_w=200)
        run = simulate(90, battery, flat(MIDNIGHT, 1, pv=2000))

        assert run.full_at == MIDNIGHT + timedelta(minutes=30)

    def test_an_empty_battery_is_already_at_reserve(self) -> None:
        battery = BatterySettings(10_000, 20, 100)
        run = simulate(15, battery, flat(MIDNIGHT, 2, load=300))

        assert run.reserve_at == MIDNIGHT
        assert run.soc[-1] == pytest.approx(15.0)
        assert sum(run.shortfall_wh) == pytest.approx(600.0)


class TestTheSafeLoadSearchIsSound:
    def test_shortfall_only_grows_with_the_load(self) -> None:
        """The property bisection rests on, over random nights with mornings in
        them. If it ever failed, the search could land on an unsafe load."""
        rng = random.Random(1729)
        for _ in range(40):
            hours = rng.randint(4, 16)
            steps = [
                Step(
                    MIDNIGHT + timedelta(hours=i),
                    1.0,
                    rng.choice([0.0, 0.0, rng.uniform(0, 3000)]),
                    rng.uniform(100, 1500),
                    0.0,
                )
                for i in range(hours)
            ]
            battery = BatterySettings(
                rng.uniform(2000, 20_000),
                rng.uniform(5, 50),
                rng.uniform(80, 100),
                max_charge_w=rng.choice([None, rng.uniform(500, 5000)]),
            )
            soc = rng.uniform(battery.reserve_pct, battery.target_pct)
            previous = -1.0
            for load in range(0, 5000, 250):
                short = sum(simulate(soc, battery, steps, constant_load_w=load).shortfall_wh)
                assert short >= previous - 1e-9
                previous = short

    def test_the_answer_is_safe_and_a_little_more_is_not(self) -> None:
        battery = BatterySettings(5000, 20, 100)
        steps = flat(MIDNIGHT, 6, load=0) + flat(MIDNIGHT + timedelta(hours=6), 2, pv=800)
        safe = safe_average_load(70, battery, steps)

        def short(load: float) -> float:
            return sum(simulate(70, battery, steps, constant_load_w=load).shortfall_wh)

        assert short(safe) <= 1.0
        assert short(safe + 5) > 1.0

    def test_a_battery_already_below_reserve_affords_nothing(self) -> None:
        battery = BatterySettings(5000, 20, 100)
        # Within the shortfall tolerance spread over eight hours, which is
        # nothing anybody could plug in.
        assert safe_average_load(10, battery, flat(MIDNIGHT, 8, load=300)) < 0.2


class TestTheNightIsAnchoredOnDarkness:
    def steps_for_a_day(self, now: datetime, anchor: tuple[float, float] | None = None):
        forecast = {MIDNIGHT + timedelta(hours=h): day_shape(h % 24) for h in range(48)}
        return build_steps(
            now,
            load=[500.0] * 24,
            local_hour=utc_hour,
            pv_forecast=forecast,
            pv_profile=None,
            anchor=anchor,
        )

    def test_a_cloud_at_noon_does_not_open_a_night(self) -> None:
        """Measured production at noon has collapsed under a cloud, below the
        load. The window must still open in the evening, not now."""
        now = MIDNIGHT + timedelta(hours=12, minutes=10)
        steps = self.steps_for_a_day(now, anchor=(200.0, 900.0))
        night = night_window(steps)

        assert steps[0].pv_wh < steps[0].load_wh
        assert night is not None
        assert night.start > 0
        assert steps[night.start].start.hour >= 16

    def test_the_takeover_is_the_first_morning_hour_the_sun_covers_the_house(self) -> None:
        """With 500 W of load, the shape crosses 500 Wh at 07:00 UTC
        (4000 x (1 - 4.5/6) = 1000 Wh at 07:00; 06:00 gives 333 Wh)."""
        now = MIDNIGHT + timedelta(hours=20)
        steps = self.steps_for_a_day(now)
        night = night_window(steps)

        assert night is not None and night.end is not None
        assert steps[night.start].start == now
        assert steps[night.end].start == MIDNIGHT + timedelta(days=1, hours=7)

    def test_before_dawn_the_window_is_the_rest_of_this_night(self) -> None:
        now = MIDNIGHT + timedelta(hours=3, minutes=30)
        steps = self.steps_for_a_day(now)
        night = night_window(steps)

        assert night is not None and night.start == 0
        assert steps[night.end].start == MIDNIGHT + timedelta(hours=7)

    def test_the_following_night_is_not_the_dim_morning(self) -> None:
        now = MIDNIGHT + timedelta(hours=3)
        steps = self.steps_for_a_day(now)
        tonight = night_window(steps)
        following = night_window(steps, first=tonight.end)

        assert following is not None
        assert steps[following.start].start.hour >= 16
        assert steps[following.start].start.date() == MIDNIGHT.date()


class TestTheProjection:
    def forecast(self) -> dict[datetime, float]:
        # Only daylight hours, as Forecast.Solar sends them.
        return {
            MIDNIGHT + timedelta(hours=h): day_shape(h % 24)
            for h in range(48)
            if day_shape(h % 24) > 0
        }

    def test_an_evening_reads_the_night_ahead(self) -> None:
        """At 20:00 with 6 kWh above reserve and 500 W of load: the takeover is
        07:00, eleven hours away. Ten of them are dark; in the 06:00 hour the sun
        gives 333 Wh of the 500, so the battery carries 5,166.7 Wh at 92%."""
        now = MIDNIGHT + timedelta(hours=20)
        battery = BatterySettings(10_000, 20, 100)
        steps = build_steps(
            now,
            load=[500.0] * 24,
            local_hour=utc_hour,
            pv_forecast=self.forecast(),
            pv_profile=None,
        )
        outlook = project(80, battery, steps, forecast_used=True)

        dawn = day_shape(6)
        drop = (10 * 500 + (500 - dawn)) / 0.92 / 10_000 * 100
        assert outlook.takeover_at == MIDNIGHT + timedelta(days=1, hours=7)
        assert outlook.lowest_pct == pytest.approx(80 - drop)
        assert outlook.lowest_at == outlook.takeover_at
        assert outlook.reserve_at is None
        assert outlook.shortfall_wh == pytest.approx(0.0)
        # 6 kWh at 92% plus the dawn hour's sun, over eleven hours.
        assert outlook.safe_load_w == pytest.approx((6000 * 0.92 + dawn) / 11, abs=1.0)

    def test_a_reserve_reached_after_the_takeover_is_not_before_the_sun(self) -> None:
        """A battery that would last the night says no reserve time, even if a
        long horizon would eventually find one."""
        now = MIDNIGHT + timedelta(hours=20)
        battery = BatterySettings(10_000, 20, 100)
        steps = build_steps(
            now,
            load=[500.0] * 24,
            local_hour=utc_hour,
            pv_forecast=self.forecast(),
            pv_profile=None,
        )
        assert project(80, battery, steps, forecast_used=True).reserve_at is None
        assert project(30, battery, steps, forecast_used=True).reserve_at is not None

    def test_at_night_the_spare_solar_is_tomorrows(self) -> None:
        now = MIDNIGHT + timedelta(hours=22)
        battery = BatterySettings(5000, 20, 100)
        steps = build_steps(
            now,
            load=[300.0] * 24,
            local_hour=utc_hour,
            pv_forecast=self.forecast(),
            pv_profile=None,
        )
        outlook = project(60, battery, steps, forecast_used=True)

        assert outlook.spare_from == outlook.takeover_at
        assert outlook.spare_wh is not None and outlook.spare_wh > 0

    def test_without_a_forecast_only_the_night_is_answered(self) -> None:
        """History understates the sun on a curtailing inverter, so the
        daytime read-outs need a forecast and stay unknown without one."""
        now = MIDNIGHT + timedelta(hours=20)
        battery = BatterySettings(10_000, 20, 100)
        steps = build_steps(
            now,
            load=[500.0] * 24,
            local_hour=utc_hour,
            pv_forecast=None,
            pv_profile=[day_shape(h) for h in range(24)],
        )
        outlook = project(80, battery, steps, forecast_used=False)

        assert outlook.full_at is None
        assert outlook.spare_wh is None
        assert outlook.lowest_pct is not None
        assert outlook.safe_load_w is not None

    def test_identical_input_gives_identical_output(self) -> None:
        now = MIDNIGHT + timedelta(hours=9, minutes=17)
        battery = BatterySettings(7000, 15, 95, max_charge_w=2500)
        steps = build_steps(
            now,
            load=[400.0 + 10 * h for h in range(24)],
            local_hour=utc_hour,
            pv_forecast=self.forecast(),
            pv_profile=None,
        )
        assert project(55, battery, steps, forecast_used=True) == project(
            55, battery, steps, forecast_used=True
        )


class TestBuildingTheSteps:
    def test_the_first_step_is_the_rest_of_this_hour(self) -> None:
        now = MIDNIGHT + timedelta(hours=10, minutes=20)
        steps = build_steps(
            now,
            load=[600.0] * 24,
            local_hour=utc_hour,
            pv_forecast={MIDNIGHT + timedelta(hours=h): 1200.0 for h in range(24)},
            pv_profile=None,
        )
        assert steps[0].start == now
        assert steps[0].hours == pytest.approx(40 / 60)
        assert steps[0].load_wh == pytest.approx(400.0)
        assert steps[0].pv_wh == pytest.approx(800.0)
        assert steps[1].start == MIDNIGHT + timedelta(hours=11)

    def test_the_anchor_replaces_the_expectation_for_the_rest_of_the_hour(self) -> None:
        now = MIDNIGHT + timedelta(hours=10, minutes=30)
        steps = build_steps(
            now,
            load=[600.0] * 24,
            local_hour=utc_hour,
            pv_forecast={MIDNIGHT + timedelta(hours=h): 1200.0 for h in range(24)},
            pv_profile=None,
            anchor=(300.0, 900.0),
        )
        assert steps[0].pv_wh == pytest.approx(150.0)
        assert steps[0].load_wh == pytest.approx(450.0)
        # Darkness is still judged on the forecast, not on the cloud.
        assert steps[0].daylight_wh == pytest.approx(600.0)

    def test_hours_missing_inside_the_forecast_are_night_and_the_horizon_ends_with_it(self) -> None:
        now = MIDNIGHT + timedelta(hours=1)
        forecast = {MIDNIGHT + timedelta(hours=h): 500.0 for h in (8, 9, 10)}
        steps = build_steps(
            now, load=[100.0] * 24, local_hour=utc_hour, pv_forecast=forecast, pv_profile=None
        )

        assert steps[-1].start == MIDNIGHT + timedelta(hours=10)
        assert steps[0].pv_wh == 0.0
        assert steps[7].pv_wh == 500.0

    def test_a_hole_in_the_consumption_profile_builds_nothing(self) -> None:
        load: list[float | None] = [300.0] * 24
        load[3] = None
        steps = build_steps(
            MIDNIGHT,
            load=load,
            local_hour=utc_hour,
            pv_forecast=None,
            pv_profile=[0.0] * 24,
        )
        assert steps == ()

    @pytest.mark.parametrize(
        ("day", "hours"),
        [(datetime(2026, 3, 29), 23), (datetime(2026, 10, 25), 25)],
    )
    def test_a_daylight_saving_day_has_the_hours_it_has(self, day: datetime, hours: int) -> None:
        """Steps are UTC hours, consumption is looked up by local hour. On the
        day the clocks change, the local day holds 23 or 25 of them, and its
        consumption is the profile summed over exactly those local hours."""
        zone = ZoneInfo("Europe/Berlin")
        start = day.replace(tzinfo=zone).astimezone(UTC)

        def local_hour(when: datetime) -> int:
            return when.astimezone(zone).hour

        load = [100.0 + h for h in range(24)]
        steps = build_steps(
            start,
            load=load,
            local_hour=local_hour,
            pv_forecast=None,
            pv_profile=[0.0] * 24,
            max_hours=30,
        )
        today = [s for s in steps if s.start.astimezone(zone).date() == day.date()]

        assert len(today) == hours
        expected = sum(load[local_hour(s.start)] for s in today)
        assert sum(s.load_wh for s in today) == pytest.approx(expected)
        if hours == 23:
            assert 2 not in {local_hour(s.start) for s in today}
        else:
            assert [local_hour(s.start) for s in today].count(2) == 2


class TestPublishing:
    def test_times_round_to_the_nearest_five_minutes(self) -> None:
        assert round_time(MIDNIGHT + timedelta(minutes=12, seconds=29)) == MIDNIGHT + timedelta(
            minutes=10
        )
        assert round_time(MIDNIGHT + timedelta(minutes=57, seconds=30)) == MIDNIGHT + timedelta(
            hours=1
        )

    def test_a_small_change_is_not_news(self) -> None:
        published = MIDNIGHT + timedelta(hours=17, minutes=30)
        candidate = published + PUBLISH_STEP - timedelta(seconds=40)
        assert settle(published, candidate, MIDNIGHT) == published

    def test_a_real_change_moves_the_time(self) -> None:
        published = MIDNIGHT + timedelta(hours=17, minutes=30)
        candidate = published + timedelta(minutes=25)
        assert settle(published, candidate, MIDNIGHT) == published + timedelta(minutes=25)

    def test_a_moment_that_has_passed_keeps_its_time(self) -> None:
        """Full at 14:35 should still say 14:35 at 16:00 while the battery sits
        full, not advance to the current time on every refresh."""
        published = MIDNIGHT + timedelta(hours=14, minutes=35)
        now = MIDNIGHT + timedelta(hours=16)
        assert settle(published, now, now) == published

    def test_nothing_to_publish_clears_it(self) -> None:
        assert settle(MIDNIGHT, None, MIDNIGHT) is None


class TestANarrowMissIsNotADifferentDay:
    """A published time stays while the projection misses it by a point or two.

    On 2026-11-09 the reference installation's peak sat at 99.4 against the 99
    full line, and "full at" swung between Monday 15:40 and Tuesday 12:20 four
    times in thirteen minutes. A one-point step in the charge reading was enough.

    The day here: 10 kWh, no losses, two hours of 450 Wh surplus, then two of
    200 Wh load. Full counts from 99%, 9,900 Wh.
    """

    BATTERY = BatterySettings(10_000, 20, 100, charge_efficiency=1.0, discharge_efficiency=1.0)

    def sunny_then_dark(self, soc: float):
        steps = flat(MIDNIGHT, 2, pv=450) + flat(MIDNIGHT + timedelta(hours=2), 2, load=200)
        return project(soc, self.BATTERY, steps, forecast_used=True)

    def published_full(self) -> datetime:
        """From 91%: 9,550 Wh after an hour, 350 Wh to the line at 450 Wh/h is
        46 min 40 s, so 1:46:40, published as 1:45."""
        outlook = self.sunny_then_dark(91)
        assert outlook.full_at == MIDNIGHT + timedelta(hours=1, minutes=46, seconds=40)
        published = settle_full(None, outlook, self.BATTERY, MIDNIGHT)
        assert published == MIDNIGHT + timedelta(hours=1, minutes=45)
        return published

    def test_a_peak_a_point_short_keeps_the_published_time(self) -> None:
        """From 89.5% the peak is 98.5, half a point short of the line and
        within two of it: the same afternoon, not another day."""
        published = self.published_full()
        outlook = self.sunny_then_dark(89.5)

        assert outlook.full_at is None
        assert max(level for _, level in outlook.trajectory) == pytest.approx(98.5)
        assert settle_full(published, outlook, self.BATTERY, MIDNIGHT) == published

    def test_a_peak_three_points_short_withdraws_it(self) -> None:
        """From 87% the peak is 96: more than two points short is a real miss."""
        published = self.published_full()
        outlook = self.sunny_then_dark(87)

        assert max(level for _, level in outlook.trajectory) == pytest.approx(96.0)
        assert settle_full(published, outlook, self.BATTERY, MIDNIGHT) is None

    def test_an_earlier_time_is_never_held_back(self) -> None:
        """Held is only ever the later answer; good news goes out at once."""
        later = MIDNIGHT + timedelta(hours=3, minutes=30)
        outlook = self.sunny_then_dark(91)
        assert settle_full(later, outlook, self.BATTERY, MIDNIGHT) == MIDNIGHT + timedelta(
            hours=1, minutes=45
        )

    def test_a_move_within_the_hour_is_ordinary_news(self) -> None:
        published = MIDNIGHT + timedelta(hours=1, minutes=45)
        candidate = MIDNIGHT + timedelta(hours=2, minutes=30)
        assert settle(published, candidate, MIDNIGHT, hold=True) == candidate

    def test_a_moment_already_past_is_not_held(self) -> None:
        """At 2:00 the published 1:45 has gone by without the battery filling;
        the hold has nothing left to protect."""
        published = self.published_full()
        outlook = self.sunny_then_dark(89.5)
        assert settle_full(published, outlook, self.BATTERY, MIDNIGHT + timedelta(hours=2)) is None

    def test_without_a_forecast_nothing_is_held(self) -> None:
        """A full time needs a forecast; history alone never publishes one."""
        published = self.published_full()
        steps = flat(MIDNIGHT, 2, pv=450) + flat(MIDNIGHT + timedelta(hours=2), 2, load=200)
        outlook = project(89.5, self.BATTERY, steps, forecast_used=False)
        assert settle_full(published, outlook, self.BATTERY, MIDNIGHT) is None

    def test_a_reserve_time_is_held_the_same_way(self) -> None:
        """Two dark hours of 100 Wh, then sun. From 21.5%, 150 Wh above the
        2,000 Wh reserve: 100 in the first hour, 50 in half of the second, so
        reserve at 1:30. From 22.5% the low is 20.5, inside two points of
        reserve, and the warning stays. From 25% the low is 23, and it goes."""
        steps = flat(MIDNIGHT, 2, load=100) + flat(MIDNIGHT + timedelta(hours=2), 2, pv=1000)

        first = project(21.5, self.BATTERY, steps, forecast_used=True)
        assert first.reserve_at == MIDNIGHT + timedelta(hours=1, minutes=30)
        published = settle_reserve(None, first, self.BATTERY, MIDNIGHT)
        assert published == MIDNIGHT + timedelta(hours=1, minutes=30)

        narrow = project(22.5, self.BATTERY, steps, forecast_used=True)
        assert narrow.reserve_at is None
        assert settle_reserve(published, narrow, self.BATTERY, MIDNIGHT) == published

        clear = project(25.0, self.BATTERY, steps, forecast_used=True)
        assert settle_reserve(published, clear, self.BATTERY, MIDNIGHT) is None
