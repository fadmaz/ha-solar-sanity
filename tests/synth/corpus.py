"""Every shape a *healthy* installation comes in.

The clean suite's job is to prove the engine stays quiet about houses that have
nothing wrong with them, and for most of this project's life it did that against
one house. ``house.build`` always emitted the same six channels, so
``check_closure`` returned ``CLOSED`` on every scenario it was ever shown and the
open-boundary path — the path the reference installation is reported through —
had never been exercised by a clean-house test at all.

That is the gap this module exists to close. The axes are the ones that change
what the *engine* does, not the ones that merely change the numbers:

``topology``
    Which channels exist and what the owner declared. This is the axis that
    matters: it decides whether the boundary closes, which hypotheses are
    generated, and whether the restricted night-hours path runs.
``season``
    Throughput. A December house moves a fifth of the energy, so an absolute
    residual that is trivial in June is a large percentage in December, and the
    absolute floors are the only thing standing between that and a false
    accusation.
``gap``
    Sensors that stop reporting. Every channel's outages union together, so a
    few percent of dropouts per channel is a noticeable fraction of lost hours.
``losses``
    Energy the identity cannot see: generation metered before the inverter, a
    battery metered on its DC side, an inverter's own supply. These are facts
    about where the sensors are, not faults, and absorbing them is the entire
    job of the loss model. This axis was missing from the first version of this
    corpus, and its absence is the same omission a design review had already
    caught once: testing the loss terms one at a time proves nothing about a
    house that has both, because they interact.
``noise``
    Meters that disagree with each other by a couple of percent, which is what
    correctly installed meters do.

Every topology here balances **exactly** — verified hour by hour, not assumed.
A fixture that does not close the identity is a house that cannot exist, and
asserting silence on one proves nothing about the engine. That is not a
hypothetical: ``split_arrays`` replaced the generation curve with a smoothed
copy of itself and adjusted nothing else, leaving up to 1,121 Wh an hour
unaccounted for, and six scenarios rested on it.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from analysis.model import AnalysisRequest, Answer, ChannelSpec, DeclaredTopology, Role

from tests.synth import house
from tests.synth.adapt import extra_spec, specs_for, to_request
from tests.synth.house import Series

SIX: Final = ("pv", "grid_export", "grid_import", "battery_charge", "battery_discharge", "load")
NO_EXPORT: Final = ("pv", "grid_import", "battery_charge", "battery_discharge", "load")
NO_BATTERY: Final = ("pv", "grid_export", "grid_import", "load")


def _self_consumed(series: Series) -> Series:
    """Surplus goes to the battery, so nothing ever leaves the house.

    Not ``house.drop("grid_export")``. That removes the *measurement* and leaves
    the export happening, which is a real fault with a real finding — and after
    the verdict window widened it is reported as one. The honest clean case for
    an unmapped export path is a house that genuinely never exports, which is
    what a correctly sized battery on a self-consumption tariff produces.
    """
    charge = [
        c + e
        for c, e in zip(series.data["battery_charge"], series.data["grid_export"], strict=True)
    ]
    return series.copy_with(battery_charge=charge, grid_export=[0.0] * series.hours)


def _two_arrays(series: Series) -> Series:
    return house.two_aspects(series, "pv", "pv_west", tilt=0.4)


@dataclass(frozen=True, slots=True)
class Losses:
    """Measurement losses a healthy installation genuinely has.

    Every value here is inside what the loss model is built to absorb. The
    efficiencies beyond them — a DC battery below 0.90 round trip, say — are a
    real shape of real installation, but the engine does not yet reach a verdict
    on one, so they belong in a test that records that boundary rather than in a
    gate that has to stay green. See ``test_clean_corpus.py``.
    """

    name: str
    pv: float = 1.0
    battery: float = 1.0
    standby_w: float = 0.0

    def applied(self, series: Series) -> Series:
        if self.pv < 1.0:
            series = house.measure_pv_dc(series, efficiency=self.pv)
        if self.battery < 1.0:
            series = house.measure_battery_dc(series, efficiency=self.battery)
        if self.standby_w:
            series = house.add_standby(series, watts=self.standby_w)
        return series


LOSSES: Final[tuple[Losses, ...]] = (
    Losses("none"),
    Losses("dc_pv", pv=0.96),
    Losses("dc_battery", battery=0.95),
    Losses("both_dc", pv=0.96, battery=0.95),
    # The author's own installation, and the one shipped first.
    Losses("both_dc_standby", pv=0.96, battery=0.95, standby_w=25.0),
)


@dataclass(frozen=True, slots=True)
class Topology:
    """One shape of installation: its channels, and what its owner declared."""

    name: str
    keys: tuple[str, ...]
    declared: DeclaredTopology
    shape: object = None
    extra: tuple[ChannelSpec, ...] = ()
    battery_wh: float = 10000.0

    def series(self, *, seed: int, kwp: float, losses: Losses | None = None) -> Series:
        built = house.build(days=30, seed=seed, kwp=kwp, battery_wh=self.battery_wh)
        shaped = built if self.shape is None else self.shape(built)  # type: ignore[operator]
        return shaped if losses is None else losses.applied(shaped)

    def specs(self) -> tuple[ChannelSpec, ...]:
        return specs_for(self.keys) + self.extra


def _declared(battery: Answer, net: Answer) -> DeclaredTopology:
    return DeclaredTopology(
        has_battery=battery,
        grid_is_single_net_sensor=net,
        load_covers_whole_house=Answer.YES,
    )


TOPOLOGIES: Final[tuple[Topology, ...]] = (
    Topology("full", SIX, _declared(Answer.YES, Answer.NO)),
    Topology("self_consumed", NO_EXPORT, _declared(Answer.YES, Answer.NO), shape=_self_consumed),
    Topology("net_meter", NO_EXPORT, _declared(Answer.YES, Answer.YES), shape=house.merge_to_net),
    Topology("no_battery", NO_BATTERY, _declared(Answer.NO, Answer.NO), battery_wh=0.0),
    Topology(
        "two_arrays",
        SIX,
        _declared(Answer.YES, Answer.NO),
        shape=_two_arrays,
        extra=(extra_spec("pv_west", Role.PV, "Solar west"),),
    ),
)

#: Throughput. Winter is not a milder summer — it is a fifth of the energy, and
#: the percentage bands see a trivial absolute residual as a large one.
SEASONS: Final[dict[str, float]] = {"summer": 6.0, "winter": 1.2}

#: What correctly installed meters do to each other.
NOISE: Final[tuple[float, ...]] = (0.0, 0.02, 0.05)

GAPS: Final[tuple[str, ...]] = ("none", "dropouts")

#: Share of each channel's hours lost when the gap axis is on.
#:
#: Deliberately modest. Outages union across channels, so five channels each
#: losing this many hours independently costs the day about three of its
#: twenty-four — close to `MIN_VALID_BUCKETS_PER_DAY` without routinely
#: crossing it, which would turn a coverage test into a "days were dropped"
#: test and quietly stop exercising anything.
DROPOUT_SHARE: Final = 0.02

#: Real sensors do not lose scattered single hours; they go away and come back.
MAX_OUTAGE_HOURS: Final = 3


def _dropouts(keys: tuple[str, ...], hours: int, *, seed: int) -> dict[str, set[int]]:
    """Contiguous outages per channel, independently placed."""
    rng = random.Random(seed)
    absent: dict[str, set[int]] = {}
    for key in keys:
        lost: set[int] = set()
        target = int(hours * DROPOUT_SHARE)
        while len(lost) < target:
            run = rng.randint(1, MAX_OUTAGE_HOURS)
            start = rng.randrange(hours)
            lost.update(h for h in range(start, min(start + run, hours)))
        absent[key] = lost
    return absent


@dataclass(frozen=True, slots=True)
class Case:
    """One scenario, small enough to parametrize 3,000 of them by."""

    topology: str
    losses: str
    season: str
    noise: float
    gap: str
    seed: int

    @property
    def label(self) -> str:
        return f"{self.topology}-{self.losses}-{self.season}-n{self.noise}-{self.gap}-s{self.seed}"


def _topology(name: str) -> Topology:
    for topology in TOPOLOGIES:
        if topology.name == name:
            return topology
    raise KeyError(name)


def _losses(name: str) -> Losses:
    for losses in LOSSES:
        if losses.name == name:
            return losses
    raise KeyError(name)


def build(case: Case) -> AnalysisRequest:
    """The request for one case. Built here rather than at collection time so
    3,000 scenarios cost 3,000 ids rather than 3,000 windows of buckets."""
    topology = _topology(case.topology)
    series = topology.series(seed=case.seed, kwp=SEASONS[case.season], losses=_losses(case.losses))
    if case.noise:
        series = house.add_noise(series, case.noise, seed=case.seed + 9_000)

    keys = topology.keys + tuple(spec.key for spec in topology.extra)
    absent = (
        _dropouts(keys, series.hours, seed=case.seed + 4_000) if case.gap == "dropouts" else None
    )
    return to_request(series, specs=topology.specs(), declared=topology.declared, missing=absent)


#: Seeds for the full corpus. Five topologies x five loss profiles x two
#: seasons x three noise levels x two gap settings x ten seeds is exactly 3,000.
FULL_SEEDS: Final = range(10)

#: Seeds for the gate that runs on every pull request. Same cross product, two
#: seeds: 600 scenarios, every axis combination still covered.
FAST_SEEDS: Final = range(0, 10, 5)


def cases(seeds: range) -> Iterator[Case]:
    """The full cross product, in a stable order."""
    for topology in TOPOLOGIES:
        for losses in LOSSES:
            for season in SEASONS:
                for noise in NOISE:
                    for gap in GAPS:
                        for seed in seeds:
                            yield Case(topology.name, losses.name, season, noise, gap, seed)


def balance_error(case: Case) -> float:
    """Worst hourly gap in the identity, for this case's topology.

    The corpus asserts silence, and silence about a house that cannot exist is
    not evidence of anything. Every generator is checked against this rather
    than trusted.

    Deliberately measured **without** the loss profile applied. A measurement
    loss is precisely an hourly gap in this identity — generation metered before
    the inverter genuinely does exceed what reaches the house — so applying one
    and then demanding the identity close would be asking the fixture to be two
    contradictory things. What has to be exact is the topology underneath: that
    a net meter, a self-consuming battery or a split array still describes a
    house where energy is conserved. The loss on top is the thing the engine is
    being asked about.
    """
    topology = _topology(case.topology)
    series = topology.series(seed=case.seed, kwp=SEASONS[case.season])
    data = series.data
    generation = [k for k in data if k.startswith("pv")]

    worst = 0.0
    for hour in range(series.hours):
        supply = sum(data[k][hour] for k in generation) + data["grid_import"][hour]
        drain = data["load"][hour] + data["grid_export"][hour]
        if "battery_discharge" in data:
            supply += data["battery_discharge"][hour]
            drain += data["battery_charge"][hour]
        worst = max(worst, abs(supply - drain))
    return worst
