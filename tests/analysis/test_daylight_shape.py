"""The morning candidate learns only yesterday, and cannot create daily energy."""

from datetime import UTC, datetime, timedelta

import pytest
from analysis.daylight_shape import SHAPE_ENABLED, SHAPE_REVISION, daylight_thirds, learn_shape
from analysis.experiment import shape_gate
from analysis.model import Bucket, BucketSource, Quality

START = datetime(2026, 10, 1, tzinfo=UTC)


def training(days=3):
    """A synthetic roof delivers less early and more late, with room to charge."""
    forecast, soc, buckets = {}, {}, []
    for day in range(days):
        for h in range(5, 19):
            when = START + timedelta(days=day, hours=h)
            value = 100.0 if 6 <= h < 18 else 0.0
            forecast[when] = value
            soc[when] = 50
            ratio = (0.2, 0.5, 0.8)[min(2, max(0, (h - 6) // 4))]
            wh = {"pv": value * ratio, "grid_import": 0.0}
            buckets.append(
                Bucket(
                    when,
                    3600,
                    wh,
                    dict.fromkeys(wh, Quality.OK),
                    dict.fromkeys(wh, BucketSource.OWN_INTEGRAL),
                )
            )
    return forecast, soc, buckets


def learned(forecast, soc, buckets, until=START + timedelta(days=3)):
    return learn_shape(
        buckets, forecast, soc_max=soc, full_pct=100, until=until, day_of=lambda t: t.date()
    )


def test_shape_preserves_daily_corrected_energy_and_remains_disabled():
    """The experiment moves energy within daylight; it does not change its scale."""
    forecast, soc, buckets = training()
    shape = learned(forecast, soc, buckets)
    assert shape is not None and shape.counts == (3, 3, 3)
    day = {t: v * 0.5 for t, v in forecast.items() if t.date() == START.date()}
    result = shape.distribute(day, 1200)
    assert result is not None
    assert sum(result.values()) == pytest.approx(sum(day.values()))
    assert result[START + timedelta(hours=7)] < result[START + timedelta(hours=15)]
    assert not SHAPE_ENABLED


def test_every_third_needs_three_distinct_usable_days():
    """A battery full each afternoon must not teach a weak late sun."""
    forecast, soc, buckets = training()
    for t in soc:
        if t.day == 3 and t.hour >= 14:
            soc[t] = 100
    assert learned(forecast, soc, buckets) is None


def test_current_and_future_days_never_teach_the_shape():
    """Holding out whole local days prevents a morning from learning its afternoon."""
    forecast, soc, buckets = training(5)
    shape = learned(forecast, soc, buckets, START + timedelta(days=3, hours=20))
    assert shape is not None and shape.counts == (3, 3, 3)


def test_missing_shoulders_or_internal_hours_refuse_shape_correction():
    """A partial forecast must not be normalized as a complete solar day."""
    forecast, _, _ = training(1)
    assert daylight_thirds(forecast) is not None
    del forecast[START + timedelta(hours=5)]
    assert daylight_thirds(forecast) is None


def test_independent_daylight_bounds_can_prove_a_sparse_providers_horizon():
    """Solar providers omit dark hours; complete daylight is sufficient evidence."""
    forecast, _, _ = training(1)
    forecast = {t: v for t, v in forecast.items() if v > 0}
    window = (START + timedelta(hours=6, minutes=20), START + timedelta(hours=17, minutes=40))
    assert daylight_thirds(forecast) is None
    assert daylight_thirds(forecast, window) is not None
    del forecast[START + timedelta(hours=17)]
    assert daylight_thirds(forecast, window) is None
    forecast, _, _ = training(1)
    del forecast[START + timedelta(hours=11)]
    assert daylight_thirds(forecast) is None


def evidence(days=14):
    records = []
    for day in range(days):
        for kind in ("morning", "evening"):
            base = {
                "status": "graded",
                "error": 2.0,
                "reserve_correct": True,
                "false_fill": False,
                "missed_fill": True,
            }
            candidate = {**base, "missed_fill": False}
            record = {
                "context": "same",
                "checkpoint": kind,
                "issued_at": (START + timedelta(days=day)).isoformat(),
                "result": base,
            }
            record["shadow"] = {**record, "model_revision": SHAPE_REVISION, "result": candidate}
            records.append(record)
    return records


def test_promotion_needs_prospective_pairs_and_every_conservative_gate():
    """Improved misses cannot pay for even one extra false promise or worse night."""
    assert not shape_gate(evidence(13), "same")["ready_for_review"]
    records = evidence()
    assert shape_gate(records, "same")["ready_for_review"]
    assert not shape_gate(records, "same")["active"]
    records[0]["shadow"]["result"]["false_fill"] = True
    assert not shape_gate(records, "same")["ready_for_review"]
    assert shape_gate(records, "different")["paired_mornings"] == 0
    records = evidence()
    records[1]["shadow"]["result"]["error"] = 4
    assert not shape_gate(records, "same")["ready_for_review"]
