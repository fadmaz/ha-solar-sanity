"""A moving horizon cannot be described as a weather or household change."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from analysis.changes import InputChanges, InputSnapshot
from analysis.outlook import Step

NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)


def snapshot(at=NOW, context="same"):
    return InputSnapshot(
        context,
        at,
        70,
        tuple(Step(at + timedelta(hours=i), 1, 1000, 200, 1000) for i in range(4)),
        0.5,
        0.4,
        0.6,
        3000,
        90,
    )


def test_elapsed_hours_and_new_horizon_hours_do_not_create_energy_changes():
    """The second projection can be shorter or shifted without changing its rate."""
    changes = InputChanges()
    changes.publish(snapshot())
    view = changes.publish(snapshot(NOW + timedelta(minutes=30)))
    assert view["status"] == "ok"
    assert view["common_start"] == (NOW + timedelta(minutes=30)).isoformat()
    assert view["common_end"] == (NOW + timedelta(hours=4)).isoformat()
    assert view["fields"] == []


def test_changed_inputs_are_reported_only_on_the_common_future():
    """Three shared hours, not all four hours, carry the additional energy."""
    changes = InputChanges()
    changes.publish(snapshot())
    current = snapshot(NOW + timedelta(hours=1))
    current = replace(
        current,
        soc_pct=65,
        steps=tuple(replace(s, pv_wh=2000, load_wh=400) for s in current.steps),
        scale_today=0.7,
    )
    view = changes.publish(current)
    fields = {r["key"]: r for r in view["fields"]}
    assert fields["solar_energy"] == {
        "key": "solar_energy",
        "previous": 3,
        "current": 6,
        "unit": "kWh",
    }
    assert fields["load_energy"]["current"] == 1.2
    assert fields["soc_pct"]["previous"] == 70
    assert fields["scale_today"]["current"] == 0.7


@pytest.mark.parametrize(
    "reset", [None, snapshot(context="changed"), snapshot(NOW - timedelta(minutes=1))]
)
def test_invalid_context_or_clock_reversal_starts_a_new_comparison(reset):
    """A configuration change and recovery must never compare unrelated estimates."""
    changes = InputChanges()
    changes.publish(snapshot())
    assert changes.publish(reset)["status"] != "ok"
    assert changes.previous is None
    if reset is None:
        assert changes.publish(snapshot(NOW + timedelta(minutes=1)))["status"] == "starting"


def test_missing_estimates_never_become_a_numeric_zero_change():
    """Learning an estimate is different from its value moving from zero."""
    changes = InputChanges()
    changes.publish(replace(snapshot(), max_charge_w=None, scale=None))
    assert changes.publish(snapshot(NOW + timedelta(minutes=1)))["fields"] == []


def test_disjoint_horizons_wait_for_a_new_valid_pair():
    """There is no energy comparison after the first horizon has entirely passed."""
    changes = InputChanges()
    changes.publish(snapshot())
    assert changes.publish(snapshot(NOW + timedelta(days=1)))["reason_code"] == "no_common_horizon"
