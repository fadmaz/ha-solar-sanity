"""The card reaches a dashboard, or it does not, and nothing else says which.

``frontend.py`` is the seam between this integration and Home Assistant's own
dashboard storage, and every branch in it exists because that storage moved
underneath us at least once: ``LovelaceData.mode`` became ``resource_mode`` in
2026.2, the resource collection has to be loaded before it will list anything,
and a YAML-mode installation cannot be registered into at all. None of it was
covered. A rename upstream would have shipped a card that silently never
appears, and the only trace would be a debug line nobody reads.

Every branch is exercised against stand-ins rather than a started Home
Assistant: the module touches ``hass`` only through ``data``, ``http``, ``bus``
and ``is_running``, so a real instance would prove nothing extra and cost the
whole fixture. It needs the module importable, which is why the suite skips
when Home Assistant is absent.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("homeassistant", reason="Home Assistant not installed")

from custom_components.solar_sanity.frontend import CARD_URL, async_register
from custom_components.solar_sanity.frontend import (
    _async_register_resource as register_resource,
)

VERSION = "0.25.1"
TARGET = f"{CARD_URL}?v={VERSION}"


class FakeResources:
    """Home Assistant's Lovelace resource collection, in the parts we touch.

    ``loaded`` starts false on purpose. The real collection returns an empty
    list from ``async_items`` until it has been loaded, which is the difference
    between updating the one resource that exists and creating a second one
    beside it every restart.
    """

    def __init__(self, items: list[dict[str, Any]] | None = None, *, loaded: bool = False) -> None:
        self._items = list(items or [])
        self.loaded = loaded
        self.load_calls = 0
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []

    async def async_load(self) -> None:
        self.load_calls += 1
        self.loaded = True

    def async_items(self) -> list[dict[str, Any]]:
        return list(self._items) if self.loaded else []

    async def async_create_item(self, payload: dict[str, Any]) -> None:
        self.created.append(payload)
        self._items.append({"id": f"id{len(self._items)}", **payload})

    async def async_update_item(self, item_id: str, changes: dict[str, Any]) -> None:
        self.updated.append((item_id, changes))
        for item in self._items:
            if item["id"] == item_id:
                item.update(changes)


def _hass(lovelace: Any) -> SimpleNamespace:
    return SimpleNamespace(data={"lovelace": lovelace} if lovelace is not None else {})


def _lovelace(resources: Any, *, mode: str | None = "storage", legacy: bool = False) -> Any:
    """A LovelaceData stand-in, on either side of the 2026.2 rename."""
    if legacy:
        return SimpleNamespace(mode=mode, resources=resources)
    return SimpleNamespace(resource_mode=mode, resources=resources)


class TestItRegistersTheResource:
    """The ordinary path: one resource, at this version, once."""

    async def test_it_creates_the_resource_when_none_exists(self) -> None:
        resources = FakeResources()
        await register_resource(_hass(_lovelace(resources)), VERSION)

        assert resources.created == [{"res_type": "module", "url": TARGET}]
        assert resources.updated == []

    async def test_it_loads_the_collection_before_reading_it(self) -> None:
        """Skip the load and every restart adds another copy of the card.

        ``async_items`` on an unloaded collection returns nothing, which reads
        exactly like "no card is registered" and is answered by registering one.
        """
        resources = FakeResources([{"id": "id0", "url": f"{CARD_URL}?v=0.1.0"}])

        await register_resource(_hass(_lovelace(resources)), VERSION)

        assert resources.load_calls == 1
        assert resources.created == []
        assert resources.updated == [("id0", {"url": TARGET})]

    async def test_an_already_loaded_collection_is_not_loaded_again(self) -> None:
        resources = FakeResources(loaded=True)
        await register_resource(_hass(_lovelace(resources)), VERSION)

        assert resources.load_calls == 0
        assert resources.created == [{"res_type": "module", "url": TARGET}]


class TestItConvergesOnOneResource:
    """A version bump must move the entry, not multiply it."""

    async def test_an_older_version_is_updated_in_place(self) -> None:
        resources = FakeResources([{"id": "abc", "url": f"{CARD_URL}?v=0.21.1"}], loaded=True)

        await register_resource(_hass(_lovelace(resources)), VERSION)

        assert resources.updated == [("abc", {"url": TARGET})]
        assert resources.created == []

    async def test_the_current_version_is_left_alone(self) -> None:
        """Restarts must not write. A no-op here is the common case."""
        resources = FakeResources([{"id": "abc", "url": TARGET}], loaded=True)

        await register_resource(_hass(_lovelace(resources)), VERSION)

        assert resources.updated == []
        assert resources.created == []

    async def test_other_peoples_resources_are_not_touched(self) -> None:
        other = {"id": "zzz", "url": "/hacsfiles/some-other-card/card.js"}
        resources = FakeResources([other], loaded=True)

        await register_resource(_hass(_lovelace(resources)), VERSION)

        assert resources.updated == []
        assert resources.created == [{"res_type": "module", "url": TARGET}]
        assert other in resources.async_items()

    async def test_a_resource_with_no_url_key_does_not_raise(self) -> None:
        """Nothing guarantees the shape of somebody else's stored resource."""
        resources = FakeResources([{"id": "zzz"}], loaded=True)

        await register_resource(_hass(_lovelace(resources)), VERSION)

        assert resources.created == [{"res_type": "module", "url": TARGET}]


class TestTheRenameIn20262:
    """``LovelaceData.mode`` became ``resource_mode``; both must work."""

    async def test_the_legacy_mode_attribute_is_still_read(self) -> None:
        resources = FakeResources(loaded=True)

        await register_resource(_hass(_lovelace(resources, legacy=True)), VERSION)

        assert resources.created == [{"res_type": "module", "url": TARGET}]

    async def test_yaml_mode_registers_nothing_under_either_name(self) -> None:
        for legacy in (False, True):
            resources = FakeResources(loaded=True)
            lovelace = _lovelace(resources, mode="yaml", legacy=legacy)

            await register_resource(_hass(lovelace), VERSION)

            assert resources.created == [], f"legacy={legacy}"
            assert resources.updated == [], f"legacy={legacy}"


class TestItRefusesQuietlyRatherThanRaising:
    """Setup must survive a dashboard component that is absent or half-built."""

    async def test_no_lovelace_at_all(self) -> None:
        await register_resource(_hass(None), VERSION)

    async def test_lovelace_without_a_resource_collection(self) -> None:
        await register_resource(_hass(_lovelace(None)), VERSION)

    async def test_neither_mode_attribute_present(self) -> None:
        """An unrecognised shape is not storage mode, so nothing is written."""
        resources = FakeResources(loaded=True)

        await register_resource(_hass(SimpleNamespace(resources=resources)), VERSION)

        assert resources.created == []


class TestItWaitsForHomeAssistantToStart:
    """Lovelace may not be loaded when ``async_setup`` runs."""

    async def test_a_running_instance_registers_immediately(self) -> None:
        resources = FakeResources(loaded=True)
        hass = SimpleNamespace(
            data={"lovelace": _lovelace(resources)},
            is_running=True,
            http=SimpleNamespace(async_register_static_paths=_noop),
            bus=SimpleNamespace(async_listen_once=_unexpected_listen),
        )

        await async_register(hass, VERSION)

        assert resources.created == [{"res_type": "module", "url": TARGET}]

    async def test_a_starting_instance_defers_to_the_started_event(self) -> None:
        resources = FakeResources(loaded=True)
        listened: list[str] = []
        tasks: list[Any] = []
        hass = SimpleNamespace(
            data={"lovelace": _lovelace(resources)},
            is_running=False,
            http=SimpleNamespace(async_register_static_paths=_noop),
            bus=SimpleNamespace(
                async_listen_once=lambda event, handler: (
                    listened.append(event),
                    handlers.append(handler),
                )
            ),
            async_create_task=tasks.append,
        )
        handlers: list[Any] = []

        await async_register(hass, VERSION)

        assert listened == ["homeassistant_started"]
        assert resources.created == [], "nothing may be written before the event fires"

        handlers[0](None)
        for coro in tasks:
            await coro

        assert resources.created == [{"res_type": "module", "url": TARGET}]


async def _noop(*_args: Any, **_kwargs: Any) -> None:
    return None


def _unexpected_listen(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("a running instance must not wait for the started event")
