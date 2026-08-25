"""Fail the build if the card outgrows its budget.

A permanent guard against someone quietly adding a charting library. The whole
reason the charts are hand-rolled SVG is that a canvas library would be five to
ten times the size of everything else, and it would need a theme listener to do
what a CSS custom property does for free.
"""

from __future__ import annotations

import gzip
import pathlib
import sys

BUDGET_KB = 90.0
CARD = pathlib.Path("custom_components/solar_sanity/frontend/solar-sanity.js")


def main() -> int:
    if not CARD.exists():
        print(f"no bundle at {CARD}; run `npm run build` first")
        return 1

    size_kb = len(gzip.compress(CARD.read_bytes())) / 1024
    verdict = "over" if size_kb > BUDGET_KB else "within"
    print(f"{CARD.name}: {size_kb:.1f} kB gzipped — {verdict} the {BUDGET_KB:.0f} kB budget")
    return 1 if size_kb > BUDGET_KB else 0


if __name__ == "__main__":
    sys.exit(main())
