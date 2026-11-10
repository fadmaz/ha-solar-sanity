"""Optional schedules are per-entry, persist across restart and cannot bypass gates."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity import experiment_policy
from custom_components.solar_sanity.const import DOMAIN, OPT_LOAD_PROFILE_ALTERNATE_DAYS
from custom_components.solar_sanity.journal import JOURNAL_KEY, MODEL_REVISION, _valid_record
from tests.integration.test_battery_outlook import BATTERY, SOC, _loaded, _week_of_history
from tests.integration.test_outlook_journal import NOW, _journal


def decision(now):
    return {
        "id": "synthetic-reviewed-decision",
        "closed_at": (now - timedelta(days=1)).isoformat(),
        "model_revision": MODEL_REVISION,
        "outcome": "baseline_retained",
        "evidence": "synthetic test evidence",
    }


async def select(hass, entry, days):
    form = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {**entry.options, OPT_LOAD_PROFILE_ALTERNATE_DAYS: days}
    )
    await hass.async_block_till_done()
    return result


async def test_no_installation_has_alternate_days_selected_automatically(
    hass, enable_custom_integrations, entry_data
):
    """The reference shape is no exception to the universally empty default."""
    entry, journal = await _journal(hass, entry_data)
    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert form["data_schema"]({})[OPT_LOAD_PROFILE_ALTERNATE_DAYS] == []
    assert not entry.options.get(OPT_LOAD_PROFILE_ALTERNATE_DAYS)
    assert journal.load_trial.summary(NOW)["reason_code"] == "load_schedule_not_selected"
    assert not journal.load_trial.summary(NOW)["ready_for_review"]


async def test_all_seven_days_are_rejected_in_the_real_options_flow(
    hass, enable_custom_integrations, entry_data
):
    """One whole-week schedule is represented by an empty list, not all seven."""
    entry, _ = await _journal(hass, entry_data)
    result = await select(hass, entry, ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {OPT_LOAD_PROFILE_ALTERNATE_DAYS: "all_schedule_days"}
    assert not entry.options.get(OPT_LOAD_PROFILE_ALTERNATE_DAYS)


async def test_two_entries_can_choose_different_groups(
    hass, enable_custom_integrations, entry_data
):
    """There is no global university/weekend preset shared between houses."""
    first, _ = await _journal(hass, entry_data)
    data = deepcopy(entry_data)
    for channel in data["channels"]:
        channel["entity_id"] += "_second"
    second = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=data, options=BATTERY))
    await select(hass, first, ["fri", "sat"])
    await select(hass, second, ["tue"])
    assert first.options[OPT_LOAD_PROFILE_ALTERNATE_DAYS] == ["fri", "sat"]
    assert second.options[OPT_LOAD_PROFILE_ALTERNATE_DAYS] == ["tue"]


async def test_day_changes_reset_only_the_experiment_epoch(
    hass, enable_custom_integrations, entry_data
):
    """Selecting the original list again must not resume its previous comparison."""
    entry, journal = await _journal(hass, entry_data)
    production = journal.context()
    epochs = []
    for days in (["sat"], ["sun"], [], ["sat"]):
        assert (await select(hass, entry, days))["type"] is FlowResultType.CREATE_ENTRY
        journal = entry.runtime_data.coordinator.journal
        assert journal.context() == production
        epochs.append(journal.load_trial.context())
        assert list(journal.load_trial.days) == sorted(days)
    assert len(set(epochs)) == 4
    assert entry.options["battery_capacity_kwh"] == BATTERY["battery_capacity_kwh"]


@pytest.mark.parametrize("new_days", [["wed"], []])
async def test_context_changes_exclude_pending_sisters_but_preserve_production_results(
    hass, enable_custom_integrations, entry_data, new_days
):
    """A routine change cannot erase an already graded production observation."""
    entry, journal = await _journal(hass, entry_data)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, OPT_LOAD_PROFILE_ALTERNATE_DAYS: ["tue"]}
    )
    with patch.object(experiment_policy, "MORNING_DECISION", decision(NOW)):
        journal.load_trial.sync(NOW)
        state = journal.coordinator.battery.state
        state.load_shadow = state.outlook
        state.load_shadow_context = journal.load_trial.context()
        state.inputs["load_shadow_steps"] = []
        assert journal.capture(NOW)
        record = journal.records[0]
        record["result"] = {
            "status": "graded",
            "error": 2,
            "actual_lowest": 50,
            "reserve_correct": True,
        }
        baseline = deepcopy(record["result"])
        end = dt_util.parse_datetime(record["until"]) + timedelta(hours=1)
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, OPT_LOAD_PROFILE_ALTERNATE_DAYS: new_days}
        )
        journal.load_trial.sync(NOW + timedelta(minutes=10))
        assert await journal._grade_pending(end)
        assert record["load_shadow"]["result"]["reason_code"] == "context_changed"
        assert record["result"] == baseline
        assert journal.summary(90, end)["nights"] == 1


async def test_selected_days_and_their_epoch_survive_unload_and_setup(
    hass, enable_custom_integrations, entry_data
):
    """A restart retains a configured experiment rather than silently starting it again."""
    entry, _ = await _journal(hass, entry_data)
    await select(hass, entry, ["wed"])
    journal = entry.runtime_data.coordinator.journal
    original = deepcopy(journal.load_trial.control)
    await journal.async_save()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.coordinator.journal.load_trial.control == original
    assert entry.options[OPT_LOAD_PROFILE_ALTERNATE_DAYS] == ["wed"]


async def test_day_selection_does_not_bypass_the_morning_decision(
    hass, enable_custom_integrations, entry_data
):
    """The active experiment must close in a reviewed release before new live pairs."""
    entry, _ = await _journal(hass, entry_data)
    await select(hass, entry, ["sat"])
    journal = entry.runtime_data.coordinator.journal
    assert experiment_policy.MORNING_DECISION is None
    assert journal.load_trial.blocked(NOW) == "morning_experiment_open"
    assert not journal.load_trial.summary(NOW)["collecting"]


@pytest.mark.parametrize("fail", [False, True])
async def test_candidate_projection_is_separate_and_cannot_disable_production(
    hass, enable_custom_integrations, entry_data, fail
):
    """The real compute path uses group means only after the policy gate opens."""
    entry, _ = await _journal(hass, entry_data)
    await select(hass, entry, ["sat"])
    coordinator = entry.runtime_data.coordinator
    _week_of_history(entry)
    first_week = list(coordinator.buckets)
    coordinator._buckets = [
        replace(b, start_utc=b.start_utc - timedelta(days=7 * week))
        for week in range(4)
        for b in first_week
    ]
    hass.states.async_set(SOC, "70", {"unit_of_measurement": "%", "device_class": "battery"})
    now = dt_util.utcnow()
    battery = coordinator.battery
    battery.refresh(force=True, now=now)
    baseline = battery.state.outlook
    assert baseline is not None
    assert battery.state.load_shadow is None
    with patch.object(experiment_policy, "MORNING_DECISION", decision(now)):
        coordinator.journal.load_trial.sync(now)
        if fail:
            with patch(
                "custom_components.solar_sanity.battery_outlook.learn_groups",
                side_effect=RuntimeError("synthetic"),
            ):
                battery.refresh(force=True, now=now)
            assert battery.state.load_reason == "load_profile_computation_failed"
        else:
            battery.refresh(force=True, now=now)
            assert battery.state.load_shadow is not None
            assert battery.state.load_profile_dates
    assert battery.state.outlook == baseline


async def test_sister_snapshots_never_nest_or_overwrite_the_morning_shadow(
    hass, enable_custom_integrations, entry_data
):
    """Both candidates retain their own inputs while the published issue is immutable."""
    entry, journal = await _journal(hass, entry_data)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, OPT_LOAD_PROFILE_ALTERNATE_DAYS: ["tue"]}
    )
    with patch.object(experiment_policy, "MORNING_DECISION", decision(NOW)):
        journal.load_trial.sync(NOW)
        state = journal.coordinator.battery.state
        state.shadow = state.outlook
        state.load_shadow = state.outlook
        state.load_shadow_context = journal.load_trial.context()
        state.inputs["load_shadow_steps"] = [{"synthetic": 1}]
        assert journal.capture(NOW)
        saved = journal.records[0]
        assert _valid_record(saved)
        assert saved["context"] != saved["load_shadow"]["context"]
        assert saved["shadow"]["model_revision"] != saved["load_shadow"]["model_revision"]
        assert "shadow" not in saved["load_shadow"]
        assert "load_shadow" not in saved["shadow"]
        state.inputs["load_shadow_steps"][0]["synthetic"] = 2
        assert saved["load_shadow"]["inputs"]["steps"] == [{"synthetic": 1}]


async def test_unknown_trial_metadata_preserves_the_entire_journal(
    hass, enable_custom_integrations, entry_data, hass_storage
):
    """A downgrade cannot overwrite a trial envelope whose schema it does not understand."""
    entry, journal = await _journal(hass, entry_data)
    await journal.async_save()
    assert await hass.config_entries.async_unload(entry.entry_id)
    key = f"{JOURNAL_KEY}.{entry.entry_id}"
    hass_storage[key]["data"]["load_trial"]["version"] = 999
    saved = deepcopy(hass_storage[key])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    journal = entry.runtime_data.coordinator.journal
    assert not journal.writable
    await journal.async_save()
    assert hass_storage[key] == saved


async def test_legacy_journal_without_trial_metadata_remains_readable(
    hass, enable_custom_integrations, entry_data, hass_storage
):
    """The optional extension must not require a migration of existing issues."""
    entry, journal = await _journal(hass, entry_data)
    journal.capture(NOW)
    expected = deepcopy(journal.records)
    await journal.async_save()
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass_storage[f"{JOURNAL_KEY}.{entry.entry_id}"]["data"].pop("load_trial")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    restored = entry.runtime_data.coordinator.journal
    assert restored.writable
    assert all(r in restored.records for r in expected)
    assert not entry.options.get(OPT_LOAD_PROFILE_ALTERNATE_DAYS)
