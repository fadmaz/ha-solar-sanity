"""The coverage snapshot must actually distinguish the cases it exists to name.

Three separate backfill defects have now been diagnosed by asking a user to read
state attributes back one at a time. This snapshot replaces that conversation,
so what matters is not that it returns a dict but that its numbers separate
"nothing was recorded", "one channel is sparse" and "there is genuinely not
enough history yet" from each other.

The method touches nothing on the coordinator but plain attributes, so it is
exercised against a stand-in rather than a started Home Assistant. It needs the
module importable, which is why the suite skips when Home Assistant is absent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant", reason="Home Assistant not installed")

from custom_components.solar_sanity.analysis.model import (
    Bucket,
    BucketSource,
    ChannelSpec,
    Quality,
    Role,
)
from custom_components.solar_sanity.coordinator import (
    SolarSanityCoordinator,
)

SPECS = (
    ChannelSpec(
        key="pv",
        role=Role.PV,
        entity_id="sensor.pv",
        friendly_name="Solar",
        declared_unit="kWh",
    ),
    ChannelSpec(
        key="load",
        role=Role.LOAD,
        entity_id="sensor.load",
        friendly_name="House",
        declared_unit="kWh",
    ),
    ChannelSpec(
        key="grid_import",
        role=Role.GRID_IMPORT,
        entity_id="sensor.imp",
        friendly_name="Grid import",
        declared_unit="kWh",
    ),
)

START = datetime(2026, 8, 1, tzinfo=UTC)


def _bucket(hour: int, present: tuple[str, ...]) -> Bucket:
    wh: dict[str, float | None] = {}
    quality: dict[str, Quality] = {}
    source: dict[str, BucketSource] = {}
    for spec in SPECS:
        if spec.key in present:
            wh[spec.key] = 100.0
            quality[spec.key] = Quality.OK
        else:
            wh[spec.key] = None
            quality[spec.key] = Quality.MISSING
        source[spec.key] = BucketSource.LTS_SUM
    return Bucket(
        start_utc=START + timedelta(hours=hour),
        seconds=3600,
        wh=wh,
        quality=quality,
        source=source,
    )


def _snapshot(buckets, *, classes=None, rows=None, unrecorded=()) -> dict:
    """Call the real method against a stand-in coordinator."""
    stub = SimpleNamespace(
        specs=SPECS,
        hass=SimpleNamespace(states=SimpleNamespace(get=lambda _entity_id: None)),
        _buckets=list(buckets),
        statistics_classes=classes or {},
        backfill_rows=rows or {},
        unrecorded_entities=tuple(unrecorded),
        _utc_offset_hours=lambda: 0.0,
    )
    return SolarSanityCoordinator.coverage_snapshot(stub)


class TestValidHours:
    """The gap between whole hours and valid hours is the whole question."""

    def test_a_missing_channel_makes_the_hour_invalid(self) -> None:
        snapshot = _snapshot([_bucket(h, ("pv", "load")) for h in range(24)])

        assert snapshot["buckets"]["whole_hours"] == 24
        assert snapshot["buckets"]["valid_hours"] == 0

    def test_a_complete_hour_counts(self) -> None:
        keys = ("pv", "load", "grid_import")
        snapshot = _snapshot([_bucket(h, keys) for h in range(24)])

        assert snapshot["buckets"]["valid_hours"] == 24
        assert snapshot["valid_hours_per_local_day"] == {"2026-08-01": 24}
        assert snapshot["days_meeting_minimum"] == 1


class TestPerChannelCoverage:
    """One sparse channel has to be visible as *that* channel."""

    def test_it_names_the_sparse_one(self) -> None:
        buckets = [_bucket(h, ("pv", "load")) for h in range(24)]
        buckets += [_bucket(h, ("pv", "load", "grid_import")) for h in range(24, 26)]

        by_key = {c["key"]: c for c in _snapshot(buckets)["channels"]}

        assert by_key["pv"]["hours_with_value"] == 26
        assert by_key["grid_import"]["hours_with_value"] == 2

    def test_an_unrecorded_channel_is_distinguishable_from_a_sparse_one(self) -> None:
        """Zero rows with a classification of "absent" is not the same problem.

        A sparse channel is a wait. An absent one is a wait that never ends,
        and the two are indistinguishable from the day count alone.
        """
        snapshot = _snapshot(
            [],
            classes={"sensor.pv": "sum", "sensor.load": "sum", "sensor.imp": "absent"},
            rows={"sensor.pv": 720, "sensor.load": 720},
            unrecorded=("sensor.imp",),
        )
        by_key = {c["key"]: c for c in snapshot["channels"]}

        assert by_key["grid_import"]["statistics"] == "absent"
        assert by_key["grid_import"]["backfilled_rows"] == 0
        assert by_key["pv"]["backfilled_rows"] == 720
        assert snapshot["unrecorded_entities"] == ["sensor.imp"]


class TestEmpty:
    """A fresh install must not raise on the way to saying nothing."""

    def test_no_buckets_is_reported_not_crashed(self) -> None:
        snapshot = _snapshot([])

        assert snapshot["buckets"] == {
            "held": 0,
            "whole_hours": 0,
            "valid_hours": 0,
            "first_utc": None,
            "last_utc": None,
            # A channel with no hours yet has no provenance to report, and an
            # empty mapping says that better than a missing key does.
            "by_source": {"pv": {}, "load": {}, "grid_import": {}},
        }
        assert snapshot["days_meeting_minimum"] == 0
