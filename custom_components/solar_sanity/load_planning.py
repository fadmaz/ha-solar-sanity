"""Installation-specific inputs for manual load planning."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.helpers.sun import get_astral_event_date

from .analysis.forecast import Correction
from .analysis.model import Role
from .analysis.outlook import HORIZON_HOURS, Step
from .analysis.profile import hourly_profile
from .const import (
    BUCKET_INTERVAL,
    CONF_CHANNELS,
    CONF_FORECAST_ENTRIES,
    CONF_ROLE,
    EXPORT_UNKNOWN,
    OPT_BATTERY_POWER,
    OPT_BATTERY_SOC,
    OPT_PLANNING_MAX_GRID_KWH,
    OPT_PLANNING_MIN_SOC_PCT,
    OPT_SOLAR_ONLY_PLANNING,
)
from .scoring import forecast_fit_for

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from .coordinator import SolarSanityCoordinator

MANUAL_PLANNING_REASON = "This result is for manual planning."
_HOUR = timedelta(hours=1)

PLANNING_REASONS = {
    "battery_setup_incomplete": "Configure the battery charge sensor and capacity.",
    "solar_only_not_confirmed": "Confirm grid-connected solar without a battery in Configure.",
    "learning_consumption": "There is not enough measured house consumption for these hours.",
    "forecast_unavailable": "Waiting for a fresh solar forecast.",
    "correction_unavailable": "Waiting for a roof correction for this forecast.",
    "forecast_incomplete": "The forecast is missing hours in this planning window.",
    "beyond_horizon": "The planning window extends beyond the current outlook.",
    "window_too_short": "The load cannot finish by the deadline.",
    "forecast_fit_pending": "The hourly planning check has not passed. See Setup for details.",
}


class PlanningUnavailableError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(PLANNING_REASONS[reason])
        self.reason_code = reason


def load_power(value: object) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError) as err:
        raise vol.Invalid("Power must be a number.") from err
    if isinstance(value, bool) or not math.isfinite(number) or number < 0:
        raise vol.Invalid("Power must be finite and non-negative.")
    return number


def load_duration(value: object) -> int:
    number = load_power(value)
    if not number.is_integer() or not 1 <= number <= 1440:
        raise vol.Invalid("Duration must be whole minutes from one minute to 24 hours.")
    return int(number)


def has_battery_channels(data: Mapping[str, Any], options: Mapping[str, Any]) -> bool:
    return bool(
        options.get(OPT_BATTERY_SOC)
        or options.get(OPT_BATTERY_POWER)
        or any(
            channel.get(CONF_ROLE) in (Role.BATTERY_CHARGE.key, Role.BATTERY_DISCHARGE.key)
            for channel in data.get(CONF_CHANNELS, ())
        )
    )


def _solar_enabled(coordinator: SolarSanityCoordinator) -> bool:
    entry = coordinator.entry
    return bool(
        entry.options.get(OPT_SOLAR_ONLY_PLANNING)
        and not has_battery_channels(entry.data, entry.options)
    )


def solar_planning_correction(
    coordinator: SolarSanityCoordinator, provider: str
) -> Correction | None:
    correction = coordinator._correction_for(provider, None)
    if correction is not None:
        return correction
    fit = forecast_fit_for(coordinator, provider)
    return Correction(1.0, "validated", fit.training_days) if fit.eligible else None


def planning_metadata(coordinator: SolarSanityCoordinator, now: datetime) -> dict:
    """Defaults and a small update signal for the card, with no I/O."""
    options = coordinator.entry.options
    if settings := coordinator.battery.settings:
        saved = options.get(OPT_PLANNING_MIN_SOC_PCT)
        minimum = saved if saved is not None and saved >= settings.reserve_pct else None
        return {
            "mode": "battery",
            "reserve_pct": settings.reserve_pct,
            "minimum_soc_pct": minimum,
        }
    if _solar_enabled(coordinator):
        tick = int(now.timestamp() // BUCKET_INTERVAL.total_seconds())
        sources = tuple(
            (
                provider,
                coordinator.forecast_capture_time(provider),
                solar_planning_correction(coordinator, provider),
            )
            for provider in coordinator.entry.data.get(CONF_FORECAST_ENTRIES) or ()
            if coordinator.forecast_available_for_planning(provider)
        )
        context = (
            coordinator.entry.entry_id,
            tick,
            sources,
            coordinator.time_zone,
            coordinator.preferred_forecast_provider,
            coordinator.export_evidence.context,
        )
        return {
            "mode": "solar_only",
            "max_extra_grid_kwh": options.get(OPT_PLANNING_MAX_GRID_KWH),
            "revision": sha256(repr(context).encode()).hexdigest(),
            "recheck_forecast": coordinator.export_evidence.mode != EXPORT_UNKNOWN,
        }
    return {"mode": None}


@dataclass(frozen=True, slots=True)
class SolarPlanningBasis:
    steps: tuple[Step, ...]
    projection_id: str
    provider: str
    captured_at: datetime
    computed_at: datetime
    correction: Correction


def solar_planning_basis(
    coordinator: SolarSanityCoordinator, now: datetime, begin: datetime, end: datetime
) -> SolarPlanningBasis:
    """Use one corrected source across the requested window, even on clock changes."""
    if not _solar_enabled(coordinator):
        entry = coordinator.entry
        reason = (
            "battery_setup_incomplete"
            if has_battery_channels(entry.data, entry.options)
            else "solar_only_not_confirmed"
        )
        raise PlanningUnavailableError(reason)
    if end <= begin:
        raise PlanningUnavailableError("window_too_short")
    if end > now + timedelta(hours=HORIZON_HOURS):
        raise PlanningUnavailableError("beyond_horizon")

    zone = coordinator.time_zone or UTC
    load = hourly_profile(
        coordinator.buckets,
        Role.LOAD.key,
        lambda hour: hour.astimezone(zone).hour,
        until=now,
    )
    slots = []
    hour = begin.replace(minute=0, second=0, microsecond=0)
    while hour < end:
        expected = load.at(hour.astimezone(zone).hour)
        if expected is None:
            raise PlanningUnavailableError("learning_consumption")
        slots.append((hour, expected))
        hour += _HOUR

    days = {hour.astimezone(zone).date() for hour, _ in slots}
    daylight: dict[date, tuple[datetime | None, datetime | None]] = {}
    for day in days:
        try:
            daylight[day] = (
                get_astral_event_date(coordinator.hass, SUN_EVENT_SUNRISE, day),
                get_astral_event_date(coordinator.hass, SUN_EVENT_SUNSET, day),
            )
        except (ValueError, OverflowError):
            daylight[day] = (None, None)

    providers = coordinator.entry.data.get(CONF_FORECAST_ENTRIES) or ()
    preferred = coordinator.preferred_forecast_provider
    ordered = ((preferred,) if preferred is not None else ()) + tuple(
        provider for provider in providers if provider != preferred
    )
    problems = set()
    for provider in ordered:
        entry = coordinator.hass.config_entries.async_get_entry(provider)
        captured = coordinator.forecast_capture_time(provider)
        points = coordinator.forecast_points.get(provider)
        if (
            entry is None
            or entry.state is not ConfigEntryState.LOADED
            or entry.disabled_by is not None
            or not points
            or captured is None
            or not coordinator.forecast_source_is_fresh(provider, captured, now)
            or not coordinator.forecast_available_for_planning(provider)
        ):
            continue

        correction = solar_planning_correction(coordinator, provider)
        if correction is None:
            reason = (
                "correction_unavailable"
                if coordinator.export_evidence.mode == EXPORT_UNKNOWN
                else "forecast_fit_pending"
            )
            problems.add(reason)
            continue
        supplied_days = {point.astimezone(zone).date() for point in points}
        steps = []
        for hour, expected in slots:
            day = hour.astimezone(zone).date()
            sunrise, sunset = daylight[day]
            solar = points.get(hour)
            if solar is None:
                # Sparse forecasts can omit darkness, but a daytime hole has
                # no evidence and cannot give a complete comparison.
                if (
                    day not in supplied_days
                    or sunrise is None
                    or sunset is None
                    or not (hour + _HOUR <= sunrise or hour >= sunset)
                ):
                    break
                solar = 0.0
            left, right = max(hour, begin), min(hour + _HOUR, end)
            fraction = (right - left).total_seconds() / 3600
            pv_wh = correction.apply(solar) * 1000 * fraction
            steps.append(Step(left, fraction, pv_wh, expected * fraction, pv_wh))
        if len(steps) == len(slots):
            return SolarPlanningBasis(
                tuple(steps),
                planning_metadata(coordinator, now)["revision"],
                entry.title,
                captured,
                now,
                correction,
            )
        problems.add("forecast_incomplete")

    for reason in ("forecast_incomplete", "forecast_fit_pending", "correction_unavailable"):
        if reason in problems:
            raise PlanningUnavailableError(reason)
    raise PlanningUnavailableError("forecast_unavailable")


def planning_status(coordinator: SolarSanityCoordinator, now: datetime) -> dict:
    metadata = planning_metadata(coordinator, now)
    if metadata["mode"] == "battery":
        problem = coordinator.battery.projection_problem(now)
        if problem is None:
            state = coordinator.battery.state
            if coordinator.battery.projection_soc_problem(state, now):
                problem = "Waiting for an outlook based on the current battery charge."
        return {
            **metadata,
            "status": "ready" if problem is None else "waiting_for_data",
            "reason": problem,
            "action": None,
        }
    try:
        next_hour = now.replace(minute=0, second=0, microsecond=0) + _HOUR
        solar_planning_basis(coordinator, now, now, next_hour)
    except PlanningUnavailableError as error:
        needs_setup = metadata["mode"] is None
        return {
            **metadata,
            "status": "not_configured" if needs_setup else "waiting_for_data",
            "reason_code": error.reason_code,
            "reason": str(error),
            "action": "configure" if needs_setup else None,
        }
    return {**metadata, "status": "ready", "reason": None, "action": None}
