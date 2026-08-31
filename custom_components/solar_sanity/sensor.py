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
from .const import CONF_FORECAST_ENTRIES
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

    # Appended to the notes the card renders rather than given an attribute of
    # its own. It is the same kind of sentence as the rest of them — something
    # true beside the verdict rather than part of it — and a reader should not
    # have to know that one of these came from a different module.
    if coordinator.yield_note is not None:
        attrs["notes"] = [*attrs.get("notes", []), coordinator.yield_note.note]

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
    # `state_class: TOTAL` for the same reason as the uncorrected figure above:
    # a forecast nobody records is a forecast nobody can score, and this one is
    # the better of the two to keep.
    SolarSanitySensorDescription(
        key="expected_tomorrow_corrected",
        translation_key="expected_tomorrow_corrected",
        icon="mdi:weather-sunny-alert",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda coordinator: coordinator.expected_tomorrow_corrected_kwh,
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

    entities: list[SensorEntity] = [
        SolarSanitySensor(coordinator, entry, description) for description in descriptions
    ]

    # One per configured provider, created because a provider is configured and
    # not because it has earned a figure yet. Earned-ness changes with the
    # weather; an entity that appears and disappears with it would break every
    # automation and history graph pointing at it, and would do so silently.
    # Before there are twenty-one comparable days the state is unknown and the
    # reason attribute says why in a sentence.
    for provider_entry_id in coordinator.entry.data.get(CONF_FORECAST_ENTRIES) or []:
        provider = hass.config_entries.async_get_entry(provider_entry_id)
        if provider is None:
            continue
        entities.append(ForecastBiasSensor(coordinator, entry, provider_entry_id, provider.title))

    async_add_entities(entities)


class ForecastBiasSensor(SolarSanityEntity, SensorEntity):
    """How far one provider's forecast sits from what the roof did.

    No ``state_class``. This is a judgement rather than a measurement: it is
    recomputed over a rolling window, it is snapped to five points so that a
    reader is not invited to watch it wobble, and it may go back to unknown when
    the days behind it stop qualifying. Recording that as a statistic would
    produce a history graph of an opinion changing its mind.

    The percentage is signed the way a person reads it: negative means the
    system produced less than was forecast.
    """

    _attr_icon = "mdi:crosshairs-question"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "forecast_bias"

    def __init__(
        self,
        coordinator: SolarSanityCoordinator,
        entry: ConfigEntry,
        provider_entry_id: str,
        provider_name: str,
    ) -> None:
        super().__init__(coordinator, entry, f"forecast_bias_{provider_entry_id}")
        self._provider_entry_id = provider_entry_id
        # The name is a placeholder rather than part of the key, so renaming the
        # provider integration renames this sensor instead of orphaning it.
        self._attr_translation_placeholders = {"provider": provider_name}

    @property
    def _score(self) -> Any | None:
        return next(
            (
                score
                for score in self.coordinator.forecast_scores
                if score.entry_id == self._provider_entry_id
            ),
            None,
        )

    @property
    def native_value(self) -> StateType:
        """``None`` until there is a figure worth showing.

        ``Bias`` separates the value it measured from the value it will state.
        Only the second is published: a bias computed from eleven days is real
        arithmetic and not yet an answer, and showing it would invite somebody
        to act on it.
        """
        score = self._score
        return None if score is None else score.bias.reportable_pct

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """``reason`` is carried verbatim.

        It is the sentence that explains an unknown state — "20 comparable days
        so far; a figure needs 21" — and rewording it here would leave two
        versions of the same explanation to drift apart.
        """
        score = self._score
        if score is None:
            return {"reason": "No forecast history has been scored yet."}
        return {
            "reason": score.bias.reason,
            "direction": score.bias.direction,
            "days_compared": score.bias.days,
            **score.bias.measurements,
        }


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
