"""Installation-scoped automation signals over the live measurements and outlook."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.util import dt as dt_util

from .analysis.model import Role
from .const import (
    DEFAULT_MINIMUM_SURPLUS_W,
    DEFAULT_RESERVE_WARNING_MINUTES,
    DEFAULT_SURPLUS_HOLD_MINUTES,
    DOMAIN,
    OPT_BATTERY_POWER,
    OPT_BATTERY_POWER_CHARGING_POSITIVE,
    OPT_BATTERY_SOC,
    OPT_MINIMUM_SURPLUS_W,
    OPT_RESERVE_WARNING_MINUTES,
    OPT_SURPLUS_HOLD_MINUTES,
)

if TYPE_CHECKING:
    from homeassistant.core import Event, State

    from .coordinator import SolarSanityCoordinator

# A report older than three minutes is a hole in the evidence, even if its
# value is still present in the state machine. Allow 50 W for meter noise.
MEASUREMENT_MAX_AGE = timedelta(minutes=3)
FLOW_TOLERANCE_W = 50.0


@dataclass(frozen=True)
class Signal:
    """A known on/off state, or the reason its evidence is unavailable."""

    is_on: bool | None
    reason: str
    attributes: dict[str, Any]

    def view(self) -> dict[str, Any]:
        state = "unavailable" if self.is_on is None else "on" if self.is_on else "off"
        return {"state": state, "reason": self.reason, **self.attributes}


class AutomationSignals:
    """The measured hold starts fresh for each loaded installation."""

    def __init__(self, coordinator: SolarSanityCoordinator) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass
        options = coordinator.entry.options
        self.lead_minutes = options.get(
            OPT_RESERVE_WARNING_MINUTES, DEFAULT_RESERVE_WARNING_MINUTES
        )
        self.minimum_w = options.get(OPT_MINIMUM_SURPLUS_W, DEFAULT_MINIMUM_SURPLUS_W)
        self.hold_minutes = options.get(OPT_SURPLUS_HOLD_MINUTES, DEFAULT_SURPLUS_HOLD_MINUTES)
        self.signal = f"{DOMAIN}_measured_surplus_{coordinator.entry.entry_id}"
        self._measured_since: datetime | None = None
        self._estimated_since: datetime | None = None
        self.estimated_ready = False
        self._battery_power = options.get(OPT_BATTERY_POWER)
        self._charge_sign = 1 if options.get(OPT_BATTERY_POWER_CHARGING_POSITIVE, True) else -1
        roles = {spec.role: spec.entity_id for spec in coordinator.specs}
        self._inputs = {
            role: roles.get(role) for role in (Role.GRID_EXPORT, Role.GRID_IMPORT, Role.PV)
        }
        self._has_battery = bool(
            options.get(OPT_BATTERY_SOC)
            or self._battery_power
            or roles.keys() & {Role.BATTERY_CHARGE, Role.BATTERY_DISCHARGE}
        )
        if self._has_battery:
            self._inputs[Role.BATTERY_DISCHARGE] = self._battery_power or roles.get(
                Role.BATTERY_DISCHARGE
            )
        self.measured = self._evaluate_measured(dt_util.utcnow())

    def reserve(self, now: datetime) -> Signal | None:
        battery = self.coordinator.battery
        settings = battery.settings
        if settings is None:
            return None
        state = battery.state
        attrs = {"lead_minutes": self.lead_minutes, "reserve_pct": settings.reserve_pct}
        if not state.soc_readable or state.soc_pct is None:
            return Signal(None, "No valid battery charge reading.", attrs)
        if state.soc_pct <= settings.reserve_pct:
            return Signal(
                True, "Battery is already at or below reserve.", {**attrs, "at_reserve": True}
            )
        if problem := battery.projection_problem(now):
            return Signal(None, problem, attrs)
        outlook = state.outlook
        assert outlook is not None
        deadline = now + timedelta(minutes=self.lead_minutes)
        when = outlook.reserve_at
        if when is not None and when < now:
            return Signal(
                None, "The projected reserve time has passed. Waiting for a new outlook.", attrs
            )
        if when is not None and when <= deadline:
            return Signal(
                True,
                "The battery is projected to reach reserve within the warning lead time.",
                {**attrs, "reserve_at": when.isoformat(), "at_reserve": False},
            )
        if outlook.horizon_end < deadline and outlook.takeover_at is None:
            return Signal(None, "The outlook does not cover the warning lead time.", attrs)
        return Signal(False, "No reserve risk within the warning lead time.", attrs)

    def _watts(self, state: State | None, now: datetime) -> float | None:
        from .coordinator import KIND_POWER, read_channel

        if (
            state is None
            or state.attributes.get("restored")
            or not timedelta(0) <= now - state.last_reported <= MEASUREMENT_MAX_AGE
        ):
            return None
        watts, kind = read_channel(state)
        return watts if kind == KIND_POWER else None

    def _evaluate_measured(
        self, now: datetime, change: tuple[str, State | None] | None = None
    ) -> Signal:
        attrs = {"minimum_w": self.minimum_w, "hold_minutes": self.hold_minutes}
        names = {
            Role.GRID_EXPORT: "grid export",
            Role.GRID_IMPORT: "grid import",
            Role.PV: "solar generation",
            Role.BATTERY_DISCHARGE: "battery discharge",
        }
        rates = {}
        for role, entity in self._inputs.items():
            if entity is None:
                self._measured_since = None
                return Signal(None, f"No {names[role]} measurement.", attrs)
            state = (
                change[1]
                if change is not None and change[0] == entity
                else self.hass.states.get(entity)
            )
            watts = self._watts(state, now)
            signed = role == Role.BATTERY_DISCHARGE and entity == self._battery_power
            if watts is None or (watts < 0 and not signed):
                self._measured_since = None
                return Signal(None, f"No fresh {names[role]} power reading.", attrs)
            rates[role] = max(0, -watts * self._charge_sign) if signed else watts

        imported = rates[Role.GRID_IMPORT]
        discharged = rates.get(Role.BATTERY_DISCHARGE, 0)
        surplus = max(0, min(rates[Role.PV], rates[Role.GRID_EXPORT] - imported - discharged))
        attrs["measured_w"] = round(surplus, 1)
        if max(imported, discharged) > FLOW_TOLERANCE_W:
            self._measured_since = None
            return Signal(False, "Grid import or battery discharge exceeds 50 W.", attrs)
        if surplus < self.minimum_w:
            self._measured_since = None
            return Signal(False, "Measured surplus is below the threshold.", attrs)
        if self._measured_since is None or now < self._measured_since:
            self._measured_since = now
        if now - self._measured_since < timedelta(minutes=self.hold_minutes):
            return Signal(False, "Waiting for measured surplus to hold.", attrs)
        return Signal(True, "Measured surplus meets the threshold and hold time.", attrs)

    @callback
    def async_refresh(self, now: datetime) -> None:
        """Publish the same evaluated states on the live tick and in Setup."""
        self.measured = self._evaluate_measured(now)
        battery = self.coordinator.battery
        estimate = battery.spare_power(now) if battery.configured else None
        if estimate is None or estimate[2] != "forecast" or estimate[0] < self.minimum_w:
            self._estimated_since = None
            self.estimated_ready = False
            return
        if self._estimated_since is None or now < self._estimated_since:
            self._estimated_since = now
        self.estimated_ready = now - self._estimated_since >= timedelta(minutes=self.hold_minutes)

    @callback
    def async_track(self) -> tuple:
        """Reset on every interruption, including those between live ticks."""
        entities = {entity for entity in self._inputs.values() if entity}

        @callback
        def changed(event: Event) -> None:
            old = event.data["old_state"]
            updated = event.data["new_state"]
            if (
                old is None
                or updated is None
                or updated.last_reported - old.last_reported > MEASUREMENT_MAX_AGE
            ):
                self._measured_since = None
            self._refresh_measurement((event.data["entity_id"], updated))

        @callback
        def reported(event: Event) -> None:
            if event.data["last_reported"] - event.data["old_last_reported"] > MEASUREMENT_MAX_AGE:
                self._measured_since = None
                self._refresh_measurement()

        return (
            async_track_state_change_event(self.hass, entities, changed),
            async_track_state_report_event(self.hass, entities, reported),
        )

    @callback
    def _refresh_measurement(self, change: tuple[str, State | None] | None = None) -> None:
        previous = self.measured
        self.measured = self._evaluate_measured(dt_util.utcnow(), change)
        # Keep up with short dropouts without broadcasting every watt change to
        # the outlook sensors. The live tick publishes the latest wattage.
        if (previous.is_on, previous.reason) != (self.measured.is_on, self.measured.reason):
            async_dispatcher_send(self.hass, self.signal)
