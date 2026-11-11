"""Preferred sources follow captures and horizons through the real HA entry."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.solar_sanity._forecast_plan import ForecastSelection
from custom_components.solar_sanity.analysis.forecast import Correction
from custom_components.solar_sanity.const import (
    CONF_CHANNELS,
    CONF_ENTITY_ID,
    CONF_FORECAST_ENTRIES,
    CONF_ROLE,
    DOMAIN,
    FORECAST_CAPTURE_INTERVAL,
    OPT_PREFERRED_FORECAST_PROVIDER,
)
from tests.integration.test_battery_outlook import BATTERY, SOC, _loaded, _week_of_history
from tests.integration.test_outlook_journal import NOW, _journal
from tests.integration.test_setup_check import check


@pytest.fixture
async def providers(hass):
    entries = [
        MockConfigEntry(domain="forecast_solar", title=title)
        for title in ("Backup roof", "Preferred roof", "Third roof")
    ]
    for entry in entries:
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.LOADED)
    try:
        yield [entry.entry_id for entry in entries]
    finally:
        for entry in entries:
            entry.mock_state(hass, ConfigEntryState.NOT_LOADED)


def forecast(now, power, *, today=True, tomorrow=True):
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "wh_hours": {
            (midnight + timedelta(days=day, hours=hour)).isoformat(): power
            for day, included in enumerate((today, tomorrow))
            if included
            for hour in range(24)
        }
    }


async def test_preferred_capture_fails_and_recovers_on_the_normal_timer(
    hass, enable_custom_integrations, entry_data, providers
):
    """A fresh previous capture cannot keep a failed preferred provider in use."""
    await hass.config.async_set_time_zone("UTC")
    first, preferred, third = providers
    now = dt_util.utcnow()
    initial = {
        first: forecast(now, 1800),
        preferred: forecast(now, 2500, tomorrow=False),
        third: forecast(now, 1400),
    }
    with (
        patch(
            "custom_components.solar_sanity.coordinator.async_get_solar_forecasts",
            new=AsyncMock(return_value=initial),
        ) as fetch,
        patch(
            "custom_components.solar_sanity.coordinator.async_record_forecast",
            new=AsyncMock(return_value=False),
        ) as archive,
    ):
        entry = await _loaded(
            hass,
            MockConfigEntry(
                domain=DOMAIN,
                data={**entry_data, CONF_FORECAST_ENTRIES: providers},
                options={**BATTERY, OPT_PREFERRED_FORECAST_PROVIDER: preferred},
            ),
        )
        coordinator = entry.runtime_data.coordinator
        _week_of_history(entry)
        hass.states.async_set(SOC, "80", {"unit_of_measurement": "%"})
        await hass.async_block_till_done()
        coordinator.battery.refresh(force=True)
        assert coordinator.battery.state.forecast_source.provider_id == preferred
        assert coordinator.select_forecast("today", now).provider_id == preferred
        assert coordinator.select_forecast("tomorrow", now).provider_id == first
        with patch.object(
            coordinator, "_correction_for", return_value=Correction(0.5, "scored", 30)
        ):
            sources = (await check(hass, entry))["forecast"]["sources"]
            assert sources["battery"]["provider"] == "Preferred roof"
            assert sources["today"]["provider"] == "Preferred roof"
            assert sources["tomorrow"]["provider"] == "Backup roof"
            assert sources["tomorrow"]["status"] == "fallback"
            assert coordinator.expected_tomorrow_corrected_kwh == pytest.approx(21.6)
        assert {call.args[1] for call in archive.call_args_list} == set(providers)
        context = coordinator.journal.context()
        captured_at = coordinator.battery.state.forecast_source.captured_at
        fetch.return_value = {key: value for key, value in initial.items() if key != preferred}
        async_fire_time_changed(hass, now + FORECAST_CAPTURE_INTERVAL + timedelta(seconds=2))
        await hass.async_block_till_done()
        assert dt_util.utcnow() - captured_at < timedelta(hours=3)
        assert coordinator.battery.state.forecast_source.provider_id == first
        assert coordinator.battery.state.forecast_source.reason_code == "capture_unavailable"
        assert coordinator.journal.context() == context

        fetch.return_value = {**initial, preferred: forecast(now, 2500)}
        await coordinator.async_capture_forecasts()
        assert coordinator.select_forecast("battery", dt_util.utcnow()).provider_id == preferred
        assert coordinator.select_forecast("tomorrow", dt_util.utcnow()).provider_id == preferred
        stale = coordinator.select_forecast("battery", dt_util.utcnow() + timedelta(hours=4))
        assert stale.provider_id is None and stale.reason_code == "forecast_stale"
        coordinator._forecast_captures[preferred] = now - timedelta(hours=4)
        backup = coordinator.select_forecast("battery", dt_util.utcnow())
        assert backup.provider_id == first and backup.reason_code == "forecast_stale"

        fetch.return_value = {}
        await coordinator.async_capture_forecasts()
        coordinator.battery.refresh(force=True)
        assert not coordinator.battery.state.outlook.forecast_used
        assert (await check(hass, entry))["forecast"]["sources"]["battery"]["status"] == "history"
        assert coordinator.expected_tomorrow_corrected_kwh is None


async def test_auto_retains_the_previous_battery_batch_until_expiry(
    hass, enable_custom_integrations, entry_data, providers
):
    """An all-provider failure in Auto has the same retention rules as 0.46."""
    now = dt_util.utcnow()
    first, second, *_ = providers
    with (
        patch(
            "custom_components.solar_sanity.coordinator.async_get_solar_forecasts",
            new=AsyncMock(return_value={first: forecast(now, 1200), second: forecast(now, 1300)}),
        ) as fetch,
        patch(
            "custom_components.solar_sanity.coordinator.async_record_forecast",
            new=AsyncMock(return_value=False),
        ),
    ):
        entry = await _loaded(
            hass,
            MockConfigEntry(
                domain=DOMAIN,
                data={**entry_data, CONF_FORECAST_ENTRIES: [first, second]},
            ),
        )
        coordinator = entry.runtime_data.coordinator
        first_issue = coordinator.select_forecast("battery", dt_util.utcnow())
        assert first_issue.provider_id == first
        assert "battery" not in (await check(hass, entry))["forecast"]["sources"]
        fetch.return_value = {}
        await coordinator.async_capture_forecasts()
        assert coordinator.select_forecast("battery", dt_util.utcnow()) == first_issue
        assert coordinator.select_forecast("tomorrow", dt_util.utcnow()).provider_id is None
        assert coordinator.select_forecast("battery", now + timedelta(hours=4)).provider_id is None


async def test_solar_only_configure_and_removing_the_preference(
    hass, enable_custom_integrations, entry_data, providers
):
    """Preference belongs to the entry and is cleared when the provider is unmapped."""
    first, preferred, *_ = providers
    with (
        patch(
            "custom_components.solar_sanity.coordinator.async_get_solar_forecasts",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "custom_components.solar_sanity.config_flow.async_forecast_providers",
            new=AsyncMock(return_value=[(first, "Backup roof"), (preferred, "Preferred roof")]),
        ),
    ):
        entry = await _loaded(
            hass,
            MockConfigEntry(
                domain=DOMAIN,
                data={**entry_data, CONF_FORECAST_ENTRIES: [first, preferred]},
            ),
        )
        auto_context = entry.runtime_data.coordinator.journal.context()
        options = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            options["flow_id"],
            {OPT_PREFERRED_FORECAST_PROVIDER: preferred},
        )
        await hass.async_block_till_done()
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[OPT_PREFERRED_FORECAST_PROVIDER] == preferred
        assert entry.runtime_data.coordinator.journal.context() != auto_context
        assert (await check(hass, entry))["forecast"]["preferred_provider"] == "Preferred roof"

        flow = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        channels = {c[CONF_ROLE]: c[CONF_ENTITY_ID] for c in entry.data[CONF_CHANNELS]}
        flow = await hass.config_entries.flow.async_configure(flow["flow_id"], channels)
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            {CONF_FORECAST_ENTRIES: [first]},
        )
        await hass.async_block_till_done()
        assert result["type"] is FlowResultType.ABORT
        assert OPT_PREFERRED_FORECAST_PROVIDER not in entry.options


async def test_issued_source_survives_reload_and_separates_calibration(
    hass, enable_custom_integrations, entry_data, providers
):
    """Switching live providers does not rewrite or invalidate issued evidence."""
    first, preferred, *_ = providers
    entry_data[CONF_FORECAST_ENTRIES] = [first, preferred]
    with patch(
        "custom_components.solar_sanity.coordinator.async_get_solar_forecasts",
        new=AsyncMock(return_value={}),
    ):
        entry, journal = await _journal(hass, entry_data)
        state = journal.coordinator.battery.state
        state.forecast_source = ForecastSelection(first, NOW - timedelta(minutes=30))
        record = journal._snapshot("2026-11-10:evening", "evening", NOW)
        assert record["forecast_provider_id"] == first
        assert record["forecast_captured_at"] == state.forecast_source.captured_at.isoformat()
        for days_back, provider in ((3, first), (2, preferred), (1, None)):
            prior = deepcopy(record)
            issued = NOW - timedelta(days=days_back)
            prior.update(
                id=f"{issued.date()}:evening",
                forecast_provider_id=provider,
                issued_at=issued.isoformat(),
                until=(issued + timedelta(hours=10)).isoformat(),
                graded_at=(issued + timedelta(hours=11)).isoformat(),
                result={
                    "status": "graded",
                    "error": 1.0,
                    "actual_lowest": 44,
                    "reserve_correct": True,
                },
            )
            journal.records.append(prior)
        assert journal.capture(NOW)
        current = deepcopy(journal.records[-1])
        assert current["interval"]["calibration_count"] == 1
        assert journal.summary(now=NOW)["nights"] == 1
        state.forecast_source = replace(state.forecast_source, provider_id=preferred)
        assert journal.summary(now=NOW)["pending"] == 0
        assert journal.records[-1] == current
        await journal.async_save()
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert current in entry.runtime_data.coordinator.journal.records
