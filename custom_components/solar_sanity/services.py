"""Read-only tools over the loaded installation's private in-memory data."""

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from ._forecast_plan import forecast_is_fresh
from .analysis.history import history_rows
from .analysis.outlook import BatterySettings, Outlook, Step, project
from .analysis.scenarios import compare_load
from .battery_controls import control_view, power_override
from .battery_outlook import OutlookState
from .const import (
    DOMAIN,
    FORECAST_MAX_AGE,
    OPT_BATTERY_CHARGE_POWER_LIMIT_W,
    OPT_INVERTER_STANDBY_W,
)
from .journal import MODEL_REVISION
from .setup_check import setup_check


def _power(value):
    try:
        number = float(value)
    except (ValueError, TypeError) as err:
        raise vol.Invalid("Power must be a number.") from err
    if isinstance(value, bool) or not math.isfinite(number) or number < 0:
        raise vol.Invalid("Power must be finite and non-negative.")
    return number


def _duration(value):
    number = _power(value)
    if not number.is_integer() or not 1 <= number <= 1440:
        raise vol.Invalid("Duration must be whole minutes from one minute to 24 hours.")
    return int(number)


def _start_time(value):
    parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or parsed.tzinfo is None:
        raise vol.Invalid("Use an ISO8601 start time with a UTC offset.")
    return parsed


def _projection_view(outlook, uncovered_wh):
    return {
        "lowest_pct": outlook.lowest_pct if outlook.takeover_at is not None else None,
        "full_at": outlook.full_at.isoformat() if outlook.full_at else None,
        "reserve_at": outlook.reserve_at.isoformat() if outlook.reserve_at else None,
        "uncovered_energy_kwh": uncovered_wh / 1000,
        "trajectory": [{"at": t.isoformat(), "soc": value} for t, value in outlook.trajectory],
    }


def _current_projection(coordinator, now: datetime) -> OutlookState:
    state = coordinator.battery.state
    # Two missed five-minute recomputations cannot be called a current basis.
    if (
        state.outlook is None
        or state.reason
        or state.computed_at is None
        or not state.projection_id
        or state.soc_pct is None
        or state.computed_at > now
        or now - state.computed_at > timedelta(minutes=10)
    ):
        raise ServiceValidationError("No current valid battery projection is available.")
    if state.outlook.forecast_used and not forecast_is_fresh(
        coordinator.forecast_captured_at, now, FORECAST_MAX_AGE
    ):
        raise ServiceValidationError(
            "The solar forecast has not been refreshed within three hours."
        )
    return state


def _frozen_inputs(state: OutlookState) -> tuple[BatterySettings, tuple[Step, ...]]:
    steps = tuple(
        Step(
            datetime.fromisoformat(row["start"]),
            row["hours"],
            row["pv_wh"],
            row["load_wh"],
            row["daylight_wh"],
        )
        for row in state.inputs["steps"]
    )
    return BatterySettings(**state.inputs["battery"]), steps


def _preview_power(value: object, *, positive: bool) -> float | None:
    if value is None:
        return None
    try:
        return power_override(value, positive=positive)
    except ValueError as error:
        raise vol.Invalid(str(error)) from error


def _preview_result(outlook: Outlook) -> dict:
    return {
        "lowest_pct": outlook.lowest_pct,
        "reserve_at": outlook.reserve_at.isoformat() if outlook.reserve_at else None,
        "full_at": outlook.full_at.isoformat() if outlook.full_at else None,
        "takeover_at": outlook.takeover_at.isoformat() if outlook.takeover_at else None,
    }


async def coordinator_for(hass: HomeAssistant, call: ServiceCall):
    """Resolve exactly one loaded entry and enforce its entities' read permissions."""
    device = dr.async_get(hass).async_get(call.data["device_id"])
    if device is None:
        raise ServiceValidationError("Select a Solar Sanity installation.")
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id in device.config_entries
        and (DOMAIN, entry.entry_id) in device.identifiers
    ]
    if len(entries) != 1:
        raise ServiceValidationError("Select one Solar Sanity installation.")
    entry = entries[0]
    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    if coordinator is None or entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError("This Solar Sanity installation is not loaded.")
    if call.context.user_id is not None:
        user = await hass.auth.async_get_user(call.context.user_id)
        entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        owned = [e for e in entities if e.platform == DOMAIN and e.device_id == device.id]
        if (
            user is None
            or not user.is_active
            or not owned
            or any(not user.permissions.check_entity(e.entity_id, POLICY_READ) for e in owned)
        ):
            raise Unauthorized()
    return coordinator


@callback
def async_setup_tools(hass: HomeAssistant) -> None:
    """Register native response actions; none triggers capture, grading or control."""

    async def get_setup_check(call: ServiceCall):
        coordinator = await coordinator_for(hass, call)
        return setup_check(coordinator, dt_util.utcnow())

    hass.services.async_register(
        DOMAIN,
        "get_setup_check",
        get_setup_check,
        schema=vol.Schema({vol.Required("device_id"): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )

    async def preview_battery_settings(call: ServiceCall):
        coordinator = await coordinator_for(hass, call)
        state = _current_projection(coordinator, dt_util.utcnow())
        if call.data["projection_id"] != state.projection_id:
            raise ServiceValidationError("The projection has changed. Preview the latest outlook.")
        if state.controls is None:
            raise ServiceValidationError("Battery settings are unavailable for this projection.")
        requested = call.data
        proposed = replace(
            state.controls,
            manual_limit_w=requested[OPT_BATTERY_CHARGE_POWER_LIMIT_W],
            manual_standby_w=requested[OPT_INVERTER_STANDBY_W],
        )
        try:
            settings, steps = _frozen_inputs(state)
            current = project(
                state.soc_pct, settings, steps, forecast_used=state.outlook.forecast_used
            )
            preview = project(
                state.soc_pct,
                proposed.apply(settings),
                steps,
                forecast_used=state.outlook.forecast_used,
            )
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            raise ServiceValidationError("Could not preview these battery settings.") from error
        return {
            "basis": {
                "projection_id": state.projection_id,
                "computed_at": state.computed_at.isoformat(),
                "time_zone": hass.config.time_zone,
                "forecast_used": state.outlook.forecast_used,
            },
            "current": {**_preview_result(current), "controls": control_view(state.controls)},
            "proposed": {**_preview_result(preview), "controls": control_view(proposed)},
        }

    hass.services.async_register(
        DOMAIN,
        "preview_battery_settings",
        preview_battery_settings,
        schema=vol.Schema(
            {
                vol.Required("device_id"): cv.string,
                vol.Required("projection_id"): cv.string,
                vol.Optional(OPT_BATTERY_CHARGE_POWER_LIMIT_W, default=None): (
                    lambda value: _preview_power(value, positive=True)
                ),
                vol.Optional(OPT_INVERTER_STANDBY_W, default=None): (
                    lambda value: _preview_power(value, positive=False)
                ),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )

    async def get_history(call: ServiceCall):
        coordinator = await coordinator_for(hass, call)
        journal = coordinator.journal
        now = dt_util.utcnow()
        since = now.astimezone(coordinator.time_zone or UTC).date() - timedelta(
            days=call.data["days"] - 1
        )
        return {
            "time_zone": hass.config.time_zone,
            "status": "error" if journal.error else "ok" if journal.records else "starting",
            "reason_code": journal.error,
            "records": history_rows(journal.records, since.isoformat(), journal.context()),
        }

    hass.services.async_register(
        DOMAIN,
        "get_forecast_history",
        get_history,
        schema=vol.Schema(
            {
                vol.Required("device_id"): cv.string,
                vol.Optional("days", default=28): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=90)
                ),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )

    async def simulate_load(call: ServiceCall):
        coordinator = await coordinator_for(hass, call)
        now = dt_util.utcnow()
        state = _current_projection(coordinator, now)
        try:
            settings, steps = _frozen_inputs(state)
            comparison = compare_load(
                state.soc_pct,
                settings,
                steps,
                forecast_used=state.outlook.forecast_used,
                power_w=call.data["power_w"],
                duration_minutes=call.data["duration_minutes"],
                start_at=call.data.get("start_at", now),
                now=now,
            )
        except (ValueError, KeyError, TypeError) as err:
            raise ServiceValidationError(str(err)) from err
        return {
            "basis": {
                "projection_id": state.projection_id,
                "computed_at": state.computed_at.isoformat(),
                "horizon_end": state.outlook.horizon_end.isoformat(),
                "time_zone": hass.config.time_zone,
                "model_revision": MODEL_REVISION,
                "forecast_used": state.outlook.forecast_used,
                "reserve_pct": settings.reserve_pct,
                "target_pct": settings.target_pct,
            },
            "applied_start": comparison.applied_start.isoformat(),
            "applied_end": comparison.applied_end.isoformat(),
            "additional_energy_kwh": comparison.additional_wh / 1000,
            "baseline": _projection_view(comparison.baseline, comparison.baseline_uncovered_wh),
            "scenario": _projection_view(comparison.scenario, comparison.scenario_uncovered_wh),
        }

    hass.services.async_register(
        DOMAIN,
        "simulate_load",
        simulate_load,
        schema=vol.Schema(
            {
                vol.Required("device_id"): cv.string,
                vol.Required("power_w"): _power,
                vol.Required("duration_minutes"): _duration,
                vol.Optional("start_at"): _start_time,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
