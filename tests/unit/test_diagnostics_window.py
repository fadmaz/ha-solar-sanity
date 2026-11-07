"""Reading a diagnostics download's measurement window back into buckets.

The battery outlook's backtest grades every projection against this, so it has
to refuse a file it cannot read rather than guess: silently mapping an
unrecognised code to OK would produce a confident wrong grade.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from analysis.model import BucketSource, Quality

from tests.synth.diagnostics import buckets_from


def test_a_window_reads_back_as_buckets() -> None:
    (bucket,) = buckets_from(
        {
            "keys": ["pv", "load"],
            "rows": [["2026-08-01T00:00:00+00:00", 3600, [1.0, None], "OM", "is"]],
        }
    )

    assert bucket.start_utc == datetime(2026, 8, 1, tzinfo=UTC)
    assert bucket.wh == {"pv": 1.0, "load": None}
    assert bucket.quality == {"pv": Quality.OK, "load": Quality.MISSING}
    assert bucket.source == {"pv": BucketSource.OWN_INTEGRAL, "load": BucketSource.LTS_SUM}


def test_an_unknown_quality_code_raises() -> None:
    with pytest.raises(KeyError):
        buckets_from(
            {
                "keys": ["pv"],
                "rows": [["2026-08-01T00:00:00+00:00", 3600, [1.0], "Z", "i"]],
            }
        )


def test_a_row_whose_codes_do_not_match_its_channels_raises() -> None:
    with pytest.raises(ValueError, match="codes"):
        buckets_from(
            {
                "keys": ["pv", "load"],
                "rows": [["2026-08-01T00:00:00+00:00", 3600, [1.0, 2.0], "O", "ii"]],
            }
        )
