"""Run every gate the repository has, in the order they depend on each other.

One command for what validate.yml spreads over seven jobs: ruff lint and
format, the TypeScript typecheck, the Python suite, the card build, the card
tests, and the bundle size budget. It stops at the first failure and names the
step, so a red run reads as one line rather than a scroll.

The order is not cosmetic. The build precedes the card tests so the bundle
smoke test checks a fresh artifact instead of skipping, and the size check runs
last because it needs that artifact. The Python steps use the interpreter that
ran this script, so whichever ``python`` you chose is the one that is checked.

Usage::

    python scripts/check.py          # everything CI gates, about two minutes
    python scripts/check.py --slow   # also the 3,000-scenario clean corpus

Without Home Assistant installed the Python step covers the pure tier only;
``tests/integration`` is not collected. Say so in the pull request.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def _npm() -> str:
    # ``shutil.which`` resolves ``npm.cmd`` on Windows, which a bare "npm" in
    # ``subprocess.run`` does not.
    found = shutil.which("npm")
    if found is None:
        sys.exit("npm is not on PATH; install Node 22 and run `npm ci` first")
    return found


def _steps(slow: bool) -> list[tuple[str, list[str]]]:
    npm = _npm()
    steps = [
        ("ruff check", [PYTHON, "-m", "ruff", "check", "."]),
        ("ruff format", [PYTHON, "-m", "ruff", "format", "--check", "."]),
        ("tsc", [npm, "run", "lint"]),
        ("pytest", [PYTHON, "-m", "pytest", "tests", "-q"]),
    ]
    if slow:
        corpus = [PYTHON, "-m", "pytest", "tests", "-q", "-m", "slow", "-n", "auto"]
        steps.append(("pytest, clean corpus", corpus))
    steps += [
        ("vite build", [npm, "run", "build"]),
        ("vitest", [npm, "test"]),
        ("bundle size", [PYTHON, str(ROOT / "scripts" / "check_size.py")]),
    ]
    return steps


def main(argv: list[str]) -> int:
    slow = "--slow" in argv
    unknown = [arg for arg in argv if arg != "--slow"]
    if unknown:
        print(f"unknown arguments: {' '.join(unknown)}; the only flag is --slow")
        return 2

    started = time.monotonic()
    for name, command in _steps(slow):
        print(f"\n=== {name}: {' '.join(Path(part).name for part in command)}", flush=True)
        step_started = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, check=False)
        elapsed = time.monotonic() - step_started
        if result.returncode != 0:
            print(f"\nFAILED at {name} after {elapsed:.0f} s (exit {result.returncode})")
            return result.returncode
        print(f"--- {name} ok in {elapsed:.0f} s", flush=True)

    print(f"\nall gates green in {time.monotonic() - started:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
