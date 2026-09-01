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
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ._forecast_plan import dayahead_write_plan, eligible, running_totals
from .const import (
    DAYAHEAD_MIN_LEAD_HOURS,
    FORECAST_DAYAHEAD_PREFIX,
    FORECAST_STATISTIC_PREFIX,
    FORECAST_SUM_LOOKBACK_DAYS,
)

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
        if series:
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
        if series:
            out[statistic_id] = series

    return out


async def async_classify_statistics(
    hass: HomeAssistant, statistic_ids: set[str]
) -> tuple[set[str], set[str], set[str]]:
    """Split ids into ``(sum_backed, mean_backed, absent)``.

    Asks the recorder what it actually holds rather than asking the state
    machine what kind of sensor something is. That distinction matters: an
    MQTT-backed inverter publishes its entities *after* Home Assistant starts,
    so at setup time the state machine knows nothing and every channel would be
    classified as neither — which is exactly how the backfill silently did
    nothing.

    Statistics metadata is available regardless, because it describes history
    that already exists.

    ``absent`` is the useful third answer: no statistics at all, which means the
    source sensor carries no ``state_class`` and its history is not being
    recorded by anyone.
    """
    if not recorder_available(hass) or not statistic_ids:
        return set(), set(), set(statistic_ids)

    from homeassistant.components.recorder.statistics import async_list_statistic_ids

    try:
        metas = await async_list_statistic_ids(hass, statistic_ids)
    except Exception:
        _LOGGER.debug("async_list_statistic_ids failed", exc_info=True)
        return set(), set(), set(statistic_ids)

    sum_backed: set[str] = set()
    mean_backed: set[str] = set()

    for meta in metas or []:
        statistic_id = meta.get("statistic_id")
        if statistic_id not in statistic_ids:
            continue
        if meta.get("has_sum"):
            sum_backed.add(statistic_id)
            continue

        # `has_mean` was replaced by `mean_type`; anything not NONE has one.
        mean_type = meta.get("mean_type")
        if mean_type is not None and int(mean_type) == 0:
            continue

        # Only power may be mean-queried. Asking for an energy statistic in
        # watts applies no conversion at all, so a kWh mean would be stored as
        # though it were watt-hours — a thousandfold error that produces
        # perfectly valid-looking buckets.
        if meta.get("unit_class") == "energy":
            continue

        mean_backed.add(statistic_id)

    absent = statistic_ids - sum_backed - mean_backed
    return sum_backed, mean_backed, absent


def provider_label(product: str, title: str | None) -> str:
    """How a forecast provider should be named wherever it is shown.

    A bare entry title is not enough to identify one. A Forecast.Solar entry is
    very often just called "Home", which says nothing about which integration
    it came from — and on a card comparing providers it is the one thing the
    reader needs. The product name leads; the title is added only when it says
    something the product name does not.
    """
    if not title or title == product:
        return product
    return f"{product} — {title}"


def _statistic_key(provider_key: str) -> str:
    r"""A config entry id, in a form the recorder will accept.

    The recorder validates external statistic ids against
    ``[\da-z_]+:[\da-z_]+`` — lowercase only. Config entry ids created since
    Home Assistant 2023.4 are ULIDs, which are uppercase, so passing one
    through verbatim produced an id the recorder refuses. Capture then failed
    on every write, and the only trace was a debug log nobody reads.

    Lowercasing is safe as well as sufficient: a lowered ULID is still unique,
    and on the older hex entry ids it changes nothing, so no archive already in
    the field is orphaned by this.
    """
    return provider_key.lower()


def forecast_statistic_id(provider_key: str) -> str:
    """External statistic id for one provider's *latest* forecast.

    External ids use a colon and have no entity behind them, which is exactly
    what we want: this is our data, not a sensor's history.
    """
    return f"{FORECAST_STATISTIC_PREFIX}{_statistic_key(provider_key)}"


def dayahead_statistic_id(provider_key: str) -> str:
    """External statistic id for one provider's *day-ahead* forecast.

    Separate from the latest series on purpose. See FORECAST_DAYAHEAD_PREFIX.
    """
    return f"{FORECAST_DAYAHEAD_PREFIX}{_statistic_key(provider_key)}"


def _metadata(statistic_id: str, name: str) -> Any:
    """Metadata for one of our external forecast series.

    ``mean_type`` and ``unit_class`` are REQUIRED. Omitting either is deprecated
    now and becomes a hard error in Home Assistant 2026.11 — and custom
    integrations are explicitly not exempt from that deprecation.
    """
    from homeassistant.components.recorder.models import (
        StatisticMeanType,
        StatisticMetaData,
    )

    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=name,
        source=statistic_id.split(":", 1)[0],
        statistic_id=statistic_id,
        unit_class="energy",
        unit_of_measurement="kWh",
    )


async def async_forecast_series(
    hass: HomeAssistant, statistic_id: str, start: datetime, end: datetime
) -> dict[datetime, float] | None:
    """Hourly forecast kWh from one of our archives, by UTC hour.

    Reads ``state`` and only ``state``. Never ``sum``, never ``change``.

    ``state`` is the figure we wrote for that hour and survives every
    re-import. The ``sum`` column is bookkeeping we maintain so the recorder's
    contract is satisfied and the series is usable as an Energy Dashboard
    source; it is a running total across a horizon that is rewritten many times
    a day, so differencing it says nothing about any hour.

    ``None`` means the query failed, which is not the same as an empty archive.
    """
    if not recorder_available(hass):
        return None

    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    try:
        raw = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            start,
            end,
            {statistic_id},
            "hour",
            {"energy": "kWh"},
            {"state"},
        )
    except Exception:
        _LOGGER.debug("forecast read failed for %s", statistic_id, exc_info=True)
        return None

    out: dict[datetime, float] = {}
    for row in (raw or {}).get(statistic_id, []):
        state = row.get("state")
        started = row.get("start")
        if state is None or started is None:
            continue
        out[dt_util.utc_from_timestamp(float(started))] = float(state)
    return out


async def _async_resume_sum(
    hass: HomeAssistant, statistic_id: str, before: datetime
) -> float | None:
    """The running total to carry into a write starting at ``before``.

    ``None`` means do not write.

    This used to resume from ``get_last_statistics``, which returns the row with
    the *greatest* start — after any normal capture that is the far end of
    tomorrow's horizon, not the hour before the window about to be written. Each
    capture therefore added an entire forecast horizon to a total that should
    have advanced by one hour, roughly fifty times a day.

    A failed lookup returns ``None`` rather than starting again at zero. Zero is
    a real answer only for an archive that is genuinely empty; anywhere else it
    sends ``sum`` backwards, and writing a broken running total is worse than
    skipping one capture.
    """
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import (
        get_last_statistics,
        statistics_during_period,
    )

    try:
        last = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, statistic_id, True, {"sum"}
        )
    except Exception:
        _LOGGER.debug("get_last_statistics failed for %s", statistic_id, exc_info=True)
        return None

    if not last or not last.get(statistic_id):
        # Genuinely empty. This is the one case where zero is the truth.
        return 0.0

    try:
        rows = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            before - timedelta(days=FORECAST_SUM_LOOKBACK_DAYS),
            before,
            {statistic_id},
            "hour",
            None,
            {"sum"},
        )
    except Exception:
        _LOGGER.debug("sum lookback failed for %s", statistic_id, exc_info=True)
        return None

    preceding = (rows or {}).get(statistic_id) or []
    if not preceding:
        # The archive holds rows, but none in the week before this write. A gap
        # that long cannot be resumed across without inventing a total.
        _LOGGER.debug("no resumable total for %s before %s", statistic_id, before)
        return None

    total = preceding[-1].get("sum")
    return float(total) if isinstance(total, (int, float)) else None


def _as_rows(points: list[tuple[datetime, float]], running: float) -> list[Any]:
    from homeassistant.components.recorder.models import StatisticData

    return [
        StatisticData(start=when, state=state, sum=total)
        for when, state, total in running_totals(points, running)
    ]


async def async_record_forecast(
    hass: HomeAssistant,
    provider_key: str,
    provider_name: str,
    wh_hours: dict[str, float],
    now: datetime | None = None,
) -> bool:
    """Persist one provider's forecast, as two series.

    Returns whether anything was written.

    Three constraints the recorder enforces and we must respect: timestamps must
    be timezone-aware; they must land exactly on the hour, because there is no
    sub-hourly external statistic, ever; and we own ``sum`` monotonicity.

    Re-importing an hour updates it in place, which is what makes the latest
    series idempotent — and is also why it cannot answer what was forecast
    yesterday. That is the day-ahead series' job.
    """
    if not recorder_available(hass) or not wh_hours:
        return False

    from homeassistant.components.recorder.statistics import async_add_external_statistics

    points = _normalise_wh_hours(wh_hours)
    if not points:
        return False

    when_now = now or dt_util.utcnow()
    written = False

    latest_id = forecast_statistic_id(provider_key)
    running = await _async_resume_sum(hass, latest_id, points[0][0])
    if running is not None:
        async_add_external_statistics(
            hass, _metadata(latest_id, f"{provider_name} forecast"), _as_rows(points, running)
        )
        _LOGGER.debug("recorded %d forecast points for %s", len(points), latest_id)
        written = True

    if await _async_record_dayahead(hass, provider_key, provider_name, points, when_now):
        written = True
    return written


async def _async_record_dayahead(
    hass: HomeAssistant,
    provider_key: str,
    provider_name: str,
    points: list[tuple[datetime, float]],
    now: datetime,
) -> bool:
    """Write only hours still far enough ahead, and only once each.

    An hour already present here keeps the value it was first given. That
    immutability is the whole point: it is what makes "this is what was
    forecast a day ahead" a statement rather than a hope.
    """
    from homeassistant.components.recorder.statistics import async_add_external_statistics

    ahead = eligible(points, now, DAYAHEAD_MIN_LEAD_HOURS)
    if not ahead:
        return False

    statistic_id = dayahead_statistic_id(provider_key)
    existing = await async_forecast_series(
        hass, statistic_id, ahead[0][0], ahead[-1][0] + timedelta(hours=1)
    )
    if existing is None:
        return False

    tail = dayahead_write_plan(points, existing, now, DAYAHEAD_MIN_LEAD_HOURS)
    if not tail:
        return False

    running = await _async_resume_sum(hass, statistic_id, tail[0][0])
    if running is None:
        return False

    async_add_external_statistics(
        hass,
        _metadata(statistic_id, f"{provider_name} forecast, a day ahead"),
        _as_rows(tail, running),
    )
    _LOGGER.debug("recorded %d day-ahead rows for %s", len(tail), statistic_id)
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
            label = provider_label(product, entry.title)
            providers.append((entry.entry_id, label))
    return providers


async def async_daily_soc_swing(
    hass: HomeAssistant,
    entity_id: str,
    start: datetime,
    end: datetime,
    utc_offset_hours: float,
) -> dict[date, float]:
    """Peak-to-trough battery state of charge per local day, in percent.

    Read from the recorder's hourly ``min`` and ``max`` rather than ``mean``,
    because the question is how far the battery swung and a mean is exactly the
    wrong statistic for that — a battery cycling 35% to 95% and one sitting at
    65% have the same mean and nothing else in common.

    Grouped by *local* day for the same reason the buckets are: a swing that
    straddles midnight UTC is one cycle, not two halves.

    Returns an empty mapping on any failure. A missing answer here degrades the
    cause to "undetermined" and the note falls back to offering both, which is
    the behaviour without this sensor at all.
    """
    if not recorder_available(hass) or not entity_id:
        return {}

    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    try:
        rows = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            start,
            end,
            {entity_id},
            "hour",
            None,
            # A fresh set every call: `statistics_during_period` mutates the one
            # it is given, and a module-level constant would be emptied by the
            # first call and silently return nothing ever after.
            {"min", "max"},
        )
    except Exception:
        _LOGGER.debug("state-of-charge statistics failed for %s", entity_id, exc_info=True)
        return {}

    offset = timedelta(hours=utc_offset_hours)
    lo: dict[date, float] = {}
    hi: dict[date, float] = {}
    for row in (rows or {}).get(entity_id, []):
        started = row.get("start")
        low, high = row.get("min"), row.get("max")
        if started is None or low is None or high is None:
            continue
        if not isinstance(started, datetime):
            started = dt_util.utc_from_timestamp(started)
        day = (started + offset).date()
        lo[day] = min(lo.get(day, low), low)
        hi[day] = max(hi.get(day, high), high)

    return {day: hi[day] - lo[day] for day in lo if day in hi}
