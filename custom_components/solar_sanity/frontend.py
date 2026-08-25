"""Serving and registering the bundled dashboard card.

HACS registers a repository under exactly one category, so this repo cannot be
both an integration and a dashboard plugin. Instead the integration ships the
card and registers it itself: one install, both halves, always version-matched.

Registration happens once per install (from ``async_setup``), not once per
config entry.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, callback

from .const import CARD_FILENAME, URL_BASE

_LOGGER = logging.getLogger(__name__)

CARD_URL = f"{URL_BASE}/{CARD_FILENAME}"


async def async_register(hass: HomeAssistant, version: str) -> None:
    """Serve the card and add it as a Lovelace resource.

    If Home Assistant has not finished starting, Lovelace may not be loaded yet,
    so registration waits for the started event rather than failing quietly.
    """
    await _async_register_static_path(hass)

    if hass.is_running:
        await _async_register_resource(hass, version)
        return

    @callback
    def _on_started(_event: Event) -> None:
        hass.async_create_task(_async_register_resource(hass, version))

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)


async def _async_register_static_path(hass: HomeAssistant) -> None:
    directory = Path(__file__).parent / "frontend"
    if not directory.is_dir():
        _LOGGER.debug("no bundled frontend directory at %s", directory)
        return

    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(URL_BASE, str(directory), False)]
        )
    except (RuntimeError, ValueError):
        # Already registered - happens on reload, and is harmless.
        _LOGGER.debug("static path %s already registered", URL_BASE)


async def _async_register_resource(hass: HomeAssistant, version: str) -> None:
    """Add or update the Lovelace resource entry.

    Only possible in storage mode. Users on YAML-mode dashboards must add the
    resource themselves, so we say so plainly rather than silently doing nothing.
    """
    lovelace: Any = hass.data.get("lovelace")
    if lovelace is None:
        _LOGGER.debug("lovelace not loaded; skipping resource registration")
        return

    # Home Assistant 2026.2 renamed LovelaceData.mode to resource_mode. Read
    # both, or the integration breaks on current releases.
    mode = getattr(lovelace, "resource_mode", None) or getattr(lovelace, "mode", None)
    resources = getattr(lovelace, "resources", None)
    if resources is None:
        return

    if mode != "storage":
        _LOGGER.info(
            "Dashboards are in YAML mode, so the Solar Sanity card cannot be "
            "registered automatically. Add this resource manually: %s (module)",
            CARD_URL,
        )
        return

    if hasattr(resources, "async_load") and not getattr(resources, "loaded", False):
        await resources.async_load()

    target = f"{CARD_URL}?v={version}"
    for item in resources.async_items():
        url = item.get("url", "")
        if not url.startswith(CARD_URL):
            continue
        if url == target:
            return
        await resources.async_update_item(item["id"], {"url": target})
        _LOGGER.debug("updated card resource to %s", target)
        return

    await resources.async_create_item({"res_type": "module", "url": target})
    _LOGGER.debug("registered card resource %s", target)
