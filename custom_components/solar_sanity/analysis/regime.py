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

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import Enum

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


class Cause(Enum):
    """Why a channel's throughput stepped.

    A sensor that starts telling the truth and a machine that starts doing
    something different are indistinguishable inside the energy balance. Both are
    a jump in one column with nothing else moving, and no amount of arithmetic on
    the other columns separates them — the balance is precisely where a
    mis-reporting meter hides, because whatever it fails to report is still
    conserved and simply turns up as residual.

    State of charge settles it, because it is not in the balance. It comes from
    the battery management system rather than from the energy meters, so a meter
    that under-reports by a factor of five leaves it completely unmoved. Compare
    the daily state-of-charge swing either side of the step and the question
    answers itself: unchanged means the battery was always doing this work and
    only its reporting changed; changed means the equipment did.

    An earlier attempt did this from the residual instead and was withdrawn. The
    quantity it compared reduced algebraically to ``pv + import - load - export``
    — the battery terms cancelled — so it was blind to the very thing it claimed
    to measure, and on a house with any unmetered path it called a genuine
    equipment change a reporting one. That is the failure this design cannot
    have: state of charge is an observation of the battery itself.
    """

    #: No state of charge mapped, not a storage channel, or too few days.
    UNDETERMINED = "undetermined"
    #: The meter changed what it says. The *older* figures were the wrong ones.
    REPORTING = "reporting"
    #: The equipment changed what it does.
    BEHAVIOUR = "behaviour"


#: How close the daily state-of-charge swing must be, either side, to count as
#: unchanged.
#:
#: A third. Not tighter: a battery's daily depth varies with weather and use, and
#: the reference installation's own swing ranges from 17% to 65% within a single
#: unchanged regime. Demanding agreement to a few percent would be reading noise.
SAME_WITHIN = 1.33

#: How far apart before the equipment is blamed instead.
#:
#: Deliberately a wide gap from ``SAME_WITHIN``: anything landing between them is
#: UNDETERMINED and the note falls back to offering both causes. Half the value
#: of this is knowing when not to answer.
DIFFERENT_BEYOND = 2.0


def attribute(
    days: tuple[DayResidual, ...],
    change: RegimeChange,
    specs: tuple[ChannelSpec, ...],
    soc_daily_swing: Mapping[date, float],
) -> Cause:
    """Whether the meter changed or the battery did.

    ``soc_daily_swing`` is peak-to-trough state of charge per local day, in
    percent, taken from a sensor that is deliberately outside the energy balance.
    Empty when the user has not mapped one, which is the common case and returns
    ``UNDETERMINED`` immediately.

    Refuses freely: a step in something that is not storage, fewer than
    ``MIN_REGIME_DAYS`` of state of charge either side, and any ratio that does
    not land clearly. An unanswered question is better than a guessed one.
    """
    if not soc_daily_swing:
        return Cause.UNDETERMINED

    stepped = next((s.role for s in specs if s.key == change.channel_key), None)
    if stepped not in (Role.BATTERY_CHARGE, Role.BATTERY_DISCHARGE):
        return Cause.UNDETERMINED

    def swings(wanted) -> list[float]:
        return [soc_daily_swing[d.day] for d in days if wanted(d.day) and d.day in soc_daily_swing]

    before = swings(lambda day: day < change.day)
    after = swings(lambda day: day >= change.day)
    if len(before) < MIN_REGIME_DAYS or len(after) < MIN_REGIME_DAYS:
        return Cause.UNDETERMINED

    was, now = median(before), median(after)
    if not was or not now:
        return Cause.UNDETERMINED

    ratio = now / was
    if 1.0 / SAME_WITHIN <= ratio <= SAME_WITHIN:
        return Cause.REPORTING
    if ratio >= DIFFERENT_BEYOND or ratio <= 1.0 / DIFFERENT_BEYOND:
        return Cause.BEHAVIOUR
    return Cause.UNDETERMINED


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


def note_for(
    change: RegimeChange,
    role: Role,
    days_since: int,
    window: int,
    cause: Cause = Cause.UNDETERMINED,
) -> str:
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

    # Two causes offered and neither asserted, which is the whole point.
    #
    # The first version said "that is usually a settings change rather than a
    # fault". It is a guess dressed as a fact, and on the only real installation
    # this project has it appears to be the wrong one: the owner changed nothing,
    # and the balance says the battery had been cycling all along while its
    # sensor under-reported — the day/night residual swing that used to be there
    # collapsed on the same day the reported throughput jumped.
    #
    # A sensor that starts telling the truth and a setting that gets changed look
    # identical from inside the window. What separates them is state of charge,
    # which this integration does not read and the owner can see in one click.
    if cause is Cause.REPORTING:
        middle = (
            "though your battery carried on doing the same work — its charge level swung "
            "about as far each day before as it does now, and that reading comes from the "
            "battery itself rather than from the meter. So the meter started reporting "
            "what was already happening, and it is the figures from before that were "
            "wrong, not these"
        )
    elif cause is Cause.BEHAVIOUR:
        middle = (
            "and your battery really is doing different work — its charge level swings a "
            "different amount each day now, measured from the battery itself rather than "
            "from the meter. Something altered how it runs, not how it reports"
        )
    else:
        middle = (
            "while everything else stayed where it was. Either the equipment changed how it "
            "runs, or its sensor changed what it reports — mapping your battery's charge "
            "level in the options lets Solar Sanity tell you which, because that reading "
            "comes from the battery rather than from the meter that changed"
        )

    return (
        f"On {when_changed} your {name} started moving {direction} energy per day "
        f"({change.before_wh / 1000:.1f} kWh before, {change.after_wh / 1000:.1f} kWh after), "
        f"{middle}. Everything here is measured over the "
        f"{days_since} days since, because an average across that change would describe "
        f"neither side of it — which means a full verdict {when}."
    )
