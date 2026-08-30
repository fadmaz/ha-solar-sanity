"""The exported window and the replayer must agree about what the codes mean.

`coordinator` writes single-character codes for quality and provenance;
`tests/synth/replay.py` reads them back. They are deliberately two tables rather
than one import, because the replayer has to stay usable with Home Assistant
absent — which is most of this repository's test suite.

Two tables that must agree and are never compared is how a format quietly rots.
A renamed code would make every replay wrong in the same direction, silently,
which is worse than a replay that fails.

Everything here compares enum *values*, never members. `pythonpath` carries both
`.` and `custom_components/solar_sanity`, so `analysis.model.Quality` and
`custom_components.solar_sanity.analysis.model.Quality` are two distinct classes
in one session — the coordinator imports one and the replayer the other, and
their members are unequal however identical they look. The first version of this
file compared members and failed in CI with
`{'S': <Quality.STALE: 'stale'>} != {'S': <Quality.STALE: 'stale'>}`, which is
the friendliest possible form of that trap. The `is` version of the same mistake
is silent.
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
    written = {code: quality.value for quality, code in _QUALITY_CODE.items()}

    assert written == {code: quality.value for code, quality in _QUALITY.items()}


def test_the_source_tables_are_exact_inverses() -> None:
    written = {code: source.value for source, code in _SOURCE_CODE.items()}

    assert written == {code: source.value for code, source in _SOURCE.items()}


def test_every_quality_the_engine_can_produce_has_a_code() -> None:
    """A `Quality` with no code is written as `?` and read back as a KeyError,
    so adding one to the enum must fail here rather than in somebody's file."""
    assert {q.value for q in _QUALITY_CODE} == {q.value for q in Quality}


def test_every_source_the_engine_can_produce_has_a_code() -> None:
    assert {s.value for s in _SOURCE_CODE} == {s.value for s in BucketSource}


def test_no_two_qualities_share_a_code() -> None:
    assert len(set(_QUALITY_CODE.values())) == len(_QUALITY_CODE)


def test_no_two_sources_share_a_code() -> None:
    assert len(set(_SOURCE_CODE.values())) == len(_SOURCE_CODE)


def test_quality_and_source_codes_do_not_collide() -> None:
    """They sit in adjacent columns of the same row. Sharing a character makes a
    transposed column readable rather than an error, which is the worst way for
    this to fail."""
    assert not set(_QUALITY_CODE.values()) & set(_SOURCE_CODE.values())
