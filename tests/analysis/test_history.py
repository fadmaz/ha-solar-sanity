"""Public history is a bounded view, not a replay or a private diagnostics dump."""

from copy import deepcopy

from analysis.history import history_rows


def row(day="2026-11-10", status="graded"):
    return {
        "id": f"{day}:evening",
        "issued_at": f"{day}T20:01:00+00:00",
        "checkpoint": "evening",
        "context": "original",
        "time_zone": "UTC",
        "inputs": {"private": "sensor.synthetic_private"},
        "soc_entity": "sensor.synthetic_private",
        "settings": {"capacity_kwh": 10, "reserve_pct": 20, "target_pct": 100},
        "published": {"lowest_pct": 40, "reserve_at": None, "full_at": None},
        "result": {"status": status, "actual_lowest": 39, "error": 1, "reserve_correct": True},
    }


def test_history_never_exposes_replay_inputs_or_changes_issued_records():
    """A UI request cannot become a diagnostics download or mutate its evidence."""
    original = [row()]
    saved = deepcopy(original)
    public = history_rows(original, "2026-11-01", "original")
    assert "synthetic_private" not in str(public)
    public[0]["published"]["lowest_pct"] = 0
    assert original == saved


def test_missed_checkpoint_has_a_scheduled_time_but_no_issue_time():
    """A late restart cannot fabricate a publication."""
    public = history_rows([row(status="missed")], "2026-11-01", "changed")[0]
    assert public["issued_at"] is None
    assert public["scheduled_at"]
    assert public["previous_setup"]


def test_local_dates_survive_a_changed_time_zone_and_filter_the_requested_period():
    """The local checkpoint key preserves dates even across later setup changes."""
    rows = [row("2026-11-09"), row("2026-11-10")]
    rows[1]["issued_at"] = "2026-11-09T22:01:00+00:00"
    assert [r["local_date"] for r in history_rows(rows, "2026-11-10", "original")] == ["2026-11-10"]


def test_hourly_fill_evidence_has_no_invented_minute_time():
    """An observed hourly maximum proves full, not an exact crossing."""
    record = row()
    record.update(
        checkpoint="morning",
        result={
            "status": "graded",
            "actual_filled": True,
            "timing_resolution": "hour",
            "time_error_minutes": None,
        },
    )
    result = history_rows([record], "2026-11-01", "original")[0]["result"]
    assert result["actual_filled"]
    assert result["actual_full_at"] is None
    assert result["time_error_minutes"] is None


def test_history_is_bounded_even_if_a_caller_supplies_too_many_records():
    """The public action cannot dump an unbounded store."""
    assert len(history_rows([row()] * 200, "2026-11-01", "original")) == 180
