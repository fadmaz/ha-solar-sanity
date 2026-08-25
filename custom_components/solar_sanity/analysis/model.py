"""Data contract for the Solar Sanity analysis engine.

This module — and every module in the ``analysis`` package — must import nothing
from Home Assistant. Purity is enforced structurally: only absolute intra-package
imports are used, so the package can be imported with ``homeassistant`` absent.
See ``tests/analysis/test_invariants.py``.

Every value that can be absent is ``None``. ``None`` never becomes a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
    SUBLOAD = ("subload", 0)
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

    ``LTS_MEAN`` is the weak one: an arithmetic hourly mean over an
    event-reporting power sensor over-weights volatile hours. Buckets from this
    source get a widened tolerance and may not support a ``certain`` finding.
    """

    OWN_INTEGRAL = "own_integral"
    LTS_SUM = "lts_sum"
    LTS_MEAN = "lts_mean"


class Confidence(Enum):
    CERTAIN = "certain"
    HIGH = "high"
    PROBABLE = "probable"

    def downgrade(self) -> Confidence:
        if self is Confidence.CERTAIN:
            return Confidence.HIGH
        if self is Confidence.HIGH:
            return Confidence.PROBABLE
        return Confidence.PROBABLE


class Severity(Enum):
    """How a finding is presented.

    ``QUESTION`` exists because a ``probable`` finding must never be asserted as
    a fault — it is shown as something we are asking, not something we know.
    """

    FAULT = "fault"
    NOTE = "note"
    QUESTION = "question"


class Status(Enum):
    """The five honest outcomes.

    Most systems are ``INSUFFICIENT_DATA`` on day one and many stay
    ``INVESTIGATING`` indefinitely. That is a correct answer, not a failure.
    """

    OK = "ok"
    INSUFFICIENT_DATA = "insufficient_data"
    NOT_CHECKABLE = "not_checkable"
    INVESTIGATING = "investigating"
    FAULT_FOUND = "fault_found"


class Coupling(Enum):
    UNKNOWN = "unknown"
    AC_COUPLED = "ac_coupled"
    DC_COUPLED = "dc_coupled"


class Answer(Enum):
    """A user's answer to a setup question. ``UNKNOWN`` defers to inference."""

    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


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

    def value(self, key: str) -> float | None:
        """Return the trustworthy value for ``key``, or ``None``."""
        if self.quality.get(key, Quality.MISSING) is not Quality.OK:
            return None
        return self.wh.get(key)


@dataclass(frozen=True, slots=True)
class LiveSnapshot:
    """An instantaneous power reading across all channels, in W.

    Only populated when every channel updated recently enough to be comparable.
    Used exclusively for mutual-exclusion and stuck/stale checks.
    """

    taken_utc: datetime
    watts: dict[str, float | None]
    age_seconds: dict[str, float]


@dataclass(frozen=True, slots=True)
class Correction:
    """A diagnostic override applied inside Solar Sanity only.

    Never auto-applied. Never written to another integration's entities.
    """

    channel_key: str
    kind: str
    factor: float | None = None
    applied_by_user: bool = True


@dataclass(frozen=True, slots=True)
class DeclaredTopology:
    """What the user told us at setup. Any of it may be ``UNKNOWN``."""

    has_battery: Answer = Answer.UNKNOWN
    grid_is_single_net_sensor: Answer = Answer.UNKNOWN
    load_covers_whole_house: Answer = Answer.UNKNOWN


@dataclass(frozen=True, slots=True)
class LossModel:
    """Fitted, per-installation expectation of genuine loss.

    Subtracted from the residual *before* any fault test. This is how expected
    loss is separated from a fault: loss is small, stable and proportional to a
    known channel; a fault snaps to a physical constant.
    """

    pv_dc_gamma: float = 0.0
    battery_dc_gamma: float = 0.0
    standby_w: float = 0.0
    samples: int = 0

    @property
    def fitted(self) -> bool:
        return self.samples > 0


@dataclass(frozen=True, slots=True)
class TopologyEstimate:
    coupling: Coupling = Coupling.UNKNOWN
    pv_measured_dc: bool | None = None
    battery_measured_dc: bool | None = None
    grid_is_net: bool | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Evidence:
    """One concrete number backing a finding.

    Findings carry these so the user can check the claim rather than trust it.
    """

    label: str
    value: float
    unit: str
    window_days: int


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    severity: Severity
    confidence: Confidence
    channel_keys: tuple[str, ...]
    headline: str
    detail: str
    source_fix: str
    evidence: tuple[Evidence, ...] = ()
    offered_correction: Correction | None = None
    explained_fraction: float = 0.0
    margin: float = 0.0
    days_supporting: int = 0
    days_evaluated: int = 0


@dataclass(frozen=True, slots=True)
class ResidualSummary:
    median_daily_abs_pct: float | None = None
    valid_days: int = 0
    total_abs_wh: float = 0.0
    band: str = "unknown"


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    """Everything ``analyse`` needs. No clock, no I/O, no HA types.

    ``now_utc`` is injected rather than read from a clock so the whole engine is
    deterministic and testable.
    """

    now_utc: datetime
    specs: tuple[ChannelSpec, ...]
    buckets: tuple[Bucket, ...]
    live_snapshots: tuple[LiveSnapshot, ...] = ()
    declared: DeclaredTopology = field(default_factory=DeclaredTopology)
    active_corrections: tuple[Correction, ...] = ()
    suppressed_codes: tuple[str, ...] = ()
    loss_model: LossModel | None = None
    #: The installation's UTC offset, so buckets group into *local* days.
    #: Grouping by UTC splits the solar curve across two days anywhere far
    #: from Greenwich, which breaks the storage probe, the standby fit and
    #: every daily asymmetry test. This is user configuration, not a clock,
    #: so the engine stays pure.
    utc_offset_hours: float = 0.0

    def spec(self, key: str) -> ChannelSpec | None:
        for s in self.specs:
            if s.key == key:
                return s
        return None


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """The result. At most one finding, ever.

    An uncorrected fault dominates the residual and makes every downstream
    statistic meaningless, so we report one, let the user act, then look again.
    """

    status: Status
    finding: Finding | None = None
    deferred: tuple[str, ...] = ()
    topology: TopologyEstimate = field(default_factory=TopologyEstimate)
    loss_model: LossModel | None = None
    residual: ResidualSummary = field(default_factory=ResidualSummary)
    stale_corrections: tuple[str, ...] = ()
    reason: str = ""
