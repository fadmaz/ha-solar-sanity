"""Persistent, installation-scoped notifications for manually running a load."""

from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, STATE_UNAVAILABLE
from homeassistant.core import Context, CoreState, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .analysis.scenarios import LoadPlan, LoadTiming, find_best_time
from .battery_outlook import OutlookState, frozen_inputs
from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import SolarSanityCoordinator

_LOGGER = logging.getLogger(__name__)
_ACTIVE = {"scheduled", "waiting"}
_STATUSES = _ACTIVE | {
    "cancelled",
    "expired",
    "dispatching",
    "sent",
    "delivery_unconfirmed",
    "failed",
}
_TIMES = ("earliest_start", "finish_by", "scheduled_at")


def reminder_store(hass: HomeAssistant, entry_id: str) -> Store[dict]:
    return Store(hass, 1, f"{DOMAIN}.load_reminder.{entry_id}", private=True, atomic_writes=True)


@dataclass(frozen=True, slots=True)
class Reminder:
    id: str
    user_id: str
    destination: str
    power_w: float
    duration_minutes: int
    earliest_start: datetime
    finish_by: datetime
    scheduled_at: datetime | None = None
    status: str = "scheduled"
    reason_code: str | None = None

    @property
    def latest_start(self) -> datetime:
        return self.finish_by - timedelta(minutes=self.duration_minutes)

    def dump(self) -> dict:
        result = asdict(self)
        for key in _TIMES:
            if result[key] is not None:
                result[key] = result[key].isoformat()
        return result

    @classmethod
    def load(cls, saved: dict) -> Reminder:
        data = dict(saved)
        for key in _TIMES:
            if data[key] is not None:
                value = datetime.fromisoformat(data[key])
                if value.utcoffset() is None:
                    raise ValueError("Reminder times must include a UTC offset.")
                data[key] = value.astimezone(UTC)
        record = cls(**data)
        if (
            record.status not in _STATUSES
            or not isinstance(record.id, str)
            or not isinstance(record.user_id, str)
            or _notify_call(record.destination) is None
            or type(record.duration_minutes) is not int
            or not 1 <= record.duration_minutes <= 1440
            or not math.isfinite(record.power_w)
            or record.power_w < 0
            or record.latest_start < record.earliest_start
        ):
            raise ValueError("Invalid load reminder.")
        if record.status == "dispatching":
            return replace(record, status="delivery_unconfirmed")
        return record


def _notify_call(destination: str) -> tuple[str, dict[str, str]] | None:
    """Resolve the selected notification route within the notify domain."""
    if re.fullmatch(r"notify\.[a-z0-9_]+", destination):
        return "send_message", {"entity_id": destination}
    if re.fullmatch(r"mobile_app_[a-z0-9_]+", destination):
        return destination, {}
    return None


class LoadReminders:
    """Own one reminder and serialize its scheduling, cancellation and delivery."""

    def __init__(self, coordinator: SolarSanityCoordinator) -> None:
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self._store = reminder_store(self.hass, coordinator.entry.entry_id)
        self._lock = asyncio.Lock()
        self._record: Reminder | None = None
        self._storage_error = False
        self._started = False
        self._checked_projection: str | None = None
        self._task: asyncio.Task | None = None
        self._timer = None
        self._startup = None

    async def async_restore(self) -> None:
        try:
            if (saved := await self._store.async_load()) is not None:
                self._record = Reminder.load(saved)
        except (ValueError, TypeError, KeyError, OSError, OverflowError, NotImplementedError):
            self._storage_error = True
            _LOGGER.exception("Could not restore the load reminder")

    @callback
    def async_start(self) -> None:
        self._started = True
        if self.hass.state is CoreState.running:
            self.async_wake(force=True)
        else:
            self._startup = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, self._on_started
            )

    @callback
    def _on_started(self, _event) -> None:
        self._startup = None
        self.async_wake(force=True)

    @callback
    def async_stop(self) -> None:
        self._started = False
        self._clear_timer()
        if self._startup is not None:
            self._startup()
            self._startup = None

    async def async_quiesce(self) -> None:
        """Wait for a write already in flight before the entry can reload."""
        self.async_stop()
        async with self._lock:
            pass

    def view(self) -> dict:
        if self._storage_error:
            return {"reminder": None, "storage_error": True}
        record = self._record
        if record is None:
            return {"reminder": None, "storage_error": False}
        result = record.dump()
        del result["user_id"]
        result["latest_start"] = record.latest_start.isoformat()
        return {"reminder": result, "storage_error": False}

    def _projection(self, now: datetime) -> OutlookState | None:
        battery = self.coordinator.battery
        state = battery.state
        if (
            battery.projection_problem(now)
            or state.outlook is None
            or not state.outlook.forecast_used
            or state.scale is None
        ):
            return None
        if state.forecast_source is not None:
            current = self.coordinator.select_forecast("battery", now)
            if (current.provider_id, current.captured_at) != (
                state.forecast_source.provider_id,
                state.forecast_source.captured_at,
            ):
                return None
        return state

    @staticmethod
    def _plan(state: OutlookState, record: Reminder, now: datetime) -> LoadPlan:
        settings, steps = frozen_inputs(state)
        assert state.soc_pct is not None
        return find_best_time(
            state.soc_pct,
            settings,
            steps,
            power_w=record.power_w,
            duration_minutes=record.duration_minutes,
            earliest_start=record.earliest_start,
            finish_by=record.finish_by,
            now=now,
        )

    def _destination(self, destination: str) -> tuple[str, dict[str, str]] | None:
        call = _notify_call(destination)
        if call is None or not self.hass.services.has_service("notify", call[0]):
            return None
        entity = call[1].get("entity_id")
        if entity and (
            (state := self.hass.states.get(entity)) is None or state.state == STATE_UNAVAILABLE
        ):
            return None
        return call

    def _require_running(self) -> None:
        if not self._started or self.hass.state is not CoreState.running:
            raise ServiceValidationError("Wait for Home Assistant to finish starting.")
        if self._storage_error:
            raise ServiceValidationError("The reminder store is unavailable.")

    async def _commit(self, record: Reminder) -> None:
        if self.hass.state is not CoreState.running or self._storage_error:
            raise ServiceValidationError("The reminder store is unavailable.")
        payload = record.dump()
        try:
            await self._store.async_save(payload)
            # Store can log a failed write without raising. Read back the record
            # before acknowledging a change or attempting the external action.
            if (
                self.hass.state is not CoreState.running
                or await self._store.async_load() != payload
            ):
                raise OSError("The reminder write could not be verified.")
        except Exception as error:
            self._storage_error = True
            _LOGGER.exception("Could not save the load reminder")
            raise ServiceValidationError("Could not save the load reminder.") from error
        self._record = record

    async def async_schedule(self, user_id: str, data: dict) -> dict:
        async with self._lock:
            self._require_running()
            if self._record is not None and self._record.status in _ACTIVE:
                raise ServiceValidationError(
                    "Cancel the active reminder before scheduling another."
                )
            now = dt_util.utcnow()
            state = self._projection(now)
            if state is None:
                raise ServiceValidationError("Wait for a current corrected solar forecast.")
            if state.projection_id != data["projection_id"]:
                raise ServiceValidationError("The outlook changed. Find the best time again.")
            if self._destination(data["destination"]) is None:
                raise ServiceValidationError(
                    "Choose an available Home Assistant notification target."
                )
            record = Reminder(
                uuid4().hex,
                user_id,
                data["destination"],
                data["power_w"],
                data["duration_minutes"],
                data.get("earliest_start", now).astimezone(UTC),
                data["finish_by"].astimezone(UTC),
            )
            try:
                plan = self._plan(state, record, now)
            except (ValueError, KeyError, TypeError, OverflowError) as error:
                raise ServiceValidationError("Could not plan this load.") from error
            if plan.recommendation is None:
                raise ServiceValidationError(
                    "There is no suitable start. Find the best time again."
                )
            await self._commit(replace(record, scheduled_at=plan.recommendation.start))
            self._schedule_timer()
            return self.view()

    async def async_cancel(self, reminder_id: str) -> dict:
        async with self._lock:
            self._require_running()
            record = self._record
            if record is None or record.id != reminder_id or record.status not in _ACTIVE:
                raise ServiceValidationError("This reminder is no longer active.")
            await self._commit(replace(record, status="cancelled", scheduled_at=None))
            self._clear_timer()
            return self.view()

    @callback
    def _clear_timer(self) -> None:
        if self._timer is not None:
            self._timer()
            self._timer = None

    @callback
    def _schedule_timer(self) -> None:
        self._clear_timer()
        record = self._record
        if self._started and record is not None and record.status in _ACTIVE:
            when = record.scheduled_at or record.latest_start + timedelta(microseconds=1)
            self._timer = async_track_point_in_utc_time(self.hass, self._on_timer, when)

    @callback
    def _on_timer(self, _now) -> None:
        self._timer = None
        self.async_wake(force=True)

    @callback
    def async_wake(self, *, force: bool = False) -> None:
        record = self._record
        if (
            not self._started
            or self.hass.state is not CoreState.running
            or self._storage_error
            or record is None
            or record.status not in _ACTIVE
            or (self._task is not None and not self._task.done())
        ):
            return
        now = dt_util.utcnow()
        state = self._projection(now)
        revision = state.projection_id if state is not None else None
        due = record.scheduled_at is not None and now >= record.scheduled_at
        destination_ready = (
            record.reason_code == "destination_unavailable"
            and self._destination(record.destination) is not None
        )
        if (
            force
            or due
            or destination_ready
            or now > record.latest_start
            or revision != self._checked_projection
        ):
            self._task = self.hass.async_create_task(self._async_check())

    def _check_plan(self, record: Reminder, now: datetime) -> tuple[LoadPlan | None, str | None]:
        state = self._projection(now)
        self._checked_projection = state.projection_id if state is not None else None
        if state is None:
            return None, "forecast_unavailable"
        try:
            plan = self._plan(state, record, now)
        except (ValueError, KeyError, TypeError, OverflowError):
            return None, "outlook_unavailable"
        return plan, plan.reason_code

    async def _reschedule(
        self, record: Reminder, plan: LoadPlan | None, reason: str | None
    ) -> None:
        if dt_util.utcnow() > record.latest_start:
            updated = replace(record, status="expired", scheduled_at=None, reason_code=None)
        elif plan is not None and plan.recommendation is not None:
            updated = replace(
                record, status="scheduled", scheduled_at=plan.recommendation.start, reason_code=None
            )
        else:
            updated = replace(record, status="waiting", scheduled_at=None, reason_code=reason)
        if updated != self._record:
            await self._commit(updated)

    @staticmethod
    def _suitable_now(plan: LoadPlan | None) -> LoadTiming | None:
        timing = plan.run_now if plan is not None else None
        return timing if timing is not None and timing.reserve_at is None else None

    async def _async_check(self) -> None:
        try:
            async with self._lock:
                record = self._record
                if record is None or record.status not in _ACTIVE:
                    return
                self._require_running()
                now = dt_util.utcnow()
                if now > record.latest_start:
                    await self._reschedule(record, None, None)
                    return
                plan, reason = self._check_plan(record, now)
                due = record.scheduled_at is not None and record.scheduled_at <= now
                best_now = plan is not None and (
                    plan.recommendation is not None and plan.recommendation.start <= now
                )
                if (due or best_now) and self._suitable_now(plan) is not None:
                    await self._deliver(record)
                else:
                    await self._reschedule(record, plan, reason)
        except ServiceValidationError:
            # The service reports write failures to the card via view().
            pass
        finally:
            if not self._storage_error:
                self._schedule_timer()

    async def _deliver(self, record: Reminder) -> None:
        user = await self.hass.auth.async_get_user(record.user_id)
        self._require_running()
        if user is None or not user.is_active or not user.is_admin:
            await self._commit(
                replace(
                    record, status="failed", scheduled_at=None, reason_code="permission_revoked"
                )
            )
            return
        if self._destination(record.destination) is None:
            await self._reschedule(record, None, "destination_unavailable")
            return

        # A crash after this durable marker may have happened on either side
        # of the notify call. Restore it as unconfirmed and never send again.
        await self._commit(replace(record, status="dispatching"))
        user = await self.hass.auth.async_get_user(record.user_id)
        if self.hass.state is not CoreState.running:
            return
        if not self._started:
            await self._commit(record)
            return
        if user is None or not user.is_active or not user.is_admin:
            await self._commit(
                replace(
                    record, status="failed", scheduled_at=None, reason_code="permission_revoked"
                )
            )
            return
        now = dt_util.utcnow()
        plan, reason = self._check_plan(record, now)
        timing = self._suitable_now(plan)
        call = self._destination(record.destination)
        if call is None or timing is None or now > record.latest_start:
            await self._reschedule(
                record,
                plan if call is not None else None,
                reason if call is not None else "destination_unavailable",
            )
            return
        service, target = call
        end = dt_util.as_local(timing.end).strftime("%H:%M")
        message = (
            f"Solar Sanity reminder. Run your {record.power_w:g} W load now for "
            f"{record.duration_minutes} minutes to finish at about {end}. "
            f"The current outlook projects at least {timing.minimum_soc_pct:.1f}% battery charge."
        )
        try:
            await self.hass.services.async_call(
                "notify",
                service,
                {"message": message, **target},
                blocking=True,
                context=Context(user_id=record.user_id),
            )
        except Exception:
            _LOGGER.exception("Could not confirm load reminder delivery")
            status = "delivery_unconfirmed"
        else:
            status = "sent"
        await self._commit(
            replace(record, status=status, scheduled_at=timing.start, reason_code=None)
        )
