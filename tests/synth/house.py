"""A physically consistent synthetic solar house, and ways to break it.

The generator asserts the energy identity closes to within 1e-9 Wh per hour
*before* any corruption is applied. Every fault fixture is then a pure
``Series -> Series`` function, so a test that injects a sign flip is testing
exactly one thing.

Seeded throughout. No wall-clock, no unseeded randomness — the same seed always
produces the same house, which is what makes three thousand scenarios a usable
CI gate rather than a source of flake.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from datetime import UTC, datetime

CHANNELS = ("pv", "grid_import", "grid_export", "battery_charge", "battery_discharge", "load")


@dataclass(frozen=True, slots=True)
class Series:
    """Hourly Wh per channel, all non-negative, all AC-side truth."""

    start: datetime
    hours: int
    data: dict[str, list[float]]

    def copy_with(self, **channels: list[float]) -> Series:
        merged = {k: list(v) for k, v in self.data.items()}
        merged.update({k: list(v) for k, v in channels.items()})
        return replace(self, data=merged)

    def residual(self, hour: int) -> float:
        d = self.data
        return (
            d["pv"][hour]
            + d["grid_import"][hour]
            + d["battery_discharge"][hour]
            - d["load"][hour]
            - d["grid_export"][hour]
            - d["battery_charge"][hour]
        )

    def assert_closes(self, tolerance: float = 1e-9) -> None:
        for hour in range(self.hours):
            error = abs(self.residual(hour))
            if error > tolerance:
                raise AssertionError(f"synthetic house does not close at hour {hour}: {error} Wh")


def _solar_bell(hour_of_day: int, kwp: float, cloud: float) -> float:
    """Clear-sky bell scaled by array size and a cloud factor in [0, 1]."""
    if not 6 <= hour_of_day <= 18:
        return 0.0
    phase = (hour_of_day - 6) / 12.0
    shape = math.sin(phase * math.pi) ** 1.6
    return kwp * 1000.0 * 0.82 * shape * cloud


def _baseline_load(hour_of_day: int, rng: random.Random) -> float:
    base = 250.0 + rng.uniform(-30.0, 30.0)
    if 6 <= hour_of_day <= 8:
        base += 900.0
    if 17 <= hour_of_day <= 21:
        base += 1300.0
    return base


def build(
    *,
    days: int = 30,
    seed: int = 0,
    kwp: float = 6.0,
    battery_wh: float = 10000.0,
    charge_efficiency: float = 1.0,
    discharge_efficiency: float = 1.0,
    start: datetime | None = None,
) -> Series:
    """Generate a clean house whose identity closes exactly.

    Efficiencies default to 1.0 so the base fixture is *exactly* balanced;
    :func:`measure_battery_dc` introduces realistic loss when a test wants it.
    """
    rng = random.Random(seed)
    origin = start or datetime(2026, 3, 1, tzinfo=UTC)
    hours = days * 24

    data: dict[str, list[float]] = {c: [0.0] * hours for c in CHANNELS}
    soc = battery_wh * 0.5

    for day in range(days):
        cloud = rng.uniform(0.35, 1.0)
        for hour_of_day in range(24):
            index = day * 24 + hour_of_day

            pv = _solar_bell(hour_of_day, kwp, cloud)
            load = _baseline_load(hour_of_day, rng)
            if hour_of_day == 19 and rng.random() < 0.4:
                load += 2500.0  # oven
            if hour_of_day == 2 and day % 3 == 0:
                load += 7400.0  # EV

            surplus = pv - load
            charge = discharge = grid_import = grid_export = 0.0

            if surplus > 0:
                room = (battery_wh - soc) / charge_efficiency
                charge = min(surplus, room, 5000.0)
                soc += charge * charge_efficiency
                grid_export = surplus - charge
            else:
                need = -surplus
                available = soc * discharge_efficiency
                discharge = min(need, available, 5000.0)
                soc -= discharge / discharge_efficiency
                grid_import = need - discharge

            data["pv"][index] = pv
            data["load"][index] = load
            data["battery_charge"][index] = charge
            data["battery_discharge"][index] = discharge
            data["grid_import"][index] = grid_import
            data["grid_export"][index] = grid_export

    series = Series(start=origin, hours=hours, data=data)
    series.assert_closes()
    return series


# --------------------------------------------------------------------------
# Corruptors. Each one breaks exactly one thing.
# --------------------------------------------------------------------------


def invert(series: Series, channel: str) -> Series:
    """Sign inversion: the magnitude is right, the direction is backwards."""
    return series.copy_with(**{channel: [-v for v in series.data[channel]]})


def scale(series: Series, channel: str, factor: float) -> Series:
    """Unit error: kW reported as W, or the reverse."""
    return series.copy_with(**{channel: [v * factor for v in series.data[channel]]})


def duplicate_into(series: Series, source: str, target: str) -> Series:
    """Double counting: one physical flow mapped to two channels."""
    return series.copy_with(**{target: list(series.data[source])})


def drop(series: Series, channel: str) -> Series:
    """A channel that exists physically but is not measured."""
    return series.copy_with(**{channel: [0.0] * series.hours})


def to_cumulative(series: Series, channel: str, start_value: float) -> Series:
    """A lifetime total mapped into a periodic slot."""
    running = start_value
    out: list[float] = []
    for value in series.data[channel]:
        running += value
        out.append(running)
    return series.copy_with(**{channel: out})


def merge_to_net(series: Series) -> Series:
    """A single signed net meter placed in the import slot, export left empty."""
    net = [
        series.data["grid_import"][i] - series.data["grid_export"][i] for i in range(series.hours)
    ]
    return series.copy_with(grid_import=net, grid_export=[0.0] * series.hours)


def net_meter_beside_export(series: Series) -> Series:
    """A signed net meter in the import slot while export is *also* mapped.

    The mistake worth reporting. Every exported hour is now counted twice: once
    as a negative in the net channel, and again in the export channel it was
    already being measured by.
    """
    net = [
        series.data["grid_import"][i] - series.data["grid_export"][i] for i in range(series.hours)
    ]
    return series.copy_with(grid_import=net)


def two_aspects(series: Series, channel: str, target: str, tilt: float = 0.4) -> Series:
    """Split one array into two genuinely separate ones, on different aspects.

    The adversary for duplicate detection, and the reason it cannot be built out
    of resemblance. The two channels sum to the original hour for hour, so the
    house still balances exactly — these are two real halves of a real array,
    not a copy of one.

    ``tilt`` slides the share across the day: east takes more in the morning,
    west more in the afternoon. At ``0.4`` the pair correlates at 0.89, close to
    the 0.83 the project plan warns about. At ``0.0`` the two are byte-identical
    curves correlating at 1.0000 with a ratio of exactly one — indistinguishable
    from a duplicated sensor by any statistic of the channels themselves, and
    the case any threshold on correlation or ratio is going to get wrong.
    """
    values = series.data[channel]
    lead: list[float] = []
    trail: list[float] = []
    for index, value in enumerate(values):
        hour = index % 24
        share = min(1.0, max(0.0, 0.5 + tilt * (12 - hour) / 12.0))
        lead.append(value * share)
        trail.append(value * (1.0 - share))
    return series.copy_with(**{channel: lead, target: trail})


def halve(series: Series, channel: str) -> Series:
    """One current clamp on a supply that has two live conductors."""
    return series.copy_with(**{channel: [v * 0.5 for v in series.data[channel]]})


def freeze(series: Series, channel: str, from_hour: int) -> Series:
    """A sensor that stops changing but keeps reporting."""
    values = list(series.data[channel])
    stuck = values[from_hour] if from_hour < len(values) else 0.0
    for i in range(from_hour, len(values)):
        values[i] = stuck
    return series.copy_with(**{channel: values})


def measure_pv_dc(series: Series, efficiency: float = 0.96) -> Series:
    """Generation measured before the inverter — a topology fact, not a fault.

    The reported number is larger than what reaches the house, by exactly the
    conversion loss.
    """
    return series.copy_with(pv=[v / efficiency for v in series.data["pv"]])


def measure_battery_dc(series: Series, efficiency: float = 0.95) -> Series:
    """Battery measured on its DC side: positive gamma on *both* directions."""
    return series.copy_with(
        battery_charge=[v * efficiency for v in series.data["battery_charge"]],
        battery_discharge=[v / efficiency for v in series.data["battery_discharge"]],
    )


def add_standby(series: Series, watts: float) -> Series:
    """A steady draw that nothing meters — an inverter's own power supply."""
    return series.copy_with(
        load=[v - watts for v in series.data["load"]],
    )


def add_noise(series: Series, pct: float, seed: int = 7) -> Series:
    """Independent meter error on every channel, in quadrature."""
    rng = random.Random(seed)
    out: dict[str, list[float]] = {}
    for channel, values in series.data.items():
        out[channel] = [v * (1.0 + rng.uniform(-pct, pct)) for v in values]
    return series.copy_with(**out)


def split_arrays(series: Series, lag_hours: int = 2) -> Series:
    """Two genuinely separate arrays on different roof aspects.

    Correlated, but time-shifted — this must *not* be reported as a duplicate.
    """
    pv = series.data["pv"]
    shifted = [0.0] * lag_hours + pv[:-lag_hours] if lag_hours else list(pv)
    combined = [(a + b) / 2.0 for a, b in zip(pv, shifted, strict=True)]
    return series.copy_with(pv=combined)
