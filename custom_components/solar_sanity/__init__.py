"""Solar Sanity — where your battery is heading, and what the sun was forecast to do.

Sets up the coordinator, wires the sampling ticks, and starts capturing
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
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from . import frontend
from .const import (
    BUCKET_INTERVAL,
    DOMAIN,
    FORECAST_CAPTURE_INTERVAL,
    LIVE_INTERVAL,
    PLATFORMS,
    SERVICE_RESCORE_FORECASTS,
    STORAGE_KEY_STATE,
    STORAGE_VERSION,
)
from .coordinator import SolarSanityCoordinator, SolarSanityData
from .journal import JOURNAL_KEY, JOURNAL_VERSION
from .repairs import RETIRED_CODES, remove_energy_issues, sync_energy_issues
from .statistics_source import (
    async_classify_statistics,
    async_hourly_series,
    utc_day_bounds,
)

_LOGGER = logging.getLogger(__name__)

type SolarSanityConfigEntry = ConfigEntry[SolarSanityData]

#: Days of statistics pulled in at setup so a fresh install has a consumption
#: profile immediately rather than after a week of its own measurement.
BACKFILL_DAYS = 30

#: Entities nothing writes any more, as (platform, key): the energy-balance
#: check's, retired in 0.30.0, and the uncorrected "Forecast tomorrow", retired
#: in 0.33.0 because the provider's own sensor says the same. Removed from the
#: registry at every setup: an entity restored as unavailable forever reads as
#: a fault. Every setup rather than once, so a downgrade and a re-upgrade are
#: covered.
RETIRED_ENTITIES: tuple[tuple[str, str], ...] = (
    ("sensor", "status"),
    ("sensor", "data_completeness"),
    ("sensor", "corrections_active"),
    ("sensor", "live_residual"),
    ("binary_sensor", "data_healthy"),
    ("sensor", "expected_tomorrow"),
)

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
    await _async_retire_the_check(hass)
    return True


async def _async_retire_the_check(hass: HomeAssistant) -> None:
    """Remove what the energy-balance check left behind. Safe to repeat.

    Its Repairs issues, which nothing will ever resolve now: a card with a Fix
    button that can only abort. And the state file from before files were kept
    per entry, which only ever held that check's fitted model. Both are shared
    across entries, so this runs once per start rather than per entry.
    """
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN and any(issue_id.startswith(code + "_") for code in RETIRED_CODES):
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    try:
        await Store(hass, STORAGE_VERSION, STORAGE_KEY_STATE).async_remove()
    except Exception:
        _LOGGER.debug("could not remove the shared state file", exc_info=True)


def _remove_retired_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Take the check's entities out of the registry, before the platforms load."""
    registry = er.async_get(hass)
    for platform, key in RETIRED_ENTITIES:
        entity_id = registry.async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")
        if entity_id is not None:
            registry.async_remove(entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: SolarSanityConfigEntry) -> bool:
    """Set up one monitored installation."""
    coordinator = SolarSanityCoordinator(hass, entry)
    await coordinator.async_restore()
    await coordinator.journal.async_restore()
    entry.async_on_unload(coordinator.journal.cancel)

    await _async_backfill(hass, coordinator)
    await coordinator.async_config_entry_first_refresh()
    coordinator.accumulate()
    sync_energy_issues(hass, entry, coordinator)

    entry.runtime_data = SolarSanityData(coordinator=coordinator, store=coordinator._store)

    # The battery outlook's quick tick: it recomputes only when the charge level
    # has moved since the last projection, so a sensor that reports every
    # second costs nothing. The five-minute tick below recomputes regardless.
    #
    # Both ticks are callbacks, so they run on the event loop. A plain function
    # given to a timer runs in a worker thread instead, where writing an
    # entity's state raises: until 0.31.1 both ticks were plain, so the outlook
    # was computed every five minutes and its sensors were written only at
    # setup and by the six-hourly refresh. It also closed hours in that thread
    # while the power integrator wrote the same buckets from the loop. One
    # computation is 2.3 ms on the reference installation's 721 hours.
    @callback
    def _tripwire(_now: Any) -> None:
        coordinator.battery.refresh()
        coordinator.notify_live_entities()
        coordinator.journal.schedule()

    entry.async_on_unload(async_track_time_interval(hass, _tripwire, LIVE_INTERVAL))

    # Power is integrated on its own state changes rather than sampled. A timer
    # cannot see a kettle that ran for ninety seconds between two ticks.
    entry.async_on_unload(coordinator.async_track_power())
    entry.async_on_unload(coordinator.battery.async_track())

    @callback
    def _sample(_now: Any) -> None:
        coordinator.accumulate()
        sync_energy_issues(hass, entry, coordinator)
        coordinator.battery.refresh(force=True)
        # The sensors describe now and must not wait for the coordinator's
        # six-hourly refresh before they are rewritten.
        coordinator.notify_live_entities()
        coordinator.journal.schedule()

    entry.async_on_unload(async_track_time_interval(hass, _sample, BUCKET_INTERVAL))

    async def _capture(_now: Any) -> None:
        try:
            await coordinator.async_capture_forecasts()
        except Exception:
            # Warning, not debug. Forecast history is the one record that cannot
            # be rebuilt afterwards, so a capture that quietly stops is the most
            # expensive silent failure this integration has — and it stayed
            # silent for a fortnight the first time it happened.
            _LOGGER.warning("Forecast capture failed; history is not being kept", exc_info=True)
        else:
            if _now is not None and coordinator.preferred_forecast_provider is not None:
                coordinator.battery.refresh(force=True)
                coordinator.notify_live_entities()
                coordinator.journal.schedule()
        try:
            await coordinator.battery.async_learn()
        except Exception:
            # The outlook then runs on the last scale it learned, or unscaled.
            _LOGGER.debug("Forecast scale could not be learned", exc_info=True)

    entry.async_on_unload(async_track_time_interval(hass, _capture, FORECAST_CAPTURE_INTERVAL))
    await _capture(None)
    # Before the platforms are set up, so the outlook sensors are born with a
    # projection, or with the reason there is none, rather than blank.
    coordinator.battery.refresh(force=True)
    await coordinator.journal.async_update()

    _remove_retired_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SolarSanityConfigEntry) -> bool:
    """Unload. ``runtime_data`` is cleared for us, so there is no bookkeeping."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Take this entry's state file with it.

    One per entry, so leaving it behind would accumulate dead kilobytes that
    Home Assistant preloads at every boot.
    """
    remove_energy_issues(hass, entry)
    store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_STATE}.{entry.entry_id}")
    try:
        await store.async_remove()
    except Exception:
        _LOGGER.debug("could not remove state for %s", entry.entry_id, exc_info=True)
    await Store(hass, JOURNAL_VERSION, f"{JOURNAL_KEY}.{entry.entry_id}").async_remove()


async def _async_backfill(hass: HomeAssistant, coordinator: SolarSanityCoordinator) -> None:
    """Seed the measurement window from long-term statistics.

    Which query to run depends on what the recorder holds, not on what the state
    machine currently says. Sum-backed statistics answer `change` exactly;
    mean-backed ones only answer `mean`, and asking the wrong one returns
    nothing at all.

    Classifying from the entity's live state looked equivalent and was not: an
    MQTT-backed inverter publishes after Home Assistant starts, so at setup
    every channel classified as neither and the backfill quietly did nothing.
    """
    specs = coordinator.specs
    if not specs:
        return

    from homeassistant.util import dt as dt_util

    wanted = {spec.entity_id for spec in specs}
    sum_backed, mean_backed, absent = await async_classify_statistics(hass, wanted)

    coordinator.unrecorded_entities = tuple(sorted(absent))
    coordinator.statistics_classes = (
        dict.fromkeys(sum_backed, "sum")
        | dict.fromkeys(mean_backed, "mean")
        | dict.fromkeys(absent, "absent")
    )

    if absent:
        # Not our bug to fix, but the user cannot act on what they cannot see.
        _LOGGER.warning(
            "No recorded history for %s. Those sensors have no state_class, so "
            "Home Assistant is not keeping statistics for them and Solar Sanity "
            "must collect its own, so the battery outlook needs about three days "
            "of its own measurement before it can answer.",
            ", ".join(sorted(absent)),
        )

    if not sum_backed and not mean_backed:
        return

    start, end = utc_day_bounds(dt_util.utcnow(), BACKFILL_DAYS)
    series = await async_hourly_series(hass, mean_backed, sum_backed, start, end)
    coordinator.backfill_rows = {entity_id: len(rows) for entity_id, rows in series.items()}
    if series:
        coordinator.ingest_backfill(series)
        # Rows, not ids. The previous message counted statistic ids, which is
        # never zero once classification succeeds, so it read as success during
        # an investigation into why nothing had been backfilled.
        _LOGGER.debug(
            "backfilled %d hourly rows across %d ids (%d sum-backed, %d mean-backed)",
            sum(coordinator.backfill_rows.values()),
            len(series),
            len(sum_backed),
            len(mean_backed),
        )


def _async_register_services(hass: HomeAssistant) -> None:
    from .services import async_setup_tools

    async_setup_tools(hass)
    if hass.services.has_service(DOMAIN, SERVICE_RESCORE_FORECASTS):
        return

    async def _rescore_forecasts(call: ServiceCall) -> dict[str, Any]:
        """Score every provider now, and say what was found.

        The scoring already runs on the coordinator's six-hourly refresh, so this
        exists for the case that refresh cannot serve: somebody who has just mapped a
        channel, or just passed the twenty-first comparable day, and wants to
        know now rather than within six hours.

        Returns ``Bias.reason`` verbatim for each provider. That sentence is the
        one the sensor's attribute carries and the one the estimator wrote —
        rephrasing it here would leave two versions of the same explanation to
        drift apart.
        """
        from .scoring import async_score_providers

        out: dict[str, Any] = {}
        for entry in hass.config_entries.async_entries(DOMAIN):
            runtime = getattr(entry, "runtime_data", None)
            coordinator = getattr(runtime, "coordinator", None)
            if coordinator is None:
                continue
            scores = await async_score_providers(hass, coordinator)
            coordinator.forecast_scores = tuple(scores)
            coordinator.async_update_listeners()
            for score in scores:
                out[score.name] = {
                    "bias_pct": score.bias.reportable_pct,
                    "days_compared": score.bias.days,
                    "reason": score.bias.reason,
                }
        return {"providers": out}

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESCORE_FORECASTS,
        _rescore_forecasts,
        supports_response=SupportsResponse.ONLY,
    )
