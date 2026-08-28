"""The no-currency rule, applied to everything a user can actually read.

The rule has been enforced since the first release — over one directory of
Python. It never looked at the card, at ``strings.json``, or at the
translations, which between them are most of the words this product says.

That gap matters more the moment there is more than one card. A day-shape panel
and a yield figure are precisely where a contributor reaches for a payback
number, and precisely where the evidence says not to: the most-liked topic in
Home Assistant's energy category is "My smarthome doesn't save me ANY money!".

Whole-file matching, not parsed literals. There is no cheap TypeScript AST in
Python, and the false positives are the point — a comment that says "cost" is a
comment worth rewording.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from tests.analysis.test_invariants import TestNoCurrency

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "solar_sanity"

#: The one expression, borrowed rather than restated. A rule written down twice
#: is a rule that gets loosened once.
FORBIDDEN = TestNoCurrency.FORBIDDEN


def _copy_files() -> list[pathlib.Path]:
    """Every file holding words a user might see."""
    files = sorted((ROOT / "frontend" / "src").rglob("*.ts"))
    files.append(COMPONENT / "strings.json")
    files.extend(sorted((COMPONENT / "translations").glob("*.json")))
    files.append(COMPONENT / "services.yaml")
    return [path for path in files if path.exists()]


def test_there_is_something_to_scan() -> None:
    """A glob that matches nothing passes silently, which is worse than failing."""
    files = _copy_files()

    assert len(files) >= 4, f"only found {[f.name for f in files]}"
    assert any(f.suffix == ".ts" for f in files), "no card source was scanned"


#: Comments in the card sources, which are not copy.
#:
#: The Python invariant excludes docstrings for the same reason and says why:
#: ``faults.py`` states this very rule in its own module docstring, and a check
#: that forbids describing itself is a check nobody can maintain. The card that
#: renders a forecast has the same paragraph at the top of it.
#:
#: A ``//`` preceded by a colon is a URL, not a comment. Cutting there would
#: swallow the rest of the line and hide anything after it.
_BLOCK_COMMENT = re.compile(r"/\*[\s\S]*?\*/")
_LINE_COMMENT = re.compile(r"(?<!:)//.*")


def _copy_only(path: pathlib.Path) -> str:
    """The file with anything that is not shown to a user taken out."""
    text = path.read_text(encoding="utf-8")
    if path.suffix != ".ts":
        return text
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))


@pytest.mark.parametrize("path", _copy_files(), ids=lambda p: p.name)
def test_no_currency_in_user_facing_copy(path: pathlib.Path) -> None:
    text = _copy_only(path)
    offenders = [
        f"line {index}: {match.group(0)!r}"
        for index, line in enumerate(text.splitlines(), start=1)
        if (match := FORBIDDEN.search(line))
    ]

    assert not offenders, (
        f"{path.relative_to(ROOT)} uses currency language, which this product "
        "never reports: " + "; ".join(offenders)
    )


def test_the_symbol_rule_still_catches_an_amount() -> None:
    """The risk in requiring a number beside the symbol, stated as a test."""
    for money in ("about $40 a year", "$ 40", "40$", "saves you 40 $"):
        assert FORBIDDEN.search(money), f"{money!r} stopped being caught"


def test_the_symbol_rule_ignores_the_things_that_are_not_money() -> None:
    """All three appear in the card sources and none of them is a price."""
    assert FORBIDDEN.search("`${hours} hours`") is None, "template interpolation"
    assert FORBIDDEN.search(r"/_status(_\d+)?$/") is None, "a regex end anchor"
    assert FORBIDDEN.search('viewBox="0 0 ${WIDTH} ${HEIGHT}"') is None, "an SVG viewBox"


def test_a_comment_is_not_copy_but_the_file_still_holds_one() -> None:
    """The exemption must not become a way to smuggle copy past the rule."""
    source = ROOT / "frontend" / "src" / "forecast-card.ts"

    assert "currency" in source.read_text(encoding="utf-8"), "module docstring changed"
    assert "currency" not in _copy_only(source), "the comment was not stripped"


def test_stripping_comments_leaves_a_url_intact() -> None:
    """Cutting at every ``//`` would swallow the rest of the line."""
    kept = _copy_only(ROOT / "frontend" / "src" / "main.ts")

    assert "https://github.com/fadmaz/ha-solar-sanity" in kept
