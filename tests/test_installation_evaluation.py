"""Different synthetic houses exercise the same private comparison command."""

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scripts.evaluate_installation import evaluate


def synthetic_download(zone_name, capacity, export, meter, start):
    """Complete hourly observations, with deliberately unfilled synthetic batteries."""
    zone = ZoneInfo(zone_name)
    keys = ["pv", "load", "grid_import", "battery_charge", "battery_discharge"]
    if export:
        keys.append("grid_export")
    rows, observations, forecast = [], [], {}
    for i in range(10 * 24):
        when = start + timedelta(hours=i)
        solar = 1000.0 if 6 <= when.astimezone(zone).hour < 18 else 0.0
        surplus = max(0.0, solar - 100)
        values = {
            "pv": solar,
            "load": 100.0,
            "grid_import": 0.0,
            "battery_charge": 0.0 if export else surplus,
            "battery_discharge": 0.0,
            "grid_export": surplus if export else 0.0,
        }
        rows.append(
            [when.isoformat(), 3600, [values[k] for k in keys], "O" * len(keys), "i" * len(keys)]
        )
        observations.append({"start": when.isoformat(), "mean": 50.0, "min": 50.0, "max": 50.0})
        forecast[when.isoformat()] = solar / 1000
    return {
        "entry": {
            "data": {
                "channels": [
                    {"role": k, "entity_id": f"sensor.synthetic_{k}_{meter}"} for k in keys
                ]
            },
            "options": {
                "battery_capacity_kwh": capacity,
                "battery_reserve_pct": 0,
                "battery_target_pct": 100,
            },
        },
        "window": {"keys": keys, "rows": rows},
        "soc_history": observations,
        "forecast_history": {"synthetic_provider": {"dayahead": forecast}},
        "coverage": {"channels": [{"kind": meter} for _ in keys]},
        "outlook_journal": [],
    }


@pytest.mark.parametrize(
    ("zone", "capacity", "start"),
    [
        ("UTC", 10, datetime(2026, 3, 5, tzinfo=UTC)),
        ("Asia/Jerusalem", 30, datetime(2026, 10, 22, tzinfo=UTC)),
        ("America/New_York", 20, datetime(2026, 3, 5, tzinfo=UTC)),
        ("Pacific/Auckland", 60, datetime(2026, 9, 24, tzinfo=UTC)),
    ],
)
@pytest.mark.parametrize("export", [False, True])
@pytest.mark.parametrize("meter", ["power", "energy"])
def test_synthetic_installations_remain_separate_and_never_prove_generalization(
    zone, capacity, start, export, meter
):
    """Export, meter kinds, capacities and DST zones must share the same grading contract."""
    data = synthetic_download(zone, capacity, export, meter, start)
    report = evaluate(
        data, installation="synthetic", kind="synthetic", zone_name=zone, keying="period-start"
    )
    assert report["battery"]["capacity_wh"] == capacity * 1000
    assert report["battery"]["reserve_pct"] == 0
    assert report["export_mapped"] is export
    assert report["channel_kinds"][meter] == len(data["window"]["keys"])
    assert report["historical_baseline"]["nights"] > 0
    assert report["historical_baseline"]["fill_time_mae"] is None
    assert report["timing_resolution"] == "hour"
    assert not report["generalization_proven"]
    assert report["issued_journal_groups"] == []
    assert "sensor.synthetic" not in json.dumps(report)


def test_unverified_provider_timing_cannot_pass_the_accuracy_gate():
    """A good apparent score cannot authorize an unverified archive clock."""
    data = synthetic_download("UTC", 30, False, "power", datetime(2026, 1, 1, tzinfo=UTC))
    report = evaluate(data, installation="one", zone_name="UTC")
    assert not report["night_accuracy_gates"]["provider_timing_verified"]
    assert not report["night_accuracy_gate_passed"]
    changed = evaluate(data, installation="one", zone_name="Asia/Jerusalem")
    assert changed["context"] != report["context"]
