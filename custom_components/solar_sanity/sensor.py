"""Sensors.

The entities are part of the product, not a byproduct — they are what other
people's automations bind to, so their names and meanings are a contract.

Two deliberate choices:

* **The status sensor's state is a word, never a percentage.** "Your residual is
  12%" tells a user nothing they can act on. Either we can name the fault or we
  say we are still looking.
* **Nothing reports money.** The evidence on that is unambiguous, and the
  predecessor's ``estimated_savings`` sensor was its weakest entity.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .analysis.model import AnalysisReport, Status
from .coordinator import SolarSanityCoordinator, SolarSanityData
from .entity import SolarSanityEntity


@dataclass(frozen=True, kw_only=True)
class SolarSanitySensorDescription(SensorEntityDescription):
    """A sensor described by a function, not by a branch in a long if/elif."""

    value_fn: Callable[[SolarSanityCoordinator], StateType]
    attrs_fn: Callable[[SolarSanityCoordinator], dict[str, Any]] | None = None


def _status(coordinator: SolarSanityCoordinator) -> StateType:
    report = coordinator.report
    return report.status.value if report else Status.INSUFFICIENT_DATA.value


def _status_attrs(report: AnalysisReport | None) -> dict[str, Any]:
    if report is None:
        return {}
    finding = report.finding
    return {
        "reason": report.reason or None,
        "finding_code": finding.code if finding else None,
        "headline": finding.headline if finding else None,
        "detail": finding.detail if finding else None,
        "source_fix": finding.source_fix if finding else None,
        "confidence": finding.confidence.value if finding else None,
        "channels": list(finding.channel_keys) if finding else [],
        "days_of_data": report.residual.valid_days,
        # Never a fault and never an alarm — what this verdict does not cover.
        # A user who is not told that half their hours were unverifiable will
        # read a clean status as covering the whole house.
        "notes": list(report.notes),
        "deferred": list(report.deferred),
    }


def _status_attrs_for(coordinator: SolarSanityCoordinator) -> dict[str, Any]:
    """Status attributes, plus anything the user needs in order to act.

    `unrecorded_entities` is the one that saves a support round-trip: if Home
    Assistant is not keeping statistics for a sensor, no amount of waiting will
    produce a verdict from history and the user needs to know which sensor.
    """
    attrs = _status_attrs(coordinator.report)
    if coordinator.unrecorded_entities:
        attrs["unrecorded_entities"] = list(coordinator.unrecorded_entities)
        attrs["unrecorded_note"] = (
            "These sensors have no state_class, so Home Assistant keeps no "
            "history for them. Solar Sanity must collect its own, which takes "
            "about a week."
        )
    return attrs


def _completeness(coordinator: SolarSanityCoordinator) -> StateType:
    """Percentage of configured channels currently readable.

    Adapted from the one genuinely good idea in the predecessor: report how much
    of the picture actually exists rather than pretending an absent input is a
    zero. It now measures what its name says — it previously reported days of
    history, which is a different question wearing the same unit.
    """
    return coordinator.channel_completeness


def _corrections_active(coordinator: SolarSanityCoordinator) -> StateType:
    return len(coordinator.corrections)


SENSORS: tuple[SolarSanitySensorDescription, ...] = (
    SolarSanitySensorDescription(
        key="status",
        translation_key="status",
        icon="mdi:clipboard-check-outline",
        device_class=SensorDeviceClass.ENUM,
        options=[s.value for s in Status],
        value_fn=_status,
        attrs_fn=_status_attrs_for,
    ),
    SolarSanitySensorDescription(
        key="data_completeness",
        translation_key="data_completeness",
        icon="mdi:database-check-outline",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_completeness,
    ),
    SolarSanitySensorDescription(
        key="corrections_active",
        translation_key="corrections_active",
        icon="mdi:tune-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_corrections_active,
    ),
    # `state_class: TOTAL` is the whole point of this sensor. Home Assistant's
    # own forecast integrations omit it, which is exactly why their history is
    # never recorded and yesterday's forecast cannot be scored.
    SolarSanitySensorDescription(
        key="expected_tomorrow",
        translation_key="expected_tomorrow",
        icon="mdi:weather-sunny",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda coordinator: coordinator.expected_tomorrow_kwh,
    ),
    SolarSanitySensorDescription(
        key="live_residual",
        translation_key="live_residual",
        icon="mdi:scale-balance",
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator: coordinator.live_residual_w,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data: SolarSanityData = entry.runtime_data
    coordinator = data.coordinator

    # An entity that can never hold a value is the same small dishonesty as one
    # that returns None from a placeholder. The live residual needs every
    # channel to report a rate; one energy channel rules it out.
    descriptions = [
        description
        for description in SENSORS
        if description.key != "live_residual" or coordinator.has_live_tier
    ]

    async_add_entities(
        SolarSanitySensor(coordinator, entry, description) for description in descriptions
    )


class SolarSanitySensor(SolarSanityEntity, SensorEntity):
    """One sensor, driven entirely by its description."""

    entity_description: SolarSanitySensorDescription

    def __init__(
        self,
        coordinator: SolarSanityCoordinator,
        entry: ConfigEntry,
        description: SolarSanitySensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.entity_description.attrs_fn is None:
            return {}
        return self.entity_description.attrs_fn(self.coordinator)
