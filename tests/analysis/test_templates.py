"""Every fault code must render with the fields its own renderer supplies.

``faults.render`` raises on a missing field, deliberately — a half-rendered
sentence with a stray ``{name}`` in it is worse than a crash. That is the right
call only if the crash happens here.

It did not. Four of the six snap-table entries — both half-coverage variants and
both unit-scale variants — needed fields the inferred renderer never provided,
so each raised ``KeyError`` at the moment it won. Inside Home Assistant that
takes the coordinator update with it and every entity goes unavailable, on
precisely the installations the product exists to help. Nothing caught it
because no test had ever driven those codes as far as a rendered finding.

This suite closes that by construction: it reads the placeholders out of every
template and checks them against the field set each renderer actually builds.
"""

from __future__ import annotations

import string

import pytest
from analysis.faults import _TEMPLATES, SNAP_TABLE, Code, render

#: What ``engine._render_hypothesis`` always puts in ``fields``.
HYPOTHESIS_FIELDS = {"days", "explained", "pct", "name"}

#: ...plus what ``engine._snap_fields`` derives per code.
SNAP_DERIVED: dict[str, set[str]] = {
    Code.PARTIAL_COVERAGE: {"fraction"},
    Code.UNIT_SCALE_1000: {"observed", "expected"},
}

#: ...plus what each structural probe carries in ``Hypothesis.extra``.
EXTRA_FIELDS: dict[str, set[str]] = {
    Code.MISSING_STORAGE: {"capacity_wh", "daily_kwh", "daily"},
    Code.MISSING_EXPORT: set(),
}

#: What ``engine._render_screen_hit`` passes through from ``ScreenHit.fields``.
SCREEN_FIELDS: dict[str, set[str]] = {
    Code.STUCK: {"name", "observed", "hours"},
    Code.UNIT_SCALE_1000: {"name", "observed", "expected"},
    Code.CUMULATIVE_IN_PERIODIC: {"name", "observed", "daily"},
    Code.SIGNED_NET_IN_DEDICATED: {"name"},
    Code.SIGNED_NET_BATTERY: {"name"},
    Code.SIMULTANEOUS_FLOW: {"name", "other", "count", "days"},
}

SAMPLES: dict[str, object] = {
    "name": "Grid import",
    "other": "Grid export",
    "hours": 7,
    "count": 61,
    "days": 12,
    "explained": 91.4,
    "pct": 18.2,
    "fraction": "half",
    "observed": 1234.5,
    "expected": 1.2,
    "capacity_wh": 9800.0,
    "daily_kwh": 9.8,
    "daily": 9.8,
    "gamma": 0.04,
    "watts": 45.0,
    "percent": 4.0,
    "correlation": 0.97,
    "loss": 3.9,
}


def placeholders(code: str) -> set[str]:
    """Field names a code's three templates interpolate."""
    found: set[str] = set()
    for text in _TEMPLATES[code]:
        for _, name, _, _ in string.Formatter().parse(text or ""):
            if name:
                found.add(name.split(".")[0].split("[")[0])
    return found


@pytest.mark.parametrize("snap", SNAP_TABLE, ids=lambda s: f"{s.code}-a{s.a}")
def test_every_snap_renders_from_what_the_engine_supplies(snap) -> None:
    """The regression: a winning hypothesis must never raise on the way out."""
    available = HYPOTHESIS_FIELDS | SNAP_DERIVED.get(snap.code, set())
    missing = placeholders(snap.code) - available

    assert not missing, (
        f"{snap.code} (a={snap.a}) needs {sorted(missing)}, which "
        "_render_hypothesis does not supply — it would raise KeyError the "
        "moment this hypothesis won"
    )


@pytest.mark.parametrize("code", sorted(EXTRA_FIELDS))
def test_structural_probes_render(code: str) -> None:
    missing = placeholders(code) - HYPOTHESIS_FIELDS - EXTRA_FIELDS[code]

    assert not missing, f"{code} needs {sorted(missing)}"


@pytest.mark.parametrize("code", sorted(SCREEN_FIELDS))
def test_every_screen_code_renders(code: str) -> None:
    missing = placeholders(code) - SCREEN_FIELDS[code]

    assert not missing, f"{code} needs {sorted(missing)} from its ScreenHit"


@pytest.mark.parametrize("code", sorted(_TEMPLATES))
def test_every_template_actually_formats(code: str) -> None:
    """Catches a bad format spec, which placeholder names alone would miss."""
    fields = {name: SAMPLES[name] for name in placeholders(code) if name in SAMPLES}
    unknown = placeholders(code) - set(SAMPLES)

    assert not unknown, f"{code} uses {sorted(unknown)}, which this suite has no sample for"

    headline, detail, fix = render(code, **fields)

    assert headline and detail
    assert "{" not in headline and "{" not in detail and "{" not in fix


def test_every_code_has_a_template() -> None:
    """A code with no template raises a KeyError one layer earlier."""
    declared = {
        value
        for name, value in vars(Code).items()
        if not name.startswith("_") and isinstance(value, str)
    }
    missing = sorted(declared - set(_TEMPLATES))

    assert not missing, f"no copy for {missing}"
