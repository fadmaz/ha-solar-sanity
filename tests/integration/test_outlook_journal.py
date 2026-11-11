"""The issued journal survives restarts without rewriting yesterday's promise."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.solar_sanity.analysis.model import Bucket, BucketSource, Quality
from custom_components.solar_sanity.analysis.observations import observation_hours
from custom_components.solar_sanity.analysis.outlook import BatterySettings, Step, project
from custom_components.solar_sanity.battery_outlook import OutlookState
from custom_components.solar_sanity.const import DOMAIN, OPT_BATTERY_CAPACITY_KWH
from custom_components.solar_sanity.journal import JOURNAL_KEY
from tests.integration.test_battery_outlook import BATTERY, _loaded

NOW = datetime(2026, 11, 10, 20, 1, tzinfo=UTC)


@pytest.mark.parametrize("cancel", [False, True])
async def test_startup_verification_waits_for_core_and_unload_cancels_it(
    hass, enable_custom_integrations, entry_data, cancel
):
    """A slow startup used to exhaust read-back retries before the recorder settled."""
    _, journal = await _journal(hass, entry_data)
    expected = {"solar_sanity:forecast_synthetic": [(NOW, 1.0)]}
    with patch.object(journal, "_verify_archive", new=AsyncMock()) as verify:
        # The upstream fixture starts Core already. Explicitly reproduce the
        # STARTING phase instead of assuming its default state.
        with patch.object(hass, "state", CoreState.starting):
            await journal.captured("provider", {NOW: 1.0}, expected)
            assert not verify.called
            assert journal.health["provider"]["verification"] == "pending"
            if cancel:
                journal.cancel()
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
        assert verify.called is not cancel


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


def _measured_flow(hours):
    return [
        Bucket(
            hour,
            3600,
            {"pv": 0, "load": 500, "battery_charge": 0},
            dict.fromkeys(("pv", "load", "battery_charge"), Quality.OK),
            dict.fromkeys(("pv", "load", "battery_charge"), BucketSource.OWN_INTEGRAL),
        )
        for hour in hours
    ]


@pytest.mark.parametrize("kind", ["evening", "morning"])
async def test_the_journal_reads_the_first_hour_without_using_outside_states(
    recorder_mock, hass, enable_custom_integrations, entry_data, hass_storage, *, kind
):
    """Recorder drops the first statistics row when the issue is a few seconds late."""
    entry, journal = await _journal(hass, entry_data)
    await hass.config.async_set_time_zone("Asia/Jerusalem")
    issued = NOW.replace(hour=18 if kind == "evening" else 6, minute=0, second=51)
    journal.created_at = journal.last_checked_at = issued - timedelta(minutes=5)
    journal._next_grade = None
    state = _projection(issued)
    if kind == "evening":
        state.outlook = replace(state.outlook, takeover_at=issued + timedelta(hours=10))
    journal.coordinator.battery.state = state
    journal.version = "0.43.0"
    assert journal.capture(issued)
    record = journal.records[0]
    original = deepcopy(record)
    journal.version = "0.43.1"
    end = datetime.fromisoformat(record["until"])
    if kind == "morning":
        assert end.astimezone(ZoneInfo("Asia/Jerusalem")).hour == 22
    hours = observation_hours(issued, end)
    journal.coordinator._buckets = _measured_flow(hours)
    settle = hours[-1] + timedelta(hours=1, minutes=15)
    soc_entity = record["soc_entity"]

    def statistics(_hass, start, stop, ids, *_args):
        assert ids == {soc_entity}
        return {
            soc_entity: [
                {
                    "start": hour.timestamp(),
                    "mean": 75,
                    "min": 10 if hour in (hours[0], hours[-1]) and kind == "evening" else 65,
                    "max": 100 if hour == hours[0] else 80,
                }
                for hour in hours
                if start <= hour < stop
            ]
        }

    def history(_hass, start, stop, ids, *, include_start_time_state, **_kwargs):
        assert ids == [soc_entity]
        if not include_start_time_state:
            return {soc_entity: []}
        # Recorder gives the state in effect at the boundary even when no
        # changes were recorded during the partial hour.
        value = "80" if start == issued else "60"
        return {
            soc_entity: [
                SimpleNamespace(last_updated=start, state=value),
                SimpleNamespace(last_updated=stop, state="10" if kind == "evening" else "100"),
            ]
        }

    with (
        patch(
            "homeassistant.components.recorder.statistics.statistics_during_period",
            side_effect=statistics,
        ) as read_stats,
        patch(
            "homeassistant.components.recorder.history.get_significant_states", side_effect=history
        ),
    ):
        await journal.async_update(settle - timedelta(minutes=5))
        assert record["result"] == {"status": "pending"}
        read_stats.assert_not_called()
        await journal.async_update(settle)
        assert read_stats.call_args.args[1:3] == (hours[0], hours[-1] + timedelta(hours=1))
        assert record["result"]["status"] == "graded"
        if kind == "evening":
            assert record["result"]["actual_lowest"] == 60
            assert record["result"]["reserve_correct"]
        else:
            assert not record["result"]["actual_filled"]
            assert record["result"]["time_error_minutes"] is None
        await journal.async_update(settle + timedelta(minutes=5))
        read_stats.assert_called_once()

    for key in original.keys() - {"result"}:
        assert record[key] == original[key]
    saved = hass_storage[f"{JOURNAL_KEY}.{entry.entry_id}"]["data"]["records"]
    assert [r for r in saved if r["id"] == record["id"]] == [record]


@pytest.mark.parametrize("problem", ["missing_hour", "query_failure", "state_gap", "grid_charge"])
async def test_the_journal_retries_missing_evidence_but_excludes_grid_charge(
    recorder_mock, hass, enable_custom_integrations, entry_data, problem
):
    """A read failure, a gap and an externally charged night have distinct outcomes."""
    _, journal = await _journal(hass, entry_data)
    assert journal.capture(NOW)
    record = journal.records[0]
    end = datetime.fromisoformat(record["until"])
    hours = observation_hours(NOW, end)
    journal.coordinator._buckets = _measured_flow(hours)
    if problem == "grid_charge":
        journal.coordinator._buckets[1].wh["battery_charge"] = 1500
    soc_entity = record["soc_entity"]
    rows = {
        soc_entity: [
            {"start": hour.timestamp(), "mean": 70, "min": 75 - index, "max": 80}
            for index, hour in enumerate(hours)
            if problem != "missing_hour" or index != 2
        ]
    }

    def history(_hass, start, stop, ids, **_kwargs):
        states = [SimpleNamespace(last_updated=start, state="80" if start == NOW else "62")]
        if problem == "state_gap" and start == NOW:
            states.append(
                SimpleNamespace(last_updated=start + timedelta(minutes=1), state="unavailable")
            )
        return {soc_entity: states}

    with (
        patch(
            "homeassistant.components.recorder.statistics.statistics_during_period",
            return_value=rows,
            side_effect=RuntimeError("query unavailable") if problem == "query_failure" else None,
        ),
        patch(
            "homeassistant.components.recorder.history.get_significant_states", side_effect=history
        ),
    ):
        if problem == "grid_charge":
            assert await journal._grade_pending(end + timedelta(hours=2))
            assert record["result"] == {"status": "excluded", "reason_code": "grid_charging"}
            return
        assert not await journal._grade_pending(end + timedelta(hours=2))
        assert record["result"] == {"status": "pending"}
        assert await journal._grade_pending(end + timedelta(days=1))
        reason = "history_read_failed" if problem == "query_failure" else "soc_incomplete"
        assert record["result"] == {"status": "excluded", "reason_code": reason}


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
