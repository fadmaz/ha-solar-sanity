"""The setup response uses the loaded installation's state and no private inputs."""

import json
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.analysis.model import Bucket, BucketSource, Quality
from custom_components.solar_sanity.const import (
    CONF_CHANNELS,
    CONF_ENTITY_ID,
    CONF_FORECAST_ENTRIES,
    CONF_ROLE,
    DOMAIN,
)
from tests.integration.test_battery_outlook import BATTERY, SOC, _loaded
from tests.integration.test_outlook_journal import NOW, _journal
from tests.integration.test_tools_services import device


async def check(hass, entry, context=None):
    return await hass.services.async_call(
        DOMAIN,
        "get_setup_check",
        {"device_id": device(hass, entry)},
        blocking=True,
        return_response=True,
        context=context,
    )


def meters(hass, data):
    for channel in data[CONF_CHANNELS]:
        hass.states.async_set(channel[CONF_ENTITY_ID], "0", {"unit_of_measurement": "W"})


def retain_hour(entry):
    wh = {row[CONF_ROLE]: 0 for row in entry.data[CONF_CHANNELS]}
    entry.runtime_data.coordinator._buckets.append(
        Bucket(
            NOW - timedelta(hours=1),
            3600,
            wh,
            dict.fromkeys(wh, Quality.OK),
            dict.fromkeys(wh, BucketSource.OWN_INTEGRAL),
        )
    )


async def test_solar_only_distinguishes_a_valid_zero_from_missing_history(
    hass, enable_custom_integrations, entry_data
):
    """Solar-only installations have their own check, even before the first usable hour."""
    entry_data[CONF_CHANNELS] = entry_data[CONF_CHANNELS][:2]
    meters(hass, entry_data)
    entry = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data))
    data = await check(hass, entry)
    assert data["measurements"]["status"] == "waiting_for_data"
    assert [
        (c["key"], c["kind"], c["unit"], c["hours"], c["status"])
        for c in data["measurements"]["channels"]
    ] == [
        ("pv", "power", "W", 0, "ready"),
        ("load", "power", "W", 0, "ready"),
    ]
    retain_hour(entry)
    data = await check(hass, entry)
    assert data["measurements"]["status"] == "ready"
    assert data["measurements"]["channels"][0]["hours"] == 1
    assert data["battery"]["status"] == data["forecast"]["status"] == "not_configured"
    assert data["history"]["counts"] is None
    assert "sensor." not in json.dumps(data)


@pytest.mark.parametrize("export", [False, True])
async def test_battery_options_and_optional_channels(
    hass, enable_custom_integrations, entry_data, export
):
    """A no-export house need not supply an export meter to be ready."""
    if export:
        entry_data[CONF_CHANNELS].append(
            {CONF_ROLE: "grid_export", CONF_ENTITY_ID: "sensor.export"}
        )
    meters(hass, entry_data)
    hass.states.async_set(SOC, "62", {"unit_of_measurement": "%"})
    entry = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data, options=BATTERY))
    retain_hour(entry)
    data = await check(hass, entry)
    assert data["measurements"]["status"] == "ready"
    roles = {c["key"] for c in data["measurements"]["channels"]}
    assert ("grid_export" in roles) is export
    assert {"grid_import", "battery_charge", "battery_discharge"}.issubset(roles)
    assert data["battery"]["settings"] == {"capacity_kwh": 10, "reserve_pct": 20, "target_pct": 100}
    assert data["battery"]["status"] == "waiting_for_data"
    assert data["history"]["status"] == "waiting_for_data"


async def test_setup_guidance_recovers_when_the_meter_becomes_supported(
    hass, enable_custom_integrations, entry_data
):
    """A non-cumulative energy source needs reconfiguration, an unavailable source can recover."""
    meters(hass, entry_data)
    meter = entry_data[CONF_CHANNELS][0][CONF_ENTITY_ID]
    attrs = {"device_class": "energy", "unit_of_measurement": "kWh"}
    hass.states.async_set(meter, "0", {**attrs, "state_class": "measurement"})
    entry = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data))
    retain_hour(entry)
    first = (await check(hass, entry))["measurements"]["channels"][0]
    assert (first["status"], first["action"], first["hours"]) == (
        "needs_attention",
        "reconfigure",
        1,
    )
    assert "total_increasing" in first["reason"]
    hass.states.async_set(meter, "unavailable", {**attrs, "state_class": "total_increasing"})
    assert (await check(hass, entry))["measurements"]["channels"][0]["status"] == "waiting_for_data"
    hass.states.async_set(meter, "12", {**attrs, "state_class": "total_increasing"})
    after = (await check(hass, entry))["measurements"]["channels"][0]
    assert (after["status"], after["action"], after["kind"], after["unit"]) == (
        "ready",
        None,
        "energy",
        "kWh",
    )


async def test_forecast_health_separates_capture_and_readback(
    hass, enable_custom_integrations, entry_data
):
    """A recent capture and an old verified batch do not verify the current batch."""
    provider = MockConfigEntry(domain="forecast_solar", title="Synthetic provider")
    provider.add_to_hass(hass)
    entry_data[CONF_FORECAST_ENTRIES] = [provider.entry_id]
    entry = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data))
    coordinator = entry.runtime_data.coordinator
    now = dt_util.utcnow()
    verified = (now - timedelta(hours=5)).isoformat()
    health = coordinator.journal.health[provider.entry_id] = {
        "captured_at": (now - timedelta(hours=4)).isoformat(),
        "verified_at": verified,
        "verification": "verified",
    }
    provider.mock_state(hass, ConfigEntryState.LOADED)
    try:
        with patch.object(coordinator, "async_capture_forecasts", new=AsyncMock()) as capture:
            stale = (await check(hass, entry))["forecast"]
            assert stale["status"] == "needs_attention"
            assert "three hours" in stale["providers"][0]["reason"]
            health.update(captured_at=now.isoformat(), verification="pending")
            pending = (await check(hass, entry))["forecast"]
            assert pending["status"] == "waiting_for_data"
            assert pending["providers"][0]["verified_at"] == verified
            assert pending["providers"][0]["verification"] == "pending"
            assert not pending["corrected"]
            assert not capture.called
    finally:
        # This test supplies capture health without starting the provider.
        provider.mock_state(hass, ConfigEntryState.NOT_LOADED)


async def test_history_counts_and_failure_are_read_without_regrading(
    hass, enable_custom_integrations, entry_data
):
    """Only current-context summaries leave the journal, with a distinct storage error."""
    entry, journal = await _journal(hass, entry_data)
    assert journal.capture(NOW)
    pending = deepcopy(journal.records[0])
    journal.records.extend(
        [
            {
                **pending,
                "id": "2026-11-09:evening",
                "result": {"status": "excluded", "reason_code": "grid_charging"},
            },
            {**pending, "id": "2026-11-08:evening", "context": "previous setup"},
        ]
    )
    before = deepcopy(journal.records)
    with (
        patch("custom_components.solar_sanity.services.dt_util.utcnow", return_value=NOW),
        patch.object(journal, "async_update", new=AsyncMock()) as update,
    ):
        history = (await check(hass, entry))["history"]
        assert history["counts"] == {
            "graded_nights": 0,
            "graded_mornings": 0,
            "pending": 1,
            "excluded": 1,
        }
        assert history["exclusions"][0]["reason"].startswith("Grid charging")
        journal.error = "journal_restore_failed"
        assert (await check(hass, entry))["history"]["status"] == "needs_attention"
        assert journal.records == before
        assert not update.called


async def test_setup_check_is_device_scoped_and_enforces_read_access(
    hass, enable_custom_integrations, entry_data
):
    """Permission to read one house does not confer access to the other house."""
    first = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data))
    second = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data, options=BATTERY))
    user = MagicMock(is_active=True)
    context = Context(user_id="synthetic-user")
    with patch.object(hass.auth, "async_get_user", new=AsyncMock(return_value=user)):
        user.permissions.check_entity.return_value = True
        assert (await check(hass, first, context))["battery"]["settings"] is None
        first_entities = {args.args[0] for args in user.permissions.check_entity.call_args_list}
        user.permissions.check_entity.side_effect = lambda entity, _: entity in first_entities
        with pytest.raises(Unauthorized):
            await check(hass, second, context)
        user.permissions.check_entity.side_effect = None
        assert (await check(hass, second, context))["battery"]["settings"]["capacity_kwh"] == 10
