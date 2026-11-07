"""Read a real installation's diagnostics download back into buckets.

The measurement window is the one thing in a diagnostics file that every
replay needs: the battery outlook's backtest (``scripts/backtest_outlook.py``)
grades its projections against it. This module is that and nothing else, so it
imports nothing beyond the bucket types and outlives everything built on top
of it.

The other half of ``coordinator.window_snapshot``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from analysis.model import Bucket, BucketSource, Quality

#: The reverse of `coordinator._QUALITY_CODE` / `_SOURCE_CODE`. Deliberately not
#: imported from there: this module must stay importable with Home Assistant
#: absent, and reading the codes out of the file being replayed would let a
#: renamed code pass silently. A mismatch should fail here, and
#: `tests/test_window_snapshot.py` checks the two tables are exact inverses.
_QUALITY = {
    "O": Quality.OK,
    "M": Quality.MISSING,
    "R": Quality.RESET_SUSPECT,
    "D": Quality.DERIVED_FROM_MEAN,
    "S": Quality.STALE,
}

_SOURCE = {
    "i": BucketSource.OWN_INTEGRAL,
    "s": BucketSource.LTS_SUM,
    "m": BucketSource.LTS_MEAN,
}


def load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def buckets_from(window: dict[str, Any]) -> tuple[Bucket, ...]:
    """The `window` block of a diagnostics file, as buckets.

    Raises rather than guessing on an unknown code. A file written by a version
    whose codes have moved is not a file this can replay, and silently mapping
    an unrecognised quality to OK would produce a confident wrong answer.
    """
    keys: list[str] = window["keys"]
    out: list[Bucket] = []
    for start, seconds, values, quality, source in window["rows"]:
        if len(quality) != len(keys) or len(source) != len(keys):
            raise ValueError(f"{start}: {len(keys)} channels, {len(quality)}/{len(source)} codes")
        out.append(
            Bucket(
                start_utc=datetime.fromisoformat(start),
                seconds=seconds,
                wh=dict(zip(keys, values, strict=True)),
                quality={k: _QUALITY[c] for k, c in zip(keys, quality, strict=True)},
                source={k: _SOURCE[c] for k, c in zip(keys, source, strict=True)},
            )
        )
    return tuple(out)
