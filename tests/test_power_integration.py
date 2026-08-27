"""Power integrated over the durations it was actually held.

Sampling a power sensor every five minutes and assuming it held that value
throughout put a standard deviation of about 570 Wh into a day on an
event-reporting load channel — enough that a *healthy* installation reported
"Still looking" roughly half the time. It manufactured no false faults; it
manufactured false doubt, which for a product whose promise is a trustworthy
verdict is its own kind of failure.

Worse, those buckets were labelled ``OWN_INTEGRAL`` — the strongest grade in the
model — while being statistically worse than the ``LTS_MEAN`` grade that exists
specifically to be distrusted.

These drive the real methods against a stand-in, so the arithmetic under test is
the arithmetic that ships. Home Assistant must be importable, so they run in CI
and are absent when working on the pure engine.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant", reason="Home Assistant not installed")

from custom_components.solar_sanity.analysis.model import ChannelSpec, Role
from custom_components.solar_sanity.coordinator import (
    SolarSanityCoordinator,
)

START = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)

SPEC = ChannelSpec(
    key="load",
    role=Role.LOAD,
    entity_id="sensor.load",
    friendly_name="House",
    declared_unit="W",
)


def _stub():
    return SimpleNamespace(
        specs=(SPEC,),
        _accumulator={},
        _live_power={},
        _gap_since={},
        _gap_seconds={},
    )


def _state(watts):
    """A state object with just enough on it for ``read_channel``."""
    if watts is None:
        return SimpleNamespace(state="unavailable", attributes={})
    return SimpleNamespace(
        state=str(watts),
        attributes={"device_class": "power", "unit_of_measurement": "W"},
    )


def _event(watts, at):
    return SimpleNamespace(
        data={"entity_id": SPEC.entity_id, "new_state": _state(watts)},
        time_fired=at,
    )


def _feed(stub, steps):
    """Replay ``(minute, watts)`` changes, then settle to the end of the hour."""
    for minute, watts in steps:
        SolarSanityCoordinator._async_power_changed(
            stub, _event(watts, START + timedelta(minutes=minute))
        )
    SolarSanityCoordinator._settle_power(stub, START + timedelta(hours=1))
    return stub._accumulator.get(SPEC.key)


class TestItIsExactForAStepSignal:
    """Which is what a power sensor reports: a value, held, until it changes."""

    def test_a_constant_load_for_an_hour(self) -> None:
        assert _feed(_stub(), [(0, 400.0)]) == pytest.approx(400.0)

    def test_a_kettle_between_two_ticks(self) -> None:
        """Ninety seconds at 3 kW is 75 Wh, and sampling never sees it at all."""
        watt_hours = _feed(_stub(), [(0, 300.0), (7, 3300.0), (8.5, 300.0)])

        # 300 W all hour, plus 3000 W extra for ninety seconds.
        assert watt_hours == pytest.approx(300.0 + 3000.0 * 1.5 / 60.0)

    def test_a_five_minute_sample_would_have_missed_it(self) -> None:
        """Stated as a test because it is the reason this path exists."""
        sampled = 300.0  # ticks at 0, 5, 10 … all see 300 W
        actual = _feed(_stub(), [(0, 300.0), (7, 3300.0), (8.5, 300.0)])

        assert actual - sampled == pytest.approx(75.0)

    def test_a_late_step_is_carried_to_the_end_of_the_hour(self) -> None:
        """The settle, without which an evening plateau vanishes overnight."""
        watt_hours = _feed(_stub(), [(0, 100.0), (30, 900.0)])

        assert watt_hours == pytest.approx(100.0 * 0.5 + 900.0 * 0.5)

    def test_many_changes_add_up_to_the_area_under_them(self) -> None:
        steps = [(minute, float(minute) * 10.0) for minute in range(0, 60, 5)]
        expected = sum(float(minute) * 10.0 * 5.0 / 60.0 for minute in range(0, 60, 5))

        assert _feed(_stub(), steps) == pytest.approx(expected)


class TestGaps:
    """A hole in the hour is not a smaller hour."""

    def test_a_brief_blip_is_tolerated(self) -> None:
        stub = _stub()
        _feed(stub, [(0, 400.0), (10, None), (11, 400.0)])

        assert stub._gap_seconds[SPEC.key] == pytest.approx(60.0)

    def test_the_time_away_contributes_nothing(self) -> None:
        """Not zero watts — nothing. The difference is the whole product."""
        watt_hours = _feed(_stub(), [(0, 600.0), (30, None), (45, 600.0)])

        assert watt_hours == pytest.approx(600.0 * 45.0 / 60.0)

    def test_a_gap_still_open_at_the_hour_is_counted(self) -> None:
        stub = _stub()
        _feed(stub, [(0, 400.0), (50, None)])

        assert stub._gap_seconds[SPEC.key] == pytest.approx(600.0)

    def test_a_long_gap_exceeds_the_tolerance(self) -> None:
        from custom_components.solar_sanity.const import POWER_GAP_TOLERANCE_SECONDS

        stub = _stub()
        _feed(stub, [(0, 400.0), (10, None), (25, 400.0)])

        assert stub._gap_seconds[SPEC.key] > POWER_GAP_TOLERANCE_SECONDS

    def test_a_short_one_does_not(self) -> None:
        from custom_components.solar_sanity.const import POWER_GAP_TOLERANCE_SECONDS

        stub = _stub()
        _feed(stub, [(0, 400.0), (10, None), (11, 400.0)])

        assert stub._gap_seconds[SPEC.key] <= POWER_GAP_TOLERANCE_SECONDS


class TestEnergyIsLeftAlone:
    """Differencing is exact at any rate, so this path must not touch it."""

    def test_an_energy_reading_is_ignored(self) -> None:
        stub = _stub()
        event = SimpleNamespace(
            data={
                "entity_id": SPEC.entity_id,
                "new_state": SimpleNamespace(
                    state="12.5",
                    attributes={"device_class": "energy", "unit_of_measurement": "kWh"},
                ),
            },
            time_fired=START,
        )
        SolarSanityCoordinator._async_power_changed(stub, event)

        assert stub._accumulator == {}
        assert stub._live_power == {}

    def test_an_unmapped_entity_is_ignored(self) -> None:
        stub = _stub()
        event = SimpleNamespace(
            data={"entity_id": "sensor.someone_elses", "new_state": _state(500.0)},
            time_fired=START,
        )
        SolarSanityCoordinator._async_power_changed(stub, event)

        assert stub._accumulator == {}


class TestSettleIsIdempotent:
    """It runs at every bucket close, and must not double-count."""

    def test_settling_twice_adds_nothing(self) -> None:
        stub = _stub()
        SolarSanityCoordinator._async_power_changed(stub, _event(500.0, START))
        SolarSanityCoordinator._settle_power(stub, START + timedelta(hours=1))
        once = stub._accumulator[SPEC.key]
        SolarSanityCoordinator._settle_power(stub, START + timedelta(hours=1))

        assert stub._accumulator[SPEC.key] == pytest.approx(once)

    def test_the_next_hour_starts_from_the_held_value(self) -> None:
        """A sensor that never changes again still contributes every hour."""
        stub = _stub()
        SolarSanityCoordinator._async_power_changed(stub, _event(500.0, START))
        SolarSanityCoordinator._settle_power(stub, START + timedelta(hours=1))
        stub._accumulator.clear()
        SolarSanityCoordinator._settle_power(stub, START + timedelta(hours=2))

        assert stub._accumulator[SPEC.key] == pytest.approx(500.0)
