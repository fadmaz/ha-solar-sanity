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


@pytest.mark.parametrize("path", _copy_files(), ids=lambda p: p.name)
def test_no_currency_in_user_facing_copy(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")
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


def test_the_symbol_rule_ignores_the_two_things_that_are_not_money() -> None:
    """Both appear in the card sources and neither is a price."""
    assert FORBIDDEN.search("`${hours} hours`") is None, "template interpolation"
    assert FORBIDDEN.search(r"/_status(_\d+)?$/") is None, "a regex end anchor"
