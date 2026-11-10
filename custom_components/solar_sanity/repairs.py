"""Actionable configuration issues, independent of transient data learning."""

from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

# The deleted energy-balance check used these codes as issue-ID prefixes.
# Never sweep every DOMAIN issue: new configuration issues must survive startup.
RETIRED_CODES = (
    "stuck_channel",
    "production_stalled",
    "unit_scale_1000",
    "cumulative_in_periodic_field",
    "signed_net_in_dedicated_slot",
    "signed_net_battery_slot",
    "channels_swapped",
    "duplicate_channel_pair",
    "simultaneous_opposing_flow",
    "channel_sign_inverted",
    "channel_never_positive",
    "double_counted_channel",
    "missing_storage_channel",
    "missing_export_channel",
    "missing_generation_channel",
    "partial_ct_coverage",
    "load_boundary_mismatch",
    "pv_measured_dc",
    "battery_measured_dc",
    "unmetered_standby",
    "correction_now_harmful",
    "unexplained_residual",
    "finding",
)


@callback
def sync_energy_issues(hass, entry, coordinator):
    """A wrong energy classification needs a source fix, not an invented period."""
    prefix = f"non_cumulative_energy_{entry.entry_id}_"
    wanted = {prefix + key for key in coordinator._unsupported_energy}
    for spec in coordinator.specs:
        issue_id = prefix + spec.key
        if issue_id not in wanted:
            continue
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="non_cumulative_energy",
            translation_placeholders={"entity_id": spec.entity_id},
        )
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith(prefix) and issue_id not in wanted:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


@callback
def remove_energy_issues(hass, entry):
    """Remove only this entry's current configuration issues on removal."""
    prefix = f"non_cumulative_energy_{entry.entry_id}_"
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id.startswith(prefix):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
