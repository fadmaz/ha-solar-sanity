"""Where the battery is heading: one projection, five read-outs.

Every answer this module gives comes from a single simulation of the battery's
charge, an hour at a time, from now until the forecast runs out:

* when it will reach its charging target;
* when it will reach its reserve, if that happens before the sun takes over;
* the lowest it will fall before the sun takes over;
* the steadiest load the house could draw until then without hitting reserve;
* how much sunshine the battery will have no room for.

They are read off one trajectory rather than computed five ways, so they cannot
contradict each other — a "full at 15:20" beside a "nothing spare today" would
be two models disagreeing, and the user would be right to trust neither.

Hourly steps, not finer ones. A forecast is energy per period and the profiles
are energy per hour, so within one step the net flow is constant and the charge
moves in a straight line. That makes the moment it crosses full or reserve
exact rather than interpolated, and the spill and shortfall exact with it. The
first step is the part-hour from now to the next hour boundary.

**Grid charging is never counted.** The projection answers "if the grid were
out, would the battery get me there?" — the question somebody with frequent
outages needs answered, and the conservative one for everybody else.

Pure, like the rest of ``analysis``: no clock, no time zone, no Home Assistant.
``now`` and the local hour are passed in.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

#: How far ahead to project when the forecast allows it. Far enough to reach
#: tomorrow's takeover from any time today, and beyond it to tomorrow's
#: "spare" — the read-out somebody planning a wash at 21:00 wants.
HORIZON_HOURS = 36

#: A step is dark when its expected production is below this, in W ...
DARK_MIN_W = 10.0
#: ... or below this share of the horizon's brightest hour, whichever is
#: higher. Relative because a December noon and a June noon differ by five
#: times, and an absolute threshold that suits one calls the other night.
DARK_PEAK_SHARE = 0.10

#: The battery counts as full this many points below its target. A battery that
#: charges to 100% spends a long time at 99% while the charger tapers, and a
#: "full at" that waits for the last point is late by most of an hour.
FULL_MARGIN_PCT = 1.0

#: Energy the battery may fail to cover, in Wh, and the load still count as
#: safe. Not zero: the clamp holds the charge exactly at reserve, and comparing
#: floats for equality there flips the answer on rounding alone.
SHORTFALL_TOLERANCE_WH = 1.0

#: Published times move in steps of this size, and only when the new estimate
#: differs from the published one by more than one step. A time that wobbles by
#: a minute on every refresh reads as a broken sensor.
PUBLISH_STEP = timedelta(minutes=5)

#: Bisection rounds for the safe load. Forty halvings of a 10 kW bracket is far
#: below a watt.
_BISECTION_ROUNDS = 40


@dataclass(frozen=True, slots=True)
class BatterySettings:
    """What the projection needs to know about the battery.

    ``target_pct`` is the level the battery is charged *to* — usually 100, lower
    where the inverter is set to stop early. Charging stops there in the
    simulation, and anything beyond it is spill.

    The efficiencies are defaults until the battery's own figure is learned:
    0.95 on the way in, 0.92 on the way out to the house, the latter including
    the inverter's conversion.
    """

    capacity_wh: float
    reserve_pct: float
    target_pct: float
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.92
    #: The fastest the battery has been seen to charge, as Wh per hour on the
    #: battery side. ``None`` means no ceiling is known.
    max_charge_w: float | None = None
    #: The inverter's own draw in W, on top of the house load at every hour.
    #: Learned (see ``profile.standby_w``); zero until it is.
    standby_w: float = 0.0


@dataclass(frozen=True, slots=True)
class Step:
    """One stretch of the projection, with its energy in Wh.

    ``daylight_wh`` is the expected production from the forecast or profile
    alone. It differs from ``pv_wh`` only in the first step, where ``pv_wh`` may
    be what was actually measured this hour, and it is what darkness is judged
    on — so a cloud passing now cannot open a night at noon.
    """

    start: datetime
    hours: float
    pv_wh: float
    load_wh: float
    daylight_wh: float

    @property
    def end(self) -> datetime:
        return self.start + timedelta(hours=self.hours)


@dataclass(frozen=True, slots=True)
class Run:
    """One simulation. ``soc`` has one more entry than there are steps."""

    soc: tuple[float, ...]
    times: tuple[datetime, ...]
    full_at: datetime | None
    reserve_at: datetime | None
    spilled_wh: tuple[float, ...]
    shortfall_wh: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class Night:
    """The next stretch the battery must carry the house through alone.

    ``start`` is the first step of the deficit that runs into darkness, which
    may be now. ``end`` is the takeover — the first later step in which
    production covers consumption — and is ``None`` when the horizon ends
    before it.
    """

    start: int
    end: int | None


@dataclass(frozen=True, slots=True)
class Outlook:
    """Everything the sensors publish, read off one trajectory."""

    soc_pct: float
    full_at: datetime | None
    reserve_at: datetime | None
    lowest_pct: float | None
    lowest_at: datetime | None
    night_from: datetime | None
    takeover_at: datetime | None
    safe_load_w: float | None
    #: Energy the battery cannot cover tonight at the expected load, in Wh. Zero
    #: when it lasts; otherwise what would come from the grid, or not at all.
    shortfall_wh: float | None
    spare_wh: float | None
    spare_from: datetime | None
    horizon_end: datetime
    forecast_used: bool
    trajectory: tuple[tuple[datetime, float], ...]


def build_steps(
    now: datetime,
    *,
    load: Sequence[float | None],
    local_hour: Callable[[datetime], int],
    pv_forecast: Mapping[datetime, float] | None,
    pv_profile: Sequence[float | None] | None,
    anchor: tuple[float, float] | None = None,
    max_hours: int = HORIZON_HOURS,
) -> tuple[Step, ...]:
    """The steps to simulate, or an empty tuple when they cannot be built.

    ``load`` is indexed by local hour and ``pv_profile`` by UTC hour (see
    ``profile.py`` for why). ``pv_forecast`` maps UTC hour starts to Wh.

    A forecast lists only the hours it predicts production in, so an hour
    missing *inside* its span is a night hour and counts as zero, while the
    horizon ends at the last hour it covers: past that point nothing is known,
    and splicing history on would put yesterday's weather into tomorrow.

    ``anchor`` is this hour's measured production and consumption, in W, used
    for the rest of the current hour in place of the expectation.

    A hole in the consumption profile returns nothing. It is never filled.
    """
    hour0 = now.replace(minute=0, second=0, microsecond=0)
    last = max(pv_forecast) if pv_forecast else None
    steps: list[Step] = []

    for index in range(max_hours + 1):
        hour = hour0 + timedelta(hours=index)
        if pv_forecast is not None:
            if last is None or hour > last:
                break
            pv_full: float | None = pv_forecast.get(hour, 0.0)
        elif pv_profile is not None:
            pv_full = pv_profile[hour.hour % 24]
        else:
            break
        if pv_full is None:
            break

        load_full = load[local_hour(hour) % 24]
        if load_full is None:
            return ()

        if index == 0:
            remaining = (hour + timedelta(hours=1) - now).total_seconds() / 3600.0
            if remaining <= 0:
                continue
            if anchor is not None:
                pv_wh, load_wh = anchor[0] * remaining, anchor[1] * remaining
            else:
                pv_wh, load_wh = pv_full * remaining, load_full * remaining
            steps.append(Step(now, remaining, pv_wh, load_wh, pv_full * remaining))
        else:
            steps.append(Step(hour, 1.0, pv_full, load_full, pv_full))

    return tuple(steps)


def simulate(
    soc_pct: float,
    battery: BatterySettings,
    steps: Sequence[Step],
    *,
    constant_load_w: float | None = None,
) -> Run:
    """Carry the charge through every step.

    A surplus charges the battery up to its target, at no more than its known
    ceiling, and whatever does not fit is spilled. A deficit discharges it down
    to reserve, and whatever it cannot cover is shortfall. With
    ``constant_load_w`` the profile's load is replaced by a flat draw, which is
    how the safe load is searched for.

    The inverter's standby is added to the load either way. The safe load is
    what the *house* may draw, and the inverter draws its own on top.
    """
    cap = battery.capacity_wh
    floor = cap * battery.reserve_pct / 100.0
    ceiling = cap * battery.target_pct / 100.0
    full_level = max(cap * (battery.target_pct - FULL_MARGIN_PCT) / 100.0, floor)
    eta_in = battery.charge_efficiency
    eta_out = battery.discharge_efficiency

    energy = cap * soc_pct / 100.0
    start = steps[0].start if steps else None
    full_at = start if energy >= full_level else None
    reserve_at = start if energy <= floor else None

    soc = [soc_pct]
    times = [start] if start is not None else []
    spilled: list[float] = []
    shortfall: list[float] = []

    for step in steps:
        load = step.load_wh if constant_load_w is None else constant_load_w * step.hours
        load += battery.standby_w * step.hours
        net = step.pv_wh - load
        lost = 0.0
        unmet = 0.0
        if net >= 0:
            rate = net * eta_in / step.hours
            if battery.max_charge_w is not None:
                rate = min(rate, battery.max_charge_w)
            stored = min(rate * step.hours, max(ceiling - energy, 0.0))
            if full_at is None and rate > 0 and energy < full_level <= energy + stored:
                full_at = step.start + timedelta(hours=(full_level - energy) / rate)
            energy += stored
            lost = net - stored / eta_in
        else:
            rate = -net / eta_out / step.hours
            available = max(energy - floor, 0.0)
            drawn = min(rate * step.hours, available)
            if reserve_at is None and available > 0 and rate * step.hours >= available:
                reserve_at = step.start + timedelta(hours=available / rate)
            energy -= drawn
            unmet = (rate * step.hours - drawn) * eta_out
        soc.append(energy / cap * 100.0)
        times.append(step.end)
        spilled.append(max(lost, 0.0))
        shortfall.append(max(unmet, 0.0))

    return Run(
        soc=tuple(soc),
        times=tuple(times),
        full_at=full_at,
        reserve_at=reserve_at,
        spilled_wh=tuple(spilled),
        shortfall_wh=tuple(shortfall),
    )


def _dark(steps: Sequence[Step]) -> list[bool]:
    peak = max((step.daylight_wh / step.hours for step in steps), default=0.0)
    threshold = max(DARK_MIN_W, DARK_PEAK_SHARE * peak)
    return [step.daylight_wh / step.hours < threshold for step in steps]


def night_window(steps: Sequence[Step], first: int = 0) -> Night | None:
    """The next night at or after step ``first``, anchored on darkness.

    Not simply "production below consumption": an overcast noon is that too,
    and calling it night would turn "lowest before sunrise" into "lowest before
    the cloud passes". The window opens where the evening deficit that runs into
    darkness begins and closes at the first later step in which production
    covers consumption again.

    With ``first`` past the start, darkness is looked for only after a light
    step, so the search for the *following* night is not satisfied by the dim
    morning hours right after a takeover.
    """
    dark = _dark(steps)
    begin = first
    if first > 0:
        begin = next((i for i in range(first, len(steps)) if not dark[i]), len(steps))
    darkest = next((i for i in range(begin, len(steps)) if dark[i]), None)
    if darkest is None:
        return None

    start = darkest
    while start > begin and steps[start - 1].pv_wh < steps[start - 1].load_wh:
        start -= 1

    end = next(
        (i for i in range(darkest + 1, len(steps)) if steps[i].pv_wh >= steps[i].load_wh),
        None,
    )
    return Night(start=start, end=end)


def safe_average_load(soc_pct: float, battery: BatterySettings, steps: Sequence[Step]) -> float:
    """The highest flat load, in W, these steps can carry without hitting reserve.

    Searched rather than solved, because morning production inside the window
    makes the closed form wrong. Sound because shortfall can only grow as the
    load grows — every clamp and efficiency in ``simulate`` preserves that.
    """
    tolerance = SHORTFALL_TOLERANCE_WH

    def short(load_w: float) -> float:
        return sum(simulate(soc_pct, battery, steps, constant_load_w=load_w).shortfall_wh)

    if short(0.0) > tolerance:
        return 0.0
    hours = sum(step.hours for step in steps)
    produced = sum(step.pv_wh for step in steps)
    high = max(1000.0, 2.0 * (battery.capacity_wh + produced) / hours)
    while short(high) <= tolerance and high < 1e7:
        high *= 2.0

    low = 0.0
    for _ in range(_BISECTION_ROUNDS):
        middle = (low + high) / 2.0
        if short(middle) <= tolerance:
            low = middle
        else:
            high = middle
    return low


def project(
    soc_pct: float,
    battery: BatterySettings,
    steps: Sequence[Step],
    *,
    forecast_used: bool,
) -> Outlook:
    """Run the projection and read every answer off it.

    ``full_at`` and ``spare`` need a forecast. Without one the production
    expectation comes from history, and on an inverter with nowhere to send a
    surplus, history records production *curtailed* to what the house and the
    battery could take — so it understates the sun on exactly the days there is
    sun to spare. The night read-outs need no such care: by evening the battery
    has room, and the takeover comes with the morning, when it has room too.
    """
    run = simulate(soc_pct, battery, steps)
    night = night_window(steps)

    lowest_pct = lowest_at = night_from = takeover_at = None
    safe_load_w = shortfall_wh = None
    if night is not None:
        stop = night.end if night.end is not None else len(steps)
        lowest = min(range(night.start, stop + 1), key=lambda i: (run.soc[i], i))
        lowest_pct = run.soc[lowest]
        lowest_at = run.times[lowest]
        night_from = steps[night.start].start
        shortfall_wh = sum(run.shortfall_wh[night.start : stop])
        if night.end is not None:
            takeover_at = steps[night.end].start
            safe_load_w = safe_average_load(
                run.soc[night.start], battery, steps[night.start : night.end]
            )

    reserve_at = run.reserve_at
    if reserve_at is not None and takeover_at is not None and reserve_at > takeover_at:
        reserve_at = None

    spare_wh = spare_from = None
    if forecast_used and night is not None:
        day: range | None = None
        if night.start > 0:
            day = range(night.start)
        elif night.end is not None:
            following = night_window(steps, first=night.end)
            if following is not None:
                day = range(night.end, following.start)
        if day is not None and len(day) > 0:
            spare_wh = sum(run.spilled_wh[i] for i in day)
            spare_from = steps[day.start].start

    return Outlook(
        soc_pct=soc_pct,
        full_at=run.full_at if forecast_used else None,
        reserve_at=reserve_at,
        lowest_pct=lowest_pct,
        lowest_at=lowest_at,
        night_from=night_from,
        takeover_at=takeover_at,
        safe_load_w=safe_load_w,
        shortfall_wh=shortfall_wh,
        spare_wh=spare_wh,
        spare_from=spare_from,
        horizon_end=steps[-1].end,
        forecast_used=forecast_used,
        trajectory=tuple(zip(run.times, run.soc, strict=True)),
    )


def round_time(when: datetime) -> datetime:
    """``when`` to the nearest publish step, on the hour's own grid."""
    step_minutes = PUBLISH_STEP.total_seconds() / 60.0
    minutes = when.minute + when.second / 60.0 + when.microsecond / 60_000_000.0
    snapped = math.floor(minutes / step_minutes + 0.5) * step_minutes
    return when.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=snapped)


def settle(previous: datetime | None, candidate: datetime | None, now: datetime) -> datetime | None:
    """The time to publish, given the one already published.

    Three rules, each stopping a different kind of flapping:

    * the estimate is rounded to the publish step;
    * a new estimate within one step of the published one is not news;
    * once the moment has passed, the published time is kept — "full at 14:35"
      should go on saying 14:35 while the battery sits full, not advance to the
      current time on every refresh.
    """
    if candidate is None:
        return None
    rounded = round_time(candidate)
    if previous is None:
        return rounded
    if abs(rounded - previous) <= PUBLISH_STEP:
        return previous
    if candidate <= now and previous <= now:
        return previous
    return rounded
