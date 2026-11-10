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
from _forecast_plan import (
    by_period_start,
    dayahead_write_plan,
    eligible,
    forecast_is_fresh,
    running_totals,
)


def test_every_forecast_consumer_shares_the_same_expiry_boundary() -> None:
    """The corrected sensors once kept publishing after the outlook's three hours."""
    now = datetime(2026, 11, 1, tzinfo=UTC)
    limit = timedelta(hours=3)
    assert not forecast_is_fresh(None, now, limit)
    assert forecast_is_fresh(now - limit, now, limit)
    assert not forecast_is_fresh(now - limit - timedelta(microseconds=1), now, limit)


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


def per_hour(wh_hours: dict[str, float]) -> dict[int, float]:
    """Energy by UTC hour, floored the way ``normalise_wh_hours`` floors it."""
    out: dict[int, float] = {}
    for raw, value in wh_hours.items():
        hour = datetime.fromisoformat(raw).astimezone(UTC).hour
        out[hour] = out.get(hour, 0.0) + value
    return out


#: One November day as Forecast.Solar sends it: sunrise and sunset stamped to
#: the second, each value the energy of the period *ending* at its key. The
#: endpoints are the real figures from core issue 191026 (Budapest, 2026-11-06).
NOVEMBER_DAY = {
    "2026-11-06T05:35:19+00:00": 0,
    "2026-11-06T06:00:00+00:00": 149,
    "2026-11-06T07:00:00+00:00": 420,
    "2026-11-06T08:00:00+00:00": 700,
    "2026-11-06T09:00:00+00:00": 900,
    "2026-11-06T10:00:00+00:00": 1000,
    "2026-11-06T11:00:00+00:00": 980,
    "2026-11-06T12:00:00+00:00": 900,
    "2026-11-06T13:00:00+00:00": 760,
    "2026-11-06T14:00:00+00:00": 640,
    "2026-11-06T15:00:00+00:00": 598,
    "2026-11-06T15:19:33+00:00": 49,
}


class TestByPeriodStart:
    """Forecast.Solar keys each value by the end of its period. Before Home
    Assistant 2026.11 nobody moved them, and read as period starts every hour
    sat an hour late, with the sunset period folded into the hour before."""

    def test_each_hour_holds_its_own_energy(self) -> None:
        hours = per_hour(by_period_start(NOVEMBER_DAY))

        assert hours[5] == 149  # 05:35:19 to 06:00, the sun's first 25 minutes
        assert hours[6] == 420
        assert hours[14] == 598
        assert hours[15] == 49  # 15:00 to sunset at 15:19:33

    def test_read_raw_the_day_was_an_hour_late(self) -> None:
        """What 0.28.1 and earlier did, and what this replaces: nothing at
        sunrise, and the hour of sunset holding two periods."""
        hours = per_hour(NOVEMBER_DAY)

        assert hours[5] == 0
        assert hours[15] == 598 + 49

    def test_no_energy_is_lost_or_made(self) -> None:
        assert sum(by_period_start(NOVEMBER_DAY).values()) == sum(NOVEMBER_DAY.values())

    def test_a_zero_at_midnight_starts_a_day_rather_than_ending_a_period(self) -> None:
        """Core's one rule at the day boundary. Without it, the sunset key
        would be handed the zero that opens the next day's payload."""
        two_days = {
            "2026-11-06T14:00:00+00:00": 640,
            "2026-11-06T15:00:00+00:00": 598,
            "2026-11-06T15:19:33+00:00": 49,
            "2026-11-07T00:00:00+00:00": 0,
            "2026-11-07T05:36:40+00:00": 0,
            "2026-11-07T06:00:00+00:00": 140,
        }

        assert by_period_start(two_days) == {
            "2026-11-06T14:00:00+00:00": 598,
            "2026-11-06T15:00:00+00:00": 49,
            "2026-11-07T00:00:00+00:00": 0,
            "2026-11-07T05:36:40+00:00": 140,
        }

    def test_midnight_is_judged_in_the_keys_own_offset(self) -> None:
        """Local midnight in Jerusalem is 22:00 UTC, and it is still midnight."""
        local = {
            "2026-11-06T16:00:00+02:00": 40,
            "2026-11-07T00:00:00+02:00": 0,
            "2026-11-07T06:00:00+02:00": 0,
        }

        assert "2026-11-06T16:00:00+02:00" not in by_period_start(local)

    def test_keys_are_ordered_by_instant_not_as_text(self) -> None:
        """The night daylight saving ends, 01:30+03:00 comes before 01:00+02:00.
        Sorted as text the two would swap, and each would get the other's energy."""
        change = {
            "2026-10-25T01:00:00+03:00": 10,
            "2026-10-25T01:30:00+03:00": 20,
            "2026-10-25T01:00:00+02:00": 30,
        }

        assert by_period_start(change) == {
            "2026-10-25T01:00:00+03:00": 20,
            "2026-10-25T01:30:00+03:00": 30,
        }

    def test_nothing_in_nothing_out(self) -> None:
        assert by_period_start({}) == {}
        assert by_period_start({"2026-11-06T06:00:00+00:00": 149}) == {}

    def test_a_key_that_is_not_a_time_is_ignored(self) -> None:
        payload = {"soon": 5, **NOVEMBER_DAY}

        assert by_period_start(payload) == by_period_start(NOVEMBER_DAY)
