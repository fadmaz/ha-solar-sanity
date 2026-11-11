"""Private, bounded snapshots of what the integration actually published."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .analysis.calibration import interval_view, issue_interval
from .analysis.daylight_shape import SHAPE_REVISION
from .analysis.experiment import shape_gate
from .analysis.grading import grade, summarize
from .analysis.load_schedule import LOAD_REVISION, WEEKDAYS
from .analysis.observations import observation_hours
from .analysis.solar_coverage import coverage_day, observe_coverage
from .battery_controls import manual_controls
from .const import (
    CONF_CHANNELS,
    CONF_ENTITY_ID,
    CONF_FORECAST_ENTRIES,
    CONF_ROLE,
    DOMAIN,
    OPT_BATTERY_CAPACITY_KWH,
    OPT_BATTERY_RESERVE_PCT,
    OPT_BATTERY_SOC,
    OPT_BATTERY_TARGET_PCT,
)
from .load_trial import LoadTrial, valid_control
from .statistics_source import async_forecast_series, async_observed_full_at, async_read_grading_soc

if TYPE_CHECKING:
    from .coordinator import SolarSanityCoordinator

_LOGGER = logging.getLogger(__name__)
JOURNAL_KEY = f"{DOMAIN}.outlook_journal"
JOURNAL_VERSION = 1
MODEL_REVISION = "baseline-0.34.2"
COLLECTION_REASONS = {
    "journal_restore_failed": "The journal could not be read; its file was left untouched.",
    "journal_write_failed": "The issued prediction could not be saved.",
    "journal_update_failed": "The journal update failed; the battery outlook is still running.",
    "channel_unreadable": "A configured measurement channel is unreadable or unsupported.",
    "waiting_for_capture": "Waiting for the first valid forecast capture.",
    "waiting_for_checkpoint": "Waiting for the first scheduled prediction.",
    "forecast_stale": "The forecast has not been captured within three hours.",
    "capture_unavailable": "The latest forecast capture did not return usable data.",
    "archive_not_submitted": "The recorder did not accept an archive batch for submission.",
    "archive_pending": "The archive batch is waiting for read-back verification.",
    "archive_unverified": "The latest archive batch could not be verified by reading it back.",
}
# Two issued snapshots per day for three months, as agreed for local grading.
RETENTION_DAYS = 90
MAX_RECORDS = 180
CHECKPOINTS = ((8, "morning"), (20, "evening"))


class OutlookJournal:
    """An optional side feature: a journal failure cannot stop the outlook."""

    def __init__(self, coordinator: SolarSanityCoordinator) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self.records: list[dict[str, Any]] = []
        self.health: dict[str, dict[str, Any]] = {}
        self.error: str | None = None
        self.writable = True
        self.started_at = dt_util.utcnow()
        self.created_at = self.started_at
        self.last_checked_at = self.started_at
        self._task: asyncio.Task | None = None
        self._next_grade: datetime | None = None
        self._verification: dict[str, asyncio.Task] = {}
        self._startup_verification: dict[str, Callable[[], None]] = {}
        self._store = Store(
            self.hass, JOURNAL_VERSION, f"{JOURNAL_KEY}.{coordinator.entry.entry_id}"
        )
        self.version = "unknown"
        self.load_trial = LoadTrial(self)

    def context(self) -> str:
        entry = self.coordinator.entry
        identity = {
            "channels": sorted(
                [
                    {"role": c.get(CONF_ROLE), "entity_id": c.get(CONF_ENTITY_ID)}
                    for c in entry.data.get(CONF_CHANNELS, [])
                ],
                key=lambda c: (str(c["role"]), str(c["entity_id"])),
            ),
            "providers": entry.data.get(CONF_FORECAST_ENTRIES, []),
            "zone": self.hass.config.time_zone,
            "revision": MODEL_REVISION,
            "settings": {
                **{
                    key: entry.options.get(key)
                    for key in (
                        OPT_BATTERY_SOC,
                        OPT_BATTERY_CAPACITY_KWH,
                        OPT_BATTERY_RESERVE_PCT,
                        OPT_BATTERY_TARGET_PCT,
                    )
                },
                **manual_controls(entry.options),
            },
        }
        preferred = self.coordinator.preferred_forecast_provider
        if preferred is not None:
            identity["preferred_forecast_provider"] = preferred
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    async def async_restore(self) -> None:
        try:
            from homeassistant.loader import async_get_integration

            integration = await async_get_integration(self.hass, DOMAIN)
            self.version = str(integration.version)
            saved = await self._store.async_load()
            if saved is not None:
                records = saved["records"]
                if not isinstance(records, list) or not all(_valid_record(r) for r in records):
                    raise ValueError("invalid journal records")
                if len({r["id"] for r in records}) != len(records):
                    raise ValueError("duplicate journal records")
                health = saved.get("health", {})
                if not isinstance(health, dict) or not all(
                    _valid_health(v) for v in health.values()
                ):
                    raise ValueError("invalid capture health")
                created = datetime.fromisoformat(saved["created_at"])
                checked = datetime.fromisoformat(saved["last_checked_at"])
                if created.tzinfo is None or checked.tzinfo is None:
                    raise ValueError("journal timestamps need a time zone")
                control = saved.get("load_trial")
                if control is not None and not valid_control(control):
                    raise ValueError("unknown or invalid load experiment metadata")
                self.load_trial.control = control or {}
                self.records, self.health = records, health
                self.created_at, self.last_checked_at = created, checked
            if self.load_trial.sync(dt_util.utcnow()):
                await self.async_save()
        except Exception:
            # Preserve an unknown envelope or a damaged file for recovery.
            self.writable = False
            self.error = "journal_restore_failed"
            _LOGGER.warning(
                "Could not read outlook journal; leaving its file untouched", exc_info=True
            )

    async def async_save(self) -> None:
        if not self.writable:
            return
        try:
            payload = {
                "records": self.records,
                "health": self.health,
                "created_at": self.created_at.isoformat(),
                "last_checked_at": self.last_checked_at.isoformat(),
            }
            if self.load_trial.control:
                payload["load_trial"] = self.load_trial.control
            await self._store.async_save(json.loads(json.dumps(payload)))
            if self.error == "journal_write_failed":
                self.error = None
        except Exception:
            self.error = "journal_write_failed"
            _LOGGER.warning("Could not save outlook journal", exc_info=True)

    @callback
    def schedule(self) -> None:
        if self._task is None or self._task.done():
            self._task = self.hass.async_create_task(self.async_update())

    @callback
    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
        for task in self._verification.values():
            task.cancel()
        for unsubscribe in self._startup_verification.values():
            unsubscribe()
        self._startup_verification.clear()

    async def captured(
        self,
        provider: str,
        points: dict[datetime, float],
        submitted: dict[str, list[tuple[datetime, float]]],
        *,
        captured_at: datetime | None = None,
    ) -> None:
        now = dt_util.utcnow().isoformat()
        health = self.health.setdefault(provider, {})
        health.update(captured_at=_iso(captured_at) or now, reason_code=None)
        previous = self._verification.get(provider)
        if previous is not None:
            previous.cancel()
        unsubscribe = self._startup_verification.pop(provider, None)
        if unsubscribe is not None:
            unsubscribe()
        if submitted:
            health.update(submitted_at=now, verification="pending")
            self._queue_verification(provider, submitted)
        elif self.coordinator._owns_archive(provider):
            health.update(reason_code="archive_not_submitted", verification="unverified")
        else:
            # A shared provider has one writer. This entry can verify content
            # without pretending that it submitted another entry's batch.
            from .statistics_source import forecast_statistic_id

            health["verification"] = "pending"
            self._queue_verification(
                provider, {forecast_statistic_id(provider): list(points.items())}
            )
        await self.async_save()

    @callback
    def _queue_verification(self, provider, expected):
        """Startup queues can wait for Core; do not exhaust retries before it starts."""

        @callback
        def _start(_event=None):
            self._startup_verification.pop(provider, None)
            self._verification[provider] = self.hass.async_create_background_task(
                self._verify_archive(provider, expected), f"{DOMAIN} archive read-back"
            )

        # is_running includes CoreState.starting, before the started event.
        if self.hass.state is CoreState.running:
            _start()
        else:
            # On the real installation, unrelated startup work lasted longer
            # than all three read-back retries; the queued batch was not bad.
            self._startup_verification[provider] = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, _start
            )

    async def _verify_archive(
        self,
        provider: str,
        expected: dict[str, list[tuple[datetime, float]]],
        delays: tuple[float, ...] = (5, 25, 90),
    ) -> None:
        # Attempts at 5, 30 and 120 seconds let the recorder commit its queue.
        # Matching content proves archive availability, not a physical write time.
        for delay in delays:
            await asyncio.sleep(delay)
            verified = True
            for statistic_id, rows in expected.items():
                series = await async_forecast_series(
                    self.hass,
                    statistic_id,
                    min(t for t, _ in rows),
                    max(t for t, _ in rows) + timedelta(hours=1),
                )
                if series is None or any(
                    t not in series or abs(series[t] - value) > 1e-7 for t, value in rows
                ):
                    verified = False
            if verified:
                self.health[provider].update(
                    verified_at=dt_util.utcnow().isoformat(),
                    verification="verified",
                    reason_code=None,
                )
                await self.async_save()
                self.coordinator.notify_live_entities()
                return
        self.health[provider].update(verification="unverified", reason_code="archive_unverified")
        await self.async_save()
        self.coordinator.notify_live_entities()

    async def async_update(self, now: datetime | None = None) -> None:
        now = now or dt_util.utcnow()
        if not self.coordinator.battery.configured or not self.writable:
            return
        try:
            changed = self.load_trial.sync(now)
            changed = self.capture(now) or changed
            self.last_checked_at = now
            if self._next_grade is None or now >= self._next_grade:
                self._next_grade = now + timedelta(minutes=5)
                changed = await self._grade_pending(now) or changed
                changed = await self._update_solar_coverage(now) or changed
            cutoff = now - timedelta(days=RETENTION_DAYS)
            retained = [r for r in self.records if datetime.fromisoformat(r["issued_at"]) >= cutoff]
            retained = retained[-MAX_RECORDS:]
            changed = changed or len(retained) != len(self.records)
            self.records = retained
            if changed:
                if self.error == "journal_update_failed":
                    self.error = None
                await self.async_save()
                self.coordinator.notify_live_entities()
        except Exception:
            self.error = "journal_update_failed"
            _LOGGER.warning("Could not update outlook journal", exc_info=True)

    def capture(self, now: datetime) -> bool:
        zone = self.coordinator.time_zone
        if zone is None:
            return False
        day = (
            max(
                self.created_at,
                self.last_checked_at - timedelta(minutes=5),
                now - timedelta(days=RETENTION_DAYS),
            )
            .astimezone(zone)
            .date()
        )
        today = now.astimezone(zone).date()
        changed = False
        while day <= today:
            for hour, kind in CHECKPOINTS:
                scheduled = datetime.combine(day, time(hour), tzinfo=zone).astimezone(UTC)
                deadline = scheduled + timedelta(minutes=5)
                key = f"{day.isoformat()}:{kind}"
                if (
                    now < scheduled
                    or deadline <= self.created_at
                    or any(r["id"] == key for r in self.records)
                ):
                    continue
                state = self.coordinator.battery.state
                issued = state.computed_at
                fresh = (
                    now < deadline
                    and state.outlook is not None
                    and issued is not None
                    and scheduled <= issued < deadline
                    and issued <= now
                )
                if now < deadline and not fresh:
                    continue
                if fresh:
                    self.records.append(self._snapshot(key, kind, now))
                else:
                    reason = (
                        "offline_checkpoint" if scheduled < self.started_at else "missed_checkpoint"
                    )
                    self.records.append(
                        {
                            "id": key,
                            "checkpoint": kind,
                            "issued_at": scheduled.isoformat(),
                            "context": self.context(),
                            "result": {"status": "missed", "reason_code": reason},
                        }
                    )
                changed = True
            day += timedelta(days=1)
        self.records.sort(key=lambda r: r["issued_at"])
        return changed

    def _snapshot(self, key: str, kind: str, now: datetime) -> dict[str, Any]:
        state = self.coordinator.battery.state
        outlook = state.outlook
        assert outlook is not None and state.computed_at is not None
        zone = self.coordinator.time_zone
        local = state.computed_at.astimezone(zone)
        end = (
            outlook.takeover_at
            if kind == "evening"
            else datetime.combine(local.date(), time(22), tzinfo=zone).astimezone(UTC)
        )
        reason = "no_night" if end is None else None
        if kind == "morning" and not outlook.forecast_used:
            reason = "no_forecast"
        source = state.forecast_source
        record = {
            "id": key,
            "checkpoint": kind,
            "issued_at": state.computed_at.isoformat(),
            "recorded_at": now.isoformat(),
            "until": end.isoformat() if end else None,
            "time_zone": self.hass.config.time_zone,
            "version": self.version,
            "model_revision": MODEL_REVISION,
            "context": self.context(),
            "soc_entity": self.coordinator.entry.options.get(OPT_BATTERY_SOC),
            "soc_pct": state.soc_pct,
            "settings": dict(state.settings),
            "provider": state.provider,
            "forecast_provider_id": source.provider_id if source is not None else None,
            "forecast_captured_at": _iso(source.captured_at) if source is not None else None,
            "published": {
                "lowest_pct": round(outlook.lowest_pct, 1)
                if outlook.lowest_pct is not None
                else None,
                "reserve_at": _iso(state.reserve_at),
                "full_at": _iso(state.full_at),
            },
            "projection": _jsonable(asdict(outlook)),
            "inputs": json.loads(json.dumps(state.inputs)),
            "result": {"status": "excluded", "reason_code": reason}
            if reason
            else {"status": "pending"},
        }

        shadow = state.shadow
        if shadow is not None:
            candidate = json.loads(json.dumps(record))
            candidate["model_revision"] = SHAPE_REVISION
            candidate["projection"] = _jsonable(asdict(shadow))
            candidate["published"] = {
                "lowest_pct": round(shadow.lowest_pct, 1)
                if shadow.lowest_pct is not None
                else None,
                "reserve_at": _iso(state.shadow_reserve_at),
                "full_at": _iso(state.shadow_full_at),
            }
            if kind == "evening":
                candidate["until"] = _iso(shadow.takeover_at)
                if shadow.takeover_at is None:
                    candidate["result"] = {"status": "excluded", "reason_code": "no_night"}
            record["shadow"] = candidate
        record["shape_reason"] = state.shape_reason
        record["shape_days"] = list(state.shape_days)
        record["load_reason"] = state.load_reason
        if (
            state.load_shadow is not None
            and state.load_shadow_context == self.load_trial.context()
            and self.load_trial.blocked(now) is None
        ):
            candidate = json.loads(
                json.dumps(
                    {
                        k: v
                        for k, v in record.items()
                        if k not in ("shadow", "load_shadow", "interval")
                    }
                )
            )
            candidate.update(
                model_revision=LOAD_REVISION,
                baseline_revision=MODEL_REVISION,
                context=state.load_shadow_context,
                load_group=int(WEEKDAYS[local.weekday()] in self.load_trial.days),
            )
            candidate["projection"] = _jsonable(asdict(state.load_shadow))
            candidate["published"] = {
                "lowest_pct": round(state.load_shadow.lowest_pct, 1)
                if state.load_shadow.lowest_pct is not None
                else None,
                "reserve_at": _iso(state.load_shadow_reserve_at),
                "full_at": _iso(state.load_shadow_full_at),
            }
            candidate["inputs"]["steps"] = candidate["inputs"].pop("load_shadow_steps")
            if kind == "evening":
                candidate["until"] = _iso(state.load_shadow.takeover_at)
                if state.load_shadow.takeover_at is None:
                    candidate["result"] = {"status": "excluded", "reason_code": "no_night"}
            record["load_shadow"] = candidate
        if kind == "evening":
            group = self._forecast_group(record, record["context"])
            candidates = self._records_for_forecast(group, record["context"]) if group else []
            record["interval"] = issue_interval(candidates, record)
        return record

    async def _grade_pending(self, now: datetime) -> bool:
        changed = False
        for record, is_load in (
            (item, label == "load_shadow")
            for base in self.records
            for label in (None, "shadow", "load_shadow")
            if (item := base if label is None else base.get(label))
        ):
            if record["result"]["status"] != "pending":
                continue
            start = datetime.fromisoformat(record["issued_at"]).astimezone(UTC)
            end = datetime.fromisoformat(record["until"]).astimezone(UTC)
            # A partial last hour still needs a closed flow bucket and time for
            # Recorder to settle, just as an hour-aligned observation does.
            if now < observation_hours(start, end)[-1] + timedelta(hours=1, minutes=15):
                continue
            context = self.load_trial.context() if is_load else self.context()
            if record["context"] != context or (
                is_load and self.load_trial.blocked(now) is not None
            ):
                record["result"] = {"status": "excluded", "reason_code": "context_changed"}
            else:
                soc = await async_read_grading_soc(self.hass, record["soc_entity"], start, end)
                if soc is None:
                    if now < end + timedelta(days=1):
                        continue
                    result = {"status": "excluded", "reason_code": "history_read_failed"}
                else:
                    observed = None
                    if record["checkpoint"] == "morning":
                        observed = await async_observed_full_at(
                            self.hass,
                            record["soc_entity"],
                            start,
                            end,
                            record["settings"]["target_pct"] - 1.0,
                        )
                    result = grade(
                        record,
                        soc,
                        self.coordinator.buckets,
                        full_observed_at=observed,
                    )
                    if result.get("reason_code") in (
                        "soc_incomplete",
                        "flow_incomplete",
                    ) and now < end + timedelta(days=1):
                        continue
                record["result"] = result
            record["graded_at"] = now.isoformat()
            changed = True
        return changed

    async def _update_solar_coverage(self, now: datetime) -> bool:
        zone = self.coordinator.time_zone
        if zone is None:
            return False
        context = self.context()
        candidates = []
        for record in reversed(self.records):
            if record["checkpoint"] != "evening" or not record.get("until"):
                continue
            prior = record.get("solar_coverage")
            if (
                isinstance(prior, dict)
                and prior.get("status") in ("observed", "not_observed", "unavailable")
                and prior.get("late_soc_status") != "pending"
            ):
                continue
            inputs = record.get("inputs")
            battery = inputs.get("battery") if isinstance(inputs, dict) else None
            if not isinstance(battery, dict):
                continue
            standby = battery.get("standby_w")
            if not isinstance(standby, (float, int)) or not math.isfinite(standby) or standby < 0:
                continue
            try:
                issued = datetime.fromisoformat(record["issued_at"])
                expected = datetime.fromisoformat(record["until"])
            except (ValueError, TypeError):
                continue
            if expected.tzinfo is None or expected <= issued:
                continue
            start, end = coverage_day(issued, zone)
            legacy = record.get("version") != self.version or now >= end + timedelta(days=1)
            candidates.append((legacy, record, prior, start, end, expected, standby))
        if not candidates:
            return False

        buckets = {b.start_utc: b for b in self.coordinator.buckets}
        changed = False
        backfilled = 0
        for legacy, record, prior, start, end, expected, standby in sorted(
            candidates, key=lambda item: item[0]
        ):
            if legacy:
                if backfilled == 2:
                    break
                backfilled += 1
            settled_hour = isinstance(prior, dict) and prior.get("status") == "observed"
            same_setup = (
                record["context"] == context
                and record.get("time_zone") == self.hass.config.time_zone
                and record["result"].get("reason_code") != "context_changed"
            )
            if settled_hour:
                result = dict(prior)
            elif same_setup:
                result = observe_coverage(buckets, start, end, expected, standby_w=standby, now=now)
            else:
                result = {"status": "unavailable", "reason_code": "context_changed"}

            if result.get("late_soc_status") == "pending":
                observed = datetime.fromisoformat(result["observed_start"])
                if same_setup and self.context() == context:
                    await self._read_late_minimum(
                        record, result, expected, observed, day_end=end, now=now
                    )
                else:
                    result.update(
                        late_soc_status="unavailable", late_soc_reason_code="context_changed"
                    )
            if result != prior:
                record["solar_coverage"] = result
                changed = True
        return changed

    async def _read_late_minimum(
        self,
        record: dict[str, Any],
        result: dict[str, Any],
        expected: datetime,
        observed: datetime,
        *,
        day_end: datetime,
        now: datetime,
    ) -> None:
        context = self.context()
        soc = await async_read_grading_soc(self.hass, record["soc_entity"], expected, observed)
        if self.context() != context:
            result.update(late_soc_status="unavailable", late_soc_reason_code="context_changed")
            return
        reading = soc.lowest_reading(expected, observed) if soc is not None else None
        if reading is not None and 0 <= reading[0] <= 100:
            result.update(late_soc_status="available", late_lowest_pct=reading[0])
            result.pop("late_soc_reason_code", None)
        else:
            result["late_soc_reason_code"] = (
                "history_read_failed" if soc is None else "soc_incomplete"
            )
            if now >= day_end + timedelta(days=1):
                result["late_soc_status"] = "unavailable"

    def _forecast_group(self, record: dict, context: str) -> str | None:
        forecast_used = (record.get("projection") or {}).get("forecast_used")
        if forecast_used is False:
            return "history"
        provider = record.get("forecast_provider_id")
        if isinstance(provider, str) and provider:
            return f"provider:{provider}"
        # The original context included the ordered provider list. Only the
        # single-provider case proves which provider made a legacy issue.
        providers = self.coordinator.entry.data.get(CONF_FORECAST_ENTRIES) or ()
        if forecast_used is True and record.get("context") == context and len(providers) == 1:
            return f"provider:{providers[0]}"
        return None

    def _records_for_forecast(
        self, group: str | None, context: str, *, include_missed: bool = False
    ) -> list[dict]:
        return [
            row
            for row in self.records
            if row.get("context") == context
            and (
                (include_missed and row["result"]["status"] == "missed")
                or self._forecast_group(row, context) == group
            )
        ]

    def active_forecast_records(self, *, include_missed: bool = False) -> list[dict]:
        """Use the published source's samples, or the latest issue during startup."""
        context = self.context()
        state = self.coordinator.battery.state
        if state.outlook is not None:
            group = self._forecast_group(
                {
                    "context": context,
                    "projection": {"forecast_used": state.outlook.forecast_used},
                    "forecast_provider_id": state.forecast_source.provider_id
                    if state.forecast_source is not None
                    else None,
                },
                context,
            )
        else:
            latest = next(
                (
                    row
                    for row in reversed(self.records)
                    if row.get("context") == context and row.get("projection")
                ),
                {},
            )
            group = self._forecast_group(latest, context)
        return self._records_for_forecast(group, context, include_missed=include_missed)

    def summary(self, days: int = 28, now: datetime | None = None) -> dict[str, Any]:
        now = now or dt_util.utcnow()
        zone = self.coordinator.time_zone or UTC
        today = now.astimezone(zone).date()
        since = datetime.combine(today - timedelta(days=days - 1), time(), tzinfo=zone)
        return summarize(self.active_forecast_records(include_missed=True), self.context(), since)

    def interval(self, now: datetime | None = None) -> dict[str, Any]:
        selected = [
            r
            for r in self.records
            if r.get("context") == self.context()
            and r.get("model_revision") == MODEL_REVISION
            and r.get("checkpoint") == "evening"
        ]
        latest = max(selected, key=lambda r: r["issued_at"]) if selected else None
        return interval_view(latest, now or dt_util.utcnow())

    def experiment(self) -> dict[str, Any]:
        return shape_gate(self.active_forecast_records(), self.context())

    def status(self) -> tuple[str, str | None]:
        if self.error:
            return "error", self.error
        channels = self.coordinator.coverage_snapshot()["channels"]
        if any(not c["readable_now"] for c in channels):
            return "degraded", "channel_unreadable"
        now = dt_util.utcnow()
        providers = self.coordinator.entry.data.get(CONF_FORECAST_ENTRIES, [])
        for provider in providers:
            health = self.health.get(provider, {})
            capture = health.get("captured_at")
            if not capture:
                return "starting", "waiting_for_capture"
            if now - datetime.fromisoformat(capture) > timedelta(hours=3):
                return "degraded", "forecast_stale"
            if health.get("reason_code"):
                return "degraded", health["reason_code"]
            if health.get("verification") == "pending" and not health.get("verified_at"):
                return "starting", "archive_pending"
        return ("ok", None) if self.records or providers else ("starting", "waiting_for_checkpoint")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _valid_record(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    try:
        for key in ("shadow", "load_shadow"):
            if key in record and (
                not isinstance(record[key], dict)
                or any(k in record[key] for k in ("shadow", "load_shadow"))
                or not _valid_record(record[key])
            ):
                return False
        stamp = datetime.fromisoformat(record["issued_at"])
        valid = (
            stamp.tzinfo is not None
            and isinstance(record["id"], str)
            and isinstance(record["context"], str)
            and record["checkpoint"] in ("morning", "evening")
            and record["result"]["status"] in ("pending", "graded", "excluded", "missed")
            and (record["result"]["status"] != "pending" or bool(record["until"]))
        )
        if not valid:
            return False
        interval = record.get("interval")
        if interval is not None:
            if not isinstance(interval, dict):
                return False
            low, high = interval.get("lower"), interval.get("upper")
            if (low is not None or high is not None) and (
                low is None or high is None or not (0 <= low <= high <= 100)
            ):
                return False
            if interval.get("publishable") and (low is None or high is None):
                return False
        status = record["result"]["status"]
        if status in ("pending", "graded"):
            end = datetime.fromisoformat(record["until"])
            if end.tzinfo is None or end <= stamp:
                return False
            if not isinstance(record["published"], dict) or not isinstance(
                record["settings"], dict
            ):
                return False
        if status == "graded":
            result = record["result"]
            if record["checkpoint"] == "evening":
                return (
                    math.isfinite(result["error"])
                    and 0 <= result["actual_lowest"] <= 100
                    and isinstance(result["reserve_correct"], bool)
                )
            return all(
                isinstance(result[k], bool)
                for k in ("fill_correct", "false_fill", "missed_fill", "actual_filled")
            ) and (
                result.get("time_error_minutes") is None
                or math.isfinite(result["time_error_minutes"])
            )
        return True
    except (KeyError, TypeError, ValueError):
        return False


def _valid_health(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        return all(
            datetime.fromisoformat(value[key]).tzinfo is not None
            for key in ("attempted_at", "captured_at", "submitted_at", "verified_at")
            if value.get(key) is not None
        )
    except (TypeError, ValueError):
        return False
