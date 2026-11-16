"""Track actual curtailment independently of power and energy measurements."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, State, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.util import dt as dt_util

from .analysis.forecast_fit import FIT_DAYS, HOUR
from .analysis.model import Role
from .const import (
    EXPORT_FIXED,
    EXPORT_UNKNOWN,
    OPT_CURTAILMENT_SENSOR,
    OPT_GRID_EXPORT_LIMIT_W,
    OPT_GRID_EXPORT_MODE,
)

if TYPE_CHECKING:
    from .coordinator import SolarSanityCoordinator


class ExportEvidence:
    """One configured context with closed-hour attestations and an open hour."""

    def __init__(self, coordinator: SolarSanityCoordinator) -> None:
        self.coordinator = coordinator
        self.since: datetime | None = None
        self.hours: dict[datetime, str] = {}
        self._context: str | None = None
        self._hour: datetime | None = None
        self._state: str | None = None
        self._all_off = False
        self._seen_on = False

    @property
    def mode(self) -> str:
        return self.coordinator.entry.options.get(OPT_GRID_EXPORT_MODE, EXPORT_UNKNOWN)

    @property
    def limit_w(self) -> float | None:
        if self.mode != EXPORT_FIXED:
            return None
        return self.coordinator.entry.options.get(OPT_GRID_EXPORT_LIMIT_W)

    @property
    def sensor(self) -> str | None:
        return self.coordinator.entry.options.get(OPT_CURTAILMENT_SENSOR)

    @property
    def context(self) -> str:
        coordinator = self.coordinator
        mapping = tuple(
            sorted(
                (spec.role.key, spec.entity_id)
                for spec in coordinator.specs
                if spec.role in (Role.PV, Role.GRID_EXPORT, Role.GRID_IMPORT)
            )
        )
        config = coordinator.hass.config
        identity = (
            self.mode,
            self.limit_w,
            self.sensor,
            mapping,
            config.time_zone,
            config.latitude,
            config.longitude,
        )
        return sha256(repr(identity).encode()).hexdigest()

    def restore(self, stored: Any, now: datetime) -> None:
        """Restore closed hours only. An outage cannot establish a held state."""
        self.advance(now)
        if not isinstance(stored, dict) or stored.get("context") != self._context:
            return
        since = _parse_time(stored.get("since"))
        if since is None or since > now:
            return
        self.since = since
        hours = stored.get("hours")
        if not isinstance(hours, dict):
            return
        for stamp, state in hours.items():
            hour = _parse_time(stamp)
            if (
                hour is not None
                and hour == _hour(hour)
                and hour >= since
                and hour + HOUR <= now
                and state in ("on", "off")
            ):
                self.hours[hour] = state
        self._prune(now)

    def snapshot(self) -> dict:
        if self.since is None:
            return {}
        return {
            "context": self._context,
            "since": self.since.isoformat(),
            "hours": {hour.isoformat(): state for hour, state in self.hours.items()},
        }

    def advance(self, now: datetime) -> None:
        context = self.context
        if self._context != context:
            self._context = context
            self.since = now if self.mode != EXPORT_UNKNOWN or self.sensor else None
            self.hours.clear()
            self._hour, self._state = None, None
            self._all_off = self._seen_on = False
        if self.sensor is None:
            return
        current = _hour(now)
        if self._hour is None:
            self._hour = current
            return
        if current <= self._hour:
            return
        # A callback delayed several hours cannot attest to the missing hours.
        # The original hour can still carry a directly observed On report.
        continuous = current == self._hour + HOUR
        if self._seen_on:
            self.hours[self._hour] = "on"
        elif continuous and self._all_off:
            self.hours[self._hour] = "off"
        self._all_off = continuous and self._state == "off"
        self._seen_on = continuous and self._state == "on"
        self._hour = current
        if not continuous:
            self._state = None
        self._prune(now)

    def observe(self, state: State | None, now: datetime) -> None:
        self.advance(now)
        reported = state is not None and not state.attributes.get("restored")
        self._state = state.state if reported and state.state in ("on", "off") else None
        if now == _hour(now):
            self._all_off = self._state == "off"
            self._seen_on = self._state == "on"
        else:
            self._all_off &= self._state == "off"
            self._seen_on |= self._state == "on"

    @property
    def observed_state(self) -> str | None:
        return self._state if self._context == self.context else None

    @property
    def confirmed_hours(self) -> int:
        if self._context != self.context:
            return 0
        return sum(state == "on" for state in self.hours.values())

    def track(self) -> tuple[Callable[[], None], ...]:
        if not (entity_id := self.sensor):
            return ()

        @callback
        def reported(event: Event) -> None:
            self.observe(event.data["new_state"], event.time_fired)

        # Identical state writes have their own event. Either kind replaces the
        # unknown startup state, and a removal ends a held reading.
        return (
            async_track_state_change_event(self.coordinator.hass, entity_id, reported),
            async_track_state_report_event(self.coordinator.hass, entity_id, reported),
        )

    def _prune(self, now: datetime) -> None:
        zone = self.coordinator.time_zone or UTC
        first = now.astimezone(zone).date() - timedelta(days=FIT_DAYS)
        self.hours = {
            hour: state
            for hour, state in self.hours.items()
            if hour.astimezone(zone).date() >= first
        }


def _hour(when: datetime) -> datetime:
    return when.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        when = dt_util.parse_datetime(value)
    except ValueError:
        return None
    return when.astimezone(UTC) if when is not None and when.tzinfo is not None else None
