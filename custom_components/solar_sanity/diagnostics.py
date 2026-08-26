"""Downloadable diagnostics, so a bug report carries what we need.

Location is redacted: forecast providers store the site's coordinates, and a
diagnostics file is something users paste into public issue trackers.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import SolarSanityData

TO_REDACT = {
    "latitude",
    "longitude",
    "api_key",
    "token",
    "password",
    "unique_id",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: Any) -> dict[str, Any]:
    data: SolarSanityData | None = getattr(entry, "runtime_data", None)
    report = data.coordinator.report if data else None

    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        # Why the verdict is what it is. Without this, a report of "Not
        # enough data yet" carries no evidence at all and the only way to
        # investigate is to ask the user to read attributes back one by one.
        "coverage": data.coordinator.coverage_snapshot() if data else None,
        "status": report.status.value if report else None,
        "reason": report.reason if report else None,
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
