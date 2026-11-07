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
from datetime import datetime
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
from .battery_outlook import REASONS, OutlookState
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

    # Created because the battery is configured, never because a projection
    # happens to exist at this moment: the charge-level sensor of an MQTT-bridged
    # inverter publishes after Home Assistant starts, and gating on it here is
    # how the live residual came to be missing on exactly those installations.
    if coordinator.battery.configured:
        entities += [
            OutlookSensor(coordinator, entry, description) for description in OUTLOOK_SENSORS
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


# --- the battery outlook ---------------------------------------------------


def _iso(when: datetime | None) -> str | None:
    return when.isoformat() if when is not None else None


def _kwh(wh: float | None) -> float | None:
    return round(wh / 1000.0, 2) if wh is not None else None


def _scale(state: OutlookState) -> float | None:
    """What the forecast was multiplied by, learned from this roof."""
    return round(state.scale.factor, 2) if state.scale is not None else None


def _standby(state: OutlookState) -> int | None:
    """The inverter's own draw, in W, added to the house load at every hour."""
    return round(state.standby_w) if state.standby_w is not None else None


def _why(state: OutlookState, unanswered: str) -> str | None:
    """The sentence explaining an unknown state.

    Two kinds of unknown, and a reader needs to tell them apart: the projection
    could not run at all, or it ran and this question has no answer in it.
    """
    if state.reason:
        return REASONS.get(state.reason, state.reason)
    if state.outlook is None:
        return None
    return unanswered


def _full_attrs(state: OutlookState) -> dict[str, Any]:
    outlook = state.outlook
    attrs: dict[str, Any] = {
        "target_pct": state.settings.get("target_pct"),
        "forecast_provider": state.provider,
        "forecast_scale": _scale(state),
    }
    if state.full_at is None:
        attrs["reason"] = _why(
            state,
            "Not reached before the forecast ends."
            if outlook is not None and outlook.forecast_used
            else "Needs a solar forecast: without one, production history on an "
            "inverter with nowhere to send a surplus understates the sun.",
        )
    return attrs


def _reserve_attrs(state: OutlookState) -> dict[str, Any]:
    outlook = state.outlook
    attrs: dict[str, Any] = {
        "reserve_pct": state.settings.get("reserve_pct"),
        "shortfall_kwh": _kwh(outlook.shortfall_wh) if outlook else None,
    }
    if state.reserve_at is None:
        attrs["reason"] = _why(state, "The battery lasts until the sun takes over.")
    return attrs


def _lowest_attrs(state: OutlookState) -> dict[str, Any]:
    outlook = state.outlook
    production: str | None = None
    if outlook is not None:
        production = f"forecast: {state.provider}" if outlook.forecast_used else "history"
    attrs: dict[str, Any] = {
        "at": _iso(outlook.lowest_at) if outlook else None,
        "night_from": _iso(outlook.night_from) if outlook else None,
        "takeover_at": _iso(outlook.takeover_at) if outlook else None,
        "shortfall_kwh": _kwh(outlook.shortfall_wh) if outlook else None,
        "consumption_days": state.load_days,
        "production_from": production,
        "forecast_scale": _scale(state),
        "inverter_standby_w": _standby(state),
        # Disclosed rather than modelled. The projection never counts the grid
        # charging the battery, because the question it answers is whether the
        # battery alone would get the house there.
        "grid_charging_hours_14d": state.grid_charged_hours,
        "trajectory": (
            [{"at": when.isoformat(), "soc": round(soc, 1)} for when, soc in outlook.trajectory]
            if outlook
            else []
        ),
    }
    if outlook is None or outlook.lowest_pct is None:
        attrs["reason"] = _why(state, "No night falls within the forecast.")
    return attrs


def _safe_load_attrs(state: OutlookState) -> dict[str, Any]:
    outlook = state.outlook
    attrs: dict[str, Any] = {
        "from": _iso(outlook.night_from) if outlook else None,
        "until": _iso(outlook.takeover_at) if outlook else None,
        "inverter_standby_w": _standby(state),
    }
    if outlook is None or outlook.safe_load_w is None:
        attrs["reason"] = _why(state, "The sun does not take over within the forecast.")
    return attrs


def _spare_attrs(state: OutlookState) -> dict[str, Any]:
    outlook = state.outlook
    attrs: dict[str, Any] = {
        "day_from": _iso(outlook.spare_from) if outlook else None,
        "forecast_provider": state.provider,
        "forecast_scale": _scale(state),
    }
    if outlook is None or outlook.spare_wh is None:
        attrs["reason"] = _why(
            state, "Needs a solar forecast covering the next stretch of daylight."
        )
    return attrs


@dataclass(frozen=True, kw_only=True)
class OutlookSensorDescription(SensorEntityDescription):
    """A read-out of the battery outlook."""

    value_fn: Callable[[OutlookState], StateType | datetime]
    attrs_fn: Callable[[OutlookState], dict[str, Any]]


def _lowest(state: OutlookState) -> StateType:
    outlook = state.outlook
    if outlook is None or outlook.lowest_pct is None:
        return None
    return round(outlook.lowest_pct, 1)


def _safe_load(state: OutlookState) -> StateType:
    outlook = state.outlook
    if outlook is None or outlook.safe_load_w is None:
        return None
    return round(outlook.safe_load_w / 1000.0, 3)


def _spare(state: OutlookState) -> StateType:
    outlook = state.outlook
    return _kwh(outlook.spare_wh) if outlook is not None else None


#: No ``state_class`` on any of these. They are judgements about the future,
#: recomputed every few minutes, and recording them as statistics would chart an
#: estimate changing its mind as though it were a measurement.
OUTLOOK_SENSORS: tuple[OutlookSensorDescription, ...] = (
    OutlookSensorDescription(
        key="battery_full_at",
        translation_key="battery_full_at",
        icon="mdi:battery-charging-high",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda state: state.full_at,
        attrs_fn=_full_attrs,
    ),
    OutlookSensorDescription(
        key="battery_reserve_at",
        translation_key="battery_reserve_at",
        icon="mdi:battery-alert-variant-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda state: state.reserve_at,
        attrs_fn=_reserve_attrs,
    ),
    # A percentage with no device class. The battery class would make this a
    # battery level, and low-battery automations and the device page would then
    # treat a forecast as the charge the battery has now.
    OutlookSensorDescription(
        key="battery_lowest_before_sunrise",
        translation_key="battery_lowest_before_sunrise",
        icon="mdi:battery-clock-outline",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=_lowest,
        attrs_fn=_lowest_attrs,
    ),
    OutlookSensorDescription(
        key="safe_average_load",
        translation_key="safe_average_load",
        icon="mdi:home-lightning-bolt-outline",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=2,
        value_fn=_safe_load,
        attrs_fn=_safe_load_attrs,
    ),
    OutlookSensorDescription(
        key="spare_solar",
        translation_key="spare_solar",
        icon="mdi:solar-power-variant-outline",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=_spare,
        attrs_fn=_spare_attrs,
    ),
)


class OutlookSensor(SolarSanityEntity, SensorEntity):
    """One read-out of the battery outlook.

    Unavailable only when the charge-level sensor is: that is the one input
    whose absence means the device itself is not reporting. Every other reason
    for having no answer is an unknown state with the reason in an attribute,
    because "unavailable" hides attributes and the reason is the useful part.
    """

    entity_description: OutlookSensorDescription
    #: The trajectory is for the card and for anybody templating against it,
    #: not for the recorder: it is a kilobyte rewritten every five minutes.
    _unrecorded_attributes = frozenset({"trajectory"})

    def __init__(
        self,
        coordinator: SolarSanityCoordinator,
        entry: ConfigEntry,
        description: OutlookSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.battery.state.soc_readable

    @property
    def native_value(self) -> StateType | datetime:
        return self.entity_description.value_fn(self.coordinator.battery.state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attrs_fn(self.coordinator.battery.state)
