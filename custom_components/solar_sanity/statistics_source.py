"""Reading Home Assistant history, and writing our own forecast archive.

Two jobs, both leaning on the recorder:

**Reading.** Long-term hourly statistics are never purged and never downsampled
— only ``states`` and the 5-minute short-term table are — so a multi-year yield
analysis is possible from data the user already has.

**Writing.** Forecast history is the thing this product exists to keep. Home
Assistant's own forecast integrations set no ``state_class`` on their energy
sensors, so nothing records them and yesterday's forecast is simply gone. We
write our own external statistics instead of a growing ``.storage`` file:
indexed, never purged, visible in Developer Tools, and it costs no boot time.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import FORECAST_STATISTIC_PREFIX

_LOGGER = logging.getLogger(__name__)


def recorder_available(hass: HomeAssistant) -> bool:
    """Whether the recorder is loaded.

    It can be disabled, and ``get_instance`` raises ``KeyError`` if it is, so
    every call site guards on this rather than catching afterwards.
    """
    return "recorder" in hass.config.components


async def async_energy_between(
    hass: HomeAssistant, statistic_id: str, start: datetime, end: datetime
) -> float | None:
    """Energy in kWh over a window, or ``None`` if it cannot be determined.

    Asks the recorder for ``change`` rather than differencing ``sum`` by hand.
    ``state`` resets with the meter and ``sum`` is a running grand total, so a
    manual difference gets the boundary row wrong; ``change`` does not. The
    singular ``statistic_during_period`` is used deliberately — the plural form
    fetches every hourly row and buckets in Python, which on a five-year query
    means tens of thousands of rows per id.
    """
    if not recorder_available(hass):
        return None

    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistic_during_period

    try:
        result = await get_instance(hass).async_add_executor_job(
            statistic_during_period,
            hass,
            start,
            end,
            statistic_id,
            {"change"},
            {"energy": "kWh"},
        )
    except Exception:
        _LOGGER.debug("statistic_during_period failed for %s", statistic_id, exc_info=True)
        return None

    change = result.get("change") if result else None
    return float(change) if isinstance(change, (int, float)) else None


async def async_hourly_series(
    hass: HomeAssistant,
    power_ids: set[str],
    energy_ids: set[str],
    start: datetime,
    end: datetime,
) -> dict[str, list[tuple[datetime, float, bool]]]:
    """Hourly energy per statistic id, in Wh, for backfilling the window.

    Returns ``(hour, wh, from_mean)`` per id.

    The two kinds have to be asked for differently, and getting this wrong is
    why the backfill previously produced nothing usable:

    * **Energy** statistics have a sum, so ``change`` gives the energy in each
      hour exactly, reset-compensated. Never difference ``sum`` by hand.
    * **Power** statistics have no sum at all — they are ``measurement`` class,
      so they carry mean/min/max. Asking for ``change`` returns ``None`` for
      every row. The hourly ``mean`` multiplied by an hour is the energy, and
      it is a far better estimate than our own five-minute polling because the
      recorder saw every state change rather than one in three hundred.

    Mean-derived values are flagged so the analysis can widen its tolerance and
    refuse to call a finding certain on them.
    """
    if not recorder_available(hass):
        return {}

    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    out: dict[str, list[tuple[datetime, float, bool]]] = {}

    async def _fetch(ids: set[str], types: set[str], units: dict[str, str] | None):
        if not ids:
            return {}
        try:
            return await get_instance(hass).async_add_executor_job(
                statistics_during_period, hass, start, end, ids, "hour", units, types
            )
        except Exception:
            _LOGGER.debug("statistics_during_period failed for %s", types, exc_info=True)
            return {}

    energy_raw = await _fetch(energy_ids, {"change"}, {"energy": "kWh"})
    for statistic_id, rows in (energy_raw or {}).items():
        series: list[tuple[datetime, float, bool]] = []
        for row in rows:
            change = row.get("change")
            started = row.get("start")
            if change is None or started is None:
                # A gap is a gap. It is not zero.
                continue
            series.append(
                (dt_util.utc_from_timestamp(float(started)), float(change) * 1000.0, False)
            )
        out[statistic_id] = series

    power_raw = await _fetch(power_ids, {"mean"}, {"power": "W"})
    for statistic_id, rows in (power_raw or {}).items():
        series = []
        for row in rows:
            mean = row.get("mean")
            started = row.get("start")
            if mean is None or started is None:
                continue
            # Mean watts over an hour is watt-hours.
            series.append((dt_util.utc_from_timestamp(float(started)), float(mean), True))
        out[statistic_id] = series

    return out


def forecast_statistic_id(provider_key: str) -> str:
    """External statistic id for one forecast provider.

    External ids use a colon and have no entity behind them, which is exactly
    what we want: this is our data, not a sensor's history.
    """
    return f"{FORECAST_STATISTIC_PREFIX}{provider_key}"


async def async_record_forecast(
    hass: HomeAssistant,
    provider_key: str,
    provider_name: str,
    wh_hours: dict[str, float],
) -> bool:
    """Persist one provider's day-ahead forecast as external statistics.

    Returns whether anything was written.

    Three constraints the recorder enforces and we must respect:

    * timestamps must be timezone-aware,
    * timestamps must land exactly on the hour — there is no sub-hourly external
      statistic, ever,
    * we own ``sum`` monotonicity, so we resume from whatever was last written.

    Re-importing an hour updates it in place, which makes backfill and
    correction idempotent.
    """
    if not recorder_available(hass) or not wh_hours:
        return False

    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.models import (
        StatisticData,
        StatisticMeanType,
        StatisticMetaData,
    )
    from homeassistant.components.recorder.statistics import (
        async_add_external_statistics,
        get_last_statistics,
    )

    statistic_id = forecast_statistic_id(provider_key)

    # `mean_type` and `unit_class` are REQUIRED. Omitting either is deprecated
    # now and becomes a hard error in Home Assistant 2026.11 — and custom
    # integrations are explicitly not exempt from that deprecation.
    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=f"{provider_name} forecast",
        source=statistic_id.split(":", 1)[0],
        statistic_id=statistic_id,
        unit_class="energy",
        unit_of_measurement="kWh",
    )

    try:
        last = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, statistic_id, True, {"sum"}
        )
    except Exception:
        _LOGGER.debug("get_last_statistics failed for %s", statistic_id, exc_info=True)
        last = {}

    running = 0.0
    rows = last.get(statistic_id) if last else None
    if rows:
        previous = rows[0].get("sum")
        if isinstance(previous, (int, float)):
            running = float(previous)

    points = _normalise_wh_hours(wh_hours)
    if not points:
        return False

    statistics: list[StatisticData] = []
    for when, kwh in points:
        running += kwh
        statistics.append(StatisticData(start=when, state=kwh, sum=running))

    async_add_external_statistics(hass, metadata, statistics)
    _LOGGER.debug("recorded %d forecast points for %s", len(statistics), statistic_id)
    return True


def _normalise_wh_hours(wh_hours: dict[str, float]) -> list[tuple[datetime, float]]:
    """Turn a provider's ``wh_hours`` map into hour-aligned kWh points.

    Two things about this payload catch people out. The values are Wh produced
    *during* the period — a delta, not a running total and not power; the
    upstream field is literally named ``wh_period``. And the periods are not
    guaranteed to be an hour: Forecast.Solar returns finer resolution near now
    and coarser further out, so entries are accumulated into their containing
    hour rather than assumed to be one each.
    """
    hourly: dict[datetime, float] = {}

    for raw_when, raw_value in wh_hours.items():
        when = dt_util.parse_datetime(raw_when)
        if when is None or not isinstance(raw_value, (int, float)):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt_util.UTC)
        slot = dt_util.as_utc(when).replace(minute=0, second=0, microsecond=0)
        hourly[slot] = hourly.get(slot, 0.0) + float(raw_value) / 1000.0

    return sorted(hourly.items())


async def async_get_solar_forecasts(
    hass: HomeAssistant, entry_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Fetch each selected forecast provider's day-ahead estimate.

    Discovery uses the public ``async_process_integration_platforms`` helper
    rather than reaching into ``energy.websocket_api``, which is private and
    singleton-backed. Which entries to score is a choice the user makes in our
    own flow, so this does not depend on their Energy Dashboard being set up.
    """
    if not entry_ids:
        return {}

    from homeassistant.helpers.integration_platform import (
        async_process_integration_platforms,
    )

    platforms: dict[str, Any] = {}

    async def _collect(_hass: HomeAssistant, domain: str, platform: Any) -> None:
        if hasattr(platform, "async_get_solar_forecast"):
            platforms[domain] = platform.async_get_solar_forecast

    try:
        await async_process_integration_platforms(hass, "energy", _collect, wait_for_platforms=True)
    except Exception:
        _LOGGER.debug("energy platform discovery failed", exc_info=True)
        return {}

    out: dict[str, dict[str, Any]] = {}
    for entry_id in entry_ids:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain not in platforms:
            continue
        try:
            forecast = await platforms[entry.domain](hass, entry_id)
        except Exception:
            _LOGGER.debug("forecast fetch failed for %s", entry_id, exc_info=True)
            continue
        if forecast:
            out[entry_id] = forecast
    return out


def utc_day_bounds(when: datetime, days_back: int) -> tuple[datetime, datetime]:
    """A UTC window ending at the top of ``when``'s hour."""
    end = when.replace(minute=0, second=0, microsecond=0)
    return end - timedelta(days=days_back), end


async def async_forecast_providers(hass: HomeAssistant) -> list[tuple[str, str]]:
    """Return ``(entry_id, label)`` for every loaded solar forecast provider.

    Discovery goes through the public integration-platform helper rather than
    ``energy.websocket_api``, which is private and singleton-backed.
    """
    from homeassistant.helpers.integration_platform import (
        async_process_integration_platforms,
    )

    domains: set[str] = set()

    async def _collect(_hass: HomeAssistant, domain: str, platform: Any) -> None:
        if hasattr(platform, "async_get_solar_forecast"):
            domains.add(domain)

    try:
        await async_process_integration_platforms(hass, "energy", _collect, wait_for_platforms=True)
    except Exception:
        _LOGGER.debug("forecast provider discovery failed", exc_info=True)
        return []

    from homeassistant.loader import async_get_integration

    providers: list[tuple[str, str]] = []
    for domain in sorted(domains):
        # A bare entry title is not enough to identify a provider — a
        # Forecast.Solar entry is often just called "Home", which tells the user
        # nothing about which integration it belongs to.
        try:
            integration = await async_get_integration(hass, domain)
            product = integration.name
        except Exception:
            product = domain

        for entry in hass.config_entries.async_entries(domain):
            title = entry.title
            label = product if not title or title == product else f"{product} — {title}"
            providers.append((entry.entry_id, label))
    return providers
