"""Collection rules that have to hold whether Home Assistant is here or not.

Most of this suite is a test of pure arithmetic and must stay runnable with Home
Assistant absent — a guarantee ``TestPurity`` enforces on the package and this
file extends to the tests themselves. That is not a convenience. Home Assistant
cannot be installed on every machine this repository is worked on: recent
versions need Python 3.13, and on 3.12 pip resolves ``homeassistant 2025.1.4``,
nineteen months stale and wrong about precisely the statistics APIs this
integration lives on. A stale Home Assistant is worse than none, because tests
against it pass while telling you nothing.

So the split is deliberate. Everything under ``tests/integration`` needs a real
``hass`` and runs in CI, on 3.13, against the current release. Everything else
runs anywhere, and is where a defect should be caught if it possibly can be.

Modules already gated with ``pytest.importorskip("homeassistant")`` keep that
gate: they import Home Assistant but never start one, so they sit between the
two and belong to neither directory.
"""

from __future__ import annotations

import importlib.util
import pathlib

HAS_HOME_ASSISTANT = importlib.util.find_spec("homeassistant") is not None


def pytest_ignore_collect(collection_path: pathlib.Path) -> bool | None:
    """Refuse the integration directory outright when there is no Home Assistant.

    ``collect_ignore_glob`` is the obvious tool and the wrong one. It is consulted
    when a *file* is considered for collection, which is after the collector has
    already descended into the directory and imported its ``conftest.py`` — and
    that conftest imports ``pytest_homeassistant_custom_component`` at module
    scope. The result is a collection *error* rather than an exclusion, and the
    whole suite stops.

    This hook runs before the descent, so the directory is never entered.
    """
    if HAS_HOME_ASSISTANT:
        return None
    return collection_path.name == "integration" and collection_path.is_dir()
