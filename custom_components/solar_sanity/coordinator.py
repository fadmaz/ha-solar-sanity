"""The coordinator: collects buckets, captures forecasts, scores them.

Two sources of hours:

* our own **hourly integrator**, which watches every mapped channel end to end
  and is the primary evidence for everything the battery outlook learns;
* a one-shot **statistics backfill** at setup, so a new install has a
  consumption profile on day one rather than day seven.

Instantaneous power is never used as an hour's energy. Entities update on their
own schedules, a P1 meter every second and a BMS every minute, so one reading
of each describes no single moment. Power is integrated over the durations the
sensor actually held its values.

The window is saved whenever an hour closes, not on a timer. Saving on the
coordinator's refresh would stop whenever nothing listens to it: a user who
disabled the sensors, or turned polling off, would lose every measured hour at
the next restart.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any, Final

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
from .analysis.forecast import Correction, RoofForecast, correction, roof_forecast
from .analysis.model import Bucket, BucketSource, ChannelSpec, Quality, Role
from .battery_outlook import BatteryOutlook
from .const import (
    CONF_CHANNELS,
    CONF_ENTITY_ID,
    CONF_FORECAST_ENTRIES,
    CONF_ORIGIN,
    CONF_ROLE,
    DOMAIN,
    ENERGY_MAX_AGE_SECONDS,
    POWER_GAP_TOLERANCE_SECONDS,
    SCORING_INTERVAL,
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
    normalise_wh_hours,
    provider_label,
)

_LOGGER = logging.getLogger(__name__)

#: Buckets held in memory. The long history lives in the recorder.
MAX_BUCKETS = 24 * 45

#: Seconds a save of the window waits, so the hours that close together (a
#: restart's first bucket and the hour after it) are written once.
SAVE_DELAY = 30


@dataclass(slots=True)
class SolarSanityData:
    """What lives on ``entry.runtime_data``."""

    coordinator: SolarSanityCoordinator
    store: Store


#: Single characters, because this is repeated once per channel per hour and a
#: month of six channels is four thousand of them.
_QUALITY_CODE: Final = {
    Quality.OK: "O",
    Quality.MISSING: "M",
    Quality.RESET_SUSPECT: "R",
    Quality.DERIVED_FROM_MEAN: "D",
    Quality.STALE: "S",
}

_SOURCE_CODE: Final = {
    BucketSource.OWN_INTEGRAL: "i",
    BucketSource.LTS_SUM: "s",
    BucketSource.LTS_MEAN: "m",
}

#: The same two tables, inverted once here rather than at every read.
#:
#: Derived rather than written out, so they cannot drift from the tables above.
#: A code the encoder never emits is therefore a code the decoder refuses, which
#: is the behaviour wanted: an unrecognised character means the file was written
#: by something else, and guessing at it would restore a window that looks right
#: and is not.
_QUALITY_BY_CODE: Final = {code: quality for quality, code in _QUALITY_CODE.items()}
_SOURCE_BY_CODE: Final = {code: source for source, code in _SOURCE_CODE.items()}


def _count_sources(buckets: Sequence[Bucket], key: str) -> dict[str, int]:
    """How many of this channel's hours came from each source.

    Free rather than a method: it needs nothing from the coordinator, and a
    test that stands one up as a stub should not have to grow a member to ask a
    question about a list of buckets.
    """
    counts: dict[str, int] = {}
    for bucket in buckets:
        source = bucket.source.get(key)
        if source is not None and bucket.wh.get(key) is not None:
            counts[source.value] = counts.get(source.value, 0) + 1
    return dict(sorted(counts.items()))


class SolarSanityCoordinator(DataUpdateCoordinator[None]):
    """Owns the measurement window, the forecasts and their scores.

    It carries no data of its own (``None``): its refresh scores the forecast
    providers, and its listeners are the sensors, rewritten from the sampling
    ticks through ``notify_live_entities``. The refresh never raises, because
    ``last_update_success`` is what every entity's availability chains to, and
    an archive that cannot be read must not take the battery outlook with it.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=SCORING_INTERVAL,
        )
        self.entry = entry
        self._buckets: list[Bucket] = []
        #: One entry per configured provider, once there is anything to say.
        #: Empty is the normal state for the first three weeks — the estimator
        #: wants twenty-one comparable days before it will put a number to
        #: anything, and says so in `Bias.reason` rather than showing a figure
        #: nobody should act on.
        self.forecast_scores: tuple[Any, ...] = ()
        self._accumulator: dict[str, float] = {}

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
        #: Latest forecast total for tomorrow, in kWh, and the provider it is
        #: from. The corrected figure corrects this provider and no other.
        self._expected_tomorrow_kwh: float | None = None
        self._expected_tomorrow_provider: str | None = None
        #: Each provider's latest forecast, in kWh per UTC hour, as the battery
        #: outlook reads it. Held in memory only: it is re-captured every thirty
        #: minutes, and the archive keeps the history.
        self.forecast_points: dict[str, dict[datetime, float]] = {}
        #: When ``forecast_points`` was last refreshed, so a provider that has
        #: stopped is not projected from forever.
        self.forecast_captured_at: datetime | None = None
        #: Entity ids the recorder holds no statistics for. Their sensors
        #: carry no state_class, so no history exists to backfill from.
        self.unrecorded_entities: tuple[str, ...] = ()
        #: How the recorder classified each mapped entity at the last
        #: backfill: "sum", "mean", or "absent". Empty until it has run.
        self.statistics_classes: dict[str, str] = {}
        #: Hourly rows the last backfill actually returned, per entity id.
        self.backfill_rows: dict[str, int] = {}
        self._accumulator_start: datetime | None = None
        # One file per entry. A single shared file once meant two installations
        # overwrote each other on every save; the second write simply won.
        self._store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_STATE}.{entry.entry_id}",
            minor_version=STORAGE_MINOR_VERSION,
        )
        #: Where the battery is heading. Built here because it reads the window,
        #: the forecast and the open hour, all of which this class owns.
        self.battery = BatteryOutlook(hass, self)

    # -- configuration ------------------------------------------------------

    @property
    def buckets(self) -> tuple[Bucket, ...]:
        """The analysis window, for readers outside this class.

        A copy, because the list is mutated in place on every bucket close and a
        caller iterating it while that happens gets an inconsistent window
        rather than an error.
        """
        return tuple(self._buckets)

    @property
    def time_zone(self) -> tzinfo | None:
        """The installation's zone, or ``None`` when it cannot be resolved.

        ``None`` is a real answer and callers must handle it: anything keying
        days on an unresolvable zone collapses its whole window into one.
        """
        return self._time_zone()

    @property
    def specs(self) -> tuple[ChannelSpec, ...]:
        """Configured channels, with what each one measures."""
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

    def open_hour_rates(self, now: datetime, min_seconds: float) -> dict[str, float]:
        """Average power so far this hour, in W, for each channel that can say.

        Read-only: nothing here touches the accumulator, so asking cannot change
        the bucket the hour will close with. A channel is left out when the hour
        has a hole in it, when it was reset, or when it has been watched for less
        than ``min_seconds``; a few minutes of a kettle is not an hour's load.

        Power channels are carried up to ``now`` at the value they currently
        hold; energy channels only up to their last five-minute reading, since
        nothing is known about a counter between readings.
        """
        start = self._accumulator_start
        if start is None:
            return {}
        began = max(start, self._first_sample_at or start)
        out: dict[str, float] = {}
        for spec in self.specs:
            key = spec.key
            if key in self._suspect or key in self._gap_since or self._gap_seconds.get(key):
                continue
            accumulated = self._accumulator.get(key)
            held = self._live_power.get(key)
            if held is not None:
                watts, since = held
                carried = watts * max((now - max(since, began)).total_seconds(), 0.0) / 3600.0
                energy = carried if accumulated is None else accumulated + carried
                elapsed = (now - began).total_seconds()
            else:
                last = self._last_energy.get(key)
                if accumulated is None or last is None:
                    continue
                energy = accumulated
                elapsed = (last[1] - began).total_seconds()
            if elapsed < min_seconds:
                continue
            out[key] = energy * 3600.0 / elapsed
        return out

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
        """Rewrite every entity from the sampling ticks.

        ``CoordinatorEntity`` only writes state when the coordinator updates,
        and the coordinator's own refresh runs every six hours. The battery
        outlook describes *now*, and would otherwise be frozen to that cadence:
        a sensor once stuck at 0% for six hours because the integration loaded
        before the inverter's entities had published and nothing rewrote it.
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
        self._store.async_delay_save(self._payload, SAVE_DELAY)

    def ingest_backfill(self, series: dict[str, list[tuple[datetime, float, bool]]]) -> None:
        """Seed the window from long-term statistics at setup.

        Mean-derived buckets are tagged ``LTS_MEAN``: an hourly mean cannot say
        whether the hour it describes was complete, and the forecast scoring
        reads that tag. Sum-derived ones are exact and tagged ``LTS_SUM``. See
        ``BucketSource``.
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
        tomorrow_provider: str | None = None
        points: dict[str, dict[datetime, float]] = {}
        for entry_id, payload in forecasts.items():
            provider = self.hass.config_entries.async_get_entry(entry_id)
            if provider is None:
                continue
            wh_hours = payload.get("wh_hours") if isinstance(payload, dict) else None
            if not isinstance(wh_hours, dict):
                continue
            # The same hour-aligned figures the archive is written from, so the
            # outlook and the archive cannot disagree about what was forecast.
            points[entry_id] = dict(normalise_wh_hours(wh_hours))
            if self._owns_archive(entry_id) and await async_record_forecast(
                self.hass, entry_id, await self._provider_name(provider), wh_hours
            ):
                written += 1

            # Surface the first provider's figure. Once accuracy scoring exists
            # this should prefer whichever provider has been most accurate here.
            if tomorrow_kwh is None:
                tomorrow_kwh = self._sum_for_tomorrow(wh_hours)
                if tomorrow_kwh is not None:
                    tomorrow_provider = entry_id

        self._expected_tomorrow_kwh = tomorrow_kwh
        self._expected_tomorrow_provider = tomorrow_provider
        if points:
            self.forecast_points = points
            self.forecast_captured_at = dt_util.utcnow()
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

    async def async_forecast_history(self, days: int = 32) -> dict[str, Any]:
        """Both archives' hourly values, for replaying the battery outlook.

        The day-ahead series is the one a replay must use: the latest series
        holds each hour's final revision, issued minutes before it, and grading
        a projection on that would grade it on hindsight. Both are included so
        the difference can be seen rather than assumed.
        """
        end = dt_util.utcnow() + timedelta(days=2)
        start = end - timedelta(days=days)
        out: dict[str, Any] = {}
        for entry_id in self.entry.data.get(CONF_FORECAST_ENTRIES) or []:
            series = {}
            for label, statistic_id in (
                ("dayahead", dayahead_statistic_id(entry_id)),
                ("latest", forecast_statistic_id(entry_id)),
            ):
                values = await async_forecast_series(self.hass, statistic_id, start, end)
                series[label] = (
                    None
                    if values is None
                    else {when.isoformat(): round(kwh, 4) for when, kwh in sorted(values.items())}
                )
            out[entry_id] = series
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

    # -- scoring ------------------------------------------------------------

    async def _async_update_data(self) -> None:
        """Score the forecast providers. Never raises; see the class docstring."""
        await self._async_score_forecasts()

    async def _async_score_forecasts(self) -> None:
        """Score each configured provider against the window.

        Swallowed on failure, which is not the usual rule here. A forecast score
        is a side dish: it reads an archive, and an archive that cannot be read
        must not cost a house its battery outlook.
        """
        from .scoring import async_score_providers

        try:
            self.forecast_scores = tuple(await async_score_providers(self.hass, self))
        except Exception:
            _LOGGER.debug("forecast scoring failed", exc_info=True)

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
        """The instance's current offset from UTC, in hours, for diagnostics."""
        try:
            tz = dt_util.get_time_zone(self.hass.config.time_zone)
            if tz is None:
                return 0.0
            offset = dt_util.utcnow().astimezone(tz).utcoffset()
            return offset.total_seconds() / 3600.0 if offset else 0.0
        except Exception:
            return 0.0

    def _payload(self) -> dict[str, Any]:
        """What is saved: the window, and nothing else.

        Passed to ``async_delay_save`` as a bound method rather than a value, so
        the write that happens thirty seconds later, or at shutdown, saves the
        window as it is then and not as it was when the save was asked for.

        What is restored is an attestation rather than a better number. Home
        Assistant's hourly mean is time-weighted and, for an hour with all
        twelve of its five-minute rows present, agrees with the integral
        computed here. The difference is that a mean cannot say whether the
        hour was complete; our own bucket knows it watched the channel end to
        end. The columnar encoding is the diagnostics block's: about 120 kB at
        the full window.
        """
        return {"window": self.window_snapshot()}

    async def async_restore(self) -> None:
        """Reload the saved window so a restart does not lose measured hours.

        Ahead of the statistics backfill, which is what makes this work:
        ``ingest_backfill`` skips any hour already present, so whatever is
        restored here wins over the same hour re-derived from statistics. That
        ordering is in ``async_setup_entry`` and is the whole mechanism.

        The file may not cost more than itself. ``Store`` calls
        ``_async_migrate_func`` whenever the stored version differs from the one
        asked for, and its default raises ``NotImplementedError``, so a file
        written by a release we have not met would take setup down with it, and
        it reaches *downgrading* users first. Older files also hold keys this
        release no longer writes (``loss_model``, ``last_status``); only
        ``window`` is read.
        """
        stored = await self._restore_from(self._store)
        if stored:
            self._buckets = self._buckets_from_snapshot(stored.get("window"))

    async def _restore_from(self, store: Store) -> dict[str, Any] | None:
        """One stored file, or ``None`` if it cannot be read for any reason.

        Deliberately broad. The interesting failure is a version this build
        cannot migrate, but a half-written file after a power cut reads no
        better and deserves the same answer — and there is no outcome here worth
        refusing to start over, because everything this file holds is derived
        and re-derivable.

        It is logged at warning rather than swallowed: a battery outlook that
        has forgotten its own measured hours after a restart is a thing an owner
        may notice, and the log is the only place that will connect the two.
        """
        try:
            return await store.async_load()
        except Exception:
            _LOGGER.warning(
                "Could not read %s; continuing without the saved window, which "
                "is refilled from the recorder's statistics",
                store.key,
                exc_info=True,
            )
            return None

    # -- reporting ----------------------------------------------------------

    def _tomorrow_correction(self) -> Correction | None:
        """The correction for the provider tomorrow's figure came from.

        That provider and no other. This once took the first provider with a
        stated bias, whichever it was, and multiplied the *first* provider's
        forecast by it: two installations' worth of providers could produce a
        figure no provider issued and no scoring covered.

        Which correction, and why the battery outlook's comes first, is
        ``analysis.forecast.correction``'s to say. A bias computed but not
        stated is never used: eleven days of arithmetic is not yet an answer,
        and multiplying tomorrow by it would launder that distinction into a
        number people plan around.
        """
        provider = self._expected_tomorrow_provider
        if provider is None:
            return None
        # Tomorrow's factor, not the overall one: learned mostly from the days
        # whose forecast looked like tomorrow's, the same factor the outlook
        # projects tomorrow with.
        return self._correction_for(
            provider, self.battery.tomorrow_factor(provider, dt_util.utcnow())
        )

    def _correction_for(self, provider: str, factor: float | None) -> Correction | None:
        """The correction for one provider on one day, given that day's learned factor."""
        learned = self.battery.scale_for(provider)
        scored = next((s.bias for s in self.forecast_scores if s.entry_id == provider), None)
        return correction(
            None if learned is None or factor is None else (factor, learned.days),
            None if scored is None else scored.reportable_pct,
            0 if scored is None else scored.days,
        )

    def _uncorrected_reason(self, provider: str) -> str:
        """Why a provider's figures cannot be corrected yet, in a sentence."""
        if self.battery.configured:
            return (
                "The battery outlook has not learned this roof yet. It needs "
                "three days with room in the battery."
            )
        scored = next((s.bias for s in self.forecast_scores if s.entry_id == provider), None)
        if scored is not None and scored.reason:
            return scored.reason
        return "This provider has not been compared with the roof yet."

    def _roof_provider(self) -> str | None:
        """The provider whose figures for today are corrected.

        The one tomorrow's figure came from when it has a forecast in hand, so
        the corrected sensors describe one provider; else the first that has.
        """
        provider = self._expected_tomorrow_provider
        if provider is not None and self.forecast_points.get(provider):
            return provider
        for entry_id in self.entry.data.get(CONF_FORECAST_ENTRIES) or []:
            if self.forecast_points.get(entry_id):
                return entry_id
        return None

    def roof_forecast(self) -> RoofForecast | None:
        """The provider's figures for now and today, corrected for this roof.

        What Forecast.Solar publishes as sensors (power now, this hour, the
        next hour, what is left of today, today) multiplied by the factor for
        the kind of day forecast, so no figure for an ideal roof needs showing
        beside them. ``None`` until there is a correction, because without one
        the figure is the provider's own sensor.
        """
        provider = self._roof_provider()
        if provider is None:
            return None
        now = dt_util.utcnow()
        tz = self._time_zone()

        def day_of(when: datetime) -> date:
            return when.astimezone(tz).date() if tz is not None else when.date()

        today = day_of(now)
        learned = self.battery.day_factors(provider, now)
        found = {
            day: self._correction_for(provider, learned.get(day))
            for day in (today, today + timedelta(days=1))
        }

        def factor_for(day: date) -> float | None:
            found_for_day = found.get(day)
            return None if found_for_day is None else found_for_day.factor

        totals = self.battery.day_totals(provider, now)
        return roof_forecast(
            self.forecast_points[provider],
            now,
            day_of=day_of,
            factor_for=factor_for,
            today_kwh=totals.get(today, 0.0) / 1000.0,
        )

    @property
    def roof_forecast_attrs(self) -> dict[str, Any]:
        """Which provider today's corrected figures come from, and by what."""
        provider = self._roof_provider()
        if provider is None:
            return {"reason": "No forecast has been captured yet."}
        entry = self.hass.config_entries.async_get_entry(provider)
        attrs: dict[str, Any] = {"provider": entry.title if entry is not None else provider}
        now = dt_util.utcnow()
        tz = self._time_zone()
        today = now.astimezone(tz).date() if tz is not None else now.date()
        found = self._correction_for(provider, self.battery.day_factors(provider, now).get(today))
        if found is None:
            attrs["reason"] = self._uncorrected_reason(provider)
            return attrs
        attrs.update(factor=found.factor, basis=found.basis, days=found.days)
        return attrs

    @property
    def expected_tomorrow_corrected_kwh(self) -> float | None:
        """Tomorrow's forecast, moved by what this roof has delivered against it.

        ``None`` until there is a correction worth applying. On the reference
        installation that was every day for a month: its forecast runs at about
        twice what the roof delivers, but the gap grows with the size of the
        day, so the scored bias was never stated, while the battery outlook had
        learned 0.52 from the same archive within three days.
        """
        base = self.expected_tomorrow_kwh
        found = self._tomorrow_correction()
        if base is None or found is None:
            return None
        return found.apply(base)

    @property
    def expected_tomorrow_corrected_attrs(self) -> dict[str, Any]:
        """Which provider was corrected, by what, and on what evidence.

        ``forecast_statistic_id`` is the rolling archive of the provider this
        figure corrects: the forecast card matches its own panels on it, so it
        can show the correction under the right provider.
        """
        provider = self._expected_tomorrow_provider
        if provider is None:
            return {"reason": "No forecast for tomorrow has been captured yet."}
        entry = self.hass.config_entries.async_get_entry(provider)
        attrs: dict[str, Any] = {
            "provider": entry.title if entry is not None else provider,
            "forecast_statistic_id": forecast_statistic_id(provider),
        }
        found = self._tomorrow_correction()
        if found is not None:
            attrs.update(factor=found.factor, basis=found.basis, days=found.days)
            return attrs
        attrs["reason"] = self._uncorrected_reason(provider)
        return attrs

    @property
    def expected_tomorrow_kwh(self) -> float | None:
        """Tomorrow's forecast total, or ``None`` when no provider is configured."""
        return self._expected_tomorrow_kwh

    def window_snapshot(self) -> dict[str, Any]:
        """Every hour in the window, as numbers, so the outlook can be replayed.

        The outlook is a pure function of these buckets, the charge level and
        the forecast. Without them a bug report says what the answer was and
        nothing about the arithmetic, so every question has to be settled by
        asking the user to run something. ``scripts/backtest_outlook.py`` reads
        this block back.

        Columnar rather than a list of objects: the same window as a dict per
        bucket is roughly four times the bytes for the same content, and this is
        a file people paste into issue trackers. Quality and source are single
        characters per channel, in ``keys`` order, for the same reason.

        Values are emitted unrounded, unlike every figure this integration shows
        a person. Those are rounded because a total that cancels to nothing
        reads as broken when it prints as 2.6e-21; this block is not read by
        eye, it is fed back into a replay, and rounding it to milliwatt-hours
        once moved a replayed figure from 5e-16 to 3e-06. Harmless here, and
        precisely the kind of drift that makes a replay stop being evidence.
        """
        specs = self.specs
        keys = [spec.key for spec in specs]
        rows = []
        for bucket in self._buckets:
            rows.append(
                [
                    bucket.start_utc.isoformat(),
                    bucket.seconds,
                    [bucket.wh.get(key) for key in keys],
                    "".join(_QUALITY_CODE.get(bucket.quality.get(key), "?") for key in keys),
                    "".join(_SOURCE_CODE.get(bucket.source.get(key), "?") for key in keys),
                ]
            )
        return {
            "keys": keys,
            "quality_codes": {code: q.value for q, code in _QUALITY_CODE.items()},
            "source_codes": {code: s.value for s, code in _SOURCE_CODE.items()},
            "columns": ["start_utc", "seconds", "wh", "quality", "source"],
            "rows": rows,
        }

    def _buckets_from_snapshot(self, snapshot: Any) -> list[Bucket]:
        """The inverse of ``window_snapshot``, for restoring a saved window.

        Deliberately strict. Every failure here returns nothing rather than
        something partial: a window restored with a channel silently missing, or
        an unreadable quality code quietly treated as OK, would launder guesses
        into the one place this integration treats as measurement. Losing the
        saved window costs a restart's worth of history; trusting a damaged one
        costs a wrong answer with no way to tell.
        """
        if not isinstance(snapshot, dict):
            return []

        keys = snapshot.get("keys")
        rows = snapshot.get("rows")
        if not isinstance(keys, list) or not isinstance(rows, list):
            return []

        restored: list[Bucket] = []
        for row in rows:
            if not isinstance(row, list) or len(row) != 5:
                return []
            start_raw, seconds, values, quality_codes, source_codes = row
            if not (
                isinstance(values, list)
                and isinstance(quality_codes, str)
                and isinstance(source_codes, str)
                and len(values) == len(keys)
                and len(quality_codes) == len(keys)
                and len(source_codes) == len(keys)
            ):
                return []

            try:
                start = datetime.fromisoformat(start_raw)
                wh = {key: values[i] for i, key in enumerate(keys)}
                quality = {key: _QUALITY_BY_CODE[quality_codes[i]] for i, key in enumerate(keys)}
                source = {key: _SOURCE_BY_CODE[source_codes[i]] for i, key in enumerate(keys)}
            except (KeyError, TypeError, ValueError):
                return []

            local_date, dst = self._local_day(start)
            restored.append(
                Bucket(
                    start_utc=start,
                    seconds=int(seconds),
                    wh=wh,
                    quality=quality,
                    source=source,
                    local_date=local_date,
                    is_dst_transition=dst,
                )
            )

        restored.sort(key=lambda bucket: bucket.start_utc)
        return restored[-MAX_BUCKETS:]

    def coverage_snapshot(self) -> dict[str, Any]:
        """Where every channel's hours came from, in one downloadable place.

        Every backfill defect so far was diagnosed by asking a user to read
        state attributes back one at a time, which is slow and gets the wrong
        answer when the attribute they read is not the one at fault. The button
        that produces this file is on the same page as the sensors, and it
        answers the whole question at once.
        """
        specs = self.specs
        buckets = tuple(self._buckets)

        channels: list[dict[str, Any]] = []
        for spec in specs:
            state = self.hass.states.get(spec.entity_id)
            value, kind = read_channel(state)
            channels.append(
                {
                    "key": spec.key,
                    "entity_id": spec.entity_id,
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
        return {
            "channels": channels,
            "unrecorded_entities": list(self.unrecorded_entities),
            "utc_offset_hours": self._utc_offset_hours(),
            "buckets": {
                "held": len(buckets),
                "whole_hours": len(whole),
                "first_utc": buckets[0].start_utc.isoformat() if buckets else None,
                "last_utc": buckets[-1].start_utc.isoformat() if buckets else None,
                # Where the numbers came from, per channel. `lts_mean` is the
                # weak one: an hourly mean cannot say whether its hour was
                # complete, so a power channel read this way can sit wrong
                # while an energy counter beside it is exact.
                "by_source": {spec.key: _count_sources(buckets, spec.key) for spec in self.specs},
            },
        }


# -- helpers ----------------------------------------------------------------


_ROLE_BY_KEY = {role.key: role for role in Role}


def _role_from_key(key: str | None) -> Role | None:
    return _ROLE_BY_KEY.get(key) if key else None


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
