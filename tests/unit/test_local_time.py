"""Which local day an hour belongs to, tested against real zone data.

The window is thirty days, so twice a year it contains a daylight-saving change.
A single UTC offset applied across it is right on one side and wrong on the
other, and where it is wrong it is wrong by a whole calendar day for every hour
near local midnight — moving a night's energy into the neighbouring day on
exactly the days the fits are already least trustworthy.

Asia/Jerusalem throughout, because that is the reference installation's zone and
its transitions are asymmetric: forward on a Friday in March, back on a Sunday
in October. A zone that changed on the same weekday both times would hide an
ordering mistake.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from _local_time import local_day

TZ = ZoneInfo("Asia/Jerusalem")

#: Verified against the zone database: forward (23-hour day), back (25-hour day).
SPRING_FORWARD = date(2026, 3, 27)
FALL_BACK = date(2026, 10, 25)


class TestTheDayAnHourBelongsTo:
    """Local midnight is where a day starts, not 00:00 UTC."""

    def test_an_hour_before_local_midnight_belongs_to_the_day_it_is_in(self) -> None:
        """21:00Z in summer is 00:00 the next day in Jerusalem."""
        day, _ = local_day(datetime(2026, 8, 26, 21, tzinfo=UTC), TZ)

        assert day == date(2026, 8, 27)

    def test_the_same_utc_hour_falls_on_the_earlier_day_in_winter(self) -> None:
        """The case a fixed offset gets wrong: +2 in winter, not +3."""
        day, _ = local_day(datetime(2026, 11, 26, 21, tzinfo=UTC), TZ)

        assert day == date(2026, 11, 26)

    def test_a_fixed_summer_offset_would_disagree_with_winter(self) -> None:
        """Stated as a test so the defect cannot quietly return."""
        when = datetime(2026, 11, 26, 21, tzinfo=UTC)
        resolved, _ = local_day(when, TZ)
        naive = (when + timedelta(hours=3)).date()

        assert resolved != naive, "the flat-offset shortcut agrees here by luck"

    def test_midday_is_never_ambiguous(self) -> None:
        day, _ = local_day(datetime(2026, 8, 26, 9, tzinfo=UTC), TZ)

        assert day == date(2026, 8, 26)

    @pytest.mark.parametrize("hour", range(24))
    def test_every_hour_of_a_local_day_agrees_on_the_day(self, hour: int) -> None:
        """The property that makes grouping work at all."""
        local_midnight = datetime(2026, 8, 26, 0, tzinfo=TZ)
        when = (local_midnight + timedelta(hours=hour)).astimezone(UTC)
        day, _ = local_day(when, TZ)

        assert day == date(2026, 8, 26)


class TestTransitionDays:
    """A 23- or 25-hour day breaks the standby term, so it has to be findable."""

    def test_the_spring_day_is_flagged(self) -> None:
        _, odd = local_day(datetime.combine(SPRING_FORWARD, datetime.min.time(), TZ), TZ)

        assert odd is True

    def test_the_autumn_day_is_flagged(self) -> None:
        _, odd = local_day(datetime.combine(FALL_BACK, datetime.min.time(), TZ), TZ)

        assert odd is True

    @pytest.mark.parametrize("offset", [-2, -1, 1, 2])
    def test_the_days_either_side_are_not(self, offset: int) -> None:
        for transition in (SPRING_FORWARD, FALL_BACK):
            day = transition + timedelta(days=offset)
            _, odd = local_day(datetime.combine(day, datetime.min.time(), TZ), TZ)

            assert odd is False, f"{day} was flagged as a transition"

    def test_an_ordinary_summer_day_is_not(self) -> None:
        _, odd = local_day(datetime(2026, 8, 26, 9, tzinfo=UTC), TZ)

        assert odd is False

    def test_exactly_two_days_a_year_are_flagged(self) -> None:
        """Deliberately checked over a whole year, against real zone data."""
        flagged = [
            day
            for index in range(365)
            if (day := date(2026, 1, 1) + timedelta(days=index))
            and local_day(datetime.combine(day, datetime.min.time(), TZ), TZ)[1]
        ]

        assert flagged == [SPRING_FORWARD, FALL_BACK]


class TestNoZone:
    """An unknown day is left unknown rather than guessed at."""

    def test_it_returns_no_day(self) -> None:
        assert local_day(datetime(2026, 8, 26, 9, tzinfo=UTC), None) == (None, False)

    def test_a_zone_without_transitions_flags_nothing(self) -> None:
        day, odd = local_day(datetime(2026, 8, 26, 9, tzinfo=UTC), UTC)

        assert day == date(2026, 8, 26)
        assert odd is False
