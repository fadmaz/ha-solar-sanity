"""Fixtures for tests that start a real Home Assistant.

Everything here is checked against ``pytest-homeassistant-custom-component``
0.13.316, which is what CI resolves — and resolves by accident rather than by
choice. 0.13.316 is the newest release that still declares
``Requires-Python >=3.13``; 0.13.317 and later need 3.14. CI runs 3.13, so pip
stops there, and 0.13.316's metadata is what pins ``homeassistant==2026.2.3``,
``pytest==9.0.0`` and ``pytest-asyncio==1.3.0`` underneath our own unbounded
floors. The day a 3.13-compatible release lands above it, all four numbers move
on their own.

**There is deliberately no autouse fixture here.** The obvious convenience —
wrapping ``enable_custom_integrations`` in ``@pytest.fixture(autouse=True)``, as
the upstream README does — would depend on ``hass``, and an autouse fixture that
depends on ``hass`` builds it before anything else in scope. That breaks
``recorder_mock`` for every test in the directory, because of the ordering rule
below. Explicit is a few more characters per test and cannot do that.

**The ordering rule.** ``recorder_mock`` must be requested *before* ``hass``::

    async def test_x(recorder_mock, hass, enable_custom_integrations) -> None:   # right
    async def test_x(hass, recorder_mock) -> None:                               # wrong

``recorder_db_url`` opens with a bare ``assert not hass_fixture_setup``, and the
``hass`` fixture appends to that list as its second statement. Get the order
wrong and the failure is ``assert not [True]`` — which names neither fixture.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.const import (
    CONF_CHANNELS,
    CONF_ENTITY_ID,
    CONF_GRID_IS_NET,
    CONF_HAS_BATTERY,
    CONF_LOAD_WHOLE_HOUSE,
    CONF_ORIGIN,
    CONF_ROLE,
    DOMAIN,
)

#: The reference installation's shape: five channels, no export meter, a
#: battery, and a consumption sensor its owner says covers the house. Chosen
#: because it is a real mapping rather than a tidy one — it exercises the open
#: boundary, which is the path most of this project's defects lived on.
CHANNELS: list[dict[str, str]] = [
    {CONF_ENTITY_ID: "sensor.generation", CONF_ROLE: "pv", CONF_ORIGIN: "user"},
    {CONF_ENTITY_ID: "sensor.consumption", CONF_ROLE: "load", CONF_ORIGIN: "user"},
    {CONF_ENTITY_ID: "sensor.from_grid", CONF_ROLE: "grid_import", CONF_ORIGIN: "user"},
    {CONF_ENTITY_ID: "sensor.into_battery", CONF_ROLE: "battery_charge", CONF_ORIGIN: "user"},
    {CONF_ENTITY_ID: "sensor.out_of_battery", CONF_ROLE: "battery_discharge", CONF_ORIGIN: "user"},
]


@pytest.fixture
def entry_data() -> dict[str, Any]:
    """A config entry payload the config flow would actually have produced."""
    return {
        CONF_CHANNELS: [dict(channel) for channel in CHANNELS],
        CONF_HAS_BATTERY: "yes",
        CONF_GRID_IS_NET: "no",
        CONF_LOAD_WHOLE_HOUSE: "yes",
    }


@pytest.fixture
def entry(entry_data: dict[str, Any]) -> MockConfigEntry:
    """An entry not yet added to any hass.

    Deliberately not added here. ``add_to_hass`` is synchronous and writes
    straight into ``hass.config_entries._entries``, so doing it in a fixture
    would require depending on ``hass`` — the exact thing the note at the top of
    this file says not to do.
    """
    return MockConfigEntry(domain=DOMAIN, data=entry_data, title="Solar Sanity")
