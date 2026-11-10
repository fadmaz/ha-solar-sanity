"""Native response actions respect installation boundaries and read permissions."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import device_registry as dr

from custom_components.solar_sanity.const import DOMAIN
from tests.integration.test_outlook_journal import NOW, _journal


async def history(hass, device_id, **extra):
    return await hass.services.async_call(
        DOMAIN,
        "get_forecast_history",
        {"device_id": device_id, **extra},
        blocking=True,
        return_response=True,
    )


def device(hass, entry):
    return dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)}).id


async def test_real_action_returns_frozen_public_history_without_triggering_work(
    hass, enable_custom_integrations, entry_data
):
    """The UI reads the actual journal without regrading or exposing its inputs."""
    entry, journal = await _journal(hass, entry_data)
    journal.capture(NOW)
    with (
        patch("custom_components.solar_sanity.services.dt_util.utcnow", return_value=NOW),
        patch.object(journal, "async_update", new=AsyncMock()) as update,
    ):
        result = await history(hass, device(hass, entry))
    assert len(result["records"]) == 1
    assert result["records"][0]["issued_at"] == NOW.isoformat()
    assert "inputs" not in result["records"][0]
    assert not update.called


async def test_empty_history_and_unreadable_history_are_distinct(
    hass, enable_custom_integrations, entry_data
):
    """A failed restore must never claim that no predictions have been issued."""
    entry, journal = await _journal(hass, entry_data)
    assert (await history(hass, device(hass, entry)))["status"] == "starting"
    journal.error = "journal_restore_failed"
    assert (await history(hass, device(hass, entry)))["status"] == "error"


async def test_foreign_devices_are_rejected(hass, enable_custom_integrations, entry_data):
    """A guessed device ID cannot resolve another integration."""
    await _journal(hass, entry_data)
    with pytest.raises(ServiceValidationError):
        await history(hass, "not-a-solar-device")


async def test_a_missing_or_disallowed_user_cannot_read_private_history(
    hass, enable_custom_integrations, entry_data
):
    """Response services must enforce read access themselves."""
    entry, _ = await _journal(hass, entry_data)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            "get_forecast_history",
            {"device_id": device(hass, entry)},
            blocking=True,
            return_response=True,
            context=Context(user_id="unknown-user"),
        )


async def test_history_respects_the_original_local_checkpoint_date(
    hass, enable_custom_integrations, entry_data
):
    """A later time-zone change must not move the saved day to yesterday."""
    entry, journal = await _journal(hass, entry_data)
    journal.capture(NOW)
    with patch(
        "custom_components.solar_sanity.services.dt_util.utcnow",
        return_value=datetime(2027, 1, 1, tzinfo=UTC),
    ):
        assert (await history(hass, device(hass, entry), days=7))["records"] == []


@pytest.mark.parametrize("allowed", [True, False])
async def test_read_permissions_are_checked_for_authenticated_users(
    hass, enable_custom_integrations, entry_data, allowed
):
    """Authentication alone is not permission to read this installation."""
    entry, _ = await _journal(hass, entry_data)
    user = MagicMock(is_active=True)
    user.permissions.check_entity.return_value = allowed
    with patch.object(hass.auth, "async_get_user", new=AsyncMock(return_value=user)):
        call = hass.services.async_call(
            DOMAIN,
            "get_forecast_history",
            {"device_id": device(hass, entry)},
            blocking=True,
            return_response=True,
            context=Context(user_id="synthetic-user"),
        )
        if allowed:
            assert (await call)["status"] == "starting"
        else:
            with pytest.raises(Unauthorized):
                await call


async def test_an_unloaded_entry_cannot_serve_stale_runtime_data(
    hass, enable_custom_integrations, entry_data
):
    """Runtime data may remain attached after unload; its presence is not availability."""
    entry, _ = await _journal(hass, entry_data)
    device_id = device(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError):
        await history(hass, device_id)
