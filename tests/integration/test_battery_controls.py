"""Configured power, the native preview and the saved journal share a projection."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.core import Context
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.analysis.outlook import BatteryControls
from custom_components.solar_sanity.const import CONF_FORECAST_ENTRIES, DOMAIN
from custom_components.solar_sanity.const import OPT_BATTERY_CHARGE_POWER_LIMIT_W as LIMIT
from custom_components.solar_sanity.const import OPT_INVERTER_STANDBY_W as STANDBY
from tests.integration.test_battery_outlook import BATTERY, SOC, _loaded, _week_of_history
from tests.integration.test_outlook_journal import NOW, _journal
from tests.integration.test_simulation_service import setup
from tests.integration.test_tools_services import device, history


async def preview(hass, entry, context=None, **changes):
    return await hass.services.async_call(
        DOMAIN,
        "preview_battery_settings",
        {
            "device_id": device(hass, entry),
            "projection_id": entry.runtime_data.coordinator.battery.state.projection_id,
            **changes,
        },
        blocking=True,
        return_response=True,
        context=context,
    )


def publish(hass, entry):
    """Publish from one measured profile and one synthetic forecast at a fixed issue time."""
    hass.states.async_set(SOC, "80", {"unit_of_measurement": "%"})
    _week_of_history(entry)
    coordinator = entry.runtime_data.coordinator
    for bucket in coordinator.buckets:
        if bucket.wh["pv"] > 0:
            bucket.wh["battery_charge"] = 1800
        else:
            bucket.wh["battery_discharge"] = 650
    hour = NOW.replace(minute=0)
    coordinator.forecast_points = {
        "synthetic": {
            hour + timedelta(hours=i): 3.0 if 6 <= (hour.hour + i) % 24 < 18 else 0.0
            for i in range(48)
        }
    }
    coordinator.forecast_captured_at = NOW
    coordinator.battery.refresh(force=True, now=NOW)
    return coordinator.battery.state


async def options(hass, entry, values):
    form = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {**BATTERY, **values}
    )
    await hass.async_block_till_done()
    return result


async def test_preview_and_live_controls_match_across_reload(
    hass, enable_custom_integrations, entry_data
):
    """Saving a lower limit and explicit zero standby reproduces the frozen preview."""
    entry_data[CONF_FORECAST_ENTRIES] = ["synthetic"]
    with patch("custom_components.solar_sanity.services.dt_util.utcnow", return_value=NOW):
        entry, journal = await _journal(hass, entry_data)
        state = publish(hass, entry)
        assert state.outlook is not None
        assert state.battery.max_charge_w == 1800
        assert state.battery.standby_w == pytest.approx(98)
        context = journal.context()
        assert journal.capture(NOW)
        await journal.async_save()
        inputs = deepcopy(journal.records[0]["inputs"])
        result = await preview(hass, entry, **{LIMIT: 900, STANDBY: 0})
        assert journal.records[0]["inputs"] == inputs
        assert entry.runtime_data.coordinator.battery.state is state
        assert result["current"]["lowest_pct"] != result["proposed"]["lowest_pct"]
        assert result["current"]["full_at"] != result["proposed"]["full_at"]
        assert result["proposed"]["controls"][STANDBY]["source"] == "manual"

        assert (await options(hass, entry, {LIMIT: 900, STANDBY: 0}))[
            "type"
        ] is FlowResultType.CREATE_ENTRY
        assert entry.options[STANDBY] == 0
        manual = publish(hass, entry)
        manual_journal = entry.runtime_data.coordinator.journal
        assert manual_journal.context() != context
        assert manual.inputs["battery"]["max_charge_w"] == 900
        assert manual.inputs["battery"]["standby_w"] == 0
        assert manual.outlook.lowest_pct == result["proposed"]["lowest_pct"]
        assert manual.outlook.full_at.isoformat() == result["proposed"]["full_at"]
        assert (await preview(hass, entry))["proposed"] == result["current"]
        assert manual_journal.records[0]["inputs"] == inputs
        assert (await history(hass, device(hass, entry)))["records"][0]["previous_setup"]
        assert await manual_journal._grade_pending(NOW + timedelta(hours=13))
        assert manual_journal.records[0]["result"]["reason_code"] == "context_changed"

        # Clearing one field keeps the other. Clearing both restores the original identity.
        await options(hass, entry, {LIMIT: 900})
        assert STANDBY not in entry.options and entry.options[LIMIT] == 900
        await options(hass, entry, {})
        assert LIMIT not in entry.options
        assert entry.runtime_data.coordinator.journal.context() == context


async def test_solar_only_hides_controls_and_has_no_preview(
    hass, enable_custom_integrations, entry
):
    """A solar-only entry still configures normally and has no valid battery basis."""
    await _loaded(hass, entry)
    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert LIMIT not in form["data_schema"].schema
    assert STANDBY not in form["data_schema"].schema
    with pytest.raises(ServiceValidationError, match="No current valid"):
        await preview(hass, entry, projection_id="unknown")


async def test_configure_rejects_a_zero_charge_limit(hass, enable_custom_integrations, entry_data):
    """The options flow keeps the previous value when the proposed limit is invalid."""
    entry = await _loaded(
        hass, MockConfigEntry(domain=DOMAIN, data=entry_data, options={**BATTERY, LIMIT: 1500})
    )
    result = await options(hass, entry, {LIMIT: 0})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"][LIMIT] == "invalid_charge_limit"
    assert entry.options[LIMIT] == 1500


@pytest.mark.parametrize(
    ("key", "value"), [(LIMIT, 0), (LIMIT, True), (STANDBY, -1), (STANDBY, float("inf"))]
)
async def test_bad_manual_power_is_rejected(
    hass, enable_custom_integrations, entry_data, key, value
):
    """Invalid input cannot be reinterpreted as Auto or an explicit number."""
    entry, _, _ = await setup(hass, entry_data)
    with pytest.raises(vol.Invalid):
        await preview(hass, entry, **{key: value})


async def test_preview_checks_permission_freshness_and_device_basis(
    hass, enable_custom_integrations, entry_data
):
    """A caller can preview only the current projection from a readable installation."""
    entry, journal, state = await setup(hass, entry_data)
    state.controls = BatteryControls()
    other = await _loaded(hass, MockConfigEntry(domain=DOMAIN, data=entry_data, options=BATTERY))
    other.runtime_data.coordinator.battery.state = deepcopy(state)
    other.runtime_data.coordinator.battery.state.projection_id = "other"
    other.runtime_data.coordinator.forecast_captured_at = NOW
    user = MagicMock(is_active=True)
    user.permissions.check_entity.return_value = False
    with (
        patch("custom_components.solar_sanity.services.dt_util.utcnow", return_value=NOW),
        patch.object(hass.auth, "async_get_user", new=AsyncMock(return_value=user)),
    ):
        with pytest.raises(Unauthorized):
            await preview(hass, entry, context=Context(user_id="synthetic-user"))
        with pytest.raises(ServiceValidationError, match="projection has changed"):
            await preview(hass, other, projection_id=state.projection_id)
        journal.coordinator.forecast_captured_at = NOW - timedelta(hours=4)
        with pytest.raises(ServiceValidationError, match="three hours"):
            await preview(hass, entry)
