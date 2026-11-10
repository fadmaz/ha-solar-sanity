"""Read-only tools over the loaded installation's private in-memory data."""

from datetime import UTC, timedelta

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .analysis.history import history_rows
from .const import DOMAIN


async def coordinator_for(hass: HomeAssistant, call: ServiceCall):
    """Resolve exactly one loaded entry and enforce its entities' read permissions."""
    device = dr.async_get(hass).async_get(call.data["device_id"])
    if device is None:
        raise ServiceValidationError("Select a Solar Sanity installation.")
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id in device.config_entries
        and (DOMAIN, entry.entry_id) in device.identifiers
    ]
    if len(entries) != 1:
        raise ServiceValidationError("Select one Solar Sanity installation.")
    entry = entries[0]
    coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
    if coordinator is None or entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError("This Solar Sanity installation is not loaded.")
    if call.context.user_id is not None:
        user = await hass.auth.async_get_user(call.context.user_id)
        entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        owned = [e for e in entities if e.platform == DOMAIN and e.device_id == device.id]
        if (
            user is None
            or not user.is_active
            or not owned
            or any(not user.permissions.check_entity(e.entity_id, POLICY_READ) for e in owned)
        ):
            raise Unauthorized()
    return coordinator


@callback
def async_setup_tools(hass: HomeAssistant) -> None:
    """Register native response actions; none triggers capture, grading or control."""

    async def get_history(call: ServiceCall):
        coordinator = await coordinator_for(hass, call)
        journal = coordinator.journal
        now = dt_util.utcnow()
        since = now.astimezone(coordinator.time_zone or UTC).date() - timedelta(
            days=call.data["days"] - 1
        )
        return {
            "time_zone": hass.config.time_zone,
            "status": "error" if journal.error else "ok" if journal.records else "starting",
            "reason_code": journal.error,
            "records": history_rows(journal.records, since.isoformat(), journal.context()),
        }

    hass.services.async_register(
        DOMAIN,
        "get_forecast_history",
        get_history,
        schema=vol.Schema(
            {
                vol.Required("device_id"): cv.string,
                vol.Optional("days", default=28): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=90)
                ),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
