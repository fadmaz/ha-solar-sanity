"""Shared load profiles for one installation, independent of the forecast."""

import asyncio
import json
import logging
from pathlib import Path
from uuid import uuid4

import voluptuous as vol
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .load_planning import load_duration, load_power

_LOGGER = logging.getLogger(__name__)
_MAX_PROFILES = 20
_PROFILES_VERSION = 1


def _name(value: str) -> str:
    if not 1 <= len(value := value.strip()) <= 60:
        raise vol.Invalid("Enter a name of 1 to 60 characters.")
    return value


LOAD_PROFILE_FIELDS = {
    vol.Required("name"): vol.All(str, _name),
    vol.Required("power_w"): load_power,
    vol.Required("duration_minutes"): load_duration,
}


def profile_revision(value: int) -> int:
    if type(value) is not int or value < 0:
        raise vol.Invalid("Use the revision returned by List saved loads.")
    return value


_SAVED_PROFILE = vol.Schema(
    {**LOAD_PROFILE_FIELDS, vol.Required("id"): vol.All(str, vol.Length(min=1))}
)
_SAVED_PROFILES = vol.Schema(vol.All([_SAVED_PROFILE], vol.Length(max=_MAX_PROFILES)))


def load_profile_store(hass: HomeAssistant, entry_id: str) -> Store[dict]:
    return Store(
        hass,
        _PROFILES_VERSION,
        f"{DOMAIN}.load_profiles.{entry_id}",
        private=True,
        atomic_writes=True,
    )


def _read_store(path: str, key: str) -> dict | None:
    # Store.async_load may move a malformed file aside or rewrite a newer minor
    # version. Read the envelope without migration to preserve unknown data.
    try:
        envelope = json.loads(Path(path).read_bytes())
    except FileNotFoundError:
        return None
    if (
        not isinstance(envelope, dict)
        or type(envelope.get("version")) is not int
        or envelope["version"] != _PROFILES_VERSION
        or envelope.get("minor_version", 1) != 1
        or envelope.get("key") != key
        or not isinstance(envelope.get("data"), dict)
    ):
        raise ValueError("Unrecognized load profile storage envelope")
    return envelope["data"]


class LoadProfiles:
    """Publish a revised list only after its private Store write is verified."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store = load_profile_store(hass, entry_id)
        self._lock = asyncio.Lock()
        self._profiles: dict[str, dict] = {}
        self._revision = 0
        self._storage_error = False
        self.accepting_writes = True

    async def async_restore(self) -> None:
        try:
            saved = await self._async_read()
            if saved is None:
                return
            revision = profile_revision(saved["revision"])
            items = _SAVED_PROFILES(saved["profiles"])
            profiles = {item["id"]: item for item in items}
            if len(profiles) != len(items) or len(
                {item["name"].casefold() for item in items}
            ) != len(items):
                raise ValueError("Duplicate saved load profiles")
            self._profiles = profiles
            self._revision = revision
        except (OSError, ValueError, KeyError, vol.Invalid):
            self._storage_error = True
            _LOGGER.exception("Could not restore saved load profiles")

    async def _async_read(self) -> dict | None:
        return await self.hass.async_add_executor_job(
            _read_store, self._store.path, self._store.key
        )

    def view(self) -> dict:
        return {
            "revision": self._revision,
            "profiles": []
            if self._storage_error
            else [dict(item) for item in self._profiles.values()],
            "storage_error": self._storage_error,
        }

    async def async_quiesce(self) -> None:
        """Reject new writes and wait for any write admitted before unloading."""
        self.accepting_writes = False
        async with self._lock:
            pass

    async def async_change(self, revision: int, profile_id: str | None, load: dict | None) -> dict:
        """Keep an admitted write and its lock alive if the caller disconnects."""
        return await asyncio.shield(
            self.hass.async_create_task(
                self._async_change(revision, profile_id, load), "save load profiles"
            )
        )

    async def _async_change(self, revision: int, profile_id: str | None, load: dict | None) -> dict:
        async with self._lock:
            if not self.accepting_writes or self.hass.state is not CoreState.running:
                raise ServiceValidationError("Wait for the installation to finish loading.")
            if self._storage_error:
                raise ServiceValidationError("Saved load storage is unavailable.")
            if revision != self._revision:
                raise ServiceValidationError(
                    "Saved loads have changed. Review the latest list and try again."
                )
            if profile_id is not None and profile_id not in self._profiles:
                raise ServiceValidationError("This saved load does not exist in the installation.")

            profiles = self._profiles.copy()
            if load is None:
                del profiles[profile_id]
            else:
                if profile_id is None and len(profiles) >= _MAX_PROFILES:
                    raise ServiceValidationError("This installation already has 20 saved loads.")
                if any(
                    key != profile_id and item["name"].casefold() == load["name"].casefold()
                    for key, item in profiles.items()
                ):
                    raise ServiceValidationError("A saved load already uses this name.")
                profile_id = profile_id or uuid4().hex
                profiles[profile_id] = {"id": profile_id, **load}

            payload = {"revision": self._revision + 1, "profiles": list(profiles.values())}
            try:
                await self._store.async_save(payload)
                # Core can log a failed write without raising it to the caller.
                if await self._async_read() != payload:
                    raise OSError("The saved loads write could not be verified")
            except (HomeAssistantError, OSError, ValueError, TypeError) as error:
                self._storage_error = True
                _LOGGER.exception("Could not write saved load profiles")
                raise ServiceValidationError("Could not save the list of loads.") from error
            self._profiles = profiles
            self._revision = payload["revision"]
            return self.view()
