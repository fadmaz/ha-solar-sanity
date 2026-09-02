"""A key named for one store must be read from that store.

``CONF_GUARANTEED_ANNUAL_KWH`` was written to ``entry.options`` by the only flow
that can write anything, and read from ``entry.data``. So the check returned
``None`` for every real installation — and ``None`` is also what "no guarantee
configured" returns, so the feature shipped, was documented and was tested while
nobody could turn it on and nothing anywhere went red. The integration test
seeded the value straight into ``data``, which is the one store no user can
reach.

The convention this pins is the repository's own: ``CONF_`` keys are what the
setup wizard gathered and live in ``entry.data``; ``OPT_`` keys are settings a
user changes afterwards and live in ``entry.options``. The prefix is not
decoration, it is the store.

Pure on purpose, and the reason is the defect itself. The integration tier needs
Home Assistant and does not run on most machines, so a mismatch there is found
by a user rather than by a suite. This reads the source instead, and fails in
the pull request that introduces one.
"""

from __future__ import annotations

import ast
import pathlib

COMPONENT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "solar_sanity"

STORES = ("data", "options")


def _reads(tree: ast.AST) -> list[tuple[str, str]]:
    """Every ``<x>.data`` or ``<x>.options`` lookup whose key is a named constant.

    A literal key is not a config key: ``hass.data.get("lovelace")`` and
    ``event.data.get("new_state")`` are unrelated uses of the same attribute
    name. Only ``ast.Name`` keys are collected, which is what every ``CONF_``
    and ``OPT_`` read looks like.
    """
    found: list[tuple[str, str]] = []

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr in STORES
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            found.append((node.func.value.attr, node.args[0].id))
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr in STORES
            and isinstance(node.slice, ast.Name)
        ):
            found.append((node.value.attr, node.slice.id))

    return found


def _offenders(prefix: str, store: str) -> list[tuple[str, str]]:
    return [
        (path.name, key)
        for path in sorted(COMPONENT.glob("*.py"))
        for read_store, key in _reads(ast.parse(path.read_text(encoding="utf-8")))
        if key.startswith(prefix) and read_store == store
    ]


def test_no_option_key_is_read_from_entry_data() -> None:
    """The defect, in the direction it actually happened."""
    wrong = _offenders("OPT_", "data")

    assert not wrong, (
        f"an OPT_ key read from entry.data: {wrong} — only the options flow can "
        "write it, so entry.data holds nothing for any real installation and the "
        "read returns None in silence"
    )


def test_no_configuration_key_is_read_from_entry_options() -> None:
    """The same silent miss in the other direction."""
    wrong = _offenders("CONF_", "options")

    assert not wrong, (
        f"a CONF_ key read from entry.options: {wrong} — the wizard writes it to "
        "entry.data, so this would read nothing for every installation set up "
        "through the interface"
    )


def test_the_walker_finds_the_reads_it_claims_to() -> None:
    """A checker nobody has watched find something is a checker that passes.

    Both assertions above are satisfied by a walker that returns nothing at all,
    which is exactly what a small change to Home Assistant's config-entry API
    would produce. This pins the two shapes it must keep recognising.
    """
    source = (
        "def f(entry, coordinator):\n"
        "    a = coordinator.entry.data.get(OPT_ONE)\n"
        "    b = entry.options[CONF_TWO]\n"
        "    c = entry.options.get(OPT_THREE, [])\n"
        "    d = hass.data.get('lovelace')\n"
    )

    found = _reads(ast.parse(source))

    assert ("data", "OPT_ONE") in found
    assert ("options", "CONF_TWO") in found
    assert ("options", "OPT_THREE") in found
    assert all(key.isidentifier() for _store, key in found)
    assert len(found) == 3, f"a literal key was collected as a config key: {found}"
