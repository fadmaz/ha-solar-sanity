"""Additional loads conserve energy at actual interval boundaries."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from analysis.outlook import BatterySettings, Step, project
from analysis.scenarios import compare_load

START = datetime(2026, 11, 10, 20, tzinfo=UTC)
BATTERY = BatterySettings(10_000, 20, 100, charge_efficiency=1, discharge_efficiency=1)


def steps(start=START):
    return tuple(Step(start + timedelta(hours=i), 1, 0, 100, 0) for i in range(12))


def run(*, power=1000, minutes=60, at=START, now=START, series=None):
    return compare_load(
        80,
        BATTERY,
        steps() if series is None else series,
        forecast_used=True,
        power_w=power,
        duration_minutes=minutes,
        start_at=at,
        now=now,
    )


def test_zero_additional_load_is_exactly_the_baseline():
    """The comparison cannot move a timestamp through a separate rounding path."""
    result = run(power=0)
    assert result.baseline == result.scenario == project(80, BATTERY, steps(), forecast_used=True)
    assert result.additional_wh == 0


@pytest.mark.parametrize("offset", [0, 1, 17, 59, 60, 61])
def test_partial_hours_preserve_exact_additional_energy(offset):
    """A 15-minute one-kW appliance consumes250Wh whichever hour it straddles."""
    result = run(minutes=15, at=START + timedelta(minutes=offset))
    assert result.additional_wh == pytest.approx(250)
    assert result.baseline.trajectory[-1][1] - result.scenario.trajectory[-1][1] == pytest.approx(
        2.5
    )


def test_only_the_future_part_of_a_started_scenario_is_applied():
    """A new live basis already reflects its past; do not charge the load twice."""
    result = run(at=START - timedelta(minutes=30))
    assert result.additional_wh == 500
    assert result.applied_start == START


def test_splitting_sunny_steps_preserves_original_solar_and_consumption():
    """A part-hour load must not lose or duplicate the original positive solar energy."""
    series = tuple(Step(START + timedelta(hours=i), 1, 300, 100, 300) for i in range(12))
    result = compare_load(
        50,
        BATTERY,
        series,
        forecast_used=True,
        power_w=1000,
        duration_minutes=15,
        start_at=START + timedelta(minutes=17),
        now=START,
    )
    # 3.6 kWh solar minus 1.2 kWh original load; no clamp or conversion loss.
    assert result.baseline.trajectory[-1][1] == pytest.approx(74)
    assert result.scenario.trajectory[-1][1] == pytest.approx(71.5)
    assert result.additional_wh == pytest.approx(250)
    assert result.baseline_uncovered_wh == result.scenario_uncovered_wh == 0
    assert sum(s.pv_wh for s in series) == 3600
    assert sum(s.load_wh for s in series) == 1200


def test_uncovered_energy_is_reported_when_the_reserve_clamps_charge():
    """A load cannot be hidden by the reserve floor."""
    result = run(power=10_000, minutes=60)
    assert result.scenario_uncovered_wh > result.baseline_uncovered_wh
    assert result.scenario.reserve_at < result.scenario.horizon_end
    assert min(value for _, value in result.scenario.trajectory) >= BATTERY.reserve_pct


@pytest.mark.parametrize("power", [-1, float("nan"), float("inf")])
def test_invalid_power_cannot_enter_the_projection(power):
    """Unknown or nonfinite input cannot become a simulated zero."""
    with pytest.raises(ValueError):
        run(power=power)


@pytest.mark.parametrize("minutes", [0, 1441, 1.5, True])
def test_duration_is_bounded_whole_elapsed_minutes(minutes):
    """Fractional coercion must not silently shorten a requested appliance run."""
    with pytest.raises(ValueError):
        run(minutes=minutes)


def test_a_short_horizon_is_rejected_instead_of_clipping_energy():
    """A partial scenario would understate its effect on the battery."""
    with pytest.raises(ValueError, match="complete scenario"):
        run(minutes=120, series=steps()[:1])


def test_a_finished_scenario_cannot_be_reapplied():
    """Automatic refresh stops when a scheduled load ends."""
    with pytest.raises(ValueError, match="finished"):
        run(at=START - timedelta(hours=1))


@pytest.mark.parametrize("day", [(2026, 3, 8), (2026, 11, 1)])
def test_clock_changes_do_not_change_elapsed_appliance_energy(day):
    """One real hour is one kWh even if the wall clock skips or repeats."""
    local = datetime(*day, 1, 30, tzinfo=ZoneInfo("America/New_York"))
    now = local.astimezone(UTC)
    result = run(at=local, now=now, series=steps(now))
    assert result.additional_wh == 1000
    assert result.applied_end - result.applied_start == timedelta(hours=1)
