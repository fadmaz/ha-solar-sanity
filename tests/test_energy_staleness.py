"""A cumulative counter that goes away, and the hours either side of it.

Differencing a counter tells you energy flowed, never over how long, so v0.11.0
added a staleness guard: past fifteen minutes the next reading re-baselines
rather than crediting a whole outage to the hour it arrived in.

It marked only that arrival hour. The hour the sensor *left* in has exactly the
same hole in it, and it was shipping a partial total stamped `Quality.OK` and
counted as a full 3600 seconds. So a dropout across an hour boundary stopped
moving energy between two hours and started deleting it: the arrival hour was
thrown away whole, the departure hour under-reported, and nothing balanced the
loss. On a healthy eight-kilowatt array a lunchtime Wi-Fi drop was enough to
take the day out by kilowatt-hours and turn on the problem flag.

Before the guard existed, the whole gap landed in the arrival hour: both hours
stayed valid and the day's total was exact. The guard was right that the *hour*
was untrustworthy and wrong about how many hours that was.

These drive the real methods against a stand-in, so the arithmetic under test is
the arithmetic that ships.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("homeassistant", reason="Home Assistant not installed")

from homeassistant.core import State

from custom_components.solar_sanity.analysis.model import (
    ChannelSpec,
    Quality,
    Role,
)
from custom_components.solar_sanity.coordinator import SolarSanityCoordinator

SPEC = ChannelSpec(
    key="pv",
    role=Role.PV,
    entity_id="sensor.pv_total",
    friendly_name="Solar",
    declared_unit="kWh",
)

#: A `total_increasing` counter — the shape this guard exists for.
ATTRS = {
    "device_class": "energy",
    "unit_of_measurement": "kWh",
    "state_class": "total_increasing",
}

NOON = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


class _Stub:
    """The state `accumulate` and `_close_bucket` touch, borrowing the real
    methods. Listed one by one so anything new the path reaches for fails loudly
    rather than silently taking a stand-in's word for it."""

    specs = (SPEC,)
    accumulate = SolarSanityCoordinator.accumulate
    _close_bucket = SolarSanityCoordinator._close_bucket
    _counter_went_quiet = SolarSanityCoordinator._counter_went_quiet
    _settle_power = SolarSanityCoordinator._settle_power
    _integrate = SolarSanityCoordinator._integrate

    def __init__(self) -> None:
        self._accumulator: dict[str, float] = {}
        self._accumulator_start: datetime | None = None
        self._first_sample_at: datetime | None = None
        self._last_energy: dict[str, tuple[float, datetime]] = {}
        self._suspect: set[str] = set()
        self._live_power: dict[str, tuple[float, datetime]] = {}
        self._gap_since: dict[str, datetime] = {}
        self._gap_seconds: dict[str, float] = {}
        self._buckets: list = []
        self._reading: str | None = None
        self.hass = type("_Hass", (), {"states": self})()

    # -- the states registry, as much of it as `accumulate` uses --------------
    def get(self, _entity_id: str) -> State | None:
        if self._reading is None:
            return None
        return State(SPEC.entity_id, self._reading, ATTRS)

    def _local_day(self, when: datetime):
        return when.date(), False

    # -- driving ------------------------------------------------------------
    def tick(self, when: datetime, reading: float | None, monkeypatch) -> None:
        """One five-minute sample. `None` means the sensor is unavailable."""
        self._reading = None if reading is None else str(reading)
        monkeypatch.setattr(
            "custom_components.solar_sanity.coordinator.dt_util.utcnow", lambda: when
        )
        self.accumulate()

    def bucket(self, start: datetime):
        return next((b for b in self._buckets if b.start_utc == start), None)


def _run(monkeypatch, readings: list[tuple[datetime, float | None]]) -> _Stub:
    stub = _Stub()
    for when, reading in readings:
        stub.tick(when, reading, monkeypatch)
    return stub


def _every_five(start: datetime, count: int):
    return [start + timedelta(minutes=5 * i) for i in range(count)]


class TestAGapAcrossAnHourBoundary:
    """The regression. A counter away from 12:45 to 13:10, over the boundary."""

    @staticmethod
    def _readings():
        out: list[tuple[datetime, float | None]] = []
        # 12:00-12:40, generating 0.5 kWh every five minutes.
        for index, when in enumerate(_every_five(NOON, 9)):
            out.append((when, 100.0 + index * 0.5))
        # Away 12:45 through 13:05 — the gap straddles the hour.
        for when in _every_five(NOON + timedelta(minutes=45), 5):
            out.append((when, None))
        # Back at 13:10, the counter having advanced through the whole outage.
        out.append((NOON + timedelta(minutes=70), 108.0))
        # And on, so the 13:00 bucket closes too.
        for index, when in enumerate(_every_five(NOON + timedelta(minutes=75), 10)):
            out.append((when, 108.5 + index * 0.5))
        return out

    def test_the_hour_it_left_in_is_distrusted(self, monkeypatch) -> None:
        """The fix. This hour used to ship a partial total stamped OK."""
        stub = _run(monkeypatch, self._readings())
        departure = stub.bucket(NOON)

        assert departure is not None, "the 12:00 bucket never closed"
        assert departure.quality["pv"] is Quality.RESET_SUSPECT

    def test_the_hour_it_came_back_in_is_distrusted_too(self, monkeypatch) -> None:
        stub = _run(monkeypatch, self._readings())
        arrival = stub.bucket(NOON + timedelta(hours=1))

        assert arrival is not None, "the 13:00 bucket never closed"
        assert arrival.quality["pv"] is Quality.RESET_SUSPECT

    def test_no_hour_the_gap_touched_is_left_usable(self, monkeypatch) -> None:
        """Together these are the point: an hour with a hole in it is not an
        hour with less energy in it, and half-marking made it exactly that."""
        stub = _run(monkeypatch, self._readings())
        touched = [stub.bucket(NOON), stub.bucket(NOON + timedelta(hours=1))]

        assert all(b is not None for b in touched)
        assert {b.quality["pv"] for b in touched} == {Quality.RESET_SUSPECT}


class TestOrdinaryJitterIsStillCredited:
    """The guard must not start voiding hours for a single missed poll."""

    def test_a_short_absence_keeps_the_hour(self, monkeypatch) -> None:
        readings: list[tuple[datetime, float | None]] = []
        for index, when in enumerate(_every_five(NOON, 10)):
            readings.append((when, 100.0 + index * 0.5))
        # Away for one tick only — eight minutes, well inside the threshold.
        readings.append((NOON + timedelta(minutes=50), None))
        readings.append((NOON + timedelta(minutes=55), 105.0))
        for index, when in enumerate(_every_five(NOON + timedelta(hours=1), 13)):
            readings.append((when, 105.5 + index * 0.5))

        stub = _run(monkeypatch, readings)
        hour = stub.bucket(NOON)

        assert hour is not None
        assert hour.quality["pv"] is Quality.OK
        assert hour.wh["pv"] is not None and hour.wh["pv"] > 0

    def test_an_uninterrupted_hour_is_untouched(self, monkeypatch) -> None:
        readings = [(when, 100.0 + index * 0.5) for index, when in enumerate(_every_five(NOON, 25))]

        stub = _run(monkeypatch, readings)
        hour = stub.bucket(NOON)

        assert hour is not None
        assert hour.quality["pv"] is Quality.OK
        # 12:05 through 13:00, differenced: eleven steps of 0.5 kWh.
        assert hour.wh["pv"] == pytest.approx(5500.0, rel=1e-6)


class TestBeforeThereIsABaseline:
    def test_an_absent_sensor_with_no_history_marks_nothing(self, monkeypatch) -> None:
        """Nothing has been read, so nothing is known to be missing. Marking
        here would distrust every hour of an installation whose sensor has not
        arrived yet."""
        readings: list[tuple[datetime, float | None]] = [
            (when, None) for when in _every_five(NOON, 25)
        ]

        stub = _run(monkeypatch, readings)
        hour = stub.bucket(NOON)

        assert hour is not None
        assert hour.quality["pv"] is Quality.MISSING
