"""Shared entity base.

The predecessor copy-pasted these twenty lines across three platform modules and
they drifted. One base class, defined once.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SolarSanityCoordinator


class SolarSanityEntity(CoordinatorEntity[SolarSanityCoordinator]):
    """Every entity this integration creates."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SolarSanityCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Solar Sanity",
            entry_type=None,
        )
