"""The coordinator: collects buckets, captures forecasts, runs the analysis.

Three tiers, and only some of them are allowed to speak:

* a 30-second **live tripwire** off ``hass.states``, which may raise only
  simultaneous-opposing-flow and stuck/stale — the things that are invisible in
  an hourly aggregate because they average out inside the bucket;
* our own **hourly integrator**, the primary evidence for everything
  inferential;
* a one-shot **statistics backfill** at setup, so a new install gets an answer
  on day one rather than day seven.

Instantaneous power cannot support the energy identity. Entities update on their
own schedules — a P1 meter every second, a BMS every minute — so a stale battery
reading against a fresh generation reading produces a kilowatt-scale residual
with no fault present at all.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter, PowerConverter

from ._local_time import local_day
from .analysis.engine import analyse
from .analysis.model import (
    AnalysisReport,
    AnalysisRequest,
    Answer,
    Bucket,
    BucketSource,
    ChannelSpec,
    Correction,
    DeclaredTopology,
    LiveSnapshot,
    LossModel,
    Quality,
    Role,
    Status,
)
from .analysis.residual import MIN_VALID_BUCKETS_PER_DAY
from .const import (
    ANALYSIS_INTERVAL,
    COMPLETENESS_GRACE,
    CONF_CHANNELS,
    CONF_ENTITY_ID,
    CONF_FORECAST_ENTRIES,
    CONF_GRID_IS_NET,
    CONF_HAS_BATTERY,
    CONF_LOAD_WHOLE_HOUSE,
    CONF_ORIGIN,
    CONF_ROLE,
    DIGEST_RETENTION_DAYS,
    DOMAIN,
    ENERGY_MAX_AGE_SECONDS,
    LIVE_MAX_AGE_SECONDS,
    OPT_CORRECTIONS,
    OPT_SUPPRESSED,
    POWER_GAP_TOLERANCE_SECONDS,
    STORAGE_KEY_STATE,
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
)
from .statistics_source import (
    async_forecast_series,
    async_get_solar_forecasts,
    async_record_forecast,
    dayahead_statistic_id,
    forecast_statistic_id,
    provider_label,
)

_LOGGER = logging.getLogger(__name__)

#: How many live snapshots to retain. Enough to satisfy the "at least 50
#: violations across at least 3 days" bar without unbounded growth.
MAX_SNAPSHOTS = 2000

#: Buckets held in memory. The long history lives in the recorder.
MAX_BUCKETS = 24 * 45


@dataclass(slots=True)
class SolarSanityData:
    """What lives on ``entry.runtime_data``."""

    coordinator: SolarSanityCoordinator
    store: Store


class SolarSanityCoordinator(DataUpdateCoordinator[AnalysisReport]):
    """Owns the measurement window and the analysis result."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=ANALYSIS_INTERVAL,
        )
        self.entry = entry
        self._buckets: list[Bucket] = []
        self._snapshots: list[LiveSnapshot] = []
        self._accumulator: dict[str, float] = {}
        #: Whether any channel has ever produced a reading. Separates "nothing
        #: has arrived yet" from "everything has stopped", which are the same
        #: arithmetic and opposite facts.
        self._has_ever_read = False
        #: When this coordinator was built, which is when the grace below
        #: starts. Not persisted: a reload genuinely does begin a new wait,
        #: because a reload is also when entities are missing.
        self._started_at = dt_util.utcnow()

        #: Previous reading per energy channel, and when it was taken. The time
        #: matters as much as the value: a difference tells you energy flowed,
        #: never over how long.
        self._last_energy: dict[str, tuple[float, datetime]] = {}
        #: Per power channel: the watts last seen, and when. Power is integrated
        #: over the durations the sensor actually held its values, not sampled.
        self._live_power: dict[str, tuple[float, datetime]] = {}
        #: When a power channel went unreadable, if it currently is.
        self._gap_since: dict[str, datetime] = {}
        #: How much of the open hour each power channel has been unreadable for.
        self._gap_seconds: dict[str, float] = {}
        #: Channels whose current hour saw a reset and cannot be trusted.
        self._suspect: set[str] = set()
        #: When sampling began, so a partial first hour is labelled honestly.
        self._first_sample_at: datetime | None = None
        #: Latest forecast total for tomorrow, in kWh.
        self._expected_tomorrow_kwh: float | None = None
        #: Entity ids the recorder holds no statistics for. Their sensors
        #: carry no state_class, so no history exists to backfill from.
        self.unrecorded_entities: tuple[str, ...] = ()
        #: How the recorder classified each mapped entity at the last
        #: backfill: "sum", "mean", or "absent". Empty until it has run.
        self.statistics_classes: dict[str, str] = {}
        #: Hourly rows the last backfill actually returned, per entity id.
        self.backfill_rows: dict[str, int] = {}
        self._accumulator_start: datetime | None = None
        self._loss_model: LossModel | None = None
        # One file per entry. A single shared file meant two installations
        # overwrote each other's fitted loss model on every analysis — the
        # second write simply won, and the first entry silently inherited a
        # model fitted on a different house.
        self._store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_STATE}.{entry.entry_id}",
            minor_version=STORAGE_MINOR_VERSION,
        )
        self._legacy_store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            STORAGE_KEY_STATE,
            minor_version=STORAGE_MINOR_VERSION,
        )

    # -- configuration ------------------------------------------------------

    @property
    def specs(self) -> tuple[ChannelSpec, ...]:
        """Configured channels, as the analysis engine wants them."""
        out: list[ChannelSpec] = []
        for raw in self.entry.data.get(CONF_CHANNELS, []):
            role = _role_from_key(raw.get(CONF_ROLE))
            entity_id = raw.get(CONF_ENTITY_ID)
            if role is None or not entity_id:
                continue
            state = self.hass.states.get(entity_id)
            out.append(
                ChannelSpec(
                    key=role.key,
                    role=role,
                    entity_id=entity_id,
                    friendly_name=_friendly_name(state, entity_id),
                    declared_unit=_unit_of(state),
                    origin=raw.get(CONF_ORIGIN, "user"),
                )
            )
        return tuple(out)

    @property
    def declared(self) -> DeclaredTopology:
        data = self.entry.data
        return DeclaredTopology(
            has_battery=_answer(data.get(CONF_HAS_BATTERY)),
            grid_is_single_net_sensor=_answer(data.get(CONF_GRID_IS_NET)),
            load_covers_whole_house=_answer(data.get(CONF_LOAD_WHOLE_HOUSE)),
        )

    @property
    def corrections(self) -> tuple[Correction, ...]:
        """Diagnostic overrides the user has explicitly accepted.

        Never applied without a click, and never written to anyone else's
        entities — these adjust our internal model only.
        """
        out: list[Correction] = []
        for raw in self.entry.options.get(OPT_CORRECTIONS, []):
            key = raw.get("channel_key")
            kind = raw.get("kind")
            if not key or not kind:
                continue
            out.append(Correction(channel_key=key, kind=kind, factor=raw.get("factor")))
        return tuple(out)

    @property
    def suppressed(self) -> tuple[str, ...]:
        """Findings the user told us were wrong. We do not raise them again."""
        return tuple(self.entry.options.get(OPT_SUPPRESSED, []))

    # -- live tier ----------------------------------------------------------

    def capture_live(self) -> None:
        """Take one instantaneous snapshot, if every channel is fresh enough.

        A snapshot where any channel is stale is discarded rather than recorded
        with a gap: comparing readings taken minutes apart is what manufactures
        phantom residuals.
        """
        specs = self.specs
        if not specs:
            return

        now = dt_util.utcnow()
        watts: dict[str, float | None] = {}
        ages: dict[str, float] = {}

        for spec in specs:
            state = self.hass.states.get(spec.entity_id)
            value, kind = read_channel(state)
            if kind != KIND_POWER:
                # The tripwire looks for two flows happening at the same instant.
                # An energy sensor cannot answer that — it reports an amount, not
                # a rate — so a mixed system simply has no live tier rather than
                # a misleading one.
                return
            age = (now - state.last_updated).total_seconds() if state else 1e9
            watts[spec.key] = value
            ages[spec.key] = age

        if any(age > LIVE_MAX_AGE_SECONDS for age in ages.values()):
            return
        if any(value is None for value in watts.values()):
            return

        self._snapshots.append(LiveSnapshot(taken_utc=now, watts=watts, age_seconds=ages))
        if len(self._snapshots) > MAX_SNAPSHOTS:
            del self._snapshots[: len(self._snapshots) - MAX_SNAPSHOTS]

    # -- hourly integrator --------------------------------------------------

    def accumulate(self) -> None:
        """Add this sample to the open hourly bucket.

        The two kinds need opposite treatment, and this is the whole reason the
        method exists rather than a one-line integration:

        * **Power** is integrated over the sampling interval to become energy.
        * **Energy** is *differenced* — the reading is already energy, so the
          bucket wants how much it advanced, not the reading itself. Integrating
          a cumulative sensor would put roughly the running total into every
          hour.

        A daily-resetting total (very common — "Daily Generation" and friends)
        goes backwards at midnight. That interval is not a negative amount of
        energy, it is a reset, so the bucket is marked suspect and dropped
        rather than guessed at.
        """
        specs = self.specs
        if not specs:
            return

        now = dt_util.utcnow()
        hour = now.replace(minute=0, second=0, microsecond=0)

        if self._accumulator_start is None:
            self._accumulator_start = hour
            # Only a full hour of samples earns a full-hour bucket.
            self._first_sample_at = now
        elif hour != self._accumulator_start:
            self._close_bucket(self._accumulator_start, hour)
            self._accumulator_start = hour
            self._first_sample_at = None

        for spec in specs:
            state = self.hass.states.get(spec.entity_id)
            value, kind = read_channel(state)
            if value is None or kind is None:
                continue

            if kind == KIND_POWER:
                # Not integrated here. Sampling a power sensor every five
                # minutes and assuming it held that value throughout is where
                # the noise came from: on an event-reporting load channel it
                # put a standard deviation of about 570 Wh into a day, enough
                # to make a healthy system read "still looking" half the time.
                #
                # The tick only establishes a starting value, for a sensor that
                # has not changed since setup and so has produced no event.
                self._live_power.setdefault(spec.key, (value, now))
                continue

            if not energy_is_cumulative(state):
                # A `measurement` energy sensor already reports the amount for
                # the period, so it is added rather than differenced.
                self._accumulator[spec.key] = self._accumulator.get(spec.key, 0.0) + value
                continue

            held = self._last_energy.get(spec.key)
            self._last_energy[spec.key] = (value, now)
            if held is None:
                # No baseline yet — the first reading establishes one rather
                # than being mistaken for an hour's worth of energy.
                continue

            previous, taken_at = held
            if (now - taken_at).total_seconds() > ENERGY_MAX_AGE_SECONDS:
                # The sensor was away. The counter advanced while it was, and
                # nothing here knows when — so crediting the whole difference to
                # the hour it came back would put an outage's worth of energy
                # into one bucket and call it measured. Re-baseline instead.
                # The hours it was absent for were marked as they closed, above,
                # so every hour the gap touches is distrusted rather than just
                # this one.
                self._suspect.add(spec.key)
                continue

            delta = value - previous
            if delta < 0:
                # A meter reset or a daily rollover. Either way this interval's
                # energy is unknowable, so the hour is not trustworthy.
                self._suspect.add(spec.key)
                continue

            self._accumulator[spec.key] = self._accumulator.get(spec.key, 0.0) + delta

    @callback
    def async_track_power(self) -> Callable[[], None]:
        """Integrate power channels on their own state changes.

        Left-Riemann over the duration each value was actually held, which is
        what Home Assistant's own integration helper does and what makes the
        result exact for a step-shaped signal rather than merely close.

        Every mapped entity is subscribed, not only the ones that look like
        power today. An MQTT-backed inverter publishes after Home Assistant
        starts, so at setup there is nothing to inspect — deciding then would
        subscribe to nothing at all on exactly the installations this is
        written for.
        """
        entities = [spec.entity_id for spec in self.specs]
        if not entities:
            return lambda: None

        return async_track_state_change_event(self.hass, entities, self._async_power_changed)

    @callback
    def _async_power_changed(self, event: Any) -> None:
        key = self._key_for_entity(event.data.get("entity_id"))
        if key is None:
            return

        state = event.data.get("new_state")
        # The event's own timestamp, not the clock. They differ by however long
        # the event loop took to reach us, and that difference lands directly in
        # the integral — which is the quantity this whole path exists to get
        # right rather than nearly right.
        fired = getattr(event, "time_fired", None)
        when = fired if isinstance(fired, datetime) else dt_util.utcnow()
        value, kind = read_channel(state)

        held = self._live_power.get(key)
        if held is not None:
            self._integrate(key, held[0], held[1], when)

        if kind == KIND_POWER and value is not None:
            self._close_gap(key, when)
            self._live_power[key] = (value, when)
            return

        if held is None:
            # Never was a power channel as far as this path is concerned —
            # energy is differenced on the tick, exactly, at any rate.
            return

        # It was readable and now is not. Nothing is added for the time it is
        # away; how long that lasts decides whether the hour survives.
        self._live_power.pop(key, None)
        self._gap_since.setdefault(key, when)

    def _key_for_entity(self, entity_id: str | None) -> str | None:
        if entity_id is None:
            return None
        return next((s.key for s in self.specs if s.entity_id == entity_id), None)

    def _integrate(self, key: str, watts: float, since: datetime, until: datetime) -> None:
        """Add one held value's contribution to the open bucket."""
        seconds = (until - since).total_seconds()
        if seconds <= 0:
            return
        self._accumulator[key] = self._accumulator.get(key, 0.0) + watts * seconds / 3600.0

    def _close_gap(self, key: str, when: datetime) -> None:
        started = self._gap_since.pop(key, None)
        if started is None:
            return
        self._gap_seconds[key] = self._gap_seconds.get(key, 0.0) + (when - started).total_seconds()

    def _settle_power(self, until: datetime) -> None:
        """Bring every power channel up to the end of the bucket.

        Without this, the last value of the hour contributes only up to its own
        event — so a load that settles at eight in the evening and stays there
        would have its whole night silently missing.
        """
        # The cursor may already be past the boundary. An hour rolls over on the
        # wall clock, but nothing notices until the next five-minute tick, so an
        # event arriving in between is integrated while the *previous* bucket is
        # still open — its segment already crossed the boundary and was counted.
        # Rewinding the cursor to the boundary then counted that same slice
        # again in the new hour, adding energy that never flowed.
        for key, (watts, since) in list(self._live_power.items()):
            if since >= until:
                continue
            self._integrate(key, watts, since, until)
            self._live_power[key] = (watts, until)

        for key, started in list(self._gap_since.items()):
            if started >= until:
                continue
            self._gap_seconds[key] = (
                self._gap_seconds.get(key, 0.0) + (until - started).total_seconds()
            )
            self._gap_since[key] = until

    def notify_live_entities(self) -> None:
        """Write live-state entities without re-running the analysis.

        ``CoordinatorEntity`` only writes state when the coordinator updates,
        and the analysis runs every six hours. Sensors that describe *now* —
        completeness and the live residual — would otherwise be frozen to that
        cadence, which is how completeness stuck at 0%: the integration loaded
        before the inverter's entities had published, the first reading found
        nothing readable, and nothing rewrote it for six hours.
        """
        self.async_update_listeners()

    def _counter_went_quiet(self, key: str, closing: datetime) -> bool:
        """Whether a cumulative counter had stopped reporting by the hour's end.

        Only cumulative energy channels appear in ``_last_energy``, so this is
        silent about power channels — those carry their own gap accounting in
        ``_gap_seconds``, which a counter never gets because it never enters
        ``_live_power``. A channel with no baseline yet is not quiet, it is
        simply new, and marking it would distrust every hour of an installation
        whose sensor has not arrived.
        """
        held = self._last_energy.get(key)
        if held is None:
            return False
        return (closing - held[1]).total_seconds() > ENERGY_MAX_AGE_SECONDS

    def _close_bucket(self, start: datetime, end: datetime | None = None) -> None:
        specs = self.specs
        self._settle_power(end or (start + timedelta(hours=1)))
        # The first bucket after a restart covers only part of an hour.
        # Claiming otherwise applies a full hour of standby draw to it and
        # silently degrades the day; build_days drops anything not 3600s.
        started = self._first_sample_at or start
        covered = int((end - max(start, started)).total_seconds()) if end else 3600
        covered = max(0, min(3600, covered))
        wh: dict[str, float | None] = {}
        quality: dict[str, Quality] = {}
        source: dict[str, BucketSource] = {}

        closing = end or (start + timedelta(hours=1))
        for spec in specs:
            value = self._accumulator.get(spec.key)
            gap = self._gap_seconds.get(spec.key, 0.0)
            if gap > POWER_GAP_TOLERANCE_SECONDS:
                # Part of this hour is simply unknown. An hour with a hole in it
                # is not an hour with less energy in it, and the difference is
                # the whole product.
                value = None
            wh[spec.key] = value
            if self._counter_went_quiet(spec.key, closing):
                # The hour a counter went quiet in has exactly the same hole in
                # it as the hour it comes back in, and only the second was ever
                # marked — the staleness guard runs when a *reading* arrives,
                # and no reading arrives while the sensor is away. So a dropout
                # across an hour boundary stopped moving energy between two
                # hours and started deleting it: this hour shipped a partial
                # total stamped OK and counted as a full 3600 seconds, the next
                # one was discarded whole, and nothing balanced the loss.
                #
                # On a healthy eight-kilowatt array a lunchtime dropout was
                # enough to take the day out by kilowatt-hours, turn on the
                # problem flag and print "the numbers do not add up" — the exact
                # false alarm this product cannot afford.
                #
                # Asked here rather than on the sampling tick because the tick
                # that would notice may not exist: `_close_bucket` runs before
                # the sampling loop, so a gap opening late in the hour crosses
                # the boundary before anything looks at it.
                quality[spec.key] = Quality.RESET_SUSPECT
            elif spec.key in self._suspect:
                quality[spec.key] = Quality.RESET_SUSPECT
            elif value is None:
                quality[spec.key] = Quality.MISSING
            else:
                quality[spec.key] = Quality.OK
            source[spec.key] = BucketSource.OWN_INTEGRAL

        local_date, dst = self._local_day(start)
        self._buckets.append(
            Bucket(
                start_utc=start,
                seconds=covered,
                wh=wh,
                quality=quality,
                source=source,
                local_date=local_date,
                is_dst_transition=dst,
            )
        )
        if len(self._buckets) > MAX_BUCKETS:
            del self._buckets[: len(self._buckets) - MAX_BUCKETS]
        self._accumulator.clear()
        self._suspect.clear()
        self._gap_seconds.clear()

    def ingest_backfill(self, series: dict[str, list[tuple[datetime, float, bool]]]) -> None:
        """Seed the window from long-term statistics at setup.

        Mean-derived buckets are tagged ``LTS_MEAN`` so the analysis widens its
        tolerance and refuses to call a finding certain on them: an arithmetic
        hourly mean over an event-reporting sensor over-weights volatile hours.
        Sum-derived ones are exact and tagged ``LTS_SUM``.
        """
        specs = self.specs
        by_key = {s.key: s.entity_id for s in specs}
        merged: dict[datetime, dict[str, tuple[float, bool]]] = {}

        for key, entity_id in by_key.items():
            for when, value, from_mean in series.get(entity_id, []):
                merged.setdefault(when, {})[key] = (value, from_mean)

        existing = {bucket.start_utc for bucket in self._buckets}

        for when in sorted(merged):
            if when in existing:
                # Our own measurement is preferred where we have it.
                continue
            local_date, dst = self._local_day(when)
            values = merged[when]
            wh: dict[str, float | None] = {}
            quality: dict[str, Quality] = {}
            source: dict[str, BucketSource] = {}
            for spec in specs:
                entry = values.get(spec.key)
                if entry is None:
                    wh[spec.key] = None
                    quality[spec.key] = Quality.MISSING
                    source[spec.key] = BucketSource.LTS_SUM
                    continue
                value, from_mean = entry
                wh[spec.key] = value
                quality[spec.key] = Quality.DERIVED_FROM_MEAN if from_mean else Quality.OK
                source[spec.key] = BucketSource.LTS_MEAN if from_mean else BucketSource.LTS_SUM
            self._buckets.append(
                Bucket(
                    start_utc=when,
                    seconds=3600,
                    wh=wh,
                    quality=quality,
                    source=source,
                    local_date=local_date,
                    is_dst_transition=dst,
                )
            )

        self._buckets.sort(key=lambda b: b.start_utc)
        if len(self._buckets) > MAX_BUCKETS:
            del self._buckets[: len(self._buckets) - MAX_BUCKETS]

    # -- forecast capture ---------------------------------------------------

    async def async_capture_forecasts(self) -> int:
        """Record each selected provider's day-ahead forecast.

        This runs from the moment the integration is installed, before anything
        displays it, because forecast history cannot be reconstructed later —
        the source sensors carry no ``state_class``, so their history is purged
        within about ten days.
        """
        entry_ids = list(self.entry.data.get(CONF_FORECAST_ENTRIES, []))
        if not entry_ids:
            return 0

        forecasts = await async_get_solar_forecasts(self.hass, entry_ids)
        written = 0
        tomorrow_kwh: float | None = None
        for entry_id, payload in forecasts.items():
            provider = self.hass.config_entries.async_get_entry(entry_id)
            if provider is None:
                continue
            wh_hours = payload.get("wh_hours") if isinstance(payload, dict) else None
            if not isinstance(wh_hours, dict):
                continue
            if self._owns_archive(entry_id) and await async_record_forecast(
                self.hass, entry_id, await self._provider_name(provider), wh_hours
            ):
                written += 1

            # Surface the first provider's figure. Once accuracy scoring exists
            # this should prefer whichever provider has been most accurate here.
            if tomorrow_kwh is None:
                tomorrow_kwh = self._sum_for_tomorrow(wh_hours)

        self._expected_tomorrow_kwh = tomorrow_kwh
        return written

    async def _provider_name(self, provider: ConfigEntry) -> str:
        """The name to store on the archive's metadata.

        Stored rather than derived at read time, because the archive outlives
        the entry: a provider that is deleted leaves real history behind, and a
        row labelled only by a config entry id nobody can resolve any more is
        history nobody can read.
        """
        from homeassistant.loader import async_get_integration

        try:
            integration = await async_get_integration(self.hass, provider.domain)
            product = integration.name
        except Exception:
            product = provider.domain
        return provider_label(product, provider.title)

    def _owns_archive(self, provider_entry_id: str) -> bool:
        """Whether this entry is the one that writes that provider's archive.

        The archive is keyed on the provider, not on us, so two installations
        selecting the same forecast integration write to the same series — each
        resuming its running total from what the other last left, which climbs
        at twice the real rate and never recovers.

        Elected rather than assigned, and recomputed every tick: deleting the
        owning entry hands the archive over within one capture interval instead
        of silently stopping the one thing that cannot be backfilled.
        """
        owners = [
            entry.entry_id
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if provider_entry_id in (entry.data.get(CONF_FORECAST_ENTRIES) or [])
            # An entry that cannot run cannot write, and electing one hands the
            # archive to nobody. `async_entries` includes disabled entries and
            # ones that failed to set up, so without this a disabled
            # installation whose id happens to sort first silences capture for
            # every other one — permanently, and with nothing said.
            and (
                entry.entry_id == self.entry.entry_id
                or (entry.disabled_by is None and entry.state is ConfigEntryState.LOADED)
            )
        ]
        return bool(owners) and min(owners) == self.entry.entry_id

    async def async_forecast_snapshot(self) -> dict[str, Any]:
        """What each provider's two archives actually hold.

        The one thing in this product that cannot be reconstructed later is the
        forecast record, so "capture is running" needs to be checkable rather
        than assumed. Both series are reported side by side because the
        interesting number is the gap: the latest series should hold roughly a
        two-day horizon, the day-ahead one should grow by a day every day and
        never shrink.
        """
        end = dt_util.utcnow() + timedelta(days=2)
        start = end - timedelta(days=32)
        out: dict[str, Any] = {}

        for entry_id in self.entry.data.get(CONF_FORECAST_ENTRIES) or []:
            provider = self.hass.config_entries.async_get_entry(entry_id)
            latest_id = forecast_statistic_id(entry_id)
            ahead_id = dayahead_statistic_id(entry_id)
            latest = await async_forecast_series(self.hass, latest_id, start, end)
            ahead = await async_forecast_series(self.hass, ahead_id, start, end)

            hours = sorted(ahead) if ahead else []
            out[entry_id] = {
                "provider": provider.title if provider else None,
                # A selected entry that no longer resolves means the provider
                # was removed or recreated; its archive is then an orphan
                # holding real history nothing will ever add to.
                "resolves": provider is not None,
                "owns_archive": self._owns_archive(entry_id),
                "latest_id": latest_id,
                "latest_rows": None if latest is None else len(latest),
                "dayahead_id": ahead_id,
                "dayahead_rows": None if ahead is None else len(ahead),
                "dayahead_first": hours[0].isoformat() if hours else None,
                "dayahead_last": hours[-1].isoformat() if hours else None,
                "dayahead_kwh": round(sum(ahead.values()), 2) if ahead else None,
            }
        return out

    def _sum_for_tomorrow(self, wh_hours: dict[str, Any]) -> float | None:
        """Total forecast energy for tomorrow, in kWh, by local date.

        The payload's values are Wh produced *during* each period — a delta, not
        a running total — so they add. Periods are not guaranteed to be an hour,
        which is why every entry is summed rather than counted.
        """
        try:
            tz = dt_util.get_time_zone(self.hass.config.time_zone)
        except Exception:
            tz = None
        now = dt_util.now(tz) if tz else dt_util.utcnow()
        tomorrow = (now + timedelta(days=1)).date()

        total = 0.0
        seen = False
        for raw_when, raw_value in wh_hours.items():
            when = dt_util.parse_datetime(raw_when)
            if when is None or not isinstance(raw_value, (int, float)):
                continue
            local = when.astimezone(tz) if tz else when
            if local.date() != tomorrow:
                continue
            total += float(raw_value)
            seen = True

        # No entries for tomorrow is not zero production — it is no forecast.
        return round(total / 1000.0, 2) if seen else None

    # -- analysis -----------------------------------------------------------

    async def _async_update_data(self) -> AnalysisReport:
        specs = self.specs
        request = AnalysisRequest(
            now_utc=dt_util.utcnow(),
            specs=specs,
            buckets=tuple(self._buckets),
            live_snapshots=tuple(self._snapshots),
            declared=self.declared,
            active_corrections=self.corrections,
            suppressed_codes=self.suppressed,
            loss_model=self._loss_model,
            unrecorded_keys=self._unrecorded_keys(),
            utc_offset_hours=self._utc_offset_hours(),
        )

        report = await self.hass.async_add_executor_job(analyse, request)

        if report.loss_model is not None and report.loss_model.fitted:
            self._loss_model = report.loss_model

        await self._async_persist(report)
        return report

    def _unrecorded_keys(self) -> tuple[str, ...]:
        """Channel keys whose entity the recorder holds no statistics for."""
        unrecorded = set(self.unrecorded_entities)
        return tuple(spec.key for spec in self.specs if spec.entity_id in unrecorded)

    def _local_day(self, when: datetime) -> tuple[date | None, bool]:
        """The local day an hour belongs to, and whether that day is 24 hours.

        Resolved per hour against the zone, never by adding a fixed offset.
        """
        return local_day(when, self._time_zone())

    def _time_zone(self) -> tzinfo | None:
        try:
            return dt_util.get_time_zone(self.hass.config.time_zone)
        except Exception:
            return None

    def _utc_offset_hours(self) -> float:
        """The instance's current offset from UTC, in hours.

        Passed into the analysis so buckets group into local days. A day that
        starts at 16:00 local splits the solar curve in two.
        """
        try:
            tz = dt_util.get_time_zone(self.hass.config.time_zone)
            if tz is None:
                return 0.0
            offset = dt_util.utcnow().astimezone(tz).utcoffset()
            return offset.total_seconds() / 3600.0 if offset else 0.0
        except Exception:
            return 0.0

    async def _async_persist(self, report: AnalysisReport) -> None:
        """Save daily digests and the fitted loss model.

        Deliberately small: kilobytes per year. Home Assistant preloads every
        ``.storage`` file at boot, so a growing archive here would cost the user
        startup time on a Raspberry Pi. The bulk history belongs in the recorder.
        """
        payload = {
            "loss_model": _loss_to_dict(report.loss_model),
            "last_status": report.status.value,
            "last_finding": report.finding.code if report.finding else None,
            "retention_days": DIGEST_RETENTION_DAYS,
        }
        self._store.async_delay_save(lambda: payload, 30)

    async def async_restore(self) -> None:
        """Reload the fitted loss model so a restart does not reset learning.

        Falls back to the pre-per-entry file once. A wrongly inherited model is
        refitted from data within a day anyway, so this needs no migration
        ceremony — but throwing away a correctly fitted one costs a user their
        loss model for no reason.
        """
        stored = await self._store.async_load()
        if not stored:
            try:
                stored = await self._legacy_store.async_load()
            except Exception:
                stored = None
        if not stored:
            return
        self._loss_model = _loss_from_dict(stored.get("loss_model"))

    # -- reporting ----------------------------------------------------------

    @property
    def expected_tomorrow_kwh(self) -> float | None:
        """Tomorrow's forecast total, or ``None`` when no provider is configured."""
        return self._expected_tomorrow_kwh

    @property
    def has_live_tier(self) -> bool:
        """Whether an instantaneous residual is possible at all.

        It needs every balance channel to report a rate. One energy channel is
        enough to rule it out — an amount cannot answer "what is flowing right
        now" — so on those systems the entity is not created rather than created
        and permanently blank.
        """
        specs = [s for s in self.specs if s.role.in_balance]
        if not specs:
            return False
        return all(
            channel_kind(self.hass.states.get(spec.entity_id)) == KIND_POWER for spec in specs
        )

    @property
    def live_residual_w(self) -> float | None:
        """Sources minus sinks right now, in watts.

        ``None`` whenever there is no usable snapshot — which is always, on a
        system with any energy channel, since the live tier is skipped there. An
        absent number is the honest answer; a zero would not be.
        """
        if not self._snapshots:
            return None
        snapshot = self._snapshots[-1]
        total = 0.0
        for spec in self.specs:
            if not spec.role.in_balance:
                continue
            value = snapshot.watts.get(spec.key)
            if value is None:
                return None
            total += spec.role.sign * value
        return round(total, 1)

    @property
    def channel_completeness(self) -> int | None:
        """Percentage of configured channels currently readable.

        This measures what its name says. It previously reported how many days
        of history existed, which is a different question with the same units.

        ``None`` until something has been read at least once, and 0 only
        afterwards. Home Assistant gives a custom integration no way to wait for
        another one's entities, so at first refresh the inverter has usually not
        published yet and every channel reads as absent. Reporting that as 0%
        states that nothing works — which is indistinguishable, on the device
        page, from the failure this sensor exists to report, at the moment a
        user is most likely to be looking at it. "Unknown" is the honest answer
        to a question nobody can answer yet.
        """
        specs = self.specs
        if not specs:
            return None
        readable = sum(
            1 for spec in specs if read_channel(self.hass.states.get(spec.entity_id))[0] is not None
        )
        if readable:
            self._has_ever_read = True
        elif not self._has_ever_read and dt_util.utcnow() - self._started_at < COMPLETENESS_GRACE:
            # Nothing read, and not long enough since setup to conclude
            # anything from that. Seeding this from backfilled history was
            # tried and was worse: the backfill completes *before* the first
            # refresh, so the flag was already true while the inverter had yet
            # to publish, and every restart reported 0% — the exact reading
            # this exists to prevent, delivered to every installation rather
            # than a rare one.
            #
            # Waiting on the clock answers both cases. A genuine outage is
            # reported once the grace expires, and after any successful read it
            # is reported the moment it happens.
            return None
        return round(readable / len(specs) * 100)

    def coverage_snapshot(self) -> dict[str, Any]:
        """Everything needed to explain a verdict, in one downloadable place.

        This exists because "Not enough data yet" is unfalsifiable from the
        outside. Every backfill defect so far was diagnosed by asking a user to
        read state attributes back one at a time, which is slow and gets the
        wrong answer when the attribute they read is not the one at fault. The
        button that produces this file is on the same page as the sensor, and
        it answers the whole question at once.
        """
        specs = self.specs
        keys = tuple(spec.key for spec in specs if spec.role.in_balance)
        buckets = tuple(self._buckets)

        channels: list[dict[str, Any]] = []
        for spec in specs:
            state = self.hass.states.get(spec.entity_id)
            value, kind = read_channel(state)
            channels.append(
                {
                    "key": spec.key,
                    "entity_id": spec.entity_id,
                    "in_balance": spec.role.in_balance,
                    "exists_now": state is not None,
                    "state_now": None if state is None else state.state,
                    "unit": spec.declared_unit,
                    "kind": kind,
                    "readable_now": value is not None,
                    "statistics": self.statistics_classes.get(spec.entity_id, "unknown"),
                    "backfilled_rows": self.backfill_rows.get(spec.entity_id, 0),
                    "hours_with_value": sum(
                        1 for bucket in buckets if bucket.value(spec.key) is not None
                    ),
                    "origin": spec.origin,
                }
            )

        whole = [b for b in buckets if b.seconds == 3600 and not b.is_dst_transition]
        valid = [b for b in whole if all(b.value(key) is not None for key in keys)]

        offset = timedelta(hours=self._utc_offset_hours())
        per_day: dict[str, int] = {}
        for bucket in valid:
            day = (bucket.start_utc + offset).date().isoformat()
            per_day[day] = per_day.get(day, 0) + 1

        return {
            "channels": channels,
            "balance_keys": list(keys),
            "unrecorded_entities": list(self.unrecorded_entities),
            "utc_offset_hours": self._utc_offset_hours(),
            "buckets": {
                "held": len(buckets),
                "whole_hours": len(whole),
                # A whole hour counts only if every balance channel has a value
                # in it. The gap between these two numbers is the entire
                # question whenever the status will not leave insufficient_data.
                "valid_hours": len(valid),
                "first_utc": buckets[0].start_utc.isoformat() if buckets else None,
                "last_utc": buckets[-1].start_utc.isoformat() if buckets else None,
            },
            "valid_hours_per_local_day": dict(sorted(per_day.items())),
            "days_meeting_minimum": sum(
                1 for count in per_day.values() if count >= MIN_VALID_BUCKETS_PER_DAY
            ),
            "minimum_hours_per_day": MIN_VALID_BUCKETS_PER_DAY,
        }

    @property
    def report(self) -> AnalysisReport | None:
        return self.data

    @property
    def status(self) -> Status:
        return self.data.status if self.data else Status.INSUFFICIENT_DATA


# -- helpers ----------------------------------------------------------------


_ROLE_BY_KEY = {role.key: role for role in Role}


def _role_from_key(key: str | None) -> Role | None:
    return _ROLE_BY_KEY.get(key) if key else None


def _answer(raw: Any) -> Answer:
    if raw is True or raw == "yes":
        return Answer.YES
    if raw is False or raw == "no":
        return Answer.NO
    return Answer.UNKNOWN


def _friendly_name(state: State | None, entity_id: str) -> str:
    if state is not None:
        name = state.attributes.get("friendly_name")
        if isinstance(name, str) and name:
            return name
    return entity_id


def _unit_of(state: State | None) -> str:
    if state is None:
        return ""
    unit = state.attributes.get("unit_of_measurement")
    return unit if isinstance(unit, str) else ""


#: What a channel measures. Power must be integrated over time to become energy;
#: energy must be differenced. Treating one as the other is not a small error —
#: it is the wrong operation entirely.
KIND_POWER = "power"
KIND_ENERGY = "energy"


def channel_kind(state: State | None) -> str | None:
    """Return ``power``, ``energy``, or ``None`` when it cannot be determined.

    Device class first, because it is the sensor's own declaration of what it
    is. Unit is the fallback for sensors that set one without the other.
    """
    if state is None:
        return None

    device_class = state.attributes.get("device_class")
    if device_class == SensorDeviceClass.POWER:
        return KIND_POWER
    if device_class == SensorDeviceClass.ENERGY:
        return KIND_ENERGY

    unit = _unit_of(state)
    if unit in PowerConverter.VALID_UNITS:
        return KIND_POWER
    if unit in EnergyConverter.VALID_UNITS:
        return KIND_ENERGY
    return None


def _numeric_state(state: State | None) -> float | None:
    """Parse a state, rejecting anything that is not a finite number.

    ``float()`` accepts ``"nan"`` and ``"inf"``. Neither may reach arithmetic —
    a NaN silently makes every threshold comparison false.
    """
    if state is None:
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def read_channel(state: State | None) -> tuple[float | None, str | None]:
    """Return ``(value, kind)``: watts for power, watt-hours for energy.

    The unit is checked against the converter's own vocabulary *before*
    converting rather than catching afterwards. Home Assistant's converters
    raise ``HomeAssistantError``, which is not a ``ValueError`` — catching the
    usual suspects would let it escape into the polling callback, where it stops
    every channel from accumulating rather than just the offending one.

    That case is ordinary, not exotic: a sensor can declare
    ``device_class: energy`` and carry a unit the converter has never heard of
    (``""``, ``"kWh/h"``, ``"VA"``), and the entity picker filters on device
    class, so such a sensor is selectable.
    """
    value = _numeric_state(state)
    if value is None:
        return None, None

    kind = channel_kind(state)
    unit = _unit_of(state)

    try:
        if kind == KIND_POWER and unit in PowerConverter.VALID_UNITS:
            return PowerConverter.convert(value, unit, UnitOfPower.WATT), kind
        if kind == KIND_ENERGY and unit in EnergyConverter.VALID_UNITS:
            return EnergyConverter.convert(value, unit, UnitOfEnergy.WATT_HOUR), kind
    except (HomeAssistantError, ValueError, TypeError, KeyError):
        # Belt and braces. A unit we cannot convert yields nothing, never the
        # raw number — passing a value through unconverted is how the
        # predecessor read a kilowatt-hour forecast as "1.2 watts".
        return None, kind

    return None, kind


def energy_is_cumulative(state: State | None) -> bool:
    """Whether an energy reading is a running total that must be differenced.

    ``total`` and ``total_increasing`` are counters. A ``measurement`` energy
    sensor reports an amount *for the period* — differencing that is as wrong as
    integrating a counter, so it is accumulated directly instead.
    """
    if state is None:
        return False
    return state.attributes.get("state_class") in (
        SensorStateClass.TOTAL,
        SensorStateClass.TOTAL_INCREASING,
    )


def _loss_to_dict(loss: LossModel | None) -> dict[str, float] | None:
    if loss is None:
        return None
    return {
        "pv_dc_gamma": loss.pv_dc_gamma,
        "battery_dc_gamma": loss.battery_dc_gamma,
        "standby_w": loss.standby_w,
        "samples": float(loss.samples),
    }


def _loss_from_dict(raw: Any) -> LossModel | None:
    if not isinstance(raw, dict):
        return None
    try:
        return LossModel(
            pv_dc_gamma=float(raw["pv_dc_gamma"]),
            battery_dc_gamma=float(raw["battery_dc_gamma"]),
            standby_w=float(raw["standby_w"]),
            samples=int(raw["samples"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def bucket_window(days: int) -> timedelta:
    return timedelta(days=days)
