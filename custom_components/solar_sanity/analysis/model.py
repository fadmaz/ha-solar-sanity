"""Data contract for the Solar Sanity analysis engine.

This module — and every module in the ``analysis`` package — must import nothing
from Home Assistant. Purity is enforced structurally: only absolute intra-package
imports are used, so the package can be imported with ``homeassistant`` absent.
See ``tests/analysis/test_invariants.py``.

Every value that can be absent is ``None``. ``None`` never becomes a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    #: Which terms were actually established. A term the fit rejected stays at
    #: 0.0, which is byte-identical to a term genuinely measured as lossless —
    #: so without this, "we could not tell" and "there is no loss here" are the
    #: same answer, and the second one gets asserted downstream as fact.
    fitted_terms: tuple[str, ...] = ()

    @property
    def fitted(self) -> bool:
        """Whether anything was actually established.

        Not ``samples > 0``: that was true of a model whose every term had been
        rejected, which the coordinator then persisted and fed back as the prior
        for the next run.
        """
        return bool(self.fitted_terms)

    def established(self, term: str) -> bool:
        """Whether one named term was fitted rather than defaulted."""
        return term in self.fitted_terms


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
    #: Channels the recorder holds no history for. One of these makes every
    #: bucket invalid, because a bucket needs every balance channel — so the
    #: honest answer is to name it rather than report a shortage of days.
    unrecorded_keys: tuple[str, ...] = ()
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
    #: The identity provably does not close, whether or not we can say why.
    #: ``INVESTIGATING`` is reached by two routes with very different weight —
    #: "the numbers move around" and "the numbers do not add up" — and the
    #: status alone cannot tell them apart, so the entity layer would have to
    #: re-derive the difference from engine internals to answer honestly.
    identity_fails: bool = False
    #: Things worth saying that are not findings: never a fault, never a
    #: Repairs issue, never an alarm. What a system's own shape makes
    #: uncheckable belongs here — it is not a defect and there may be nothing
    #: the user can do about it, but leaving it unsaid means a verdict that
    #: quietly covers less than the user thinks.
    notes: tuple[str, ...] = ()
    #: Numbers that were measured and then not acted on, so a diagnosis does not
    #: have to guess at them. A rejected fit leaves its term at 0.0 and says
    #: nothing about what it saw, which makes "we could not explain this"
    #: unfalsifiable from outside — the same failure as a status that only says
    #: it is still looking.
    measurements: dict[str, float] = field(default_factory=dict)
    deferred: tuple[str, ...] = ()
    topology: TopologyEstimate = field(default_factory=TopologyEstimate)
    loss_model: LossModel | None = None
    residual: ResidualSummary = field(default_factory=ResidualSummary)
    stale_corrections: tuple[str, ...] = ()
    reason: str = ""
