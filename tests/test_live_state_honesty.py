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
    """Only what `channel_completeness` touches, borrowing the real property."""

    channel_completeness = SolarSanityCoordinator.channel_completeness

    def __init__(self, values: dict[str, str | None]) -> None:
        self.specs = SPECS
        self.hass = type("_Hass", (), {"states": _States(values)})()
        self._has_ever_read = False

    def publish(self, values: dict[str, str | None]) -> None:
        self.hass.states = _States(values)


NOTHING: dict[str, str | None] = {"sensor.pv": None, "sensor.load": None}
BOTH = {"sensor.pv": "0", "sensor.load": "1168"}
HALF: dict[str, str | None] = {"sensor.pv": "0", "sensor.load": None}


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
