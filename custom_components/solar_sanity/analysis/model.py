"""The shapes the battery outlook and the forecast scoring share.

An hour of energy per channel (``Bucket``), what each channel measures
(``Role``, ``ChannelSpec``), and how far each value can be trusted
(``Quality``, ``BucketSource``).

This module, like every module in the ``analysis`` package, imports nothing
from Home Assistant; ``tests/analysis/test_purity.py`` enforces it.

Every value that can be absent is ``None``. ``None`` never becomes a number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class Role(Enum):
    """What a channel measures, and which side of the identity it sits on.

    ``sign`` is the coefficient in ``R = sum(sign * value)``. Sources are +1,
    sinks are -1. Channels that take no part in the balance carry 0.
    """

    PV = ("pv", 1)
    GRID_IMPORT = ("grid_import", 1)
    BATTERY_DISCHARGE = ("battery_discharge", 1)
    LOAD = ("load", -1)
    GRID_EXPORT = ("grid_export", -1)
    BATTERY_CHARGE = ("battery_charge", -1)
    BATTERY_SOC = ("battery_soc", 0)

    def __init__(self, key: str, sign: int) -> None:
        self.key = key
        self.sign = sign

    @property
    def in_balance(self) -> bool:
        """Whether this role contributes a term to the energy identity."""
        return self.sign != 0


class Quality(Enum):
    """Why a bucket value may not be trustworthy."""

    OK = "ok"
    MISSING = "missing"
    STALE = "stale"
    RESET_SUSPECT = "reset_suspect"
    DERIVED_FROM_MEAN = "derived_from_mean"


class BucketSource(Enum):
    """How a bucket value was obtained.

    ``LTS_MEAN`` is the weak one, though not for the reason this docstring gave
    for most of the project's life. It said an hourly mean over an
    event-reporting sensor over-weights volatile hours. That is false, and it
    was checked against Home Assistant's own source rather than argued about:
    ``sensor/recorder.py::_time_weighted_arithmetic_mean`` weights every state
    by how long it was held and divides by the span, and the hourly figure is
    ``func.avg`` over twelve equal five-minute rows. For an hour with all twelve
    present, mean times one hour *is* the integral this integration computes.

    It is weak when the hour is **incomplete**, which is a different and
    narrower thing. ``func.avg`` over eight rows returns the average of those
    eight and the hour is then treated as though they were all of it; and where
    there was no prior state at all, the time-weighted mean moves its own start
    forward, so a channel that begins reporting mid-hour is averaged over only
    the part anybody watched. Both are ordinary on an MQTT-bridged inverter that
    publishes after Home Assistant has started.

    So the widened tolerance is right and the reason is imputation rather than
    weighting. What ``OWN_INTEGRAL`` carries that the recorder cannot
    reconstruct is not a better number — it is the attestation that the channel
    was watched end to end for that whole hour with no gap worth the name.
    """

    OWN_INTEGRAL = "own_integral"
    LTS_SUM = "lts_sum"
    LTS_MEAN = "lts_mean"


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    """A configured measurement channel.

    ``origin`` matters: an autodetected channel is less trustworthy than one the
    user confirmed, so findings that rest on it are downgraded one confidence
    step.
    """

    key: str
    role: Role
    entity_id: str
    friendly_name: str
    declared_unit: str
    parent_key: str | None = None
    origin: str = "user"

    @property
    def autodetected(self) -> bool:
        return self.origin == "autodetected"


@dataclass(frozen=True, slots=True)
class Bucket:
    """One hour of energy, in Wh, for every channel.

    A value of ``None`` in ``wh`` means the channel had no trustworthy reading
    for this hour. It is never imputed and never defaulted to zero.
    """

    start_utc: datetime
    seconds: int
    wh: dict[str, float | None]
    quality: dict[str, Quality]
    source: dict[str, BucketSource]
    solar_elevation_deg: float | None = None
    is_dst_transition: bool = False
    #: The local calendar day this hour belongs to, resolved where the time zone
    #: is known.
    #:
    #: Carried as data rather than derived here, because deriving it needs a
    #: time zone and this package has no clock and no zone database — that is
    #: what keeps ``analyse`` byte-identical for identical input. A single
    #: offset applied across a window is not a substitute: it is wrong on one
    #: side of every daylight-saving change, and wrong by a whole day for the
    #: hours either side of local midnight.
    local_date: date | None = None

    #: Qualities whose value may still be used. A mean-derived reading is
    #: usable but weaker — the tolerance is widened for it and it cannot support
    #: a certain finding — so it must not be discarded outright the way a
    #: missing, stale or reset-suspect reading is.
    _USABLE = (Quality.OK, Quality.DERIVED_FROM_MEAN)

    def value(self, key: str) -> float | None:
        """Return the usable value for ``key``, or ``None``."""
        if self.quality.get(key, Quality.MISSING) not in self._USABLE:
            return None
        return self.wh.get(key)
