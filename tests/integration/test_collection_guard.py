"""A guard that proves this directory is reached where Home Assistant exists.

Imports Home Assistant at module scope with no gate of its own. If
``tests/conftest.py`` ever stops excluding this directory on a machine without
Home Assistant, that becomes a loud collection error rather than a quiet loss of
coverage — and if the exclusion is ever left switched on where Home Assistant
*is* installed, this file stops running and its absence from the CI count says
so.

The version assertion is not decoration. The whole reason integration tests are
CI-only is that Python 3.12 resolves ``homeassistant 2025.1.4``, nineteen months
stale and wrong about the statistics APIs this integration is built on. A test
run against that would pass and mean nothing, so the floor is asserted rather
than assumed.

The floor is read from ``hacs.json`` rather than written here. It used to be a
hand-typed ``(2026, 1)`` — the release CI happened to be resolving when this
file was written — which made the number users are held to and the number CI
asserts two separate facts, free to drift apart. They did: the declared floor
said 2025.1 while this said 2026.1, and neither was the truth.
"""

from __future__ import annotations

import json
import pathlib

import homeassistant.const

HACS = pathlib.Path(__file__).resolve().parents[2] / "hacs.json"


def _declared_floor() -> tuple[int, int]:
    """The minimum this repository promises users, from the file promising it."""
    declared = json.loads(HACS.read_text(encoding="utf-8"))["homeassistant"]
    year, month = (int(part) for part in declared.split(".")[:2])
    return year, month


def test_home_assistant_is_importable_here() -> None:
    assert homeassistant.const.__version__


def test_it_is_recent_enough_for_the_result_to_mean_anything() -> None:
    version = homeassistant.const.__version__
    running = tuple(int(part) for part in version.split(".")[:2])

    assert running >= _declared_floor(), (
        f"Home Assistant {version} is below the floor hacs.json declares; "
        f"a pass here would not transfer to what the integration actually runs on"
    )
