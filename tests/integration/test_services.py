"""The two services, registered on a real hass rather than asserted about.

``tests/test_declaration_joins.py`` already proves the three-way join between
``services.yaml`` and both string files, purely and in a second. This is the
other half of that: whether the names in those files are the names actually
registered, and whether calling one does something.

The join test cannot catch a typo in the Python, and the Python cannot catch a
missing entry in the YAML. Between them there is a gap exactly one rename wide.

Services here are registered globally in ``async_setup`` rather than per entry,
which is why two installations must not fight over them — and why removing one
must not take the services away from the other.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.const import DOMAIN

SERVICES = ("validate_now", "export_report")


async def test_both_services_are_registered_under_the_names_the_yaml_declares(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    for service in SERVICES:
        assert hass.services.has_service(DOMAIN, service), (
            f"{service} is declared in services.yaml and both string files but "
            f"is not registered — the join test cannot see a typo in the Python"
        )


async def test_nothing_is_registered_that_the_yaml_does_not_declare(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """The other direction, which hassfest also fails the build over.

    A service registered and undeclared has no description, no field hints and
    no translation — it exists in the developer tools as a bare name.
    """
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registered = set(hass.services.async_services().get(DOMAIN, {}))

    assert registered == set(SERVICES), (
        f"registered but undeclared: {sorted(registered - set(SERVICES))}; "
        f"declared but unregistered: {sorted(set(SERVICES) - registered)}"
    )


async def test_export_report_returns_something_when_called(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry: MockConfigEntry,
) -> None:
    """Registered is not the same as working.

    ``export_report`` returns a response, so it is the one of the two that can
    be checked without waiting for an analysis cycle. A service that raises on
    call is registered exactly as convincingly as one that does not.
    """
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    response = await hass.services.async_call(
        DOMAIN, "export_report", {}, blocking=True, return_response=True
    )

    assert response is not None


async def test_removing_one_installation_leaves_the_services_registered(
    hass: HomeAssistant,
    enable_custom_integrations: None,
    entry_data: dict,
) -> None:
    """They are global, so tearing one entry down must not disarm the other.

    The symmetric failure — registering per entry and unregistering on the
    first removal — is invisible until somebody with two installations deletes
    one and finds their automations silently stop firing.
    """
    first = MockConfigEntry(domain=DOMAIN, data=entry_data, title="House")
    second = MockConfigEntry(domain=DOMAIN, data=entry_data, title="Barn")
    for config_entry in (first, second):
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_remove(first.entry_id)
    await hass.async_block_till_done()

    for service in SERVICES:
        assert hass.services.has_service(DOMAIN, service)
