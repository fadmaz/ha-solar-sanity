"""The committed version is what a checkout, a diagnostics file and a card URL say.

``release.yml`` stamps the tag into ``manifest.json`` before it builds, so every
released zip has always been correct whatever the repository said. That is what
made this easy to lose: from v0.22.0 to v0.25.1 the committed version sat at
0.21.1 through seven releases and nothing went red, because the only thing that
read it at release time overwrote it first.

Everything that reads it *without* the workflow was wrong for those seven
releases. Home Assistant's loader takes the integration version straight from
this file, so a checkout install reports the stale number on the integrations
page and in every diagnostics download. ``vite.config.ts`` reads the same file
to define ``__SS_VERSION__``, so a local ``npm run build`` stamps it into the
card, and ``frontend.py`` builds the Lovelace resource URL as ``?v=<version>``
— an input that never changed, so those installs kept serving a card four
releases old from a URL that claimed to be fresh.

The fix is the convention that held for the first forty-five tags: the release
pull request bumps the manifest in the same commit as its changelog entry. This
makes that convention checkable, and it runs in the pure suite so it fails
inside the pull request rather than after a tag exists, when the remedies are a
deleted release or a version nobody can trust.

It compares against the changelog rather than ``git tag`` because tags are not
fetched by a shallow CI checkout and do not exist while the release pull request
is open. The tag is checked where the tag exists, in ``release.yml``.

On a branch that adds no changelog entry — every fix, feat, test and docs pull
request that is not itself a release — neither side moves and this passes with
the author doing nothing.
"""

from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The release heading, and the only thing in the changelog matched at all. A
#: ``### Notes`` section or any other prose is ignored rather than failed: this
#: guard is about the version, not about changelog formatting.
RELEASE_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}$", re.MULTILINE)


def _manifest_version() -> str:
    manifest = ROOT / "custom_components" / "solar_sanity" / "manifest.json"
    return json.loads(manifest.read_text(encoding="utf-8"))["version"]


def _released() -> list[str]:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return RELEASE_HEADING.findall(changelog)


def test_the_changelog_still_has_the_shape_this_reads() -> None:
    """A guard that silently matches nothing is not a guard."""
    released = _released()

    assert released, "no `## [X.Y.Z] - YYYY-MM-DD` heading in CHANGELOG.md — the format moved"


def test_the_manifest_carries_the_version_at_the_top_of_the_changelog() -> None:
    """Both edits belong to one commit, which is why this holds on every commit.

    It fails in exactly two cases and both are the bug: a release entry written
    without the bump, which is the drift that ran from v0.22.0 to v0.25.1, or a
    bump with no entry to justify it. An entry added anywhere but the top fails
    here too, so no separate ordering guard is needed.
    """
    manifest_version = _manifest_version()
    newest = _released()[0]

    assert manifest_version == newest, (
        f"manifest.json says {manifest_version} and the newest CHANGELOG entry is "
        f"{newest} — a release pull request bumps manifest.json in the same commit "
        "as its changelog heading, because Home Assistant reads the committed file "
        "for the integrations page, for diagnostics downloads and for the card's "
        "?v= cache-buster"
    )


def test_the_committed_manifest_is_what_the_release_step_would_write() -> None:
    """The release workflow rewrites this file with ``json.dumps(..., indent=2)``.

    If the committed formatting differed, the check-then-stamp step would produce
    a spurious diff and the two versions of the file would disagree about
    whitespace while agreeing about everything that matters.
    """
    manifest = ROOT / "custom_components" / "solar_sanity" / "manifest.json"
    raw = manifest.read_text(encoding="utf-8")

    assert raw == json.dumps(json.loads(raw), indent=2) + "\n", (
        "manifest.json is not formatted the way release.yml writes it back"
    )
