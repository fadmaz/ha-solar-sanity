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

#: Skipped wholesale rather than per-test when Home Assistant is missing.
#:
#: ``importorskip`` cannot help here: these modules need fixtures from
#: ``pytest_homeassistant_custom_component``, and a missing fixture is a
#: collection error rather than a skip. Refusing to collect them is the only
#: thing that leaves the rest of the suite runnable.
collect_ignore_glob: list[str] = []

if importlib.util.find_spec("homeassistant") is None:  # pragma: no cover - env
    collect_ignore_glob.append("integration/*")
