"""The battery outlook, fed from what the coordinator already holds.

The arithmetic is in ``analysis/outlook.py`` and is pure. This module is the
part that has to touch Home Assistant: reading the charge level, deciding
whether it can be believed, choosing which forecast to use, and keeping the
published times steady between refreshes.

Nothing here reads a state during setup. On an installation whose inverter is
bridged over MQTT the entities publish after Home Assistant has started, so the
charge level is read only when a projection is computed, and its absence then
is an answer ("unavailable") rather than an error.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any, Final

from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from ._forecast_plan import forecast_is_fresh
from .analysis.daylight_shape import DaylightShape, learn_shape
from .analysis.forecast import corrected_hours
from .analysis.model import Role
from .analysis.outlook import (
    BatterySettings,
    Outlook,
    build_steps,
    project,
    settle_full,
    settle_reserve,
    spare_power_w,
    usable_energy_wh,
)
from .analysis.profile import (
    PROFILE_DAYS,
    ForecastScale,
    forecast_scale,
    grid_charged_hours,
    hourly_profile,
    max_charge_w,
    standby_w,
)
from .const import (
    CONF_FORECAST_ENTRIES,
    DEFAULT_RESERVE_PCT,
    DEFAULT_TARGET_PCT,
    FORECAST_MAX_AGE,
    OPT_BATTERY_CAPACITY_KWH,
    OPT_BATTERY_RESERVE_PCT,
    OPT_BATTERY_SOC,
    OPT_BATTERY_TARGET_PCT,
)
from .statistics_source import async_forecast_series, async_hourly_soc, dayahead_statistic_id

if TYPE_CHECKING:
    from .coordinator import SolarSanityCoordinator

_LOGGER = logging.getLogger(__name__)

#: How old a charge reading may be before it is doubted. Doubted, not rejected:
#: a battery sitting full at noon reports 100% once and then has nothing new to
#: say, so age alone would call a healthy idle battery stale every afternoon.
SOC_MAX_AGE: Final = timedelta(hours=2)

#: ...so an old reading is rejected only if the battery has moved at least this
#: share of its capacity since, which is a charge level that should have changed
#: and did not. Without battery channels mapped there is no way to tell, and
#: the longer limit below applies instead.
SOC_STALE_MOVEMENT: Final = 0.02
SOC_MAX_AGE_UNCHECKED: Final = timedelta(hours=6)

#: The open hour must have been watched for this long before its measured
#: average replaces the expectation for the rest of it. Five minutes of a
#: kettle is not an hour's consumption.
ANCHOR_MIN_SECONDS: Final = 900.0

#: Why there is no outlook, as sentences the sensors publish verbatim. Keys
#: are stable; the sentences may be reworded.
REASONS: Final = {
    "soc_unavailable": "The battery charge level sensor is not reporting a number.",
    "soc_stale": (
        "The battery charge level has not changed although the battery has been "
        "charging or discharging, so it cannot be believed."
    ),
    "learning_consumption": (
        "Learning how much the house uses at each hour; this needs three days "
        "of consumption history."
    ),
    "no_production_expectation": (
        "No forecast is available and there is not yet enough production "
        "history to know when the sun comes up."
    ),
    "no_horizon": "There is nothing ahead to project.",
}


@dataclass(slots=True)
class OutlookState:
    """The latest projection, or the reason there is none."""

    outlook: Outlook | None = None
    reason: str | None = "soc_unavailable"
    soc_age_s: float | None = None
    #: The charge level the projection started from, kept even when there is
    #: no projection yet, so the card can show the battery while the
    #: consumption profile is still being learned.
    soc_pct: float | None = None
    computed_at: datetime | None = None
    full_at: datetime | None = None
    reserve_at: datetime | None = None
    provider: str | None = None
    load_days: int = 0
    grid_charged_hours: int | None = None
    max_charge_w: float | None = None
    standby_w: float | None = None
    scale: ForecastScale | None = None
    #: The factor the forecast was multiplied by today and tomorrow, each
    #: learned mostly from days forecast like it (``ForecastScale.for_day``).
    scale_today: float | None = None
    scale_tomorrow: float | None = None
    #: The battery as the projection saw it, learned ceiling and standby
    #: included, so the spare power can be worked between refreshes.
    battery: BatterySettings | None = None
    #: What the battery can still give before its reserve, in Wh.
    usable_wh: float | None = None
    #: The power sensors the card shows live (``coordinator.power_sources``).
    power_sources: dict[str, Any] = field(default_factory=dict)
    load_profile: tuple[float | None, ...] = ()
    pv_profile: tuple[float | None, ...] = ()
    settings: dict[str, float] = field(default_factory=dict)
    #: Exact steps used at issue time; the journal must never reconstruct them
    #: from a later forecast or a later consumption profile.
    inputs: dict[str, Any] = field(default_factory=dict)
    shadow: Outlook | None = None
    shadow_full_at: datetime | None = None
    shadow_reserve_at: datetime | None = None
    shape_reason: str = "insufficient_shape_days"
    shape_days: tuple[int, ...] = ()

    @property
    def soc_readable(self) -> bool:
        return self.reason not in ("soc_unavailable", "soc_stale")


def battery_settings(options: dict[str, Any]) -> BatterySettings | None:
    """The configured battery, or ``None`` until the outlook can run.

    Both the charge-level sensor and the capacity are required: without the
    first there is nothing to project from, and without the second a
    percentage cannot become hours.
    """
    if not options.get(OPT_BATTERY_SOC):
        return None
    capacity = _number(options.get(OPT_BATTERY_CAPACITY_KWH))
    if capacity is None or capacity <= 0:
        return None
    reserve = _number(options.get(OPT_BATTERY_RESERVE_PCT))
    target = _number(options.get(OPT_BATTERY_TARGET_PCT))
    return BatterySettings(
        capacity_wh=capacity * 1000.0,
        reserve_pct=DEFAULT_RESERVE_PCT if reserve is None else reserve,
        target_pct=DEFAULT_TARGET_PCT if target is None else target,
    )


def _number(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    return value if math.isfinite(value) else None


class BatteryOutlook:
    """Owns the projection and the times already published from it."""

    def __init__(self, hass: HomeAssistant, coordinator: SolarSanityCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.state = OutlookState()
        self._dirty = True
        self._scales: dict[str, ForecastScale | None] = {}
        #: Each provider's day-ahead archive, in Wh per UTC hour, from the last
        #: learn. It says what kind of day today was expected to be for the
        #: hours that have already passed.
        self._dayahead: dict[str, dict[datetime, float]] = {}
        self._learned_hour: datetime | None = None
        self._shapes: dict[str, DaylightShape | None] = {}
        self._forecast_provider_id: str | None = None
        self._daylight_windows: dict[date, tuple[datetime, datetime]] = {}
        self._shape_zone: str | None = None

    def corrected_hours(self, provider: str, now: datetime) -> dict[datetime, float]:
        """One active corrected series in Wh, shared by projection and sensor data."""
        scale = self._scales.get(provider)
        overall = 1.0 if scale is None else scale.factor
        factors = self.day_factors(provider, now)
        day_of = _local_date(self.coordinator.time_zone)
        return corrected_hours(
            {
                when: kwh * 1000
                for when, kwh in self.coordinator.forecast_points.get(provider, {}).items()
            },
            day_of=day_of,
            factor_for=lambda day: factors.get(day, overall),
        )

    def shape_hours(self, provider: str, now: datetime) -> tuple[dict[datetime, float] | None, str]:
        """The candidate only; incomplete horizons or training keep the baseline."""
        if self._shape_zone != self.hass.config.time_zone:
            return None, "shape_context_changed"
        shape = self._shapes.get(provider)
        if shape is None:
            return None, "insufficient_shape_days"
        day_of = _local_date(self.coordinator.time_zone)
        current = self.coordinator.forecast_points.get(provider, {})
        archived = self._dayahead.get(provider, {})
        hour = now.replace(minute=0, second=0, microsecond=0)
        merged = {when: value for when, value in archived.items() if when < hour}
        merged.update({when: kwh * 1000 for when, kwh in current.items() if when >= hour})
        factors = self.day_factors(provider, now)
        scale = self._scales.get(provider)
        overall = 1.0 if scale is None else scale.factor
        grouped: dict[date, dict[datetime, float]] = {}
        for when, value in merged.items():
            grouped.setdefault(day_of(when), {})[when] = value
        output = self.corrected_hours(provider, now)
        changed = False
        for day, raw in grouped.items():
            if day < day_of(now):
                continue
            factor = factors.get(day, overall)
            shaped = shape.distribute(
                {t: v * factor for t, v in raw.items()},
                sum(raw.values()),
                self._daylight_windows.get(day),
            )
            if shaped is None:
                continue
            changed = True
            output.update({t: v for t, v in shaped.items() if t in current})
        return (output, "shadow_only") if changed else (None, "shape_horizon_incomplete")

    @property
    def settings(self) -> BatterySettings | None:
        return battery_settings(dict(self.coordinator.entry.options))

    @property
    def configured(self) -> bool:
        return self.settings is not None

    def scale_for(self, provider_entry_id: str) -> ForecastScale | None:
        """The learned scale for one provider, or ``None`` before there is one.

        Read by the corrected tomorrow forecast, so that figure uses the same
        correction this projection does rather than a second opinion.
        """
        return self._scales.get(provider_entry_id)

    def day_factors(self, provider_entry_id: str, now: datetime) -> dict[date, float]:
        """The factor for today and for tomorrow, each by the day forecast.

        Keyed by local date; empty before there is a scale. A day is judged by
        its whole forecast, taking for each hour the freshest figure still
        meaningful: the current forecast for an hour to come, the day-ahead
        archive for an hour that has passed or that the current forecast does
        not reach. Never the current forecast for a passed hour: Forecast.Solar
        rewrites a day's elapsed hours as the day goes on, and on the reference
        installation its last capture of every day from 4 to 8 November added
        up to 72-74 kWh, a clear sky, whatever the weather had been. Judged by
        that, a cloudy afternoon would be scaled as a sunny one.

        Later days keep the overall factor. The projection reaches them only
        before sunrise, and a total the forecast covers only partly would make
        a sunny day look like a cloudy one.
        """
        scale = self._scales.get(provider_entry_id)
        if scale is None:
            return {}
        totals = self.day_totals(provider_entry_id, now)
        return {day: scale.for_day(total) for day, total in totals.items()}

    def day_totals(self, provider_entry_id: str, now: datetime) -> dict[date, float]:
        """Today's and tomorrow's whole forecast, in Wh, as ``day_factors`` judges it.

        Without a day-ahead archive in hand (no battery, so nothing learned),
        every hour comes from the current forecast, which is then the
        provider's own figure for the day.
        """
        day_of = _local_date(self.coordinator.time_zone)
        today = day_of(now)
        tomorrow = today + timedelta(days=1)
        hour = now.replace(minute=0, second=0, microsecond=0)
        current = self.coordinator.forecast_points.get(provider_entry_id) or {}
        archived = self._dayahead.get(provider_entry_id) or {}
        totals = {today: 0.0, tomorrow: 0.0}
        for when in set(current) | set(archived):
            day = day_of(when)
            if day not in totals:
                continue
            if when in current and (when >= hour or not archived):
                totals[day] += current[when] * 1000.0
            else:
                totals[day] += archived.get(when, 0.0)
        return totals

    def spare_power(self, now: datetime | None = None) -> tuple[float, float, str] | None:
        """Solar power going to waste now, as ``spare_power_w`` works it.

        Read when the sensor is written, every thirty seconds, from the live
        readings rather than from the last refresh: the house's load moves by
        the second, and an air conditioner switched on by this figure must see
        it move. The battery and its charge come from the last refresh.
        ``None`` while the charge level cannot be believed.
        """
        state = self.state
        if state.battery is None or state.soc_pct is None or not state.soc_readable:
            return None
        rates = self.coordinator.live_rates(now or dt_util.utcnow(), ANCHOR_MIN_SECONDS)
        load = rates.get(Role.LOAD.key)
        if load is None:
            return None
        figures = self.coordinator.roof_forecast()
        return spare_power_w(
            state.soc_pct,
            state.battery,
            measured_pv_w=rates.get(Role.PV.key),
            forecast_pv_w=None if figures is None else figures.power_now_w,
            load_w=load,
        )

    def tomorrow_factor(self, provider_entry_id: str, now: datetime) -> float | None:
        """``day_factors`` for tomorrow's local date, or ``None`` without a scale."""
        tomorrow = _local_date(self.coordinator.time_zone)(now) + timedelta(days=1)
        return self.day_factors(provider_entry_id, now).get(tomorrow)

    @callback
    def async_track(self) -> Callable[[], None]:
        """Recompute soon after the charge level changes.

        Subscribed by entity id, which works before the entity exists. The
        callback only marks the projection stale; the thirty-second tick does
        the work, so a sensor that reports every second costs nothing.
        """
        entity_id = self.coordinator.entry.options.get(OPT_BATTERY_SOC)
        if not entity_id or not self.configured:
            return lambda: None

        @callback
        def _changed(_event: Any) -> None:
            self._dirty = True

        return async_track_state_change_event(self.hass, [entity_id], _changed)

    async def async_learn(self, now: datetime | None = None) -> None:
        """Re-learn how far each provider's forecast runs from this roof.

        Reads the recorder twice, for the day-ahead archive and the charge
        level's hourly maxima. So it runs from the forecast capture, at most once
        an hour, and never from the thirty-second tick. A read that fails keeps
        the last scale rather than dropping to an unscaled forecast for an hour.
        """
        settings = self.settings
        entity_id = self.coordinator.entry.options.get(OPT_BATTERY_SOC)
        if settings is None or not entity_id:
            return
        hour = (now or dt_util.utcnow()).replace(minute=0, second=0, microsecond=0)
        if self._learned_hour == hour:
            return
        start = hour - timedelta(days=PROFILE_DAYS)
        day_of = _local_date(self.coordinator.time_zone)
        soc_max: dict[datetime, float] = {}
        for row in await async_hourly_soc(self.hass, entity_id, start, hour):
            when = dt_util.parse_datetime(str(row.get("start")))
            level = _number(row.get("max"))
            if when is not None and level is not None:
                soc_max[dt_util.as_utc(when)] = level
        if not soc_max:
            return
        scales: dict[str, ForecastScale | None] = {}
        dayahead: dict[str, dict[datetime, float]] = {}
        for entry_id in self.coordinator.entry.data.get(CONF_FORECAST_ENTRIES) or []:
            # A day either side of the fortnight: each day learned from is
            # judged by its whole forecast, so the first one must be whole,
            # and today and tomorrow are judged from the same archive.
            series = await async_forecast_series(
                self.hass,
                dayahead_statistic_id(entry_id),
                start - timedelta(days=1),
                hour + timedelta(days=2),
            )
            if series is None:
                scales[entry_id] = self._scales.get(entry_id)
                dayahead[entry_id] = self._dayahead.get(entry_id) or {}
                continue
            dayahead[entry_id] = {when: kwh * 1000.0 for when, kwh in series.items()}
            scales[entry_id] = forecast_scale(
                self.coordinator.buckets,
                dayahead[entry_id],
                soc_max=soc_max,
                full_pct=settings.target_pct,
                pv=Role.PV.key,
                grid=Role.GRID_IMPORT.key,
                until=hour,
                day_of=day_of,
            )
            try:
                for day in {day_of(t) for t in dayahead[entry_id]}:
                    sunrise = get_astral_event_date(self.hass, SUN_EVENT_SUNRISE, day)
                    sunset = get_astral_event_date(self.hass, SUN_EVENT_SUNSET, day)
                    if sunrise is not None and sunset is not None:
                        self._daylight_windows[day] = (sunrise, sunset)
                self._shapes[entry_id] = learn_shape(
                    self.coordinator.buckets,
                    dayahead[entry_id],
                    soc_max=soc_max,
                    full_pct=settings.target_pct,
                    until=hour,
                    day_of=day_of,
                    daylight=self._daylight_windows,
                )
            except Exception:
                self._shapes[entry_id] = None
                _LOGGER.debug("Could not learn daylight-shape shadow", exc_info=True)
        self._shape_zone = self.hass.config.time_zone
        self._scales = scales
        self._dayahead = dayahead
        self._learned_hour = hour

    def refresh(self, *, force: bool = False, now: datetime | None = None) -> None:
        """Recompute the projection if anything has changed, or if asked to."""
        if not force and not self._dirty:
            return
        self._dirty = False
        settings = self.settings
        if settings is None:
            self.state = OutlookState(reason=None)
            return
        try:
            self.state = self._compute(settings, now or dt_util.utcnow())
        except Exception:
            # The outlook is a reading, never a reason to take the integration
            # down: a bad state from somebody else's sensor must cost this
            # projection, not the forecast archive beside it.
            _LOGGER.warning("Battery outlook could not be computed", exc_info=True)

    def _compute(self, settings: BatterySettings, now: datetime) -> OutlookState:
        coordinator = self.coordinator
        buckets = coordinator.buckets
        tz = coordinator.time_zone
        local_hour = _local_hour(tz)

        load = hourly_profile(buckets, Role.LOAD.key, local_hour, until=now)
        pv = hourly_profile(buckets, Role.PV.key, _utc_hour, until=now, typical="median")
        ceiling = max_charge_w(buckets, Role.BATTERY_CHARGE.key, until=now)
        standby = standby_w(
            buckets,
            pv=Role.PV.key,
            load=Role.LOAD.key,
            charge=Role.BATTERY_CHARGE.key,
            discharge=Role.BATTERY_DISCHARGE.key,
            grid=Role.GRID_IMPORT.key,
            discharge_efficiency=settings.discharge_efficiency,
            until=now,
        )
        settings = BatterySettings(
            capacity_wh=settings.capacity_wh,
            reserve_pct=settings.reserve_pct,
            target_pct=settings.target_pct,
            max_charge_w=ceiling,
            standby_w=0.0 if standby is None else standby,
        )
        base = OutlookState(
            computed_at=now,
            load_days=load.days,
            grid_charged_hours=grid_charged_hours(
                buckets,
                pv=Role.PV.key,
                load=Role.LOAD.key,
                charge=Role.BATTERY_CHARGE.key,
                until=now,
            ),
            max_charge_w=ceiling,
            standby_w=standby,
            load_profile=load.wh,
            pv_profile=pv.wh,
            settings={
                "capacity_kwh": settings.capacity_wh / 1000.0,
                "reserve_pct": settings.reserve_pct,
                "target_pct": settings.target_pct,
            },
        )

        base.power_sources = coordinator.power_sources
        soc, age, reason = self._read_soc(settings, now)
        base.soc_age_s = age
        base.soc_pct = soc
        base.battery = settings
        if soc is None:
            base.reason = reason
            return base
        base.usable_wh = usable_energy_wh(soc, settings)
        if not load.ready:
            base.reason = "learning_consumption"
            return base

        provider, forecast, scale, factors = self._forecast(now)
        base.scale = scale
        today = _local_date(coordinator.time_zone)(now)
        base.scale_today = factors.get(today)
        base.scale_tomorrow = factors.get(today + timedelta(days=1))
        if forecast is None and not pv.ready:
            base.reason = "no_production_expectation"
            return base

        steps = build_steps(
            now,
            load=load.wh,
            local_hour=local_hour,
            pv_forecast=forecast,
            pv_profile=None if forecast is not None else pv.wh,
            anchor=self._anchor(now),
        )
        if not steps:
            base.reason = "no_horizon"
            return base

        outlook = project(soc, settings, steps, forecast_used=forecast is not None)
        base.inputs = {
            "forecast_wh": (
                {when.isoformat(): value for when, value in forecast.items()}
                if forecast is not None
                else None
            ),
            "day_factors": {day.isoformat(): value for day, value in factors.items()},
            "daylight_windows": {
                day.isoformat(): [a.isoformat(), b.isoformat()]
                for day, (a, b) in self._daylight_windows.items()
            },
            "steps": [
                {
                    "start": step.start.isoformat(),
                    "hours": step.hours,
                    "load_wh": step.load_wh,
                    "pv_wh": step.pv_wh,
                    "daylight_wh": step.daylight_wh,
                }
                for step in steps
            ],
            "battery": {name: getattr(settings, name) for name in settings.__dataclass_fields__},
        }
        base.outlook = outlook
        # Run the candidate alongside the active baseline. Nothing published or
        # learned from these outcomes is promoted before the prospective gate.
        try:
            entry_id = self._forecast_provider_id
            if entry_id is not None and forecast is not None:
                candidate, base.shape_reason = self.shape_hours(entry_id, now)
                shape = self._shapes.get(entry_id)
                base.shape_days = shape.counts if shape is not None else ()
                if candidate is not None:
                    candidate_steps = build_steps(
                        now,
                        load=load.wh,
                        local_hour=local_hour,
                        pv_forecast=candidate,
                        anchor=self._anchor(now),
                    )
                    base.shadow = project(soc, settings, candidate_steps, forecast_used=True)
                    base.shadow_full_at = settle_full(
                        self.state.shadow_full_at, base.shadow, settings, now
                    )
                    base.shadow_reserve_at = settle_reserve(
                        self.state.shadow_reserve_at, base.shadow, settings, now
                    )
                    base.inputs["shadow_forecast_wh"] = {
                        t.isoformat(): v for t, v in candidate.items()
                    }
        except Exception:
            base.shape_reason = "shape_computation_failed"
            _LOGGER.debug("Daylight-shape shadow failed", exc_info=True)
        base.reason = None
        base.provider = provider
        base.full_at = settle_full(self.state.full_at, outlook, settings, now)
        base.reserve_at = settle_reserve(self.state.reserve_at, outlook, settings, now)
        return base

    def _read_soc(
        self, settings: BatterySettings, now: datetime
    ) -> tuple[float | None, float | None, str]:
        """The charge level, its age in seconds, and why it was refused if it was."""
        entity_id = self.coordinator.entry.options.get(OPT_BATTERY_SOC)
        state: State | None = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return None, None, "soc_unavailable"
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None, None, "soc_unavailable"
        if not math.isfinite(value) or not 0.0 <= value <= 100.0:
            return None, None, "soc_unavailable"

        reported = getattr(state, "last_reported", None) or state.last_updated
        age = max((now - reported).total_seconds(), 0.0)
        if age > SOC_MAX_AGE.total_seconds():
            moved, complete = self._battery_moved_wh(reported, now)
            if moved > SOC_STALE_MOVEMENT * settings.capacity_wh or (
                not complete and age > SOC_MAX_AGE_UNCHECKED.total_seconds()
            ):
                return None, age, "soc_stale"
        return value, age, ""

    def _battery_moved_wh(self, since: datetime, now: datetime) -> tuple[float, bool]:
        """Observed movement and whether both directions have complete history.

        Missing buckets once returned zero and let a twelve-hour-old charge
        reading survive indefinitely. Partial evidence can prove movement, but
        only complete, usable hours in both directions can attest to quiet.
        """
        keys = [
            spec.key
            for spec in self.coordinator.specs
            if spec.role in (Role.BATTERY_CHARGE, Role.BATTERY_DISCHARGE)
        ]
        floor = since.replace(minute=0, second=0, microsecond=0)
        end = now.replace(minute=0, second=0, microsecond=0)
        by_hour = {b.start_utc: b for b in self.coordinator.buckets if b.seconds == 3600}
        complete = len(keys) == 2
        total = 0.0
        while floor < end:
            bucket = by_hour.get(floor)
            for key in keys:
                value = None if bucket is None else bucket.value(key)
                if value is None or not math.isfinite(value) or value < 0:
                    complete = False
                else:
                    total += value
            floor += timedelta(hours=1)
        return total, complete

    def _forecast(
        self, now: datetime
    ) -> tuple[str | None, dict[datetime, float] | None, ForecastScale | None, dict[date, float]]:
        """The first configured provider with a fresh forecast reaching past now.

        In Wh per UTC hour, scaled by what this roof has delivered against that
        provider's day-ahead forecast (see ``profile.forecast_scale``). The scale
        is learned only from hours the battery had room in, so an inverter that
        turns production down when the battery is full does not teach it that
        the sun is weaker than it is. Today and tomorrow each take the factor
        for the kind of day forecast (``day_factors``); a later day, the overall
        one. Unscaled until there is enough to learn from.
        """
        self._forecast_provider_id = None
        captured = self.coordinator.forecast_captured_at
        if not forecast_is_fresh(captured, now, FORECAST_MAX_AGE):
            return None, None, None, {}
        hour = now.replace(minute=0, second=0, microsecond=0)
        points = self.coordinator.forecast_points
        for entry_id in self.coordinator.entry.data.get(CONF_FORECAST_ENTRIES) or []:
            series = points.get(entry_id)
            if not series or max(series) < hour:
                continue
            provider = self.hass.config_entries.async_get_entry(entry_id)
            scale = self._scales.get(entry_id)
            factors = self.day_factors(entry_id, now)
            self._forecast_provider_id = entry_id
            return (
                provider.title if provider is not None else entry_id,
                self.corrected_hours(entry_id, now),
                scale,
                factors,
            )
        return None, None, None, {}

    def _anchor(self, now: datetime) -> tuple[float, float] | None:
        """This hour's measured production and consumption, in W, if trustworthy."""
        rates = self.coordinator.open_hour_rates(now, ANCHOR_MIN_SECONDS)
        produced = rates.get(Role.PV.key)
        used = rates.get(Role.LOAD.key)
        if produced is None or used is None:
            return None
        return produced, used


def _utc_hour(when: datetime) -> int:
    return when.hour


def _local_date(tz: tzinfo | None) -> Callable[[datetime], date]:
    """The local date of a UTC instant, or the UTC date when no zone resolves."""
    if tz is None:
        return lambda when: when.date()
    return lambda when: when.astimezone(tz).date()


def _local_hour(tz: tzinfo | None) -> Callable[[datetime], int]:
    """The local hour of a UTC instant, or the UTC hour when no zone resolves."""
    if tz is None:
        return _utc_hour

    def resolve(when: datetime) -> int:
        return when.astimezone(tz).hour

    return resolve
