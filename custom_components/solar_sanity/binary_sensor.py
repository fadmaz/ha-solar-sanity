"""Binary sensors — the one-glance verdict, and the outage alert."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .analysis.model import AnalysisReport, Severity, Status
from .coordinator import SolarSanityCoordinator, SolarSanityData
from .entity import SolarSanityEntity


@dataclass(frozen=True, kw_only=True)
class SolarSanityBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[AnalysisReport | None], bool | None]


def _data_healthy(report: AnalysisReport | None) -> bool | None:
    """``None`` when we genuinely do not know.

    A binary sensor that reports "fine" while it has no data is lying, and this
    one is the entity most likely to be wired into somebody's automation.
    """
    if report is None or report.status in (
        Status.INSUFFICIENT_DATA,
        Status.NOT_CHECKABLE,
    ):
        return None
    return report.finding is None or report.finding.severity is Severity.NOTE


BINARY_SENSORS: tuple[SolarSanityBinaryDescription, ...] = (
    SolarSanityBinaryDescription(
        key="data_healthy",
        translation_key="data_healthy",
        icon="mdi:check-decagram-outline",
        device_class=BinarySensorDeviceClass.PROBLEM,
        # PROBLEM is inverted by convention: on means there IS a problem.
        value_fn=lambda report: None if (healthy := _data_healthy(report)) is None else not healthy,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data: SolarSanityData = entry.runtime_data
    async_add_entities(
        SolarSanityBinarySensor(data.coordinator, entry, description)
        for description in BINARY_SENSORS
    )


class SolarSanityBinarySensor(SolarSanityEntity, BinarySensorEntity):
    entity_description: SolarSanityBinaryDescription

    def __init__(
        self,
        coordinator: SolarSanityCoordinator,
        entry: ConfigEntry,
        description: SolarSanityBinaryDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.report)

    @property
    def available(self) -> bool:
        return super().available and self.is_on is not None
