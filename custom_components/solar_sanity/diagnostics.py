"""Downloadable diagnostics, so a bug report carries what we need.

Location is redacted: forecast providers store the site's coordinates, and a
diagnostics file is something users paste into public issue trackers.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .battery_outlook import OutlookState
from .const import OPT_BATTERY_SOC
from .coordinator import SolarSanityData
from .statistics_source import async_hourly_soc

#: ``unique_id`` is deliberately absent. It held a join of the mapped entity
#: ids, which appear unredacted two lines below inside the entry's own data — so
#: redacting it concealed nothing while hiding which identity scheme an entry
#: was created under, which is exactly what a duplicate-entry report needs.
TO_REDACT = {
    "latitude",
    "longitude",
    "api_key",
    "token",
    "password",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: Any) -> dict[str, Any]:
    data: SolarSanityData | None = getattr(entry, "runtime_data", None)
    report = data.coordinator.report if data else None

    forecasts = await data.coordinator.async_forecast_snapshot() if data else {}
    soc_entity = entry.options.get(OPT_BATTERY_SOC)
    now = dt_util.utcnow()

    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        # The one record that cannot be reconstructed later, so whether it is
        # actually accumulating has to be checkable rather than assumed.
        "forecast_archive": forecasts,
        # Why the verdict is what it is. Without this, a report of "Not
        # enough data yet" carries no evidence at all and the only way to
        # investigate is to ask the user to read attributes back one by one.
        "coverage": data.coordinator.coverage_snapshot() if data else None,
        # The arithmetic itself. `coverage` says how much data there is; this is
        # the data, so somebody holding this file can run the engine over it and
        # get the same verdict rather than reasoning about a summary of it.
        "window": data.coordinator.window_snapshot() if data else None,
        # The battery outlook as it stands, with everything it was computed from.
        "outlook": _outlook(data.coordinator.battery.state) if data else None,
        # What the battery actually did, and what was forecast a day ahead.
        # Together with `window`, enough to replay the outlook hour by hour and
        # grade it against the charge level it predicted.
        "soc_history": (
            await async_hourly_soc(hass, soc_entity, now - timedelta(days=32), now)
            if soc_entity
            else []
        ),
        "forecast_history": await data.coordinator.async_forecast_history() if data else {},
        "status": report.status.value if report else None,
        # Whether the identity was *shown* to fail, as opposed to merely not
        # closing tidily. "Still looking" is reached both ways and the word is
        # the same, so without this the two are indistinguishable from outside.
        "identity_fails": report.identity_fails if report else None,
        "reason": report.reason if report else None,
        "notes": list(report.notes) if report else [],
        # Measured, then not acted on. Without these, "nothing could be
        # established" is unfalsifiable from outside the process.
        "measurements": dict(report.measurements) if report else {},
        "finding": (
            {
                "code": report.finding.code,
                "confidence": report.finding.confidence.value,
                "severity": report.finding.severity.value,
                "channels": list(report.finding.channel_keys),
                "explained_fraction": report.finding.explained_fraction,
                "days_supporting": report.finding.days_supporting,
            }
            if report and report.finding
            else None
        ),
        "deferred": list(report.deferred) if report else [],
        "topology": (
            {
                "coupling": report.topology.coupling.value,
                "pv_measured_dc": report.topology.pv_measured_dc,
                "battery_measured_dc": report.topology.battery_measured_dc,
            }
            if report
            else None
        ),
        "loss_model": (
            {
                "pv_dc_gamma": report.loss_model.pv_dc_gamma,
                "battery_dc_gamma": report.loss_model.battery_dc_gamma,
                "standby_w": report.loss_model.standby_w,
                "samples": report.loss_model.samples,
                # Which terms were established rather than defaulted. Every
                # term falls back to 0.0, so this is the only way to tell a
                # measured absence of loss from a fit that found nothing.
                "fitted_terms": list(report.loss_model.fitted_terms),
            }
            if report and report.loss_model
            else None
        ),
        "residual": (
            {
                "median_daily_abs_pct": report.residual.median_daily_abs_pct,
                "valid_days": report.residual.valid_days,
                "band": report.residual.band,
            }
            if report
            else None
        ),
    }


def _outlook(state: OutlookState) -> dict[str, Any]:
    outlook = state.outlook

    def iso(when: Any) -> str | None:
        return when.isoformat() if when is not None else None

    return {
        "reason": state.reason,
        "computed_at": iso(state.computed_at),
        "settings": state.settings,
        "soc_age_s": state.soc_age_s,
        "provider": state.provider,
        "consumption_days": state.load_days,
        "grid_charged_hours_14d": state.grid_charged_hours,
        "max_charge_w": state.max_charge_w,
        "standby_w": state.standby_w,
        "forecast_scale": (
            {"factor": state.scale.factor, "hours": state.scale.hours, "days": state.scale.days}
            if state.scale is not None
            else None
        ),
        # Per hour of the day: consumption by local hour, production by UTC hour.
        "load_profile_wh": list(state.load_profile),
        "pv_profile_wh": list(state.pv_profile),
        "published": {"full_at": iso(state.full_at), "reserve_at": iso(state.reserve_at)},
        "projection": (
            {
                "soc_pct": outlook.soc_pct,
                "full_at": iso(outlook.full_at),
                "reserve_at": iso(outlook.reserve_at),
                "lowest_pct": outlook.lowest_pct,
                "lowest_at": iso(outlook.lowest_at),
                "night_from": iso(outlook.night_from),
                "takeover_at": iso(outlook.takeover_at),
                "safe_load_w": outlook.safe_load_w,
                "shortfall_wh": outlook.shortfall_wh,
                "spare_wh": outlook.spare_wh,
                "spare_from": iso(outlook.spare_from),
                "horizon_end": iso(outlook.horizon_end),
                "forecast_used": outlook.forecast_used,
                "trajectory": [[iso(when), round(soc, 2)] for when, soc in outlook.trajectory],
            }
            if outlook is not None
            else None
        ),
    }
