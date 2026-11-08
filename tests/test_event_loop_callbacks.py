"""Everything handed to Home Assistant to call later runs on its event loop.

Home Assistant decides where to run a function from how it is written. An
``async def`` or an ``@callback`` runs on the event loop; anything else is sent
to a worker thread. From a worker thread ``async_write_ha_state`` raises for a
custom integration, so a plain ``def`` given to a timer fails every time it
fires, and the error is logged far from the code that caused it.

That is how the battery outlook's sensors went unwritten from 0.1.5 to 0.31.0.
The thirty-second and five-minute ticks were plain functions: the outlook was
recomputed in the worker thread, and every write after it raised. The sensors
moved only at setup and on the six-hourly refresh. On 2026-11-08 the reference
installation's diagnostics held a projection computed at 12:17 UTC while its
device page showed the sensors unavailable, and its logbook showed the
outlook's times changing at 22:14:50 and 04:14:51, six hours apart and nowhere
between. The one guard was a test that the tick's source mentioned the
notifier, which it did.

Parsed rather than imported, so this runs without Home Assistant installed.
"""

from __future__ import annotations

import ast
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "solar_sanity"

#: Each scheduling helper, and the position of the function it will call.
SCHEDULERS: dict[str, int] = {
    "async_track_time_interval": 1,
    "async_track_time_change": 1,
    "async_track_utc_time_change": 1,
    "async_track_point_in_time": 1,
    "async_track_point_in_utc_time": 1,
    "async_track_state_change_event": 2,
    "async_call_later": 2,
    "async_listen": 1,
    "async_listen_once": 1,
}
#: The same function passed by keyword.
CALLABLE_KEYWORDS = frozenset({"action", "listener"})


def _name_of(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _target(call: ast.Call, position: int) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg in CALLABLE_KEYWORDS:
            return keyword.value
    return call.args[position] if len(call.args) > position else None


def _on_the_loop(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if isinstance(function, ast.AsyncFunctionDef):
        return True
    for decorator in function.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "callback":
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr == "callback":
            return True
    return False


def scheduled(source: str) -> list[tuple[str, bool]]:
    """Every function this source hands to a scheduler, and whether it runs on the loop.

    Resolved by name within the module, which is where every one of them is
    defined. A name that resolves to nothing, a lambda or a partial counts as
    off the loop: none of them can carry ``@callback``, so Home Assistant sends
    them to a thread.
    """
    tree = ast.parse(source)
    defined: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.setdefault(node.name, []).append(node)

    found: list[tuple[str, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        position = SCHEDULERS.get(_name_of(node) or "")
        if position is None:
            continue
        target = _target(node, position)
        if isinstance(target, ast.Name):
            name = target.id
        elif isinstance(target, ast.Attribute):
            name = target.attr
        else:
            found.append((ast.unparse(target) if target is not None else "<missing>", False))
            continue
        candidates = defined.get(name, [])
        found.append((name, bool(candidates) and all(_on_the_loop(f) for f in candidates)))
    return found


def _component_files() -> list[Path]:
    return sorted(COMPONENT.rglob("*.py"))


class TestEverythingScheduledRunsOnTheEventLoop:
    """A plain function given to Home Assistant runs in a thread and cannot write state."""

    def test_every_scheduled_function_is_async_or_a_callback(self) -> None:
        """The failure this guards was silent for thirty releases: the error is
        raised in a worker thread and logged as a future nobody awaited."""
        off_the_loop = [
            f"{path.relative_to(COMPONENT)}: {name}"
            for path in _component_files()
            for name, on_loop in scheduled(path.read_text(encoding="utf-8"))
            if not on_loop
        ]
        assert not off_the_loop, (
            "these run in a worker thread, where async_write_ha_state raises; "
            f"make them async or decorate them with @callback: {off_the_loop}"
        )

    def test_the_walk_finds_the_ticks_it_exists_for(self) -> None:
        """A walk that matched nothing would pass forever. These are the
        functions the outlook and the archive depend on."""
        names = {
            name
            for path in _component_files()
            for name, _ in scheduled(path.read_text(encoding="utf-8"))
        }
        assert {"_tripwire", "_sample", "_capture", "_changed", "_async_power_changed"} <= names

    def test_a_plain_function_given_to_a_timer_is_caught(self) -> None:
        """The shape of the 0.31.0 tick, which this test would have failed."""
        source = (
            "def setup(hass, coordinator):\n"
            "    def _tick(_now):\n"
            "        coordinator.notify_live_entities()\n"
            "    async_track_time_interval(hass, _tick, INTERVAL)\n"
        )
        assert scheduled(source) == [("_tick", False)]

    def test_a_callback_or_a_coroutine_passes(self) -> None:
        source = (
            "def setup(hass):\n"
            "    @callback\n"
            "    def _tick(_now):\n"
            "        pass\n"
            "    async def _capture(_now):\n"
            "        pass\n"
            "    async_track_time_interval(hass, _tick, INTERVAL)\n"
            "    async_track_time_interval(hass, action=_capture, interval=INTERVAL)\n"
        )
        assert scheduled(source) == [("_tick", True), ("_capture", True)]

    def test_a_lambda_is_never_on_the_loop(self) -> None:
        """A lambda cannot carry @callback, so Home Assistant always threads it."""
        source = "hass.bus.async_listen_once(EVENT, lambda _event: None)\n"
        assert scheduled(source) == [("lambda _event: None", False)]
