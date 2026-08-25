"""Solar Sanity — tells you whether your solar data adds up.

Sets up the coordinator, wires the three sampling tiers, and starts capturing
forecasts immediately. That last one matters more than it looks: Home
Assistant's forecast integrations set no ``state_class`` on their energy
sensors, so nothing records them and yesterday's forecast is gone within about
ten days. Every day capture is not running is history that cannot be recovered.

No config-entry update listener is registered anywhere. Combining one with
``async_update_reload_and_abort`` or ``OptionsFlowWithReload`` is deprecated as
of Home Assistant 2026.6 and an error from 2026.12, so this integration takes
the listener-free path throughout.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from . import frontend
from .analysis.model import Status
from .const import (
    BUCKET_INTERVAL,
    DOMAIN,
    FORECAST_CAPTURE_INTERVAL,
    LIVE_INTERVAL,
    PLATFORMS,
    SERVICE_EXPORT_REPORT,
    SERVICE_VALIDATE_NOW,
)
from .coordinator import SolarSanityCoordinator, SolarSanityData
from .repairs import async_sync_issues
from .statistics_source import async_hourly_series, utc_day_bounds

_LOGGER = logging.getLogger(__name__)

type SolarSanityConfigEntry = ConfigEntry[SolarSanityData]

#: Days of statistics pulled in at setup so a fresh install has an answer
#: immediately rather than after a week of its own measurement.
BACKFILL_DAYS = 30

#: There is nothing to configure in YAML — everything comes from the config
#: entry. ``async_setup`` exists only to register the bundled card, which has
#: to happen once per install rather than once per entry.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the bundled card. Once per install, not once per entry."""
    version = "0.1.0"
    integration = hass.data.get("integrations", {}).get(DOMAIN)
    if integration is not None and getattr(integration, "version", None):
        version = str(integration.version)

    await frontend.async_register(hass, version)
    _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SolarSanityConfigEntry) -> bool:
    """Set up one monitored installation."""
    coordinator = SolarSanityCoordinator(hass, entry)
    await coordinator.async_restore()

    await _async_backfill(hass, coordinator)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = SolarSanityData(coordinator=coordinator, store=coordinator._store)

    # The live tripwire. Cheap, and the only way to see instantaneous
    # impossibilities that average out inside an hourly bucket.
    entry.async_on_unload(
        async_track_time_interval(hass, lambda _now: coordinator.capture_live(), LIVE_INTERVAL)
    )

    def _sample(_now: Any) -> None:
        coordinator.accumulate()
        # Sensors describing live state must not wait for the six-hourly
        # analysis before they are rewritten.
        coordinator.notify_live_entities()

    entry.async_on_unload(async_track_time_interval(hass, _sample, BUCKET_INTERVAL))

    async def _capture(_now: Any) -> None:
        try:
            await coordinator.async_capture_forecasts()
        except Exception:
            _LOGGER.debug("forecast capture failed", exc_info=True)

    entry.async_on_unload(async_track_time_interval(hass, _capture, FORECAST_CAPTURE_INTERVAL))
    await _capture(None)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_sync_issues(hass, entry, coordinator.report)

    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: hass.async_create_task(async_sync_issues(hass, entry, coordinator.report))
        )
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SolarSanityConfigEntry) -> bool:
    """Unload. ``runtime_data`` is cleared for us, so there is no bookkeeping."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_backfill(hass: HomeAssistant, coordinator: SolarSanityCoordinator) -> None:
    """Seed the analysis window from long-term statistics."""
    specs = coordinator.specs
    if not specs:
        return

    from homeassistant.util import dt as dt_util

    start, end = utc_day_bounds(dt_util.utcnow(), BACKFILL_DAYS)
    series = await async_hourly_series(hass, {spec.entity_id for spec in specs}, start, end)
    if series:
        coordinator.ingest_backfill(series)


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_VALIDATE_NOW):
        return

    async def _validate_now(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_entries(DOMAIN):
            data = getattr(entry, "runtime_data", None)
            if data is not None:
                await data.coordinator.async_request_refresh()

    async def _export_report(call: ServiceCall) -> dict[str, Any]:
        """Return a plain-language summary.

        Deliberately shaped for pasting into a warranty email or a forum post —
        that is the situation a user is usually in when they want this.
        """
        reports: list[dict[str, Any]] = []
        for entry in hass.config_entries.async_entries(DOMAIN):
            data = getattr(entry, "runtime_data", None)
            if data is None or data.coordinator.report is None:
                continue
            report = data.coordinator.report
            reports.append(
                {
                    "title": entry.title,
                    "status": report.status.value,
                    "finding": report.finding.headline if report.finding else None,
                    "detail": report.finding.detail if report.finding else None,
                    "days_of_data": report.residual.valid_days,
                    "checked": report.status is not Status.NOT_CHECKABLE,
                }
            )
        return {"installations": reports}

    hass.services.async_register(DOMAIN, SERVICE_VALIDATE_NOW, _validate_now)
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_REPORT,
        _export_report,
        supports_response=SupportsResponse.ONLY,
    )
