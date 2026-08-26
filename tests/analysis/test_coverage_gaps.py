"""What happens when a channel is missing, sparse, or unrecorded.

Every backfill bug so far shipped through this hole: nothing constructed a
bucket from statistics-shaped data and checked what the engine made of it.

The distinction these tests protect is the one the product exists to make.
"Not enough data yet" means *wait*. "One sensor has no history" means *act* —
and reporting the second as the first sends the user away to wait for something
that will never arrive.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from analysis.engine import analyse
from analysis.model import (
    AnalysisRequest,
    Bucket,
    BucketSource,
    ChannelSpec,
    Quality,
    Role,
    Status,
)

SPECS = (
    ChannelSpec("pv", Role.PV, "sensor.pv", "Solar generation", "Wh"),
    ChannelSpec("load", Role.LOAD, "sensor.load", "House consumption", "Wh"),
    ChannelSpec("grid_import", Role.GRID_IMPORT, "sensor.imp", "Grid import", "Wh"),
)


def _buckets(days: int, *, missing: set[str] = frozenset(), gap_hours: int = 0):
    out = []
    for day in range(days):
        for hour in range(24):
            wh: dict[str, float | None] = {}
            quality: dict[str, Quality] = {}
            for spec in SPECS:
                absent = spec.key in missing or hour < gap_hours
                wh[spec.key] = None if absent else 400.0
                quality[spec.key] = Quality.MISSING if absent else Quality.OK
            out.append(
                Bucket(
                    start_utc=datetime(2026, 3, 1, tzinfo=UTC) + timedelta(days=day, hours=hour),
                    seconds=3600,
                    wh=wh,
                    quality=quality,
                    source=dict.fromkeys(wh, BucketSource.LTS_SUM),
                )
            )
    return tuple(out)


def _request(buckets, **kwargs) -> AnalysisRequest:
    return AnalysisRequest(
        now_utc=datetime(2026, 4, 1, tzinfo=UTC),
        specs=SPECS,
        buckets=buckets,
        **kwargs,
    )


class TestUnrecordedChannel:
    """A channel with no history is not a shortage of days."""

    def test_it_is_reported_as_not_checkable(self) -> None:
        report = analyse(_request(_buckets(30), unrecorded_keys=("grid_import",)))
        assert report.status is Status.NOT_CHECKABLE

    def test_the_reason_names_the_sensor(self) -> None:
        """Without the name the user has nothing to act on."""
        report = analyse(_request(_buckets(30), unrecorded_keys=("grid_import",)))
        assert "Grid import" in report.reason
        assert "state class" in report.reason

    def test_a_non_balance_channel_does_not_block(self) -> None:
        """Only channels in the identity can invalidate an hour."""
        report = analyse(_request(_buckets(30), unrecorded_keys=("battery_soc",)))
        assert report.status is not Status.NOT_CHECKABLE


class TestCoverageFloor:
    """The floor must be a floor, not a cliff."""

    def test_a_day_missing_five_hours_is_still_usable(self) -> None:
        """Five channels each missing a different hour is an ordinary day on an
        MQTT-backed system, and used to discard the entire month."""
        report = analyse(_request(_buckets(30, gap_hours=5)))
        assert report.status is not Status.INSUFFICIENT_DATA

    def test_a_day_missing_most_hours_is_not_usable(self) -> None:
        report = analyse(_request(_buckets(30, gap_hours=12)))
        assert report.status is Status.INSUFFICIENT_DATA


class TestShortageIsExplained:
    """A shortage should say what is causing it."""

    def test_one_sparse_channel_is_named(self) -> None:
        buckets = list(_buckets(3))
        # Grid import present for only a handful of hours, everything else full.
        thinned = []
        for index, bucket in enumerate(buckets):
            wh = dict(bucket.wh)
            quality = dict(bucket.quality)
            if index % 10:
                wh["grid_import"] = None
                quality["grid_import"] = Quality.MISSING
            thinned.append(
                Bucket(
                    start_utc=bucket.start_utc,
                    seconds=3600,
                    wh=wh,
                    quality=quality,
                    source=bucket.source,
                )
            )

        report = analyse(_request(tuple(thinned)))
        assert report.status is Status.INSUFFICIENT_DATA
        assert "Grid import" in report.reason, report.reason

    def test_no_buckets_says_so_plainly(self) -> None:
        report = analyse(_request(()))
        assert report.status is Status.INSUFFICIENT_DATA
        assert "No measurements yet" in report.reason
