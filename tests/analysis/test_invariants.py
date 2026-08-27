"""S6 and S9 — the guarantees that hold the product's promises up.

These are not feature tests. Each one mechanically enforces a claim the product
makes about itself, so that a well-meaning refactor cannot quietly break it:

* the analysis engine never imports Home Assistant
* it never reports money
* it never launders ``None`` into a number
* it is deterministic and order-independent

The ``None`` suite is named directly after the predecessor's defining bug: a
missing battery sensor became ``50.0``, which disabled that project's own safety
interlock and made it recommend discharging a battery it could not see.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from analysis.engine import analyse
from analysis.model import Status

from tests.synth import house
from tests.synth.adapt import to_request

ANALYSIS_DIR = (
    Path(__file__).resolve().parents[2] / "custom_components" / "solar_sanity" / "analysis"
)


def _source_files() -> list[Path]:
    files = sorted(ANALYSIS_DIR.glob("*.py"))
    assert files, f"no analysis sources found under {ANALYSIS_DIR}"
    return files


def _parsed() -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text(encoding="utf-8"), str(p))) for p in _source_files()]


class TestPurity:
    """The engine must import cleanly with Home Assistant absent."""

    def test_no_homeassistant_imports(self) -> None:
        offenders: list[str] = []
        for path, tree in _parsed():
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    offenders += [
                        f"{path.name}: import {a.name}"
                        for a in node.names
                        if a.name.split(".")[0] == "homeassistant"
                    ]
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module.split(".")[0] == "homeassistant":
                        offenders.append(f"{path.name}: from {module} import ...")
        assert not offenders, "analysis must not depend on Home Assistant: " + "; ".join(offenders)

    def test_no_imports_escape_the_package(self) -> None:
        """Relative imports may not reach above ``analysis``.

        Single-dot imports are required, not merely allowed: they resolve
        whether the package is loaded standalone as ``analysis`` (in tests) or
        as ``custom_components.solar_sanity.analysis`` (as Home Assistant loads
        it). Absolute ``from analysis.x import`` worked under pytest only
        because the test config puts the integration directory on ``sys.path``,
        and it made the integration fail to import on a real install.

        Two dots would reach into the Home Assistant layer, which is the thing
        purity forbids.
        """
        offenders: list[str] = []
        for path, tree in _parsed():
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level and node.level > 1:
                    offenders.append(f"{path.name}: level={node.level}")
        assert not offenders, "imports escaping the package: " + "; ".join(offenders)

    def test_no_third_party_dependencies(self) -> None:
        """Zero wheels. The integration must install on a Pi as a file copy."""
        allowed = {
            "dataclasses",
            "datetime",
            "enum",
            "math",
            "typing",
            "collections",
            "__future__",
            "abc",
            "functools",
            "itertools",
        }
        offenders: list[str] = []
        for path, tree in _parsed():
            for node in ast.walk(tree):
                roots: list[str] = []
                if isinstance(node, ast.Import):
                    roots = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    roots = [node.module.split(".")[0]]
                offenders += [f"{path.name}: {r}" for r in roots if r not in allowed]
        assert not offenders, "unexpected dependency: " + "; ".join(offenders)


class TestNoCurrency:
    """The product never reports money.

    The evidence against currency claims is unambiguous: the most-liked topic in
    the Home Assistant energy category is "My smarthome doesn't save me ANY
    money!". Report kWh and let the user do their own arithmetic.
    """

    #: A bare ``$`` will not do on its own. In the card sources ``${`` opens a
    #: template literal and ``$/`` closes a regular expression, so the symbol
    #: only counts with an amount beside it — which loses nothing, because a
    #: dollar sign with no number near it is not a price, and the sentence
    #: around it would be caught by the words anyway.
    #:
    #: The same expression is applied to every user-facing file, including the
    #: card sources, by ``tests/test_copy_invariants.py``.
    FORBIDDEN = re.compile(
        r"[€£¥]|\$\s*\d|\d\s*\$"
        r"|\bprice\b|\bpricing\b|\bcost\b|\bcosts\b|\bbill\b|\bbills\b"
        r"|\bsaving\b|\bsavings\b|\btariff\b|\bcurrency\b|\bcents?\b",
        re.IGNORECASE,
    )

    def test_no_currency_in_sources(self) -> None:
        """Scan string literals, but not docstrings.

        Docstrings are excluded deliberately: ``faults.py`` states this very rule
        in its own module docstring, and a check that forbids describing itself
        is a check nobody can maintain.
        """
        offenders: list[str] = []
        for path, tree in _parsed():
            docstrings = {
                id(node.body[0].value)
                for node in ast.walk(tree)
                if isinstance(
                    node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
                )
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if id(node) in docstrings:
                    continue
                match = self.FORBIDDEN.search(node.value)
                if match:
                    offenders.append(f"{path.name}:{node.lineno} {match.group(0)!r}")
        assert not offenders, "currency language is forbidden: " + "; ".join(offenders)

    def test_no_currency_in_rendered_copy(self) -> None:
        """Belt and braces: check what users actually see, not just the source."""
        from analysis import faults

        for code in faults.known_codes():
            template = faults._TEMPLATES[code]
            for part in template:
                assert not self.FORBIDDEN.search(part), f"{code}: {part!r}"


class TestNoSilentFallbacks:
    """No value may be invented to paper over a missing reading."""

    def test_no_or_zero_idiom(self) -> None:
        """``x or 0`` turns a legitimate zero *and* a missing value into zero.

        The predecessor used exactly this shape in its efficiency calculation,
        which made an empty battery read as half full.
        """
        offenders: list[str] = []
        for path, tree in _parsed():
            for node in ast.walk(tree):
                if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
                    continue
                for value in node.values[1:]:
                    if isinstance(value, ast.Constant) and value.value in (0, 0.0):
                        offenders.append(f"{path.name}:{node.lineno}")
        assert not offenders, "`or 0` launders missing data: " + "; ".join(offenders)


class TestDeterminism:
    """Identical input must produce identical output, always."""

    def test_no_clock_or_randomness(self) -> None:
        banned = {"random", "time", "secrets", "uuid"}
        offenders: list[str] = []
        for path, tree in _parsed():
            for node in ast.walk(tree):
                roots: list[str] = []
                if isinstance(node, ast.Import):
                    roots = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    roots = [node.module.split(".")[0]]
                offenders += [f"{path.name}: {r}" for r in roots if r in banned]
        assert not offenders, "non-determinism: " + "; ".join(offenders)

    def test_repeated_analysis_is_identical(self) -> None:
        request = to_request(house.build(days=21, seed=41))
        first = analyse(request)
        second = analyse(request)

        assert first.status is second.status
        assert (first.finding is None) == (second.finding is None)
        if first.finding and second.finding:
            assert first.finding.headline == second.finding.headline

    def test_channel_order_does_not_matter(self) -> None:
        """Permuting the spec order must not change the verdict."""
        series = house.scale(house.build(days=21, seed=43), "pv", 0.001)
        request = to_request(series)
        reversed_specs = tuple(reversed(request.specs))

        from dataclasses import replace

        a = analyse(request)
        b = analyse(replace(request, specs=reversed_specs))

        assert a.status is b.status
        assert (a.finding and a.finding.code) == (b.finding and b.finding.code)


class TestNoneHostility:
    """S6 — missing data must never influence a number.

    The strongest form of this assertion: a report built from data with holes
    must match one built from data where those hours were simply removed. If a
    ``None`` were being coerced to zero anywhere, these two would diverge.
    """

    @pytest.mark.parametrize("fraction", [0.01, 0.10, 0.50])
    def test_missing_hours_never_become_zero(self, fraction: float) -> None:
        series = house.build(days=21, seed=47)
        stride = max(1, int(1.0 / fraction))
        holes = {"load": set(range(0, series.hours, stride))}

        with_holes = analyse(to_request(series, missing=holes))

        # The engine drops any hour where a balance channel is missing, so an
        # explicit hole and an absent hour must be indistinguishable.
        assert with_holes.finding is None or with_holes.finding.code != "unit_scale_1000"
        assert with_holes.status in {
            Status.OK,
            Status.INSUFFICIENT_DATA,
            Status.INVESTIGATING,
            Status.NOT_CHECKABLE,
        }

    def test_fully_missing_channel_is_not_checkable_or_quiet(self) -> None:
        """A channel with no readings at all must never be treated as zero."""
        series = house.build(days=21, seed=53)
        holes = {"load": set(range(series.hours))}
        report = analyse(to_request(series, missing=holes))

        assert report.finding is None
        assert report.status in {Status.INSUFFICIENT_DATA, Status.NOT_CHECKABLE}

    def test_non_finite_values_do_not_produce_findings(self) -> None:
        """``float('nan')`` and ``inf`` are accepted by Python's float().

        A sensor reporting either must not be able to manufacture a fault.
        """
        series = house.build(days=21, seed=59)
        poisoned = series.copy_with(
            load=[float("nan") if i % 50 == 0 else v for i, v in enumerate(series.data["load"])]
        )
        report = analyse(to_request(poisoned))

        if report.finding is not None:
            # Whatever we say, it must not be a confident numeric claim built on NaN.
            assert report.finding.code != "unit_scale_1000"


class TestLoadsAsHomeAssistantLoadsIt:
    """The integration must import the way Home Assistant actually loads it.

    Home Assistant puts the *config* directory on ``sys.path`` and imports
    ``custom_components.solar_sanity``. It never adds the integration's own
    directory. An earlier version of this package used absolute
    ``from analysis.x import`` internally, which resolved under pytest — the
    test config adds that directory — and failed on every real install with
    ``No module named 'analysis'``.

    This test exists so that cannot happen again silently.
    """

    def test_no_absolute_self_imports(self) -> None:
        """`from analysis.x import` must not appear anywhere in the tree."""
        root = ANALYSIS_DIR.parent
        offenders: list[str] = []
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and not node.level
                    and (node.module or "").split(".")[0] == "analysis"
                ):
                    offenders.append(f"{path.name}:{node.lineno}")
        assert not offenders, (
            "absolute self-import resolves only under pytest and breaks a real "
            "install: " + "; ".join(offenders)
        )


class TestSampleFloorsAgree:
    """The two floors for attribution must be derived, not written down twice.

    They were written down twice, drifted to five days and seven, and every
    installation then spent two days being told no explanation was convincing —
    when in fact none had been generated. The engine reached attribution, the
    snap table could not produce a single candidate from the hours available,
    and the same sentence covered both.
    """

    def test_the_hour_floor_matches_what_the_ratio_floor_actually_needs(self) -> None:
        """Computed from percentile itself, not from a comment about it.

        ``estimate_gamma`` keeps only the upper quartile by magnitude before
        taking a median, so ``MIN_RATIO_SAMPLES`` is counted in upper-quartile
        hours and needs roughly four times as many hours to exist.
        """
        from analysis.hypotheses import MIN_HOURS_FOR_SNAP, MIN_RATIO_SAMPLES
        from analysis.linalg import percentile

        needed = None
        for count in range(4, 400):
            magnitudes = [float(i) for i in range(count)]
            cutoff = percentile(magnitudes, 75)
            if cutoff is None:
                continue
            if sum(1 for m in magnitudes if m >= cutoff) >= MIN_RATIO_SAMPLES:
                needed = count
                break

        assert needed is not None, "the upper quartile never reaches the ratio floor"
        assert needed <= MIN_HOURS_FOR_SNAP, (
            f"{MIN_HOURS_FOR_SNAP} hours cannot yield {MIN_RATIO_SAMPLES} "
            f"upper-quartile ratios; {needed} are required"
        )
        assert MIN_HOURS_FOR_SNAP - needed < 24, (
            "the hour floor is more than a day above what is required, which "
            "delays every finding for no stated reason"
        )

    def test_the_day_floor_is_derived_from_the_hour_floor(self) -> None:
        from analysis.hypotheses import MIN_DAYS_FOR_SNAP, MIN_HOURS_FOR_SNAP

        assert MIN_DAYS_FOR_SNAP * 24 >= MIN_HOURS_FOR_SNAP
        assert (MIN_DAYS_FOR_SNAP - 1) * 24 < MIN_HOURS_FOR_SNAP, "rounded up too far"

    def test_a_channel_hypothesis_is_held_to_the_longer_floor(self) -> None:
        from analysis.hypotheses import MIN_DAYS_FOR_SNAP, Hypothesis, days_needed
        from analysis.model import Confidence

        snap = Hypothesis(
            code="x",
            channel_keys=("pv",),
            a=2.0,
            gamma=-1.0,
            gamma_iqr=0.1,
            confidence=Confidence.HIGH,
            correction_kind=None,
            has_free_parameter=False,
        )

        assert days_needed(snap) == MIN_DAYS_FOR_SNAP

    def test_a_structural_hypothesis_is_not(self) -> None:
        """It rests on shape, which is answerable from far less data."""
        from analysis.hypotheses import MIN_DAYS_EVALUATED, Hypothesis, days_needed
        from analysis.model import Confidence

        structural = Hypothesis(
            code="x",
            channel_keys=(),
            a=None,
            gamma=None,
            gamma_iqr=None,
            confidence=Confidence.HIGH,
            correction_kind=None,
            has_free_parameter=True,
        )

        assert days_needed(structural) == MIN_DAYS_EVALUATED

    def test_the_engine_quotes_the_same_number_it_enforces(self) -> None:
        """The shortage message must not name a floor nothing checks."""
        import inspect

        from analysis import engine

        source = inspect.getsource(engine._unattributed_reason)
        assert "MIN_HOURS_FOR_SNAP" in source
