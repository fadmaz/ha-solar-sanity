"""The rules that hold for every module in ``analysis/``, engine or not.

These are not feature tests. Each one mechanically enforces a claim the product
makes about itself, so that a well-meaning refactor cannot quietly break it:

* the analysis package never imports Home Assistant
* it never reports money
* it never launders ``None`` into a number
* it reads no clock and draws no random numbers
* it imports itself the way Home Assistant loads it

Kept apart from ``test_invariants.py``, which also holds the energy-balance
engine's own invariants and imports it: these rules apply to the battery
outlook and the forecast scoring as much as to the engine, and must not depend
on it to run.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

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
        r"[€£¥]|\$\s*\d|\d\s*\$(?!\{)"
        r"|\bprice\b|\bpricing\b|\bcost\b|\bcosts\b|\bbill\b|\bbills\b"
        r"|\bsaving\b|\bsavings\b|\btariff\b|\bcurrency\b|\bcents?\b",
        re.IGNORECASE,
    )

    def test_no_currency_in_sources(self) -> None:
        """Scan string literals, but not docstrings.

        Docstrings are excluded deliberately: a module may state this very rule
        in its own docstring, and a check that forbids describing itself is a
        check nobody can maintain.
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


#: The one expression, at module level so other files borrow it without
#: importing the class, which would collect its tests a second time.
FORBIDDEN = TestNoCurrency.FORBIDDEN


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
