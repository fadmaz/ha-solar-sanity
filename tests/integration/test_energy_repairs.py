"""Source configuration faults are actionable; data-learning gaps are not Repairs."""

from types import SimpleNamespace

from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity import _async_retire_the_check
from custom_components.solar_sanity.const import DOMAIN
from custom_components.solar_sanity.repairs import sync_energy_issues


async def test_unsupported_energy_issue_survives_cleanup_and_clears_when_source_recovers(hass):
    """The legacy domain-wide sweep used to erase every new configuration issue."""
    entry = MockConfigEntry(domain=DOMAIN)
    spec = SimpleNamespace(key="pv", entity_id="sensor.synthetic_energy")
    coordinator = SimpleNamespace(specs=[spec], _unsupported_energy={"pv"})
    sync_energy_issues(hass, entry, coordinator)
    key = (DOMAIN, f"non_cumulative_energy_{entry.entry_id}_pv")
    assert key in ir.async_get(hass).issues
    await _async_retire_the_check(hass)
    assert key in ir.async_get(hass).issues
    coordinator._unsupported_energy.clear()
    sync_energy_issues(hass, entry, coordinator)
    assert key not in ir.async_get(hass).issues
