"""When the installation stopped being the same installation.

Everything else in this package assumes the thirty days in the window describe
one system. That assumption is invisible until it breaks, and when it breaks it
does not announce itself as a fault — it quietly poisons every statistic
computed over the window.

The reference installation is where this was found. On one day its battery
throughput went from about 6 kWh to about 31 kWh, five times over, while
generation and consumption barely moved: somebody had started charging from the
grid overnight. Pooled across that change the joint loss fit returns a
generation coefficient of **0.3125** — outside any window that would accept it,
so no loss is subtracted and the model that exists to explain a DC-measured
inverter explains nothing. Fitted on the eleven days *after* the change alone it
returns **0.0400**, a 96%-efficient inverter, which is exactly what that house
is. The model was never broken. It was being asked about two houses at once.

The verdict was wrong in a quieter way. Before the change 97.4% of the hourly
error cancelled within the day; after it, 78.6%. The report told its owner
"nearly all of it cancels out" — true of the pooled month, and substantially
less true of how their house had run for the last eleven days. An average
across a change describes neither side of it.

So: find the change, analyse only what came after it, and say so. Refusing a
verdict for a fortnight is a real cost and it is the honest one — there is no
way to have fourteen days of evidence about a system that is eleven days old.

**Deliberately hard to trigger.** A false positive here withholds a verdict from
a healthy house, so the test is full separation with a multiplicative margin: no
day on one side may come within ``STEP_RATIO`` of any day on the other. Seasonal
drift cannot pass it — generation on the reference installation runs 22-32 kWh
before the change and 17-29 after, overlapping heavily, and is correctly
ignored. What passes is a step: 4.9-7.4 against 23.4-36.5, disjoint by 3.2x.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .linalg import median
from .model import ChannelSpec, Role
from .residual import DayResidual

#: Days required on each side before a split is considered at all.
#:
#: Not a statistical threshold — a definitional one. Two days either side of a
#: boundary is not two regimes, it is a weekend, and calling it a regime change
#: would withhold a verdict every time somebody ran the dishwasher twice.
MIN_REGIME_DAYS = 5

#: How far apart the two sides must be, as a multiple.
#:
#: Applied to the *extremes* rather than the averages: the quietest day of the
#: busy period must still beat the busiest day of the quiet one by this much.
#: That is a much stronger claim than a difference of means, and it is the
#: claim we want — a step, not a shift.
STEP_RATIO = 2.0


@dataclass(frozen=True)
class RegimeChange:
    """A day on which this installation started behaving like a different one."""

    #: First day of the current regime. Everything before it is another system.
    day: date
    #: The channel whose throughput stepped. Named because the user will want to
    #: know what changed, and "something changed" is not an answer they can act on.
    channel_key: str
    #: Median daily throughput either side, in Wh, for the sentence.
    before_wh: float
    after_wh: float

    @property
    def factor(self) -> float:
        """How many times larger the current regime is. Below 1 means smaller."""
        if self.before_wh <= 0.0:
            return float("inf")
        return self.after_wh / self.before_wh


def _daily_throughput(day: DayResidual, key: str) -> float:
    """One channel's unsigned energy for one day.

    Unsigned, because a battery that cycles evenly nets to nothing and the whole
    point is to see how hard it is working. ``None`` hours contribute zero
    rather than disqualifying the day — a day is already required to be complete
    to reach here, and a channel that is briefly absent should not be able to
    fake a step by going quiet.
    """
    total = 0.0
    for bucket in day.buckets:
        value = bucket.value(key)
        if value is not None:
            total += abs(value)
    return total


def _splits_cleanly(before: list[float], after: list[float]) -> bool:
    """Whether no day on one side comes within ``STEP_RATIO`` of any on the other.

    Extremes, not means. A difference of means is satisfied by a gradual drift
    with a lot of days on each end, which is exactly the thing this must not
    fire on.
    """
    if not before or not after:
        return False
    if min(after) > STEP_RATIO * max(before):
        return True
    return min(before) > STEP_RATIO * max(after)


def find_latest_change(
    days: tuple[DayResidual, ...], specs: tuple[ChannelSpec, ...]
) -> RegimeChange | None:
    """The most recent day on which a channel's daily throughput stepped.

    The *most recent*, not the largest. Two changes in a window leave three
    regimes and only the newest one describes the system as it is now; taking
    the biggest step would analyse a period that has itself already been
    superseded.

    Each side is compared against *everything* before it rather than against the
    neighbouring segment alone, which makes a later boundary qualify only when
    the current regime is clear of the whole history. That is conservative in
    the useful direction: a house that steps up and then partly back down has
    one qualifying boundary, not two, and the window it keeps is the longer one.

    Returns ``None`` when nothing qualifies, which is the overwhelmingly common
    case and the one to keep cheap.
    """
    keys = [spec.key for spec in specs if spec.role.in_balance]
    if len(days) < 2 * MIN_REGIME_DAYS or not keys:
        return None

    best: RegimeChange | None = None
    for key in keys:
        series = [_daily_throughput(day, key) for day in days]
        # Walk from the end so the first hit for this channel is its latest.
        for cut in range(len(days) - MIN_REGIME_DAYS, MIN_REGIME_DAYS - 1, -1):
            before, after = series[:cut], series[cut:]
            if not _splits_cleanly(before, after):
                continue
            # `_splits_cleanly` already refused both empty sides, so neither
            # median is None. Read back rather than asserted: an assert in an
            # analysis path is a crash in somebody's dashboard, and `or 0.0`
            # would turn a genuine zero into a missing one.
            before_wh, after_wh = median(before), median(after)
            if before_wh is None or after_wh is None:
                continue
            candidate = RegimeChange(
                day=days[cut].day,
                channel_key=key,
                before_wh=before_wh,
                after_wh=after_wh,
            )
            if best is None or candidate.day > best.day:
                best = candidate
            break

    return best


#: What to call each role in a sentence.
#:
#: The same words the setup screen used, so the vocabulary somebody learned when
#: they mapped their sensors is the vocabulary they meet again here.
#:
#: Deliberately the *role*, never the channel's friendly name, and the first
#: version of this got that wrong in a way only production showed.
#: ``_friendly_name`` falls back to the entity id when a state carries no
#: ``friendly_name`` attribute, which is ordinary on an MQTT-bridged inverter —
#: the reference installation was told "your
#: sensor.siseli_inverter_1_..._battery_charge_energy started moving roughly 6
#: times more energy per day". Worse, diagnostics do not carry friendly names at
#: all, so a note built from one cannot survive a replay, and the replay
#: reproducing the verdict *including its notes* is the only non-synthetic test
#: this project owns. ``_generation_name`` in ``engine.py`` had already written
#: that down; this did not read it.
_ROLE_NAMES = {
    Role.PV: "solar generation",
    Role.LOAD: "house consumption",
    Role.GRID_IMPORT: "grid import",
    Role.GRID_EXPORT: "grid export",
    Role.BATTERY_CHARGE: "battery charging",
    Role.BATTERY_DISCHARGE: "battery discharging",
}


def note_for(change: RegimeChange, role: Role, days_since: int, window: int) -> str:
    """What the user is told. One sentence of fact, one of consequence.

    Names what changed and when, because "something changed" is not something
    anybody can check, and the owner almost always knows what they did — at
    which point this stops being a warning and becomes a confirmation.
    """
    name = _ROLE_NAMES.get(role, role.key.replace("_", " "))
    direction = f"roughly {change.factor:.0f} times more"
    if change.factor < 1.0:
        direction = f"roughly {1.0 / change.factor:.0f} times less"

    remaining = max(0, window - days_since)
    when = "tomorrow" if remaining <= 1 else f"in about {remaining} days"

    # `%-d` is glibc-only and raises on Windows, where the pure suite runs.
    when_changed = f"{change.day.day} {change.day:%B}"

    return (
        f"On {when_changed} your {name} started moving {direction} energy per day "
        f"({change.before_wh / 1000:.1f} kWh before, {change.after_wh / 1000:.1f} kWh after), "
        f"and nothing else changed with it. That is usually a settings change rather than a "
        f"fault. Everything here is measured over the {days_since} days since, because an "
        f"average across that change would describe neither side of it — which means a full "
        f"verdict {when}."
    )
