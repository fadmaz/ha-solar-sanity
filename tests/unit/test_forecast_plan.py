"""The rule that makes the day-ahead archive an answer rather than a hope.

The rolling archive keeps the latest revision of every hour. That makes it
idempotent and useless for scoring: by the time an hour has passed, what is
stored for it was issued minutes before, not the day before. A bias figure
computed from that would flatter every provider equally and mean nothing.

So an hour lands in the day-ahead archive once, at real lead time, and is never
revised. Every test here is that sentence in one form or another.

Imported absolutely, like ``analysis``: the module has no Home Assistant imports
and none of its own package's, so it loads with Home Assistant absent. That is a
structural guarantee rather than a convention.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from _forecast_plan import dayahead_write_plan, eligible, running_totals

LEAD = 12
NOW = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)


def hour(offset: int) -> datetime:
    return NOW + timedelta(hours=offset)


def points(*offsets: int, value: float = 1.0) -> list[tuple[datetime, float]]:
    return [(hour(o), value) for o in offsets]


class TestEligible:
    """A forecast for two hours' time is a nowcast, whatever it is called."""

    def test_hours_inside_the_lead_window_are_excluded(self) -> None:
        assert eligible(points(1, 6, 11), NOW, LEAD) == []

    def test_the_boundary_hour_counts(self) -> None:
        assert eligible(points(12), NOW, LEAD) == [(hour(12), 1.0)]

    def test_hours_beyond_it_count(self) -> None:
        assert len(eligible(points(12, 18, 30), NOW, LEAD)) == 3

    def test_the_past_is_never_eligible(self) -> None:
        assert eligible(points(-5, -1), NOW, LEAD) == []


class TestImmutability:
    """An hour keeps the value it was first given."""

    def test_an_already_recorded_hour_is_not_revised(self) -> None:
        existing = {hour(20): 4.0}
        plan = dayahead_write_plan(points(20, value=9.9), existing, NOW, LEAD)

        assert plan == [], "a recorded hour was offered for rewriting"

    def test_an_earlier_recorded_hour_is_left_out_entirely(self) -> None:
        """Nothing before the earliest new hour is touched at all."""
        existing = {hour(20): 4.0}
        plan = dict(dayahead_write_plan(points(20, 21, value=9.9), existing, NOW, LEAD))

        assert hour(20) not in plan
        assert plan[hour(21)] == 9.9

    def test_the_tail_rewrite_is_not_a_back_door_for_revisions(self) -> None:
        """A recorded hour caught up in a replay keeps its original value.

        This is the one place a revision could enter unnoticed: rewriting the
        tail after a gap means re-writing hours that were already settled.
        """
        existing = {hour(21): 4.0, hour(23): 4.0}
        plan = dict(dayahead_write_plan(points(21, 22, 23, value=9.9), existing, NOW, LEAD))

        assert plan[hour(22)] == 9.9, "the new hour was not written"
        assert plan[hour(23)] == 4.0, "a settled hour was revised during a replay"
        assert hour(21) not in plan, "an hour before the gap was rewritten"

    def test_a_new_hour_is_written(self) -> None:
        plan = dayahead_write_plan(points(20, value=3.5), {}, NOW, LEAD)

        assert plan == [(hour(20), 3.5)]


class TestGapBehindTheNewestRow:
    """Capture stops sometimes, and the running total must survive it."""

    def test_the_tail_after_the_gap_is_replayed(self) -> None:
        """Appending into a gap alone would send ``sum`` backwards at the join."""
        existing = {hour(20): 1.0, hour(22): 1.0, hour(23): 1.0}
        plan = dayahead_write_plan(points(21, 22, 23), existing, NOW, LEAD)

        assert [when for when, _ in plan] == [hour(21), hour(22), hour(23)]

    def test_nothing_before_the_gap_is_touched(self) -> None:
        existing = {hour(20): 1.0, hour(22): 1.0}
        plan = dayahead_write_plan(points(21, 22), existing, NOW, LEAD)

        assert hour(20) not in dict(plan)

    def test_the_plan_is_always_ascending(self) -> None:
        existing = {hour(30): 1.0, hour(25): 1.0}
        plan = dayahead_write_plan(points(20, 25, 30, 35), existing, NOW, LEAD)
        starts = [when for when, _ in plan]

        assert starts == sorted(starts)


class TestNothingDue:
    """The common case: most captures see nothing worth writing."""

    def test_all_hours_already_recorded(self) -> None:
        existing = {hour(o): 1.0 for o in (12, 18, 24)}

        assert dayahead_write_plan(points(12, 18, 24), existing, NOW, LEAD) == []

    def test_all_hours_too_close(self) -> None:
        assert dayahead_write_plan(points(1, 2, 3), {}, NOW, LEAD) == []

    def test_no_points_at_all(self) -> None:
        assert dayahead_write_plan([], {}, NOW, LEAD) == []


class TestRunningTotals:
    """Bookkeeping the recorder's contract needs, and scoring must never read."""

    def test_it_accumulates_from_where_it_resumed(self) -> None:
        rows = running_totals(points(12, 13, 14, value=2.0), resume=10.0)

        assert [total for _, _, total in rows] == [12.0, 14.0, 16.0]

    def test_the_state_is_the_hour_not_the_total(self) -> None:
        """The distinction the whole scoring path depends on."""
        rows = running_totals(points(12, 13, value=2.0), resume=100.0)

        assert [state for _, state, _ in rows] == [2.0, 2.0]

    def test_it_never_goes_backwards_on_non_negative_input(self) -> None:
        rows = running_totals(points(12, 13, 14, 15, value=0.0), resume=5.0)
        totals = [total for _, _, total in rows]

        assert totals == sorted(totals)

    @pytest.mark.parametrize("resume", [0.0, 1234.5])
    def test_an_empty_series_writes_nothing(self, resume: float) -> None:
        assert running_totals([], resume) == []
