"""Load reminders use the issued projection, native actions and real HA storage."""

from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized

from custom_components.solar_sanity._forecast_plan import ForecastSelection
from custom_components.solar_sanity.analysis.outlook import Step, project
from custom_components.solar_sanity.analysis.profile import ForecastScale
from custom_components.solar_sanity.const import CONF_FORECAST_ENTRIES, DOMAIN
from custom_components.solar_sanity.reminders import LoadReminders
from tests.integration.ha_harness import MockConfigEntry
from tests.integration.test_simulation_service import NOW, invoke
from tests.integration.test_simulation_service import setup as setup_simulation
from tests.integration.test_tools_services import device

OWNER = Context(user_id="admin")


@contextmanager
def mock_owner(hass):
    """Stub the action caller while preserving HTTP's real system user."""
    user = MagicMock(is_active=True, is_admin=True)
    user.permissions.check_entity.return_value = True
    real_get_user = hass.auth.async_get_user

    async def get_user(user_id):
        return user if user_id == OWNER.user_id else await real_get_user(user_id)

    with patch.object(hass.auth, "async_get_user", side_effect=get_user):
        yield user


@pytest.fixture
def clock(hass):
    """Keep service calls and reminder callbacks on the same clock."""
    with (
        mock_owner(hass) as user,
        patch("custom_components.solar_sanity.reminders.dt_util.utcnow", return_value=NOW) as now,
    ):
        try:
            yield now, user
        finally:
            for entry in hass.config_entries.async_entries():
                if (
                    isinstance(entry, MockConfigEntry)
                    and entry.title == "Synthetic action provider"
                ):
                    entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


def action_provider(hass, coordinator, state, captured_at, *, domain="forecast_solar"):
    provider = MockConfigEntry(domain=domain, title="Synthetic action provider")
    provider.add_to_hass(hass)
    provider.mock_state(hass, ConfigEntryState.LOADED)
    key = provider.entry_id
    hass.config_entries.async_update_entry(
        coordinator.entry, data={**coordinator.entry.data, CONF_FORECAST_ENTRIES: [key]}
    )
    coordinator.forecast_points = {key: {state.outlook.horizon_end: 1}}
    coordinator._forecast_captures = {key: captured_at}
    coordinator._latest_forecast_batch = frozenset({key})
    coordinator.journal.health[key] = {"provider_updated_at": captured_at.isoformat()}
    state.forecast_source = ForecastSelection(key, captured_at)
    return key


async def setup(hass, entry_data):
    entry, journal, state = await setup_simulation(hass, entry_data)
    action_provider(hass, journal.coordinator, state, NOW)
    return entry, journal, state


async def action(hass, entry, name, /, *, context=OWNER, **data):
    return await hass.services.async_call(
        DOMAIN,
        name,
        {"device_id": device(hass, entry), **data},
        context=context,
        blocking=True,
        return_response=True,
    )


async def schedule(hass, entry, *, destination="notify.test_phone", **data):
    return (
        await action(
            hass,
            entry,
            "schedule_load_reminder",
            **{
                "projection_id": "synthetic-basis",
                "destination": destination,
                "power_w": 1000,
                "duration_minutes": 60,
                "earliest_start": (NOW + timedelta(minutes=15)).isoformat(),
                "finish_by": (NOW + timedelta(hours=3)).isoformat(),
                **data,
            },
        )
    )["reminder"]


def install_notify(hass, reminders, *, legacy=False):
    messages = []

    async def notify(call):
        assert (await reminders._store.async_load())["status"] == "dispatching"
        messages.append(dict(call.data))

    hass.services.async_register(
        "notify", "mobile_app_test_phone" if legacy else "send_message", notify
    )
    if not legacy:
        hass.states.async_set("notify.test_phone", "unknown")
    return messages


async def wake(hass, runner, *, force=False):
    runner.async_wake(force=force)
    await hass.async_block_till_done()
    return runner.view()["reminder"]


def publish(coordinator, state, now, *, revision, sunshine_hour=None, late_demand=False):
    settings = state.battery
    steps = [
        Step(
            NOW + timedelta(hours=hour),
            1,
            2000 if hour == sunshine_hour else 0,
            1000 if late_demand else 1500 if hour == 0 else 0,
            2000 if hour == sunshine_hour else 0,
        )
        for hour in range(12)
    ]
    coordinator.battery.state = replace(
        state,
        computed_at=now,
        projection_id=revision,
        scale=ForecastScale(1, 30, 5),
        outlook=project(80, settings, steps, forecast_used=True),
        inputs={
            **state.inputs,
            "steps": [{**asdict(step), "start": step.start.isoformat()} for step in steps],
        },
    )
    coordinator.forecast_captured_at = now


@pytest.mark.parametrize("legacy", [False, True])
async def test_reminder_sends_once_using_the_same_simulation_as_what_if(
    hass, enable_custom_integrations, entry_data, clock, legacy
):
    """Notify can only run after the attempt is on disk and the full run is eligible."""
    now, _ = clock
    entry, journal, state = await setup(hass, entry_data)
    coordinator = journal.coordinator
    runner = coordinator.reminders
    state.scale = ForecastScale(1, 30, 5)
    messages = install_notify(hass, runner, legacy=legacy)
    result = await schedule(
        hass, entry, destination="mobile_app_test_phone" if legacy else "notify.test_phone"
    )
    assert result["status"] == "scheduled"
    assert result["scheduled_at"] == (NOW + timedelta(minutes=15)).isoformat()
    assert not messages

    now.return_value = datetime.fromisoformat(result["scheduled_at"])
    state.computed_at = now.return_value
    simulated = await invoke(hass, entry)
    sent = await wake(hass, runner, force=True)
    assert sent["status"] == "sent"
    minimum = min(point["soc"] for point in simulated["scenario"]["trajectory"])
    assert len(messages) == 1
    assert f"{minimum:.1f}% battery charge" in messages[0]["message"]
    assert messages[0].get("entity_id") == (None if legacy else "notify.test_phone")
    await wake(hass, runner, force=True)
    assert len(messages) == 1
    assert journal.records == []


async def test_new_projections_move_the_start_both_later_and_earlier(
    hass, enable_custom_integrations, entry_data, clock
):
    """Rank a new solar profile while a future reminder is still pending."""
    now, _ = clock
    entry, journal, state = await setup(hass, entry_data)
    coordinator = journal.coordinator
    runner = coordinator.reminders
    messages = install_notify(hass, runner)
    publish(coordinator, state, NOW, revision="synthetic-basis", sunshine_hour=1)
    first = await schedule(hass, entry, finish_by=(NOW + timedelta(hours=4)).isoformat())
    assert first["scheduled_at"] == "2026-11-10T21:15:00+00:00"
    now.return_value += timedelta(minutes=1)
    publish(coordinator, state, now.return_value, revision="later", sunshine_hour=2)
    coordinator.notify_live_entities()
    await hass.async_block_till_done()
    assert runner.view()["reminder"]["scheduled_at"] == "2026-11-10T22:15:00+00:00"
    publish(coordinator, state, now.return_value, revision="earlier", sunshine_hour=1)
    assert (await wake(hass, runner))["scheduled_at"] == first["scheduled_at"]
    assert not messages


async def test_late_reserve_and_forecast_loss_wait_then_recover_or_expire(
    hass, enable_custom_integrations, entry_data, clock
):
    """The battery must clear reserve beyond the load end, even when the due time arrives."""
    now, _ = clock
    entry, journal, state = await setup(hass, entry_data)
    coordinator = journal.coordinator
    runner = coordinator.reminders
    messages = install_notify(hass, runner)
    publish(coordinator, state, NOW, revision="synthetic-basis", sunshine_hour=1)
    result = await schedule(hass, entry)
    now.return_value = datetime.fromisoformat(result["scheduled_at"])
    publish(coordinator, state, now.return_value, revision="demand", late_demand=True)
    simulation = await invoke(hass, entry)
    assert datetime.fromisoformat(
        simulation["scenario"]["reserve_at"]
    ) > now.return_value + timedelta(hours=1)
    assert (await wake(hass, runner, force=True))["reason_code"] == "baseline_reaches_reserve"
    assert not messages
    coordinator.battery.state.scale = None
    assert (await wake(hass, runner))["reason_code"] == "forecast_unavailable"
    publish(coordinator, state, now.return_value, revision="back", sunshine_hour=2)
    assert (await wake(hass, runner))["status"] == "scheduled"
    now.return_value = datetime.fromisoformat(result["latest_start"]) + timedelta(seconds=1)
    assert (await wake(hass, runner, force=True))["status"] == "expired"
    assert not messages


@pytest.mark.parametrize("fits", [False, True])
async def test_due_load_is_checked_against_its_own_end_when_the_outlook_shortens(
    hass, enable_custom_integrations, entry_data, clock, fits
):
    """A short forecast may still cover the run, even when it ends before the deadline."""
    now, _ = clock
    entry, journal, state = await setup(hass, entry_data)
    coordinator = journal.coordinator
    runner = coordinator.reminders
    messages = install_notify(hass, runner)
    state.scale = ForecastScale(1, 30, 5)
    reminder = await schedule(hass, entry)
    now.return_value = datetime.fromisoformat(reminder["scheduled_at"])
    state.computed_at = now.return_value
    steps = (Step(NOW, 1, 0, 200, 0),)
    if fits:
        steps += (Step(NOW + timedelta(hours=1), 1, 0, 200, 0),)
    state.outlook = project(80, state.battery, steps, forecast_used=True)
    state.inputs["steps"] = [{**asdict(step), "start": step.start.isoformat()} for step in steps]
    result = await wake(hass, runner, force=True)
    assert result["finish_by"] == reminder["finish_by"]
    if fits:
        assert result["status"] == "sent"
        assert len(messages) == 1
    else:
        assert result["reason_code"] == "beyond_horizon"
        assert not messages


async def test_provider_eligibility_is_shared_by_planning_scheduling_and_delivery(
    hass, enable_custom_integrations, entry_data, clock
):
    """Planning remains usable when the actual forecast source cannot authorize actions."""
    now, _ = clock
    entry, journal, state = await setup(hass, entry_data)
    coordinator = journal.coordinator
    runner = coordinator.reminders
    state.scale = ForecastScale(1, 30, 5)
    messages = install_notify(hass, runner)
    action_provider(hass, coordinator, state, NOW, domain="synthetic_forecast")
    plan = await invoke(
        hass, entry, action="find_best_time", finish_by=(NOW + timedelta(hours=2)).isoformat()
    )
    assert plan["recommendation"] is not None
    assert plan["load_actions"]["status"] == "unsupported"
    with pytest.raises(ServiceValidationError, match="supports planning only"):
        await schedule(hass, entry)

    key = action_provider(hass, coordinator, state, NOW)
    coordinator.journal.health[key].pop("provider_updated_at")
    saved = await schedule(hass, entry)
    now.return_value = datetime.fromisoformat(saved["scheduled_at"])
    state.computed_at = now.return_value
    assert (await wake(hass, runner, force=True))["reason_code"] == "provider_update_unverified"
    action_provider(hass, coordinator, state, now.return_value, domain="synthetic_forecast")
    assert (await wake(hass, runner))["reason_code"] == "provider_unsupported_for_actions"
    assert not messages
    action_provider(hass, coordinator, state, now.return_value)
    assert (await wake(hass, runner, force=True))["status"] == "sent"
    assert len(messages) == 1


async def test_reminder_actions_require_admin_current_projection_and_valid_target(
    hass, enable_custom_integrations, entry_data, clock
):
    """Only an administrator can read or change a reminder, including after cancellation."""
    _, user = clock
    entry, journal, state = await setup(hass, entry_data)
    runner = journal.coordinator.reminders
    state.scale = ForecastScale(1, 30, 5)
    install_notify(hass, runner)
    user.is_admin = False
    with pytest.raises(Unauthorized):
        await action(hass, entry, "get_load_reminder")
    with pytest.raises(Unauthorized):
        await schedule(hass, entry)
    user.is_admin = True
    with pytest.raises(Unauthorized):
        await action(hass, entry, "get_load_reminder", context=Context())
    with pytest.raises(ServiceValidationError):
        await action(hass, entry, "get_load_reminder", device_id="another-integration")
    with pytest.raises(ServiceValidationError, match="outlook changed"):
        await schedule(hass, entry, projection_id="old")
    with pytest.raises(ServiceValidationError, match="notification target"):
        await schedule(hass, entry, destination="switch.not_a_notification")
    record = await schedule(hass, entry)
    with pytest.raises(ServiceValidationError, match="active reminder"):
        await schedule(hass, entry)
    with pytest.raises(ServiceValidationError):
        await action(hass, entry, "cancel_load_reminder", reminder_id="other-entry-record")
    result = await action(hass, entry, "cancel_load_reminder", reminder_id=record["id"])
    assert result["reminder"]["status"] == "cancelled"


async def test_reminders_are_isolated_between_loaded_installations(
    hass, enable_custom_integrations, entry_data, clock
):
    """Both entries can schedule independently and have separate storage and cancellation."""
    first, first_journal, first_state = await setup(hass, entry_data)
    other_data = {
        **entry_data,
        "channels": [
            {**channel, "entity_id": channel["entity_id"].replace("sensor.", "sensor.other_")}
            for channel in entry_data["channels"]
        ],
    }
    second, second_journal, second_state = await setup(hass, other_data)
    first_state.scale = second_state.scale = ForecastScale(1, 30, 5)
    install_notify(hass, first_journal.coordinator.reminders)
    first_record = await schedule(hass, first)
    assert (await action(hass, second, "get_load_reminder"))["reminder"] is None
    second_record = await schedule(hass, second)
    assert first_record["id"] != second_record["id"]
    assert (await second_journal.coordinator.reminders._store.async_load())["id"] == second_record[
        "id"
    ]
    await action(hass, first, "cancel_load_reminder", reminder_id=first_record["id"])
    assert (await action(hass, second, "get_load_reminder"))["reminder"] == second_record


async def test_reminder_rechecks_after_recording_dispatch_attempt(
    hass, enable_custom_integrations, entry_data, clock
):
    """A source that disappears during the durable write cannot authorize delivery."""
    now, _ = clock
    entry, journal, state = await setup(hass, entry_data)
    runner = journal.coordinator.reminders
    state.scale = ForecastScale(1, 30, 5)
    messages = install_notify(hass, runner)
    record = await schedule(hass, entry)
    save = runner._store.async_save

    async def lose_forecast(payload):
        await save(payload)
        if payload["status"] == "dispatching":
            state.scale = None

    now.return_value = datetime.fromisoformat(record["scheduled_at"])
    state.computed_at = now.return_value
    with patch.object(runner._store, "async_save", side_effect=lose_forecast):
        result = await wake(hass, runner, force=True)
    assert result["status"] == "waiting"
    assert result["reason_code"] == "forecast_unavailable"
    assert not messages


async def test_store_failure_is_visible_and_never_sends_without_a_durable_marker(
    hass, enable_custom_integrations, entry_data, clock
):
    """Home Assistant may swallow a write failure, and a read failure must stay local."""
    now, _ = clock
    entry, journal, state = await setup(hass, entry_data)
    coordinator = journal.coordinator
    runner = coordinator.reminders
    state.scale = ForecastScale(1, 30, 5)
    messages = install_notify(hass, runner)
    record = await schedule(hass, entry)
    now.return_value = datetime.fromisoformat(record["scheduled_at"])
    state.computed_at = now.return_value
    with patch.object(runner._store, "async_save", new=AsyncMock()):
        await wake(hass, runner, force=True)
    assert runner.view() == {"reminder": None, "storage_error": True}
    assert not messages
    assert (await runner._store.async_load())["status"] == "scheduled"
    restored = LoadReminders(coordinator)
    with patch.object(
        restored._store, "async_load", side_effect=HomeAssistantError("Cannot read storage.")
    ):
        await restored.async_restore()
    assert restored.view() == runner.view()
    assert (await invoke(hass, entry))["scenario"]["reserve_at"] is None


@pytest.mark.parametrize("interrupted", [False, True])
async def test_restart_restores_active_reminder_and_never_retries_an_attempt(
    hass, enable_custom_integrations, entry_data, clock, interrupted
):
    """The durable marker is distinct from a confirmed notification and a pending schedule."""
    now, _ = clock
    entry, journal, state = await setup(hass, entry_data)
    coordinator = journal.coordinator
    original = coordinator.reminders
    state.scale = ForecastScale(1, 30, 5)
    install_notify(hass, original)
    issued = await schedule(hass, entry)
    if interrupted:
        data = await original._store.async_load()
        await original._store.async_save({**data, "status": "dispatching"})
    await original.async_quiesce()
    restored = LoadReminders(coordinator)
    await restored.async_restore()
    coordinator.reminders = restored
    messages = install_notify(hass, restored)
    try:
        restored.async_start()
        await hass.async_block_till_done()
        record = restored.view()["reminder"]
        assert record["id"] == issued["id"]
        assert record["status"] == ("delivery_unconfirmed" if interrupted else "scheduled")
        now.return_value = datetime.fromisoformat(issued["scheduled_at"])
        state.computed_at = now.return_value
        await wake(hass, restored, force=True)
        assert len(messages) == (0 if interrupted else 1)
    finally:
        await restored.async_quiesce()


async def test_fold_offsets_preserve_elapsed_duration_and_no_early_send(
    hass, enable_custom_integrations, entry_data, clock
):
    """The two occurrences of 01:30 are different instants across the fall clock change."""
    now, _ = clock
    now.return_value = datetime(2026, 11, 1, 5, tzinfo=UTC)
    entry, journal, state = await setup(hass, entry_data)
    runner = journal.coordinator.reminders
    await hass.config.async_set_time_zone("America/New_York")
    base = now.return_value
    steps = [Step(base + timedelta(hours=hour), 1, 0, 200, 0) for hour in range(12)]
    state.computed_at = base
    state.outlook = project(80, state.battery, steps, forecast_used=True)
    state.inputs["steps"] = [{**asdict(step), "start": step.start.isoformat()} for step in steps]
    state.scale = ForecastScale(1, 30, 5)
    journal.coordinator.forecast_captured_at = base
    action_provider(hass, journal.coordinator, state, base)
    messages = install_notify(hass, runner)
    reminder = await schedule(
        hass,
        entry,
        earliest_start="2026-11-01T01:30:00-04:00",
        finish_by="2026-11-01T01:30:00-05:00",
    )
    assert reminder["scheduled_at"] == reminder["latest_start"] == "2026-11-01T05:30:00+00:00"
    assert not messages
