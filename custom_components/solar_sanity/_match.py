"""Whole-word keyword matching for entity discovery.

Substring matching is not good enough here. ``"charge"`` is a substring of
``"discharge"``, so a naive match puts a discharge sensor in the charge slot and
silently swaps the two battery directions — a fault that still roughly balances
on a symmetric day, so it would never surface as one.

Tokenising rather than using a regex keeps the intent obvious and avoids
escaping a user-supplied entity name into a pattern.
"""

from __future__ import annotations

import re

_SPLIT = re.compile(r"[^a-z0-9]+")


def tokens(text: str) -> list[str]:
    """Lowercase word tokens, with punctuation and underscores discarded."""
    return [token for token in _SPLIT.split(text.lower()) if token]


def matches(keyword: str, haystack: str) -> bool:
    """Whether ``keyword`` appears as whole word(s) in ``haystack``.

    Multi-word keywords must appear consecutively, so ``"grid import"`` matches
    "Grid Import Power" but not "Grid Power, Import Total".
    """
    words = tokens(keyword)
    if not words:
        return False

    found = tokens(haystack)
    if len(words) == 1:
        return words[0] in found

    span = len(words)
    return any(found[i : i + span] == words for i in range(len(found) - span + 1))
