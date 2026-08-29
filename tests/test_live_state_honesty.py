"""What the live sensors say before they know anything.

Home Assistant gives a custom integration no way to wait for another one's
entities. At first refresh the inverter has usually not published yet, every
channel reads as absent, and the arithmetic for "none of five are readable" is
0% — which on the device page is indistinguishable from the failure this sensor
exists to report, at exactly the moment a user is most likely to be looking.

The reference installation was photographed showing `Data completeness 0%` while
its own diagnostics, taken minutes later, listed all five channels readable with
719 of 719 hours held.

These drive the real property against a stand-in so the arithmetic under test is
the arithmetic that ships. Home Assistant must be importable, so they run in CI.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("homeassistant", reason="Home Assistant not installed")

from homeassistant.core import State

from custom_components.solar_sanity.analysis.model import ChannelSpec, Role
from custom_components.solar_sanity.coordinator import SolarSanityCoordinator

SPECS = (
    ChannelSpec(
        key="pv", role=Role.PV, entity_id="sensor.pv", friendly_name="Solar", declared_unit="W"
    ),
    ChannelSpec(
        key="load",
        role=Role.LOAD,
        entity_id="sensor.load",
        friendly_name="House",
        declared_unit="W",
    ),
)

ATTRS = {"device_class": "power", "unit_of_measurement": "W", "state_class": "measurement"}


class _States:
    def __init__(self, values: dict[str, str | None]) -> None:
        self._values = values

    def get(self, entity_id: str) -> State | None:
        raw = self._values.get(entity_id)
        return None if raw is None else State(entity_id, raw, ATTRS)


class _Stub:
    """Only what `channel_completeness` touches, borrowing the real property —
    plus `ingest_backfill`, because history is where the flag gets its memory."""

    channel_completeness = SolarSanityCoordinator.channel_completeness
    ingest_backfill = SolarSanityCoordinator.ingest_backfill

    def __init__(self, values: dict[str, str | None]) -> None:
        self.specs = SPECS
        self.hass = type("_Hass", (), {"states": _States(values)})()
        self._has_ever_read = False
        self._buckets: list = []

    def publish(self, values: dict[str, str | None]) -> None:
        self.hass.states = _States(values)

    def _local_day(self, when: datetime):
        return when.date(), False

    def restart(self, history) -> _Stub:
        """What a reload actually does: a new object with the flag back at
        False and no buckets, then a fresh backfill from long-term statistics.

        Modelled properly rather than by copying buckets across, because the
        difference is the whole fix — the recovery has to come from the
        backfill, which is the only durable thing a restart still has.
        """
        fresh = _Stub(self.hass.states._values)
        fresh.ingest_backfill(history)
        return fresh


NOTHING: dict[str, str | None] = {"sensor.pv": None, "sensor.load": None}
BOTH = {"sensor.pv": "0", "sensor.load": "1168"}
HALF: dict[str, str | None] = {"sensor.pv": "0", "sensor.load": None}


def _history(hours: int = 3) -> dict[str, list[tuple[datetime, float, bool]]]:
    """Long-term statistics for both channels, as setup would have fetched."""
    start = datetime(2026, 8, 1, 0, tzinfo=UTC)
    return {
        entity: [(start.replace(hour=h), 100.0 + h, False) for h in range(hours)]
        for entity in ("sensor.pv", "sensor.load")
    }


class TestARestartDuringAnOutage:
    """The information must not be withdrawn because the user acted on it.

    A healthy install reads 100% for weeks. The inverter integration breaks,
    every channel goes unreadable, and the sensor correctly reports 0% — the one
    entity doing its job. The obvious response is to restart Home Assistant, and
    a core update would do it unprompted. That rebuilds the coordinator with
    `_has_ever_read = False`, and a correct 0% became "Unknown" for the rest of
    the outage.
    """

    def test_a_restart_mid_outage_still_reports_zero(self) -> None:
        stub = _Stub(BOTH)
        stub.ingest_backfill(_history())
        assert stub.channel_completeness == 100

        stub.publish(NOTHING)
        assert stub.channel_completeness == 0

        after = stub.restart(_history())

        assert after.channel_completeness == 0, "a restart withdrew a correct 0%"

    def test_history_alone_is_enough_without_a_live_read(self) -> None:
        """The restarted coordinator never sees a good reading — the outage is
        still going. History is the only evidence it has, and it is enough."""
        stub = _Stub(NOTHING)
        stub.ingest_backfill(_history())

        assert stub.channel_completeness == 0

    def test_a_new_install_with_no_history_still_says_unknown(self) -> None:
        """The control. This is the case the flag was added for, and seeding it
        from history must not weaken it."""
        stub = _Stub(NOTHING)
        stub.ingest_backfill({})

        assert stub.channel_completeness is None

    def test_history_that_is_entirely_holes_proves_nothing(self) -> None:
        """Buckets exist but no channel ever reported in them, which is what a
        backfill over a dead sensor looks like."""
        stub = _Stub(NOTHING)
        stub.ingest_backfill({"sensor.pv": [], "sensor.load": []})

        assert stub.channel_completeness is None


class TestBeforeAnythingHasArrived:
    def test_nothing_readable_yet_is_unknown_not_zero(self) -> None:
        """The photographed bug. 0% asserts that every sensor is broken."""
        assert _Stub(NOTHING).channel_completeness is None

    def test_it_stays_unknown_while_it_keeps_finding_nothing(self) -> None:
        stub = _Stub(NOTHING)

        assert [stub.channel_completeness for _ in range(3)] == [None, None, None]

    def test_no_configured_channels_is_also_unknown(self) -> None:
        stub = _Stub(NOTHING)
        stub.specs = ()

        assert stub.channel_completeness is None


class TestOnceSomethingHasArrived:
    def test_a_full_house_reads_a_hundred(self) -> None:
        assert _Stub(BOTH).channel_completeness == 100

    def test_a_partial_house_reads_the_fraction(self) -> None:
        assert _Stub(HALF).channel_completeness == 50

    def test_zero_is_reportable_once_it_means_something(self) -> None:
        """The distinction the flag exists for: nothing has arrived yet, versus
        everything has stopped. Same arithmetic, opposite facts."""
        stub = _Stub(BOTH)

        assert stub.channel_completeness == 100

        stub.publish(NOTHING)
        assert stub.channel_completeness == 0

    def test_one_good_reading_is_enough_to_start_reporting(self) -> None:
        stub = _Stub(NOTHING)
        assert stub.channel_completeness is None

        stub.publish(HALF)
        assert stub.channel_completeness == 50

        stub.publish(NOTHING)
        assert stub.channel_completeness == 0

    def test_recovery_reads_correctly_again(self) -> None:
        stub = _Stub(NOTHING)
        stub.publish(BOTH)
        stub.publish(NOTHING)
        stub.publish(BOTH)

        assert stub.channel_completeness == 100


class TestTheStartupSequenceItWasWrittenFor:
    def test_the_reference_installation_no_longer_claims_nothing_works(self) -> None:
        """Setup, then the inverter publishes on the next tripwire tick."""
        stub = _Stub(NOTHING)

        at_first_refresh = stub.channel_completeness
        stub.publish(BOTH)
        after_the_tripwire = stub.channel_completeness

        assert at_first_refresh is None, "reported a number before it could know one"
        assert after_the_tripwire == 100
