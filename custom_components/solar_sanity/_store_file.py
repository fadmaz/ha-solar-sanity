"""Read a Home Assistant Store without recovering or migrating its file."""

import json
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store


def store_data(envelope: Any, store: Store[dict]) -> dict:
    minor = envelope.get("minor_version", 1) if isinstance(envelope, dict) else None
    if (
        not isinstance(envelope, dict)
        or type(envelope.get("version")) is not int
        or envelope["version"] != store.version
        or type(minor) is not int
        or minor != store.minor_version
        or envelope.get("key") != store.key
        or not isinstance(envelope.get("data"), dict)
    ):
        raise ValueError("Unrecognized storage envelope")
    return envelope["data"]


def _read(store: Store[dict]) -> dict | None:
    try:
        raw = Path(store.path).read_bytes()
    except FileNotFoundError:
        return None
    # Store.async_load may rename malformed files or rewrite unknown schemas.
    return store_data(json.loads(raw), store)


async def async_read_store(hass: HomeAssistant, store: Store[dict]) -> dict | None:
    """Read the durable copy including its envelope, bypassing the preload cache."""
    return await hass.async_add_executor_job(_read, store)
