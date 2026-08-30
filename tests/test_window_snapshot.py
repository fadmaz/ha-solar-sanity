"""The exported window and the replayer must agree about what the codes mean.

`coordinator` writes single-character codes for quality and provenance;
`tests/synth/replay.py` reads them back. They are deliberately two tables rather
than one import, because the replayer has to stay usable with Home Assistant
absent — which is most of this repository's test suite.

Two tables that must agree and are never compared is how a format quietly rots.
A renamed code would make every replay wrong in the same direction, silently,
which is worse than a replay that fails.
"""

from __future__ import annotations

import pytest

pytest.importorskip("homeassistant", reason="Home Assistant not installed")

from custom_components.solar_sanity.analysis.model import (
    BucketSource,
    Quality,
)
from custom_components.solar_sanity.coordinator import (
    _QUALITY_CODE,
    _SOURCE_CODE,
)
from tests.synth.replay import _QUALITY, _SOURCE


def test_the_quality_tables_are_exact_inverses() -> None:
    assert {code: quality for quality, code in _QUALITY_CODE.items()} == _QUALITY


def test_the_source_tables_are_exact_inverses() -> None:
    assert {code: source for source, code in _SOURCE_CODE.items()} == _SOURCE


def test_every_quality_the_engine_can_produce_has_a_code() -> None:
    """A `Quality` with no code is written as `?` and read back as a KeyError,
    so adding one to the enum must fail here rather than in somebody's file."""
    assert set(_QUALITY_CODE) == set(Quality)


def test_every_source_the_engine_can_produce_has_a_code() -> None:
    assert set(_SOURCE_CODE) == set(BucketSource)


def test_no_two_qualities_share_a_code() -> None:
    assert len(set(_QUALITY_CODE.values())) == len(_QUALITY_CODE)


def test_no_two_sources_share_a_code() -> None:
    assert len(set(_SOURCE_CODE.values())) == len(_SOURCE_CODE)


def test_quality_and_source_codes_do_not_collide() -> None:
    """They sit in adjacent columns of the same row. Sharing a character makes a
    transposed column readable rather than an error, which is the worst way for
    this to fail."""
    assert not set(_QUALITY_CODE.values()) & set(_SOURCE_CODE.values())
