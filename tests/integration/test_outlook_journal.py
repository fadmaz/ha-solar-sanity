"""The issued journal survives restarts without rewriting yesterday's promise."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.solar_sanity.analysis.outlook import BatterySettings, Step, project
from custom_components.solar_sanity.battery_outlook import OutlookState
from custom_components.solar_sanity.const import DOMAIN, OPT_BATTERY_CAPACITY_KWH
from custom_components.solar_sanity.journal import JOURNAL_KEY
from tests.integration.test_battery_outlook import BATTERY, _loaded

NOW = datetime(2026, 11, 10, 20, 1, tzinfo=UTC)


async def test_evening_checkpoint_freezes_interval_evidence_and_expires_public_limits(
    hass, enable_custom_integrations, entry_data
):
    """No retrospective range can appear on an old or uncalibrated issue."""
    _, journal = await _journal(hass, entry_data)
    assert journal.capture(NOW)
    saved = journal.records[0]
    assert saved["interval"]["calibration_count"] == 0
    assert saved["interval"]["lower"] is None
    assert journal.interval(NOW)["reason_code"] == "interval_needs_calibration"
    end = datetime.fromisoformat(saved["until"])
    assert journal.interval(end)["reason_code"] == "interval_expired"
    hass.config_entries.async_update_entry(
        journal.coordinator.entry,
        options={**journal.coordinator.entry.options, OPT_BATTERY_CAPACITY_KWH: 11},
    )
    assert journal.interval(NOW)["reason_code"] == "interval_waiting_evening"


async def test_shadow_is_frozen_separately_without_changing_the_published_baseline(
    hass, enable_custom_integrations, entry_data
):
    """Prospective comparison must retain both issue-time predictions unchanged."""
    from custom_components.solar_sanity.analysis.daylight_shape import SHAPE_REVISION

    _, journal = await _journal(hass, entry_data)
    state = journal.coordinator.battery.state
    state.shadow = _projection().outlook
    state.shape_reason = "shadow_only"
    state.shape_days = (3, 3, 3)
    assert journal.capture(NOW)
    saved = journal.records[0]
    assert saved["model_revision"] == "baseline-0.34.2"
    assert saved["shadow"]["model_revision"] == SHAPE_REVISION
    assert saved["shadow"]["context"] == saved["context"]
    assert saved["shadow"]["issued_at"] == saved["issued_at"]
    state.inputs["marker"].append(2)
    assert saved["inputs"]["marker"] == [1]
    assert saved["shadow"]["inputs"]["marker"] == [1]


def _projection(now=NOW):
    steps = [
        Step(now + timedelta(hours=i), 1, 0 if i < 10 else 2000, 300, 0 if i < 10 else 2000)
        for i in range(16)
    ]
    settings = BatterySettings(10_000, 20, 100)
    outlook = project(80, settings, steps, forecast_used=True)
    return OutlookState(
        outlook=outlook,
        computed_at=now,
        soc_pct=80,
        settings={"capacity_kwh": 10, "reserve_pct": 20, "target_pct": 100},
        inputs={"marker": [1]},
        battery=settings,
    )


async def _journal(hass, entry_data):
    await hass.config.async_set_time_zone("UTC")
    entry = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data, options=BATTERY))
    journal = entry.runtime_data.coordinator.journal
    journal.records.clear()
    journal.created_at = NOW - timedelta(hours=3)
    journal.last_checked_at = journal.created_at
    journal.coordinator.battery.state = _projection()
    return entry, journal


async def test_the_first_published_snapshot_is_immutable(
    hass, enable_custom_integrations, entry_data
):
    """A later recomputation or a duplicate tick must not rewrite the baseline."""
    _, journal = await _journal(hass, entry_data)
    assert journal.capture(NOW)
    original = journal.records[0]["published"].copy()
    journal.coordinator.battery.state = _projection(NOW + timedelta(minutes=1))
    journal.coordinator.battery.state.soc_pct = 25
    assert not journal.capture(NOW + timedelta(minutes=1))
    assert journal.records[0]["soc_pct"] == 80
    assert journal.records[0]["published"] == original


async def test_a_late_start_marks_the_checkpoint_missed(
    hass, enable_custom_integrations, entry_data
):
    """An issued forecast is never reconstructed after its five-minute window."""
    _, journal = await _journal(hass, entry_data)
    journal.coordinator.battery.state = _projection(NOW + timedelta(minutes=5))
    journal.capture(NOW + timedelta(minutes=5))
    assert journal.records[0]["result"]["status"] == "missed"


async def test_a_new_install_does_not_invent_missed_history(
    hass, enable_custom_integrations, entry_data
):
    """The journal starts at installation, not at an imaginary earlier checkpoint."""
    _, journal = await _journal(hass, entry_data)
    journal.created_at = NOW + timedelta(hours=1)
    journal.last_checked_at = journal.created_at
    assert not journal.capture(journal.created_at)
    assert journal.records == []


async def test_an_outage_records_missed_slots_between_runs(
    hass, enable_custom_integrations, entry_data
):
    """A restart three days later must not conceal its unobserved checkpoints."""
    _, journal = await _journal(hass, entry_data)
    journal.capture(NOW + timedelta(days=3, minutes=5))
    assert len(journal.records) == 7
    assert all(r["result"]["status"] == "missed" for r in journal.records)


@pytest.mark.parametrize("day", [datetime(2026, 3, 8, 20, 1), datetime(2026, 11, 1, 20, 1)])
async def test_checkpoints_follow_calendar_days_across_dst(
    hass, enable_custom_integrations, entry_data, day
):
    """The 08:00 and 20:00 slots survive both 23- and 25-hour days."""
    _, journal = await _journal(hass, entry_data)
    await hass.config.async_set_time_zone("America/New_York")
    now = day.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)
    journal.created_at = now - timedelta(hours=2)
    journal.last_checked_at = journal.created_at
    journal.coordinator.battery.state = _projection(now)
    assert journal.capture(now)
    assert journal.records[0]["id"] == f"{day.date()}:evening"
    assert not journal.capture(now + timedelta(minutes=1))


async def test_a_saved_journal_is_restored_with_its_original_inputs(
    hass, enable_custom_integrations, entry_data, hass_storage
):
    """The old measured-window store is independent of this new envelope."""
    entry, journal = await _journal(hass, entry_data)
    journal.capture(NOW)
    await journal.async_save()
    expected = journal.records[0].copy()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    restored = entry.runtime_data.coordinator.journal.records
    assert any(r == expected for r in restored)
    assert hass_storage[f"{JOURNAL_KEY}.{entry.entry_id}"]["version"] == 1


async def test_an_unknown_journal_version_is_never_overwritten(
    hass, enable_custom_integrations, entry_data, hass_storage
):
    """Downgrading must preserve an unreadable journal and still load the outlook."""
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data, options=BATTERY)
    key = f"{JOURNAL_KEY}.{entry.entry_id}"
    original = {"version": 999, "minor_version": 1, "key": key, "data": {"future": True}}
    hass_storage[key] = original.copy()
    await _loaded(hass, entry)
    journal = entry.runtime_data.coordinator.journal
    assert not journal.writable
    await journal.async_save()
    assert hass_storage[key] == original


async def test_context_changes_separate_metrics_and_exclude_pending_observations(
    hass, enable_custom_integrations, entry_data
):
    """A changed capacity must not mix incompatible errors or regrade an old promise."""
    entry, journal = await _journal(hass, entry_data)
    journal.capture(NOW)
    old_context = journal.context()
    hass.config_entries.async_update_entry(entry, options={**BATTERY, OPT_BATTERY_CAPACITY_KWH: 20})
    assert journal.context() != old_context
    await journal._grade_pending(NOW + timedelta(days=1))
    assert journal.records[0]["result"]["reason_code"] == "context_changed"
    assert journal.summary(now=NOW)["nights"] == 0


async def test_an_uncommitted_batch_is_not_a_verified_archive(
    hass, enable_custom_integrations, entry_data
):
    """A queued write and a successful read-back are distinct facts."""
    _, journal = await _journal(hass, entry_data)
    journal.health["provider"] = {"captured_at": NOW.isoformat(), "verification": "pending"}
    with patch(
        "custom_components.solar_sanity.journal.async_forecast_series",
        new=AsyncMock(side_effect=[{}, {NOW: 1.5}]),
    ):
        await journal._verify_archive(
            "provider", {"solar_sanity:forecast_synthetic": [(NOW, 1.5)]}, delays=(0, 0)
        )
    assert journal.health["provider"]["verification"] == "verified"
    assert journal.health["provider"]["verified_at"]


async def test_failed_readback_remains_unverified(hass, enable_custom_integrations, entry_data):
    """Read failure cannot manufacture a successful write timestamp."""
    _, journal = await _journal(hass, entry_data)
    journal.health["provider"] = {"verification": "pending"}
    with patch(
        "custom_components.solar_sanity.journal.async_forecast_series",
        new=AsyncMock(return_value=None),
    ):
        await journal._verify_archive(
            "provider", {"solar_sanity:forecast_synthetic": [(NOW, 1)]}, delays=(0,)
        )
    assert journal.health["provider"]["verification"] == "unverified"
    assert "verified_at" not in journal.health["provider"]


async def test_the_real_timer_invokes_the_journal(
    hass, enable_custom_integrations, entry_data, freezer
):
    """Calling update by hand would miss a callback scheduled on the wrong thread."""
    _, journal = await _journal(hass, entry_data)
    with patch.object(journal, "async_update", new=AsyncMock()) as update:
        freezer.tick(timedelta(seconds=31))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()
    assert update.called
