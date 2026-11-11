"""Present the installation's current setup without starting collection or grading."""

from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_conversion import EnergyConverter, PowerConverter

from ._forecast_plan import forecast_is_fresh
from .analysis.history import OBSERVATION_REASONS
from .analysis.model import ChannelSpec, Role
from .battery_outlook import REASONS
from .const import CONF_FORECAST_ENTRIES, FORECAST_MAX_AGE, OPT_BATTERY_SOC
from .coordinator import (
    KIND_ENERGY,
    KIND_POWER,
    SolarSanityCoordinator,
    channel_kind,
    energy_is_cumulative,
    read_channel,
)
from .journal import COLLECTION_REASONS

READY = "ready"
WAITING = "waiting_for_data"
ATTENTION = "needs_attention"
NOT_CONFIGURED = "not_configured"


def _check(status: str, reason: str | None = None, *, action: str | None = None) -> dict:
    return {"status": status, "reason": reason, "action": action}


def _measurement(coordinator: SolarSanityCoordinator, spec: ChannelSpec, hours: int) -> dict:
    state = coordinator.hass.states.get(spec.entity_id)
    kind = channel_kind(state)
    value, _ = read_channel(state)
    units = {KIND_ENERGY: EnergyConverter.VALID_UNITS, KIND_POWER: PowerConverter.VALID_UNITS}
    unit = spec.declared_unit if spec.declared_unit in units.get(kind, ()) else None
    if state is None:
        if er.async_get(coordinator.hass).async_get(spec.entity_id) is None:
            check = _check(
                ATTENTION, "The configured sensor could not be found.", action="reconfigure"
            )
        else:
            check = _check(WAITING, "The configured sensor has not reported a state.")
    elif kind == KIND_ENERGY and not energy_is_cumulative(state) and unit is not None:
        check = _check(
            ATTENTION,
            "Energy sensors must use the total or total_increasing state class. Choose a "
            "cumulative energy sensor or a power sensor.",
            action="reconfigure",
        )
    elif state.state in ("unknown", "unavailable"):
        check = _check(WAITING, "The configured sensor is temporarily unavailable.")
    elif unit is None:
        check = _check(
            ATTENTION,
            "The sensor must report a supported power or energy unit.",
            action="reconfigure",
        )
    elif value is None:
        check = _check(WAITING, "Waiting for a numeric reading.")
    else:
        check = _check(READY)
    return {"key": spec.key, "kind": kind, "unit": unit, "hours": hours, **check}


def _measurements(coordinator: SolarSanityCoordinator) -> dict:
    buckets = coordinator.buckets
    channels = [
        _measurement(coordinator, spec, sum(b.value(spec.key) is not None for b in buckets))
        for spec in coordinator.specs
        if spec.role is not Role.BATTERY_SOC
    ]
    required = (Role.PV.key, Role.LOAD.key)
    mapped = {channel["key"] for channel in channels}
    for key in required:
        if key not in mapped:
            channels.append(
                {
                    "key": key,
                    "kind": None,
                    "unit": None,
                    "hours": 0,
                    **_check(
                        ATTENTION, "Choose a sensor for this measurement.", action="reconfigure"
                    ),
                }
            )
    if any(c["status"] == ATTENTION for c in channels):
        check = _check(ATTENTION)
    elif any(c["status"] == WAITING for c in channels):
        check = _check(WAITING)
    elif any(not c["hours"] for c in channels if c["key"] in required):
        check = _check(
            WAITING, "Waiting for usable solar generation and house consumption history."
        )
    else:
        check = _check(READY)
    return {**check, "channels": channels}


def _provider(coordinator: SolarSanityCoordinator, entry_id: str, now: datetime) -> dict:
    entry = coordinator.hass.config_entries.async_get_entry(entry_id)
    health = coordinator.journal.health.get(entry_id, {})
    capture = health.get("captured_at")
    code = health.get("reason_code")
    if entry is None:
        check = _check(
            ATTENTION, "The configured provider could not be found.", action="reconfigure"
        )
    elif entry.disabled_by is not None:
        check = _check(ATTENTION, "The configured provider is disabled.", action="reconfigure")
    elif capture and not forecast_is_fresh(datetime.fromisoformat(capture), now, FORECAST_MAX_AGE):
        check = _check(ATTENTION, COLLECTION_REASONS["forecast_stale"])
    elif entry.state is not ConfigEntryState.LOADED:
        check = _check(WAITING, "Waiting for the forecast provider to load.")
    elif code in ("archive_not_submitted", "archive_unverified"):
        check = _check(ATTENTION, COLLECTION_REASONS[code])
    elif code == "capture_unavailable":
        check = _check(WAITING, COLLECTION_REASONS[code])
    elif not capture:
        check = _check(WAITING, COLLECTION_REASONS["waiting_for_capture"])
    elif health.get("verification") != "verified":
        check = _check(WAITING, COLLECTION_REASONS["archive_pending"])
    else:
        check = _check(READY)
    return {
        **check,
        "integration": entry.domain if entry is not None else None,
        "captured_at": capture,
        "verified_at": health.get("verified_at"),
        "verification": health.get("verification"),
    }


def _forecast(coordinator: SolarSanityCoordinator, now: datetime) -> dict:
    providers = [
        _provider(coordinator, entry_id, now)
        for entry_id in coordinator.entry.data.get(CONF_FORECAST_ENTRIES, [])
    ]
    figures = coordinator.roof_forecast() if providers else None
    corrected = (
        figures is not None
        and any(
            value is not None
            for value in (
                figures.power_now_w,
                figures.this_hour_kwh,
                figures.next_hour_kwh,
                figures.remaining_today_kwh,
                figures.today_kwh,
            )
        )
    ) or bool(providers and coordinator.expected_tomorrow_corrected_kwh is not None)
    if not providers:
        check = _check(
            NOT_CONFIGURED, "No solar forecast provider is selected.", action="reconfigure"
        )
    elif any(provider["status"] == ATTENTION for provider in providers):
        check = _check(ATTENTION)
    elif not corrected or any(provider["status"] == WAITING for provider in providers):
        check = _check(WAITING)
    else:
        check = _check(READY)
    attrs = coordinator.roof_forecast_attrs if providers and not corrected else {}
    return {
        **check,
        "providers": providers,
        "corrected": corrected,
        "correction_reason": attrs.get("reason")
        or (
            "The current capture does not cover the corrected forecast hours."
            if providers and not corrected
            else None
        ),
    }


def _battery(coordinator: SolarSanityCoordinator, now: datetime) -> dict:
    battery = coordinator.battery
    settings = battery.settings
    soc_source = coordinator.entry.options.get(OPT_BATTERY_SOC)
    if settings is None:
        check = (
            _check(ATTENTION, "Set a usable battery capacity.", action="configure")
            if soc_source
            else _check(NOT_CONFIGURED, "No battery outlook is set up.", action="configure")
        )
        return {**check, "settings": None, "charge_level": None}

    state = battery.state
    if coordinator.hass.states.get(soc_source) is None and (
        er.async_get(coordinator.hass).async_get(soc_source) is None
    ):
        charge_level = _check(
            ATTENTION, "The configured charge level sensor could not be found.", action="configure"
        )
    elif not state.soc_readable:
        charge_level = _check(WAITING, REASONS[state.reason])
    elif state.soc_pct is None:
        charge_level = _check(WAITING, "Waiting for a usable charge level.")
    else:
        charge_level = _check(READY)

    if charge_level["status"] != READY:
        check = _check(charge_level["status"])
    elif state.outlook is None or state.reason:
        check = _check(WAITING, REASONS.get(state.reason, "Waiting for a battery projection."))
    elif state.outlook.forecast_used and not forecast_is_fresh(
        coordinator.forecast_captured_at, now, FORECAST_MAX_AGE
    ):
        check = _check(WAITING, COLLECTION_REASONS["forecast_stale"])
    else:
        check = _check(READY)
    return {
        **check,
        "settings": {
            "capacity_kwh": settings.capacity_wh / 1000,
            "reserve_pct": settings.reserve_pct,
            "target_pct": settings.target_pct,
        },
        "charge_level": charge_level,
    }


def _history(coordinator: SolarSanityCoordinator, now: datetime) -> dict:
    journal = coordinator.journal
    summary = journal.summary(now=now) if coordinator.battery.configured else None
    if journal.error:
        check = _check(
            ATTENTION, COLLECTION_REASONS.get(journal.error, "The journal is unavailable.")
        )
    elif summary is None:
        check = _check(NOT_CONFIGURED, "History is collected when a battery outlook is set up.")
    elif not (summary["nights"] or summary["mornings"]):
        check = _check(WAITING, "Waiting for the first completed prediction in this setup.")
    else:
        check = _check(READY)
    return {
        **check,
        "days": 28,
        "counts": None
        if summary is None
        else {
            "graded_nights": summary["nights"],
            "graded_mornings": summary["mornings"],
            "pending": summary["pending"],
            "excluded": sum(summary["excluded"].values()),
        },
        "exclusions": [
            {
                "reason": OBSERVATION_REASONS.get(code, "Other reason."),
                "count": count,
            }
            for code, count in sorted(summary["excluded"].items())
        ]
        if summary is not None
        else [],
    }


def setup_check(coordinator: SolarSanityCoordinator, now: datetime) -> dict[str, Any]:
    """Return an allowlisted view, without source entity IDs or journal records."""
    return {
        "time_zone": coordinator.hass.config.time_zone,
        "measurements": _measurements(coordinator),
        "forecast": _forecast(coordinator, now),
        "battery": _battery(coordinator, now),
        "history": _history(coordinator, now),
    }
