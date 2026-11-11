"""Observed coverage survives upgrades independently of the original grade."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.solar_sanity.analysis.observations import SocSeries
from custom_components.solar_sanity.analysis.solar_coverage import HOUR, coverage_day
from tests.integration.test_outlook_journal import NOW, _journal, _measured_flow
from tests.integration.test_tools_services import device, history


@pytest.mark.parametrize("has_charge_history", [True, False])
async def test_previous_issues_gain_a_separate_saved_observation(
    hass, enable_custom_integrations, entry_data, freezer, *, has_charge_history
):
    """Legacy and new History results stay distinct when Recorder lacks charge history."""
    freezer.move_to(NOW)
    entry, journal = await _journal(hass, entry_data)
    state = journal.coordinator.battery.state
    start, end = coverage_day(NOW, journal.coordinator.time_zone)
    expected, observed = start + 6 * HOUR, start + 7 * HOUR
    state.outlook = replace(state.outlook, takeover_at=expected)
    state.inputs["battery"] = {"standby_w": 100}
    journal.version = "0.43.1"
    assert journal.capture(NOW)
    journal.version = "0.44.0"
    record = journal.records[0]
    record["result"] = {
        "status": "graded",
        "actual_lowest": 55,
        "error": 2,
        "reserve_correct": True,
    }
    frozen = deepcopy(record)
    journal.coordinator._buckets = _measured_flow([start + i * HOUR for i in range(8)])
    journal.coordinator._buckets[-1].wh["pv"] = 600
    soc = SocSeries([], {expected: 52} if has_charge_history else {}, {})
    freezer.move_to(end + timedelta(days=1))
    journal._next_grade = None

    with patch(
        "custom_components.solar_sanity.journal.async_read_grading_soc",
        new=AsyncMock(return_value=soc),
    ) as read:
        await journal.async_update()
        read.assert_awaited_once_with(hass, record["soc_entity"], expected, observed)
    assert {k: record[k] for k in frozen} == frozen
    result = record["solar_coverage"]
    assert result["status"] == "observed"
    assert result["observed_start"] == observed.isoformat()
    assert result["relation"] == "later"
    assert result["late_soc_status"] == ("available" if has_charge_history else "unavailable")

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    restored = entry.runtime_data.coordinator.journal
    assert [r for r in restored.records if r["id"] == record["id"]] == [record]
    with patch.object(restored, "_update_solar_coverage", new=AsyncMock()) as update:
        public = await history(hass, device(hass, entry))
    update.assert_not_awaited()
    row = next(r for r in public["records"] if r["id"] == record["id"])
    assert row["result"]["actual_lowest"] == 55
    assert row["solar_coverage"]["late_lowest_pct"] == (52 if has_charge_history else None)
    assert row["solar_coverage"]["observed_end"] == (observed + HOUR).isoformat()
    assert "inputs" not in row and "soc_entity" not in row


async def test_backfill_is_bounded_and_prioritizes_active_issues(
    hass, enable_custom_integrations, entry_data
):
    """An old archive cannot delay a new observation or consume all work in one tick."""
    _, journal = await _journal(hass, entry_data)
    state = journal.coordinator.battery.state
    state.inputs["battery"] = {"standby_w": 0}
    assert journal.capture(NOW)
    root = journal.records.pop()
    for days in (5, 4, 3, 0):
        issue = NOW - timedelta(days=days)
        record = deepcopy(root)
        record.update(
            id=f"{issue.date()}:evening",
            issued_at=issue.isoformat(),
            until=(datetime.fromisoformat(root["until"]) - timedelta(days=days)).isoformat(),
            version="0.43.1" if days else journal.version,
        )
        journal.records.append(record)
    assert await journal._update_solar_coverage(NOW + timedelta(hours=7))
    assert "solar_coverage" not in journal.records[0]
    assert [r["solar_coverage"]["status"] for r in journal.records[1:]] == [
        "unavailable",
        "unavailable",
        "pending",
    ]
