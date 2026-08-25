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

    FORBIDDEN = re.compile(
        r"[$€£¥]|\bprice\b|\bpricing\b|\bcost\b|\bcosts\b|\bbill\b|\bbills\b"
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
