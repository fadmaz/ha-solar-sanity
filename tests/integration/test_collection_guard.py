"""A placeholder that proves the collection guard works.

Imports Home Assistant at module scope with no gate of its own. If
``tests/conftest.py`` stops excluding this directory on a machine without Home
Assistant, this file is what turns that into a loud collection error instead of
a quiet loss of coverage.
"""

from __future__ import annotations

import homeassistant


def test_home_assistant_is_importable_here() -> None:
    assert homeassistant.__version__
