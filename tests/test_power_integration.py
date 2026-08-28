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


class _Stub:
    """The state the integration path touches, borrowing the real methods.

    Listed one by one on purpose: this is the surface under test, and anything
    the path starts reaching for that is not here fails loudly rather than
    silently taking a stand-in's word for it.
    """

    specs = (SPEC,)
    _key_for_entity = SolarSanityCoordinator._key_for_entity
    _integrate = SolarSanityCoordinator._integrate
    _close_gap = SolarSanityCoordinator._close_gap
    _settle_power = SolarSanityCoordinator._settle_power
    _async_power_changed = SolarSanityCoordinator._async_power_changed

    def __init__(self) -> None:
        self._accumulator: dict[str, float] = {}
        self._live_power: dict[str, tuple[float, datetime]] = {}
        self._gap_since: dict[str, datetime] = {}
        self._gap_seconds: dict[str, float] = {}


def _stub() -> _Stub:
    return _Stub()


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
        stub._async_power_changed(_event(watts, START + timedelta(minutes=minute)))
    stub._settle_power(START + timedelta(hours=1))
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
        stub._async_power_changed(event)

        assert stub._accumulator == {}
        assert stub._live_power == {}

    def test_an_unmapped_entity_is_ignored(self) -> None:
        stub = _stub()
        event = SimpleNamespace(
            data={"entity_id": "sensor.someone_elses", "new_state": _state(500.0)},
            time_fired=START,
        )
        stub._async_power_changed(event)

        assert stub._accumulator == {}


class TestSettleIsIdempotent:
    """It runs at every bucket close, and must not double-count."""

    def test_settling_twice_adds_nothing(self) -> None:
        stub = _stub()
        stub._async_power_changed(_event(500.0, START))
        stub._settle_power(START + timedelta(hours=1))
        once = stub._accumulator[SPEC.key]
        stub._settle_power(START + timedelta(hours=1))

        assert stub._accumulator[SPEC.key] == pytest.approx(once)

    def test_the_next_hour_starts_from_the_held_value(self) -> None:
        """A sensor that never changes again still contributes every hour."""
        stub = _stub()
        stub._async_power_changed(_event(500.0, START))
        stub._settle_power(START + timedelta(hours=1))
        stub._accumulator.clear()
        stub._settle_power(START + timedelta(hours=2))

        assert stub._accumulator[SPEC.key] == pytest.approx(500.0)


class TestTheHourBoundary:
    """An hour rolls over on the wall clock; nothing notices for five minutes.

    A power event arriving in that window is integrated while the *previous*
    bucket is still open, so its segment has already crossed the boundary and
    been counted. Rewinding the cursor back to the boundary counted that same
    slice a second time in the new hour — adding energy that never flowed, to
    every power channel, every hour, forever.
    """

    def test_a_late_rollover_does_not_count_a_slice_twice(self) -> None:
        stub = _stub()
        stub._async_power_changed(_event(1000.0, START))
        # 10:01 — past the boundary, before the tick that notices it.
        stub._async_power_changed(_event(2000.0, START + timedelta(minutes=61)))
        stub._settle_power(START + timedelta(hours=1))
        first_hour = stub._accumulator[SPEC.key]

        stub._accumulator.clear()
        stub._settle_power(START + timedelta(hours=2))
        second_hour = stub._accumulator[SPEC.key]

        # 1000 W held for the 61 minutes up to the second event.
        assert first_hour == pytest.approx(1000.0 * 61 / 60)
        # 2000 W from that event to the end of the next hour — 59 minutes, not
        # 60. The extra minute belongs to the hour that already counted it.
        assert second_hour == pytest.approx(2000.0 * 59 / 60)

    def test_the_cursor_is_never_moved_backwards(self) -> None:
        stub = _stub()
        stub._async_power_changed(_event(500.0, START + timedelta(minutes=61)))
        stub._settle_power(START + timedelta(hours=1))

        assert stub._live_power[SPEC.key][1] == START + timedelta(minutes=61)

    def test_no_energy_is_invented_across_a_day_of_rollovers(self) -> None:
        """The property that matters: the total is the area under the curve.

        Not simply ``watts * 24``. The last event sits a minute past the last
        boundary, so the run genuinely covers a day and a minute — and the point
        of the test is that it covers it *once*. Before the fix this same loop
        returned twenty-four extra minutes of energy that never flowed.
        """
        stub = _stub()
        watts = 800.0
        stub._async_power_changed(_event(watts, START))
        total = 0.0
        for hour in range(1, 25):
            # Every rollover is noticed a minute late, as a five-minute tick
            # against a wall-clock hour boundary guarantees it will be.
            stub._async_power_changed(_event(watts, START + timedelta(hours=hour, minutes=1)))
            stub._settle_power(START + timedelta(hours=hour))
            total += stub._accumulator.get(SPEC.key, 0.0)
            stub._accumulator.clear()

        covered_hours = 24 + 1 / 60
        assert total == pytest.approx(watts * covered_hours, rel=1e-9)

    def test_an_open_gap_is_not_double_counted_either(self) -> None:
        stub = _stub()
        stub._async_power_changed(_event(400.0, START))
        stub._async_power_changed(_event(None, START + timedelta(minutes=61)))
        stub._settle_power(START + timedelta(hours=1))

        # The gap started after the boundary, so none of it belongs to the hour
        # being closed.
        assert stub._gap_seconds.get(SPEC.key, 0.0) == pytest.approx(0.0)
