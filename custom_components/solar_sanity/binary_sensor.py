"""Read-only automation entities for a Solar Sanity installation."""

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SolarSanityConfigEntry
from .automation_signals import Signal
from .coordinator import SolarSanityCoordinator
from .entity import SolarSanityEntity

RESERVE = BinarySensorEntityDescription(
    key="reserve_risk", translation_key="reserve_risk", icon="mdi:battery-alert-variant-outline"
)
SURPLUS = BinarySensorEntityDescription(
    key="solar_surplus_ready", translation_key="solar_surplus_ready", icon="mdi:solar-power"
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarSanityConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    descriptions = (SURPLUS, RESERVE) if coordinator.battery.configured else (SURPLUS,)
    async_add_entities(
        AutomationSensor(coordinator, entry, description) for description in descriptions
    )


class AutomationSensor(SolarSanityEntity, BinarySensorEntity):
    """Expose the signal used by Setup and the notification blueprints."""

    entity_description: BinarySensorEntityDescription
    _unrecorded_attributes = frozenset({"measured_w"})

    def __init__(
        self,
        coordinator: SolarSanityCoordinator,
        entry: SolarSanityConfigEntry,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description
        self._signal = self._read()

    def _read(self) -> Signal:
        signals = self.coordinator.automation
        if self.entity_description is SURPLUS:
            return signals.measured
        reserve = signals.reserve(dt_util.utcnow())
        assert reserve is not None
        return reserve

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.entity_description is SURPLUS:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass, self.coordinator.automation.signal, self._handle_coordinator_update
                )
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        self._signal = self._read()
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        return self._signal.is_on is not None

    @property
    def is_on(self) -> bool | None:
        return self._signal.is_on

    @property
    def extra_state_attributes(self) -> dict:
        return {"reason": self._signal.reason, **self._signal.attributes}
