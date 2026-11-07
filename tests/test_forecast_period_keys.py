"""Which providers' forecasts are re-keyed by period start, and on which releases.

Forecast.Solar stamps each value with the end of its period. Home Assistant
2026.11 began moving each value to the start of its period in the energy
platform this integration reads (core PR 188790). Before it, every
Forecast.Solar hour this integration archived and projected on sat an hour
late, which is what the reference installation's archive showed until its
owner upgraded.

The correction must apply on exactly the releases that need it. Applied twice,
on 2026.11 or later, it would move every hour an hour *early*. CI runs on
releases before 2026.11 only, so the version is patched to test both sides.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("homeassistant", reason="Home Assistant not installed")

from custom_components.solar_sanity import statistics_source
from custom_components.solar_sanity.statistics_source import (
    async_get_solar_forecasts,
    stamps_period_end,
)

PERIOD_END = {
    "2026-11-06T05:35:19+00:00": 0,
    "2026-11-06T06:00:00+00:00": 149,
    "2026-11-06T07:00:00+00:00": 420,
}
PERIOD_START = {
    "2026-11-06T05:35:19+00:00": 149,
    "2026-11-06T06:00:00+00:00": 420,
}


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ((2025, 11), True),
        ((2026, 10), True),
        ((2026, 11), False),
        ((2026, 12), False),
        # A minor version compared on its own would call this older than 2026.11.
        ((2027, 1), False),
    ],
)
def test_forecast_solar_is_re_keyed_only_before_2026_11(
    version: tuple[int, int], expected: bool
) -> None:
    with patch.object(statistics_source, "ha_version", return_value=version):
        assert stamps_period_end("forecast_solar") is expected


def test_other_providers_are_left_as_they_come() -> None:
    """Nothing documents another provider's keys as period ends."""
    with patch.object(statistics_source, "ha_version", return_value=(2025, 11)):
        assert stamps_period_end("solcast_solar") is False


def _fake_hass(domain: str) -> SimpleNamespace:
    entry = SimpleNamespace(domain=domain)
    return SimpleNamespace(config_entries=SimpleNamespace(async_get_entry=lambda _id: entry))


async def _fetch(domain: str, version: tuple[int, int]) -> dict:
    async def platform_forecast(_hass, _entry_id):
        return {"wh_hours": dict(PERIOD_END)}

    async def process(hass, _name, collect, **_kwargs):
        await collect(hass, domain, SimpleNamespace(async_get_solar_forecast=platform_forecast))

    with (
        patch(
            "homeassistant.helpers.integration_platform.async_process_integration_platforms",
            process,
        ),
        patch.object(statistics_source, "ha_version", return_value=version),
    ):
        return await async_get_solar_forecasts(_fake_hass(domain), ["provider"])


async def test_the_fetch_hands_every_reader_period_starts() -> None:
    """Corrected once, where the payload arrives, so the archive, the outlook
    and the forecast-tomorrow sensor cannot disagree about which hour is which."""
    fetched = await _fetch("forecast_solar", (2026, 10))

    assert fetched["provider"]["wh_hours"] == PERIOD_START


async def test_on_2026_11_the_payload_is_already_right_and_is_not_moved_again() -> None:
    fetched = await _fetch("forecast_solar", (2026, 11))

    assert fetched["provider"]["wh_hours"] == PERIOD_END
