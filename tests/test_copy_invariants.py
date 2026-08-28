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


class TestEveryFindingHasABody:
    """A finding the user cannot get past the headline of is barely a finding.

    Home Assistant renders ``issues.<key>.description`` for an issue with no Fix
    button, and ``fix_flow`` only for one that has it. Every finding's detail and
    remedy lived exclusively inside ``fix_flow`` — so a finding that offers no
    correction showed its headline and nothing else. The reader was told
    "Solar production and Second sensor are the same energy measured twice" and
    given no way to reach the sentence explaining what to do about it.

    Not a rare corner. Findings are non-fixable whenever the honest answer is a
    configuration change rather than an internal override, which is most of
    them: a duplicated pair, a correction that has outlived its fault, partial
    CT coverage, a net meter mapped beside an export sensor.

    And the status card sends people there in as many words —
    ``frontend/src/status-card.ts`` offers "Show me" pointing at
    ``/config/repairs``, on the promise that the explanation is waiting.
    """

    ISSUE_KEYS = ("finding", "finding_question")

    @staticmethod
    def _issues(path: pathlib.Path) -> dict:
        import json

        return json.loads(path.read_text(encoding="utf-8"))["issues"]

    @pytest.mark.parametrize(
        "path",
        [
            COMPONENT / "strings.json",
            COMPONENT / "translations" / "en.json",
        ],
        ids=["strings", "en"],
    )
    @pytest.mark.parametrize("key", ISSUE_KEYS)
    def test_a_non_fixable_issue_can_still_be_read(self, path: pathlib.Path, key: str) -> None:
        entry = self._issues(path)[key]

        assert "description" in entry, (
            f"{path.name}:{key} has no description, so a finding that offers no "
            f"correction renders as a headline and nothing else"
        )

    @pytest.mark.parametrize(
        "path",
        [
            COMPONENT / "strings.json",
            COMPONENT / "translations" / "en.json",
        ],
        ids=["strings", "en"],
    )
    @pytest.mark.parametrize("key", ISSUE_KEYS)
    def test_the_body_carries_both_halves(self, path: pathlib.Path, key: str) -> None:
        """What is wrong, and what to do — the second is the useful one."""
        description = self._issues(path)[key]["description"]

        assert "{detail}" in description
        assert "{source_fix}" in description

    def test_every_placeholder_is_one_repairs_actually_supplies(self) -> None:
        """Otherwise Home Assistant renders the brace and the user reads it."""
        import json
        import re

        supplied = set(
            re.findall(
                r'"(\w+)":',
                (COMPONENT / "repairs.py")
                .read_text(encoding="utf-8")
                .split("translation_placeholders={", 1)[1]
                .split("}", 1)[0],
            )
        )
        assert supplied, "the placeholder block moved — this test cannot see it"

        for key in self.ISSUE_KEYS:
            entry = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))["issues"][
                key
            ]
            used = set(re.findall(r"\{(\w+)\}", entry["description"]))

            assert used <= supplied, f"{key} asks for {sorted(used - supplied)}"

    def test_both_files_say_the_same_thing(self) -> None:
        """They drifted once already; en.json is what users actually read."""
        for key in self.ISSUE_KEYS:
            assert (
                self._issues(COMPONENT / "strings.json")[key]["description"]
                == self._issues(COMPONENT / "translations" / "en.json")[key]["description"]
            ), key
