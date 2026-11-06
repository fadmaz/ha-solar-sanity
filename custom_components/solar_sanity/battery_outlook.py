"""The battery outlook, fed from what the coordinator already holds.

The arithmetic is in ``analysis/outlook.py`` and is pure. This module is the
part that has to touch Home Assistant: reading the charge level, deciding
whether it can be believed, choosing which forecast to use, and keeping the
published times steady between refreshes.

Nothing here reads a state during setup. On an installation whose inverter is
bridged over MQTT the entities publish after Home Assistant has started, so the
charge level is read only when a projection is computed, and its absence then
is an answer ("unavailable") rather than an error.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .analysis.model import Role
from .analysis.outlook import BatterySettings, Outlook, build_steps, project, settle
from .analysis.profile import grid_charged_hours, hourly_profile, max_charge_w
from .const import (
    CONF_FORECAST_ENTRIES,
    DEFAULT_RESERVE_PCT,
    DEFAULT_TARGET_PCT,
    OPT_BATTERY_CAPACITY_KWH,
    OPT_BATTERY_RESERVE_PCT,
    OPT_BATTERY_SOC,
    OPT_BATTERY_TARGET_PCT,
)

if TYPE_CHECKING:
    from .coordinator import SolarSanityCoordinator

_LOGGER = logging.getLogger(__name__)

#: How old a charge reading may be before it is doubted. Doubted, not rejected:
#: a battery sitting full at noon reports 100% once and then has nothing new to
#: say, so age alone would call a healthy idle battery stale every afternoon.
SOC_MAX_AGE: Final = timedelta(hours=2)

#: ...so an old reading is rejected only if the battery has moved at least this
#: share of its capacity since, which is a charge level that should have changed
#: and did not. Without battery channels mapped there is no way to tell, and
#: the longer limit below applies instead.
SOC_STALE_MOVEMENT: Final = 0.02
SOC_MAX_AGE_UNCHECKED: Final = timedelta(hours=6)

#: A forecast captured longer ago than this is not used. Captures run every
#: thirty minutes; three hours without one means the provider has stopped, and
#: its last revision is then describing weather that has already happened.
FORECAST_MAX_AGE: Final = timedelta(hours=3)

#: The open hour must have been watched for this long before its measured
#: average replaces the expectation for the rest of it. Five minutes of a
#: kettle is not an hour's consumption.
ANCHOR_MIN_SECONDS: Final = 900.0

#: Why there is no outlook, as sentences the sensors publish verbatim. Keys
#: are stable; the sentences may be reworded.
REASONS: Final = {
    "soc_unavailable": "The battery charge level sensor is not reporting a number.",
    "soc_stale": (
        "The battery charge level has not changed although the battery has been "
        "charging or discharging, so it cannot be believed."
    ),
    "learning_consumption": (
        "Learning how much the house uses at each hour; this needs three days "
        "of consumption history."
    ),
    "no_production_expectation": (
        "No forecast is available and there is not yet enough production "
        "history to know when the sun comes up."
    ),
    "no_horizon": "There is nothing ahead to project.",
}


@dataclass(slots=True)
class OutlookState:
    """The latest projection, or the reason there is none."""

    outlook: Outlook | None = None
    reason: str | None = "soc_unavailable"
    soc_age_s: float | None = None
    computed_at: datetime | None = None
    full_at: datetime | None = None
    reserve_at: datetime | None = None
    provider: str | None = None
    load_days: int = 0
    grid_charged_hours: int | None = None
    max_charge_w: float | None = None
    load_profile: tuple[float | None, ...] = ()
    pv_profile: tuple[float | None, ...] = ()
    settings: dict[str, float] = field(default_factory=dict)

    @property
    def soc_readable(self) -> bool:
        return self.reason not in ("soc_unavailable", "soc_stale")


def battery_settings(options: dict[str, Any]) -> BatterySettings | None:
    """The configured battery, or ``None`` until the outlook can run.

    Both the charge-level sensor and the capacity are required: without the
    first there is nothing to project from, and without the second a
    percentage cannot become hours.
    """
    if not options.get(OPT_BATTERY_SOC):
        return None
    capacity = _number(options.get(OPT_BATTERY_CAPACITY_KWH))
    if capacity is None or capacity <= 0:
        return None
    reserve = _number(options.get(OPT_BATTERY_RESERVE_PCT))
    target = _number(options.get(OPT_BATTERY_TARGET_PCT))
    return BatterySettings(
        capacity_wh=capacity * 1000.0,
        reserve_pct=DEFAULT_RESERVE_PCT if reserve is None else reserve,
        target_pct=DEFAULT_TARGET_PCT if target is None else target,
    )


def _number(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    return value if math.isfinite(value) else None


class BatteryOutlook:
    """Owns the projection and the times already published from it."""

    def __init__(self, hass: HomeAssistant, coordinator: SolarSanityCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.state = OutlookState()
        self._dirty = True

    @property
    def settings(self) -> BatterySettings | None:
        return battery_settings(dict(self.coordinator.entry.options))

    @property
    def configured(self) -> bool:
        return self.settings is not None

    @callback
    def async_track(self) -> Callable[[], None]:
        """Recompute soon after the charge level changes.

        Subscribed by entity id, which works before the entity exists. The
        callback only marks the projection stale; the thirty-second tick does
        the work, so a sensor that reports every second costs nothing.
        """
        entity_id = self.coordinator.entry.options.get(OPT_BATTERY_SOC)
        if not entity_id or not self.configured:
            return lambda: None

        @callback
        def _changed(_event: Any) -> None:
            self._dirty = True

        return async_track_state_change_event(self.hass, [entity_id], _changed)

    def refresh(self, *, force: bool = False, now: datetime | None = None) -> None:
        """Recompute the projection if anything has changed, or if asked to."""
        if not force and not self._dirty:
            return
        self._dirty = False
        settings = self.settings
        if settings is None:
            self.state = OutlookState(reason=None)
            return
        try:
            self.state = self._compute(settings, now or dt_util.utcnow())
        except Exception:
            # The outlook is a reading, never a reason to take the integration
            # down: a bad state from somebody else's sensor must cost this
            # projection, not the forecast archive beside it.
            _LOGGER.warning("Battery outlook could not be computed", exc_info=True)

    def _compute(self, settings: BatterySettings, now: datetime) -> OutlookState:
        coordinator = self.coordinator
        buckets = coordinator.buckets
        tz = coordinator.time_zone
        local_hour = _local_hour(tz)

        load = hourly_profile(buckets, Role.LOAD.key, local_hour, until=now)
        pv = hourly_profile(buckets, Role.PV.key, _utc_hour, until=now, typical="median")
        ceiling = max_charge_w(buckets, Role.BATTERY_CHARGE.key, until=now)
        settings = BatterySettings(
            capacity_wh=settings.capacity_wh,
            reserve_pct=settings.reserve_pct,
            target_pct=settings.target_pct,
            max_charge_w=ceiling,
        )
        base = OutlookState(
            computed_at=now,
            load_days=load.days,
            grid_charged_hours=grid_charged_hours(
                buckets,
                pv=Role.PV.key,
                load=Role.LOAD.key,
                charge=Role.BATTERY_CHARGE.key,
                until=now,
            ),
            max_charge_w=ceiling,
            load_profile=load.wh,
            pv_profile=pv.wh,
            settings={
                "capacity_kwh": settings.capacity_wh / 1000.0,
                "reserve_pct": settings.reserve_pct,
                "target_pct": settings.target_pct,
            },
        )

        soc, age, reason = self._read_soc(settings, now)
        base.soc_age_s = age
        if soc is None:
            base.reason = reason
            return base
        if not load.ready:
            base.reason = "learning_consumption"
            return base

        provider, forecast = self._forecast(now)
        if forecast is None and not pv.ready:
            base.reason = "no_production_expectation"
            return base

        steps = build_steps(
            now,
            load=load.wh,
            local_hour=local_hour,
            pv_forecast=forecast,
            pv_profile=None if forecast is not None else pv.wh,
            anchor=self._anchor(now),
        )
        if not steps:
            base.reason = "no_horizon"
            return base

        outlook = project(soc, settings, steps, forecast_used=forecast is not None)
        base.outlook = outlook
        base.reason = None
        base.provider = provider
        base.full_at = settle(self.state.full_at, outlook.full_at, now)
        base.reserve_at = settle(self.state.reserve_at, outlook.reserve_at, now)
        return base

    def _read_soc(
        self, settings: BatterySettings, now: datetime
    ) -> tuple[float | None, float | None, str]:
        """The charge level, its age in seconds, and why it was refused if it was."""
        entity_id = self.coordinator.entry.options.get(OPT_BATTERY_SOC)
        state: State | None = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return None, None, "soc_unavailable"
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None, None, "soc_unavailable"
        if not math.isfinite(value) or not 0.0 <= value <= 100.0:
            return None, None, "soc_unavailable"

        reported = getattr(state, "last_reported", None) or state.last_updated
        age = max((now - reported).total_seconds(), 0.0)
        if age > SOC_MAX_AGE.total_seconds():
            moved = self._battery_moved_wh(reported)
            if moved is None:
                if age > SOC_MAX_AGE_UNCHECKED.total_seconds():
                    return None, age, "soc_stale"
            elif moved > SOC_STALE_MOVEMENT * settings.capacity_wh:
                return None, age, "soc_stale"
        return value, age, ""

    def _battery_moved_wh(self, since: datetime) -> float | None:
        """Energy through the battery since ``since``, from closed hours.

        ``None`` when no battery channel is mapped, which is "cannot tell", not
        "did not move".
        """
        keys = [
            spec.key
            for spec in self.coordinator.specs
            if spec.role in (Role.BATTERY_CHARGE, Role.BATTERY_DISCHARGE)
        ]
        if not keys:
            return None
        floor = since.replace(minute=0, second=0, microsecond=0)
        total = 0.0
        for bucket in self.coordinator.buckets:
            if bucket.start_utc < floor:
                continue
            for key in keys:
                value = bucket.value(key)
                if value is not None and value > 0:
                    total += value
        return total

    def _forecast(self, now: datetime) -> tuple[str | None, dict[datetime, float] | None]:
        """The first configured provider with a fresh forecast reaching past now.

        Raw, in Wh per UTC hour. The provider's measured bias is deliberately
        not applied yet: on an inverter that curtails when the battery is full,
        the production it is scored against reads low on exactly the sunny
        days, so the correction would hide spare sunshine where it exists.
        """
        captured = self.coordinator.forecast_captured_at
        if captured is None or now - captured > FORECAST_MAX_AGE:
            return None, None
        hour = now.replace(minute=0, second=0, microsecond=0)
        points = self.coordinator.forecast_points
        for entry_id in self.coordinator.entry.data.get(CONF_FORECAST_ENTRIES) or []:
            series = points.get(entry_id)
            if not series or max(series) < hour:
                continue
            provider = self.hass.config_entries.async_get_entry(entry_id)
            return (
                provider.title if provider is not None else entry_id,
                {when: kwh * 1000.0 for when, kwh in series.items()},
            )
        return None, None

    def _anchor(self, now: datetime) -> tuple[float, float] | None:
        """This hour's measured production and consumption, in W, if trustworthy."""
        rates = self.coordinator.open_hour_rates(now, ANCHOR_MIN_SECONDS)
        produced = rates.get(Role.PV.key)
        used = rates.get(Role.LOAD.key)
        if produced is None or used is None:
            return None
        return produced, used


def _utc_hour(when: datetime) -> int:
    return when.hour


def _local_hour(tz: tzinfo | None) -> Callable[[datetime], int]:
    """The local hour of a UTC instant, or the UTC hour when no zone resolves."""
    if tz is None:
        return _utc_hour

    def resolve(when: datetime) -> int:
        return when.astimezone(tz).hour

    return resolve
