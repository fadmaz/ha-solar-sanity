"""The canary for the whole integration seam.

``pytest-homeassistant-custom-component`` declares ``hass`` — and roughly twenty
other fixtures — as a plain ``@pytest.fixture`` on an ``async def`` generator.
pytest-asyncio's strict mode *ignores* an async fixture that carries no explicit
mark, so the fixture never runs and every test asking for it errors at setup
complaining about something unrelated. Marking the test by hand does not help,
because the fixture is what is unmarked.

That failure is loud but badly aimed: it looks like twenty broken tests rather
than one unset option. This file is the thing that says which it is.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant


def test_the_hass_fixture_yields_a_home_assistant_and_not_a_generator(
    hass: HomeAssistant,
) -> None:
    """If ``asyncio_mode`` is not "auto", this errors at setup rather than
    failing — which is exactly the signal that was missing."""
    assert isinstance(hass, HomeAssistant)


def test_the_mode_is_what_the_fixtures_require(request) -> None:
    """Names the cause directly, so a config regression reads as one line rather
    than as twenty confusing setup errors."""
    assert request.config.getini("asyncio_mode") == "auto"
