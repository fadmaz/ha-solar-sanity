"""Sensors.

The entities are part of the product, not a byproduct: they are what other
people's automations bind to, so their names and meanings are a contract.

Two deliberate choices:

* **No answer is an unknown with a reason, never a guess.** Every outlook
  sensor without a figure carries a ``reason`` attribute in a sentence.
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
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .battery_controls import control_view
from .battery_outlook import REASONS, OutlookState
from .const import CONF_FORECAST_ENTRIES
from .coordinator import SolarSanityCoordinator, SolarSanityData
from .entity import SolarSanityEntity
from .journal import COLLECTION_REASONS, MODEL_REVISION
from .statistics_source import forecast_statistic_id


@dataclass(frozen=True, kw_only=True)
class SolarSanitySensorDescription(SensorEntityDescription):
    """A sensor described by a function, not by a branch in a long if/elif."""

    value_fn: Callable[[SolarSanityCoordinator], StateType]
    attrs_fn: Callable[[SolarSanityCoordinator], dict[str, Any]] | None = None


SENSORS: tuple[SolarSanitySensorDescription, ...] = (
    # `state_class: TOTAL`: a forecast nobody records is a forecast nobody can
    # look back on, and Home Assistant's own forecast integrations omit it. The
    # attributes say which provider it corrects and by what, and the forecast
    # card reads the factor from them. Until 0.33.0 an uncorrected "Forecast
    # tomorrow" sat beside it; the provider's own sensor already says that.
    SolarSanitySensorDescription(
        key="expected_tomorrow_corrected",
        translation_key="expected_tomorrow_corrected",
        icon="mdi:weather-sunny-alert",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda coordinator: coordinator.expected_tomorrow_corrected_kwh,
        attrs_fn=lambda coordinator: coordinator.expected_tomorrow_corrected_attrs,
    ),
)


def _performance_attrs(coordinator: SolarSanityCoordinator, key: str) -> dict[str, Any]:
    summary = coordinator.journal.summary()
    count_key = {
        "night_lowest_mae": "nights",
        "reserve_call_accuracy": "nights",
        "morning_fill_accuracy": "mornings",
        "fill_time_mae": "timed_mornings",
    }[key]
    attrs = {
        "window_days": 28,
        "sample_count": summary[count_key],
        "model_revision": MODEL_REVISION,
        "forecast_source": summary["forecast_source"],
        "excluded": summary["excluded"],
        "pending": summary["pending"],
        "last_7_days": coordinator.journal.summary(7),
    }
    if summary[key] is None:
        attrs.update(reason_code="no_graded_samples", reason="No complete observations graded yet.")
    return attrs


def _metric(coordinator: SolarSanityCoordinator, key: str) -> StateType:
    value = coordinator.journal.summary()[key]
    return round(value, 2) if value is not None else None


INTERVAL_REASONS = {
    "interval_not_yet_issued": "This evening forecast has not been issued yet.",
    "interval_waiting_evening": "Waiting for a recorded 20:00 forecast.",
    "interval_not_recorded": "No interval was recorded with this evening forecast.",
    "interval_revision_unknown": "This interval uses an unrecognized calibration revision.",
    "interval_needs_calibration": (
        "Needs at least 30 complete calibration nights in this configuration."
    ),
    "interval_needs_validation": "Needs 14 later complete nights of silent interval validation.",
    "interval_coverage_low": "Observed interval coverage has not reached the 80% target.",
    "interval_no_prediction": "This evening forecast has no complete upcoming night prediction.",
    "interval_expired": "This evening forecast's night has ended.",
}


def _interval_value(coordinator, bound):
    value = coordinator.journal.interval().get(bound)
    return round(value, 1) if value is not None else None


def _interval_attrs(coordinator):
    view = coordinator.journal.interval()
    attrs = {
        key: value
        for key, value in view.items()
        if key not in ("lower", "upper", "calibration_ids")
    }
    attrs["reason"] = INTERVAL_REASONS.get(view.get("reason_code"))
    return attrs


JOURNAL_SENSORS: tuple[SolarSanitySensorDescription, ...] = (
    SolarSanitySensorDescription(
        key="night_lowest_mae",
        translation_key="night_lowest_mae",
        native_unit_of_measurement="pp",
        icon="mdi:chart-bell-curve-cumulative",
        value_fn=lambda c: _metric(c, "night_lowest_mae"),
        attrs_fn=lambda c: _performance_attrs(c, "night_lowest_mae"),
    ),
    SolarSanitySensorDescription(
        key="reserve_call_accuracy",
        translation_key="reserve_call_accuracy",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:bullseye-arrow",
        value_fn=lambda c: _metric(c, "reserve_call_accuracy"),
        attrs_fn=lambda c: _performance_attrs(c, "reserve_call_accuracy"),
    ),
    SolarSanitySensorDescription(
        key="morning_fill_accuracy",
        translation_key="morning_fill_accuracy",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:battery-check-outline",
        value_fn=lambda c: _metric(c, "morning_fill_accuracy"),
        attrs_fn=lambda c: _performance_attrs(c, "morning_fill_accuracy"),
    ),
    SolarSanitySensorDescription(
        key="fill_time_mae",
        translation_key="fill_time_mae",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda c: _metric(c, "fill_time_mae"),
        attrs_fn=lambda c: _performance_attrs(c, "fill_time_mae"),
    ),
)


INTERVAL_SENSORS = (
    SolarSanitySensorDescription(
        key="evening_lowest_lower_bound",
        translation_key="evening_lowest_lower_bound",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:arrow-collapse-down",
        value_fn=lambda c: _interval_value(c, "lower"),
        attrs_fn=_interval_attrs,
    ),
    SolarSanitySensorDescription(
        key="evening_lowest_upper_bound",
        translation_key="evening_lowest_upper_bound",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:arrow-collapse-up",
        value_fn=lambda c: _interval_value(c, "upper"),
        attrs_fn=_interval_attrs,
    ),
)


COLLECTION_SENSOR = SolarSanitySensorDescription(
    key="collection_status",
    translation_key="collection_status",
    device_class=SensorDeviceClass.ENUM,
    entity_category=EntityCategory.DIAGNOSTIC,
    options=["starting", "ok", "degraded", "error"],
    value_fn=lambda c: c.journal.status()[0],
    attrs_fn=lambda c: {
        "reason_code": c.journal.status()[1],
        "reason": COLLECTION_REASONS.get(c.journal.status()[1]),
        "battery_outlook_configured": c.battery.configured,
    },
)


@dataclass(frozen=True, kw_only=True)
class CaptureDescription(SensorEntityDescription):
    """The integration's own capture, not a copy of a provider's sensor."""

    health_key: str


CAPTURE_SENSORS = (
    CaptureDescription(
        key="forecast_last_captured_at",
        translation_key="forecast_last_captured_at",
        health_key="captured_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    CaptureDescription(
        key="forecast_archive_verified_at",
        translation_key="forecast_archive_verified_at",
        health_key="verified_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


class CaptureSensor(SolarSanityEntity, SensorEntity):
    """A per-provider timestamp with explicit capture and verification stages."""

    def __init__(self, coordinator, entry, provider_id, description):
        super().__init__(coordinator, entry, f"{description.key}_{provider_id}")
        self.provider_id = provider_id
        self.entity_description = description
        provider = coordinator.hass.config_entries.async_get_entry(provider_id)
        self._attr_translation_placeholders = {
            "provider": provider.title if provider else provider_id
        }

    @property
    def native_value(self) -> datetime | None:
        stamp = self.coordinator.journal.health.get(self.provider_id, {}).get(
            self.entity_description.health_key
        )
        return datetime.fromisoformat(stamp) if stamp else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        health = self.coordinator.journal.health.get(self.provider_id, {})
        code = health.get("reason_code")
        if not health.get(self.entity_description.health_key) and not code:
            code = (
                "archive_pending"
                if self.entity_description.health_key == "verified_at"
                else "waiting_for_capture"
            )
        return {
            "reason_code": code,
            "reason": COLLECTION_REASONS.get(code),
            "attempted_at": health.get("attempted_at"),
            "submitted_at": health.get("submitted_at"),
            "verification": health.get("verification", "pending"),
            "forecast_statistic_id": forecast_statistic_id(self.provider_id),
            "provider": self._attr_translation_placeholders["provider"],
        }


def _roof(coordinator: SolarSanityCoordinator, figure: str) -> StateType:
    """One of the provider's figures for now and today, corrected for this roof."""
    figures = coordinator.roof_forecast()
    return None if figures is None else getattr(figures, figure)


def _roof_attrs(coordinator: SolarSanityCoordinator) -> dict[str, Any]:
    return coordinator.roof_forecast_attrs


#: The figures Forecast.Solar publishes as sensors, as this roof would make
#: them (``analysis.forecast.roof_forecast``), so a dashboard can show only
#: corrected figures. 0.33.0, at the reference installation's owner's request:
#: the provider forecasts an ideal roof, and theirs is not one. No
#: ``state_class``, like the provider's own: these are rewritten as the
#: forecast and the clock move, not measured. The two peak-time sensors have no
#: corrected twin, because one factor for the whole day cannot move its peak.
ROOF_SENSORS: tuple[SolarSanitySensorDescription, ...] = (
    SolarSanitySensorDescription(
        key="solar_power_now_corrected",
        translation_key="solar_power_now_corrected",
        icon="mdi:solar-power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda coordinator: _roof(coordinator, "power_now_w"),
        attrs_fn=_roof_attrs,
    ),
    SolarSanitySensorDescription(
        key="solar_energy_this_hour_corrected",
        translation_key="solar_energy_this_hour_corrected",
        icon="mdi:solar-power-variant",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda coordinator: _roof(coordinator, "this_hour_kwh"),
        attrs_fn=_roof_attrs,
    ),
    SolarSanitySensorDescription(
        key="solar_energy_next_hour_corrected",
        translation_key="solar_energy_next_hour_corrected",
        icon="mdi:solar-power-variant",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda coordinator: _roof(coordinator, "next_hour_kwh"),
        attrs_fn=_roof_attrs,
    ),
    SolarSanitySensorDescription(
        key="solar_energy_remaining_today_corrected",
        translation_key="solar_energy_remaining_today_corrected",
        icon="mdi:weather-sunset-down",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda coordinator: _roof(coordinator, "remaining_today_kwh"),
        attrs_fn=_roof_attrs,
    ),
    SolarSanitySensorDescription(
        key="solar_energy_today_corrected",
        translation_key="solar_energy_today_corrected",
        icon="mdi:weather-sunny",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda coordinator: _roof(coordinator, "today_kwh"),
        attrs_fn=_roof_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data: SolarSanityData = entry.runtime_data
    coordinator = data.coordinator

    entities: list[SensorEntity] = [
        SolarSanitySensor(coordinator, entry, description) for description in SENSORS
    ]
    entities.append(SolarSanitySensor(coordinator, entry, COLLECTION_SENSOR))
    entities += [
        CaptureSensor(coordinator, entry, provider_id, description)
        for provider_id in entry.data.get(CONF_FORECAST_ENTRIES, [])
        for description in CAPTURE_SENSORS
    ]

    # Created because a provider is configured: without one there is nothing
    # to correct, and an entity that can never hold a value is a small
    # dishonesty.
    if coordinator.entry.data.get(CONF_FORECAST_ENTRIES):
        entities += [
            SolarSanitySensor(coordinator, entry, description) for description in ROOF_SENSORS
        ]

    # Created because the battery is configured, never because a projection
    # happens to exist at this moment: the charge-level sensor of an MQTT-bridged
    # inverter publishes after Home Assistant starts, and an entity gated on
    # live state goes missing on exactly those installations.
    if coordinator.battery.configured:
        entities += [
            SolarSanitySensor(coordinator, entry, d) for d in (*JOURNAL_SENSORS, *INTERVAL_SENSORS)
        ]
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
    _unrecorded_attributes = frozenset({"corrected_hours"})

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
    """The overall ratio of this roof to the forecast, over every day learned."""
    return round(state.scale.factor, 2) if state.scale is not None else None


def _scales(state: OutlookState) -> dict[str, float | None]:
    """The overall ratio, and the factors today and tomorrow were projected with.

    The two differ from the overall one by the kind of day forecast: on the
    reference installation a sunny day took about a third of its forecast and
    a cloudy one nearly all of it.
    """

    def rounded(value: float | None) -> float | None:
        return round(value, 2) if value is not None else None

    return {
        "forecast_scale": _scale(state),
        "forecast_scale_today": rounded(state.scale_today),
        "forecast_scale_tomorrow": rounded(state.scale_tomorrow),
    }


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
        **_scales(state),
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
    source = state.forecast_source
    production: str | None = None
    if outlook is not None:
        production = f"forecast: {state.provider}" if outlook.forecast_used else "history"
    attrs: dict[str, Any] = {
        "at": _iso(outlook.lowest_at) if outlook else None,
        "night_from": _iso(outlook.night_from) if outlook else None,
        "takeover_at": _iso(outlook.takeover_at) if outlook else None,
        "shortfall_kwh": _kwh(outlook.shortfall_wh) if outlook else None,
        # What the card draws from. The charge it started at is the
        # trajectory's first point, published on its own so it is there before
        # the first projection is; reserve and target sit here as well so the
        # chart's lines come from the same refresh as the line between them.
        "soc_pct": round(state.soc_pct, 1) if state.soc_pct is not None else None,
        "computed_at": _iso(state.computed_at),
        "projection_id": state.projection_id,
        "projection_controls": control_view(state.controls) if state.controls is not None else None,
        "outlook_changes": state.outlook_changes,
        "reserve_pct": state.settings.get("reserve_pct"),
        "target_pct": state.settings.get("target_pct"),
        "consumption_days": state.load_days,
        "production_from": production,
        "forecast_statistic_id": forecast_statistic_id(source.provider_id)
        if outlook is not None and source is not None and source.provider_id is not None
        else None,
        **_scales(state),
        "inverter_standby_w": _standby(state),
        # Disclosed rather than modelled. The projection never counts the grid
        # charging the battery, because the question it answers is whether the
        # battery alone would get the house there.
        "grid_charging_hours_14d": state.grid_charged_hours,
        # Which sensors the card shows live as the flow between the sun, the
        # house, the battery and the grid. Entity ids, not readings: the card
        # reads them itself, and the recorder keeps no second copy of a power.
        "power_sources": state.power_sources,
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
        **_scales(state),
    }
    if outlook is None or outlook.spare_wh is None:
        attrs["reason"] = _why(
            state, "Needs a solar forecast covering the next stretch of daylight."
        )
    return attrs


@dataclass(frozen=True, kw_only=True)
class OutlookSensorDescription(SensorEntityDescription):
    """A read-out of the battery outlook.

    ``live_fn`` and ``live_attrs_fn``, when given, are read from the coordinator
    every time the sensor is written instead of from the last refresh: for a
    figure about this second rather than about the night.
    """

    value_fn: Callable[[OutlookState], StateType | datetime]
    attrs_fn: Callable[[OutlookState], dict[str, Any]]
    live_fn: Callable[[SolarSanityCoordinator], StateType] | None = None
    live_attrs_fn: Callable[[SolarSanityCoordinator], dict[str, Any]] | None = None


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


def _usable(state: OutlookState) -> StateType:
    return _kwh(state.usable_wh)


def _usable_attrs(state: OutlookState) -> dict[str, Any]:
    return {
        "reserve_pct": state.settings.get("reserve_pct"),
        "capacity_kwh": state.settings.get("capacity_kwh"),
    }


def _tonight(state: OutlookState) -> StateType:
    outlook = state.outlook
    return _kwh(outlook.night_load_wh) if outlook is not None else None


def _tonight_attrs(state: OutlookState) -> dict[str, Any]:
    outlook = state.outlook
    attrs: dict[str, Any] = {
        "from": _iso(outlook.night_from) if outlook else None,
        "until": _iso(outlook.takeover_at) if outlook else None,
        "inverter_standby_w": _standby(state),
    }
    if outlook is None or outlook.night_load_wh is None:
        attrs["reason"] = _why(state, "The sun does not take over within the forecast.")
    return attrs


def _tens(watts: float) -> int:
    """To the nearest 10 W, so a load that flickers does not rewrite the state."""
    return int(round(watts / 10.0) * 10)


def _spare_power(coordinator: SolarSanityCoordinator) -> StateType:
    found = coordinator.battery.spare_power()
    return None if found is None else _tens(found[0])


def _spare_power_attrs(coordinator: SolarSanityCoordinator) -> dict[str, Any]:
    found = coordinator.battery.spare_power()
    if found is not None:
        spare_w, potential, source = found
        signals = coordinator.automation
        return {
            "potential_w": _tens(potential),
            "source": source,
            "estimated_alert_ready": (
                source == "forecast" and signals.estimated_ready and spare_w >= signals.minimum_w
            ),
        }
    state = coordinator.battery.state
    if state.reason in ("soc_unavailable", "soc_stale"):
        return {"reason": REASONS[state.reason]}
    return {
        "reason": (
            "Needs the house's consumption reporting and, while the battery is "
            "full or charging as fast as it can, a solar forecast: measured "
            "production is then held down to what is used."
        )
    }


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
    # 0.34.0, three figures the owner asked for something to act on with. This
    # one is a measurement of the battery now, not a forecast, so it is the one
    # outlook sensor recorded as a statistic.
    OutlookSensorDescription(
        key="battery_usable_energy",
        translation_key="battery_usable_energy",
        icon="mdi:battery-arrow-down-outline",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_usable,
        attrs_fn=_usable_attrs,
    ),
    OutlookSensorDescription(
        key="expected_use_tonight",
        translation_key="expected_use_tonight",
        icon="mdi:home-clock-outline",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=_tonight,
        attrs_fn=_tonight_attrs,
    ),
    # Worked from the live readings on every write, every thirty seconds, so an
    # automation running an air conditioner on the sun sees the load it adds.
    OutlookSensorDescription(
        key="spare_solar_power",
        translation_key="spare_solar_power",
        icon="mdi:solar-power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda _state: None,
        attrs_fn=lambda _state: {},
        live_fn=_spare_power,
        live_attrs_fn=_spare_power_attrs,
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
    #: not for the recorder: it is a kilobyte rewritten every five minutes. The
    #: starting charge and the time of the refresh change on every refresh too,
    #: and the charge-level sensor already records the first of them.
    _unrecorded_attributes = frozenset(
        {
            "trajectory",
            "soc_pct",
            "computed_at",
            "projection_id",
            "projection_controls",
            "outlook_changes",
            "potential_w",
        }
    )

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
        if self.entity_description.live_fn is not None:
            return self.entity_description.live_fn(self.coordinator)
        return self.entity_description.value_fn(self.coordinator.battery.state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.coordinator.battery.state
        if self.entity_description.live_attrs_fn is not None:
            attrs = self.entity_description.live_attrs_fn(self.coordinator)
        else:
            attrs = self.entity_description.attrs_fn(state)
        if attrs.get("reason"):
            codes = {
                "battery_full_at": "no_full",
                "battery_reserve_at": "reserve_not_reached",
                "battery_lowest_before_sunrise": "no_night",
                "safe_average_load": "no_takeover",
                "expected_use_tonight": "no_takeover",
                "spare_solar": "needs_forecast",
                "spare_solar_power": "needs_forecast",
            }
            attrs["reason_code"] = state.reason or codes.get(self.entity_description.key)
        return attrs
