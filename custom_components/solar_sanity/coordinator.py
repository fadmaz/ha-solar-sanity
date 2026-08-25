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
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from analysis.engine import analyse
from analysis.model import (
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
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter, PowerConverter

from .const import (
    ANALYSIS_INTERVAL,
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
    LIVE_MAX_AGE_SECONDS,
    OPT_CORRECTIONS,
    OPT_SUPPRESSED,
    STORAGE_KEY_STATE,
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
)
from .statistics_source import (
    async_get_solar_forecasts,
    async_record_forecast,
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
        self._accumulator_start: datetime | None = None
        self._loss_model: LossModel | None = None
        self._store = Store[dict[str, Any]](
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
            value = _state_as_watts(state)
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
        """Integrate current power into the open hourly bucket."""
        specs = self.specs
        if not specs:
            return

        now = dt_util.utcnow()
        hour = now.replace(minute=0, second=0, microsecond=0)

        if self._accumulator_start is None:
            self._accumulator_start = hour
        elif hour != self._accumulator_start:
            self._close_bucket(self._accumulator_start)
            self._accumulator_start = hour

        for spec in specs:
            state = self.hass.states.get(spec.entity_id)
            watts = _state_as_watts(state)
            if watts is None:
                continue
            # Rectangular integration over the sampling interval. Good enough at
            # five-minute granularity, and it never invents a value it did not see.
            self._accumulator[spec.key] = self._accumulator.get(spec.key, 0.0) + watts / 12.0

    def _close_bucket(self, start: datetime) -> None:
        specs = self.specs
        wh: dict[str, float | None] = {}
        quality: dict[str, Quality] = {}
        source: dict[str, BucketSource] = {}

        for spec in specs:
            value = self._accumulator.get(spec.key)
            wh[spec.key] = value
            quality[spec.key] = Quality.OK if value is not None else Quality.MISSING
            source[spec.key] = BucketSource.OWN_INTEGRAL

        self._buckets.append(
            Bucket(
                start_utc=start,
                seconds=3600,
                wh=wh,
                quality=quality,
                source=source,
            )
        )
        if len(self._buckets) > MAX_BUCKETS:
            del self._buckets[: len(self._buckets) - MAX_BUCKETS]
        self._accumulator.clear()

    def ingest_backfill(self, series: dict[str, list[tuple[datetime, float]]]) -> None:
        """Seed the window from long-term statistics at setup.

        Marked as ``LTS_SUM`` so a finding that rests on it can be graded
        accordingly — a backfilled bucket is good evidence, but it is not our own
        measurement.
        """
        specs = self.specs
        by_key = {s.key: s.entity_id for s in specs}
        merged: dict[datetime, dict[str, float]] = {}

        for key, entity_id in by_key.items():
            for when, value in series.get(entity_id, []):
                merged.setdefault(when, {})[key] = value

        for when in sorted(merged):
            values = merged[when]
            wh: dict[str, float | None] = {}
            quality: dict[str, Quality] = {}
            source: dict[str, BucketSource] = {}
            for spec in specs:
                value = values.get(spec.key)
                wh[spec.key] = value
                quality[spec.key] = Quality.OK if value is not None else Quality.MISSING
                source[spec.key] = BucketSource.LTS_SUM
            self._buckets.append(
                Bucket(start_utc=when, seconds=3600, wh=wh, quality=quality, source=source)
            )

        self._buckets.sort(key=lambda b: b.start_utc)

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
        for entry_id, payload in forecasts.items():
            provider = self.hass.config_entries.async_get_entry(entry_id)
            if provider is None:
                continue
            wh_hours = payload.get("wh_hours") if isinstance(payload, dict) else None
            if not isinstance(wh_hours, dict):
                continue
            if await async_record_forecast(
                self.hass, entry_id, provider.title or provider.domain, wh_hours
            ):
                written += 1
        return written

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
        )

        report = await self.hass.async_add_executor_job(analyse, request)

        if report.loss_model is not None and report.loss_model.fitted:
            self._loss_model = report.loss_model

        await self._async_persist(report)
        return report

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
        """Reload the fitted loss model so a restart does not reset learning."""
        stored = await self._store.async_load()
        if not stored:
            return
        self._loss_model = _loss_from_dict(stored.get("loss_model"))

    # -- reporting ----------------------------------------------------------

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


def _state_as_watts(state: State | None) -> float | None:
    """Convert a power state to watts, or return ``None``.

    An unrecognised unit returns ``None`` rather than the raw number. Silently
    passing a value through unconverted is precisely how the predecessor turned
    a kilowatt-hour forecast into "1.2 watts" and broke its own thresholds.
    """
    if state is None:
        return None

    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        # float() accepts 'nan' and 'inf'. Neither may reach arithmetic.
        return None

    unit = _unit_of(state)
    if not unit:
        return None

    try:
        return PowerConverter.convert(value, unit, "W")
    except (ValueError, TypeError, KeyError):
        pass
    try:
        # Some inverters expose an energy-per-hour sensor; treat Wh/h as W.
        return EnergyConverter.convert(value, unit, "Wh")
    except (ValueError, TypeError, KeyError):
        return None


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
