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
"""

from __future__ import annotations

import homeassistant.const

#: Below this, the statistics API this integration writes through is a different
#: shape and nothing tested against it transfers. Chosen as the release CI was
#: resolving when these tests were written, minus room to move.
OLDEST_USEFUL = (2026, 1)


def test_home_assistant_is_importable_here() -> None:
    assert homeassistant.const.__version__


def test_it_is_recent_enough_for_the_result_to_mean_anything() -> None:
    version = homeassistant.const.__version__
    year, month = (int(part) for part in version.split(".")[:2])

    assert (year, month) >= OLDEST_USEFUL, (
        f"Home Assistant {version} is older than these tests can speak about; "
        f"a pass here would not transfer to what the integration actually runs on"
    )
