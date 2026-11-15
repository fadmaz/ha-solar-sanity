"""Check the stored load and run only its linked script on explicit request."""

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.solar_sanity.analysis.outlook import BatterySettings, Step, project
from custom_components.solar_sanity.analysis.profile import ForecastScale
from custom_components.solar_sanity.const import OPT_BATTERY_SOC
from custom_components.solar_sanity.reminders import LoadReminders
from tests.integration import test_load_reminders
from tests.integration.test_load_reminders import (
    action,
    action_provider,
    install_notify,
    publish,
    schedule,
    setup,
    wake,
)
from tests.integration.test_simulation_service import NOW, invoke

clock = test_load_reminders.clock
SCRIPT = "script.test_reminder_start"
EVENT = "solar_sanity_test_load_start"


@pytest.fixture
async def prepared(hass, enable_custom_integrations, entry_data, clock):
    """Provide a real script that signals a start without operating equipment."""
    entry, journal, state = await setup(hass, entry_data)
    coordinator = journal.coordinator
    state.scale = ForecastScale(1, 30, 5)
    hass.states.async_set(entry.options[OPT_BATTERY_SOC], "80")
    notifications = install_notify(hass, coordinator.reminders)
    assert await async_setup_component(
        hass,
        "script",
        {"script": {"test_reminder_start": {"alias": "Test load", "sequence": [{"event": EVENT}]}}},
    )
    await hass.async_block_till_done()
    events = []

    async def started(event):
        events.append(event)

    hass.bus.async_listen(EVENT, started)
    return entry, coordinator, state, events, notifications


async def check(hass, entry, record):
    return await action(hass, entry, "check_load_reminder", reminder_id=record["id"])


async def start(hass, entry, record, projection_id="synthetic-basis"):
    return await action(
        hass, entry, "start_load_reminder", reminder_id=record["id"], projection_id=projection_id
    )


async def test_fallback_without_update_evidence_cannot_start_a_linked_script(hass, prepared, clock):
    """The linked script follows the actual provider, including after a fallback switch."""
    entry, coordinator, state, events, notifications = prepared
    now, _ = clock
    saved = await schedule(hass, entry, script_entity_id=SCRIPT)
    now.return_value = datetime.fromisoformat(saved["earliest_start"])
    state.computed_at = now.return_value
    action_provider(hass, coordinator, state, now.return_value, domain="synthetic_forecast")
    result = await check(hass, entry, saved)
    assert result["reason_code"] == "provider_unsupported_for_actions"
    assert (await start(hass, entry, saved))["check"] == result
    assert not notifications
    assert not events


async def test_no_reminder_or_script_uses_solar_before_the_current_charge(hass, prepared, clock):
    """Elapsed forecast generation once allowed a 36% battery to hit reserve."""
    entry, coordinator, state, events, notifications = prepared
    now, _ = clock
    battery = BatterySettings(10_000, 35, 100)
    steps = (
        Step(NOW, 1, 3000, 0, 3000),
        Step(NOW + timedelta(hours=1), 1, 0, 0, 0),
    )
    state.soc_pct = 36
    state.battery = battery
    state.outlook = project(36, battery, steps, forecast_used=True)
    state.inputs = {
        "battery": asdict(battery),
        "steps": [{**asdict(step), "start": step.start.isoformat()} for step in steps],
    }
    hass.states.async_set(entry.options[OPT_BATTERY_SOC], "36")
    earliest = NOW + timedelta(minutes=9)
    record = await schedule(
        hass,
        entry,
        script_entity_id=SCRIPT,
        power_w=3000,
        earliest_start=earliest.isoformat(),
        finish_by=(earliest + timedelta(hours=1)).isoformat(),
    )
    now.return_value = earliest
    verdict = await check(hass, entry, record)
    assert verdict["reason_code"] == "reaches_reserve"
    assert verdict["minimum_soc_pct"] == 35
    assert (await start(hass, entry, record))["check"] == verdict
    assert (await wake(hass, coordinator.reminders, force=True))["status"] == "waiting"
    assert not events
    assert not notifications


@pytest.mark.parametrize("after_notification", [False, True])
async def test_check_and_explicit_start_match_what_if_without_duplicate_execution(
    hass, prepared, clock, after_notification
):
    """The script runs once from the owner's request, independent of the notification."""
    entry, coordinator, state, events, notifications = prepared
    now, _ = clock
    saved = await schedule(hass, entry, script_entity_id=SCRIPT)
    assert (await check(hass, entry, saved))["reason_code"] == "before_earliest_start"
    now.return_value = datetime.fromisoformat(saved["earliest_start"])
    state.computed_at = now.return_value
    if after_notification:
        assert (await wake(hass, coordinator.reminders, force=True))["status"] == "sent"
    before = await coordinator.reminders._store.async_load()
    checked = await check(hass, entry, saved)
    simulation = await invoke(hass, entry)
    assert checked["ready"]
    assert checked["script_entity_id"] == SCRIPT
    assert checked["finish_at"] == simulation["applied_end"]
    assert checked["minimum_soc_pct"] == min(p["soc"] for p in simulation["scenario"]["trajectory"])
    assert await coordinator.reminders._store.async_load() == before
    assert not events

    replies = await asyncio.gather(
        start(hass, entry, saved), start(hass, entry, saved), return_exceptions=True
    )
    assert sum(isinstance(reply, ServiceValidationError) for reply in replies) == 1
    sent = next(reply for reply in replies if isinstance(reply, dict))["reminder"]
    assert sent["start_state"] == "requested"
    await wake(hass, coordinator.reminders, force=True)
    assert len(events) == 1
    assert events[0].context.user_id == "admin"
    assert len(notifications) == int(after_notification)


async def test_old_unlinked_reminder_checks_until_the_exact_deadline(hass, prepared, clock):
    """A 0.50 reminder stays available for the manual check and has no script path."""
    entry, coordinator, state, events, _ = prepared
    now, _ = clock
    saved = await schedule(hass, entry)
    old = await coordinator.reminders._store.async_load()
    assert "script_registry_id" not in old and "start_state" not in old
    now.return_value = datetime.fromisoformat(saved["latest_start"])
    state.computed_at = now.return_value
    assert (await check(hass, entry, saved))["ready"]
    with pytest.raises(ServiceValidationError, match="no linked"):
        await start(hass, entry, saved)
    now.return_value += timedelta(microseconds=1)
    assert (await check(hass, entry, saved))["reason_code"] == "window_too_short"
    assert not events


async def test_invalid_projection_charge_or_later_reserve_blocks_start(hass, prepared, clock):
    """A changed input can veto a successful check without consuming the reminder."""
    entry, coordinator, state, events, _ = prepared
    now, _ = clock
    saved = await schedule(hass, entry, script_entity_id=SCRIPT)
    now.return_value = datetime.fromisoformat(saved["earliest_start"])
    state.computed_at = now.return_value
    assert (await check(hass, entry, saved))["ready"]
    hass.states.async_set(entry.options[OPT_BATTERY_SOC], "70")
    assert (await start(hass, entry, saved))["check"]["reason_code"] == "soc_changed"
    hass.states.async_set(entry.options[OPT_BATTERY_SOC], "unavailable")
    assert (await check(hass, entry, saved))["reason_code"] == "soc_unavailable"
    hass.states.async_set(entry.options[OPT_BATTERY_SOC], "80")
    state.scale = None
    assert (await check(hass, entry, saved))["reason_code"] == "forecast_unavailable"
    publish(coordinator, state, now.return_value, revision="load-after-run", late_demand=True)
    assert (await start(hass, entry, saved))["check"]["reason_code"] == "projection_changed"
    decision = await check(hass, entry, saved)
    assert decision["reason_code"] == "reaches_reserve"
    simulated = await invoke(hass, entry)
    assert datetime.fromisoformat(simulated["scenario"]["reserve_at"]) > datetime.fromisoformat(
        decision["finish_at"]
    )
    assert not events
    publish(coordinator, state, now.return_value, revision="recovered")
    assert (await check(hass, entry, saved))["ready"]


async def test_checks_permissions_and_resolves_only_the_original_script(hass, prepared, clock):
    """An old entity ID cannot redirect the saved action to a replacement."""
    entry, _, state, events, _ = prepared
    now, user = clock
    with pytest.raises(ServiceValidationError, match="script"):
        await schedule(hass, entry, script_entity_id="switch.unrelated")
    saved = await schedule(hass, entry, script_entity_id=SCRIPT)
    now.return_value = datetime.fromisoformat(saved["earliest_start"])
    state.computed_at = now.return_value
    user.is_admin = False
    with pytest.raises(Unauthorized):
        await check(hass, entry, saved)
    with pytest.raises(Unauthorized):
        await start(hass, entry, saved)
    user.is_admin = True
    user.permissions.check_entity.side_effect = lambda _, policy: policy != POLICY_CONTROL
    assert (await start(hass, entry, saved))["check"]["script_reason_code"] == "script_permission"
    user.permissions.check_entity.side_effect = None
    with pytest.raises(ServiceValidationError):
        await action(hass, entry, "check_load_reminder", reminder_id=saved["id"], device_id="other")
    hass.states.async_set(SCRIPT, "on")
    assert (await start(hass, entry, saved))["check"]["script_reason_code"] == "script_running"
    registry = er.async_get(hass)
    changed = registry.async_update_entity(SCRIPT, new_entity_id="script.test_reminder_renamed")
    await hass.async_block_till_done()
    assert (await check(hass, entry, saved))["script_entity_id"] == changed.entity_id
    registry.async_remove(changed.entity_id)
    replacement = registry.async_get_or_create(
        "script", "script", "replacement", suggested_object_id=SCRIPT.split(".")[1]
    )
    hass.states.async_set(replacement.entity_id, "off")
    assert (await start(hass, entry, saved))["check"]["script_reason_code"] == "script_unavailable"
    assert not events


@pytest.mark.parametrize("fail_write", [False, True])
async def test_revalidates_after_the_durable_marker(hass, prepared, clock, fail_write):
    """Loss of forecast during a write or a failed marker prevents any script call."""
    entry, coordinator, state, events, _ = prepared
    now, _ = clock
    runner = coordinator.reminders
    saved = await schedule(hass, entry, script_entity_id=SCRIPT)
    now.return_value = datetime.fromisoformat(saved["earliest_start"])
    state.computed_at = now.return_value
    save = runner._store.async_save

    async def losing_forecast(payload):
        await save(payload)
        if payload.get("start_state") == "requesting":
            state.scale = None

    with patch.object(
        runner._store,
        "async_save",
        new=AsyncMock(side_effect=None if fail_write else losing_forecast),
    ):
        if fail_write:
            with pytest.raises(ServiceValidationError, match="save"):
                await start(hass, entry, saved)
        else:
            denied = await start(hass, entry, saved)
            assert denied["check"]["reason_code"] == "forecast_unavailable"
            assert denied["reminder"]["status"] == "scheduled"
    assert "start_state" not in await runner._store.async_load()
    assert not events


async def test_restore_during_script_request_never_retries_and_suppresses_notification(
    hass, prepared, clock
):
    """A persisted attempt may have started the script before the process exited."""
    entry, coordinator, state, events, notifications = prepared
    now, _ = clock
    runner = coordinator.reminders
    saved = await schedule(hass, entry, script_entity_id=SCRIPT)
    data = await runner._store.async_load()
    await runner._store.async_save({**data, "start_state": "requesting"})
    await runner.async_quiesce()
    restored = LoadReminders(coordinator)
    await restored.async_restore()
    coordinator.reminders = restored
    try:
        now.return_value = datetime.fromisoformat(saved["earliest_start"])
        state.computed_at = now.return_value
        restored.async_start()
        assert (await wake(hass, restored, force=True))["start_state"] == "unconfirmed"
        with pytest.raises(ServiceValidationError, match="no longer available"):
            await start(hass, entry, saved)
        assert not events and not notifications
    finally:
        await restored.async_quiesce()


async def test_cancelled_reminder_cannot_start_from_an_open_card(hass, prepared, clock):
    """Cancellation wins when its write completes before a start request."""
    entry, _, state, events, _ = prepared
    now, _ = clock
    saved = await schedule(hass, entry, script_entity_id=SCRIPT)
    now.return_value = datetime.fromisoformat(saved["earliest_start"])
    state.computed_at = now.return_value
    assert (await check(hass, entry, saved))["ready"]
    await action(hass, entry, "cancel_load_reminder", reminder_id=saved["id"])
    with pytest.raises(ServiceValidationError, match="no longer available"):
        await start(hass, entry, saved)
    assert not events
