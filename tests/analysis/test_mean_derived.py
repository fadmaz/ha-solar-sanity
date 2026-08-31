"""Mean-derived buckets must be usable, but weaker.

The statistics backfill supplies power channels as hourly means, because power
sensors carry no sum and asking for `change` returns nothing. Those readings are
good — the recorder saw every state change, where our own polling sees one in
three hundred — but an arithmetic mean over an event-reporting sensor
cannot say whether the hour it describes was complete, so they cannot
support a certain finding.

Two ways to get this wrong, both silent:
  * discard them, and the backfill produces nothing at all (the original bug);
  * treat them as exact, and a finding is graded higher than the evidence allows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from analysis.model import (
    Bucket,
    BucketSource,
    ChannelSpec,
    LossModel,
    Quality,
    Role,
)
from analysis.residual import build_days

SPECS = (
    ChannelSpec("pv", Role.PV, "sensor.pv", "PV", "Wh"),
    ChannelSpec("load", Role.LOAD, "sensor.load", "Load", "Wh"),
)


def _day(quality: Quality, source: BucketSource) -> tuple[Bucket, ...]:
    return tuple(
        Bucket(
            start_utc=datetime(2026, 3, 1, tzinfo=UTC) + timedelta(hours=hour),
            seconds=3600,
            wh={"pv": 500.0, "load": 500.0},
            quality={"pv": quality, "load": quality},
            source={"pv": source, "load": source},
        )
        for hour in range(24)
    )


class TestMeanDerivedIsUsable:
    def test_mean_derived_buckets_are_not_discarded(self) -> None:
        """The original backfill bug: usable readings thrown away."""
        days = build_days(
            _day(Quality.DERIVED_FROM_MEAN, BucketSource.LTS_MEAN),
            SPECS,
            LossModel(),
        )
        assert len(days) == 1, "mean-derived readings must still form a day"

    def test_mean_derived_days_are_flagged(self) -> None:
        """The flag is what widens tolerance and blocks a certain finding."""
        days = build_days(
            _day(Quality.DERIVED_FROM_MEAN, BucketSource.LTS_MEAN),
            SPECS,
            LossModel(),
        )
        assert days[0].from_mean is True

    def test_exact_days_are_not_flagged(self) -> None:
        days = build_days(_day(Quality.OK, BucketSource.LTS_SUM), SPECS, LossModel())
        assert days[0].from_mean is False

    def test_unusable_qualities_are_still_discarded(self) -> None:
        """A reset or a gap is not a weak reading — it is no reading."""
        for quality in (Quality.MISSING, Quality.RESET_SUSPECT, Quality.STALE):
            days = build_days(_day(quality, BucketSource.OWN_INTEGRAL), SPECS, LossModel())
            assert days == (), f"{quality} should invalidate the bucket"
