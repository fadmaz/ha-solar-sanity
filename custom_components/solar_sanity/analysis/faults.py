"""Fault codes, the snap-to-physics table, and the exact user-facing copy.

The governing rule of the whole engine lives here: **snap to physics or stay
silent.** Every fault reduces to estimating one number per channel,

    R_h = sum(gamma_c * u_c,h) + L_h        gamma_c = 1 - a_c

and ``gamma`` snaps to a small set of physically meaningful values. If an
estimate lands between them — say 1.43 — that is not a fault we can name, so we
say nothing. Forever, if necessary. This is the primary defence against false
positives, and false positives are the one thing that would kill this product.

Copy rules, enforced by ``tests/analysis/test_invariants.py``:
  * No currency, ever. No prices, bills, savings or tariffs.
  * Direction words, never signs: "runs 12% under", not "-12% error".
  * Say what was measured, then what it means. Never a bare percentage.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Confidence


class Code:
    """Stable fault identifiers. These appear in entity attributes and repairs."""

    # Stage A - categorical, per-channel, near-certain
    STUCK = "stuck_channel"
    STALE = "stale_channel"
    UNIT_SCALE_1000 = "unit_scale_1000"
    CUMULATIVE_IN_PERIODIC = "cumulative_in_periodic_field"
    SIGNED_NET_IN_DEDICATED = "signed_net_in_dedicated_slot"
    SIGNED_NET_BATTERY = "signed_net_battery_slot"
    CHANNELS_SWAPPED = "channels_swapped"
    DUPLICATE_CHANNEL = "duplicate_channel_pair"
    SIMULTANEOUS_FLOW = "simultaneous_opposing_flow"

    # Stage B - inferred from the residual
    SIGN_INVERTED = "channel_sign_inverted"
    DOUBLE_COUNTED = "double_counted_channel"
    SUBMETER_IN_PARENT = "submeter_included_in_parent"
    MISSING_STORAGE = "missing_storage_channel"
    MISSING_EXPORT = "missing_export_channel"
    MISSING_GENERATION = "missing_generation_channel"
    PARTIAL_COVERAGE = "partial_ct_coverage"
    LOAD_BOUNDARY = "load_boundary_mismatch"

    # Stage C - calibration observations, never alarming
    PV_MEASURED_DC = "pv_measured_dc"
    BATTERY_MEASURED_DC = "battery_measured_dc"
    UNMETERED_STANDBY = "unmetered_standby"

    # Housekeeping
    CORRECTION_NOW_HARMFUL = "correction_now_harmful"
    UNEXPLAINED = "unexplained_residual"


@dataclass(frozen=True, slots=True)
class Snap:
    """One entry in the snap-to-physics table.

    ``low``/``high`` bound the gamma window. ``max_iqr`` requires the estimate to
    be *tight* as well as centred — a wide spread means we are looking at noise
    that happens to average to a fault-shaped number.
    """

    code: str
    low: float
    high: float
    max_iqr: float
    a: float
    correction_kind: str | None
    confidence: Confidence
    bidirectional_only: bool = False


#: The complete set of faults we are willing to name from a gamma estimate.
#: Anything outside these windows is reported as nothing at all.
SNAP_TABLE: tuple[Snap, ...] = (
    # a = -1: the channel's sign is backwards. Only meaningful for a channel
    # that can legitimately flow both ways.
    Snap(Code.SIGN_INVERTED, 1.85, 2.15, 0.20, -1.0, "sign_flip", Confidence.HIGH, True),
    # a = 0: the channel contributes nothing real - its energy is already inside
    # another configured channel.
    Snap(Code.DOUBLE_COUNTED, 0.90, 1.10, 0.15, 0.0, "drop_channel", Confidence.HIGH),
    # a = 2: the channel sees exactly half. One CT on a split-phase service, or
    # one of two MPPT strings.
    Snap(Code.PARTIAL_COVERAGE, -1.10, -0.90, 0.20, 2.0, None, Confidence.HIGH),
    # a = 3: one of three phases.
    Snap(Code.PARTIAL_COVERAGE, -2.10, -1.90, 0.20, 3.0, None, Confidence.HIGH),
    # a = 1000: kW reported as W, or kWh as Wh.
    Snap(Code.UNIT_SCALE_1000, -1000.5, -998.5, 0.05, 1000.0, "scale", Confidence.CERTAIN),
    # a = 0.001: the reverse.
    Snap(Code.UNIT_SCALE_1000, 0.9985, 0.9995, 0.05, 0.001, "scale", Confidence.CERTAIN),
)

#: Gamma windows that indicate a *topology fact*, not a fault. These feed the
#: loss model instead of raising anything.
DC_MEASUREMENT_WINDOW = (0.02, 0.10)
DC_MEASUREMENT_MAX_IQR = 0.03

#: Above this, DC-side measurement stops being a plausible explanation and the
#: loss is large enough to be worth mentioning.
DC_MEASUREMENT_FAULT_THRESHOLD = 0.15


_TEMPLATES: dict[str, tuple[str, str, str]] = {
    # code: (headline, detail, source_fix)
    Code.SIGN_INVERTED: (
        "{name} is counted the wrong way round",
        "When {name} reads a positive number, the energy is actually flowing the "
        "other way. The magnitude is right; the direction is backwards. Over the "
        "last {days} days this explains {explained:.0f}% of the energy that does "
        "not add up.",
        "Some inverters report battery power positive-on-charge and others "
        "positive-on-discharge. Check whether your integration offers a polarity "
        "option, or wrap the sensor in a template that negates it.",
    ),
    Code.DOUBLE_COUNTED: (
        "{name} is being counted twice",
        "The energy in {name} is already included in another sensor you have "
        "mapped, so counting it separately inflates the total. It accounts for "
        "{explained:.0f}% of the mismatch over {days} days.",
        "Remove {name} from the Solar Sanity configuration, or map the other "
        "sensor instead — whichever measures the boundary you actually care about.",
    ),
    Code.DUPLICATE_CHANNEL: (
        "{name} and {other} are the same energy measured twice",
        "These two track each other at {correlation:.0%} with a ratio near one. "
        "That is one flow measured in two places — typically before and after an "
        "inverter — not two separate flows.",
        "Map only one of them. If one is measured on the AC side, prefer that "
        "one: it is what actually reaches your house.",
    ),
    Code.UNIT_SCALE_1000: (
        "{name} is out by a factor of a thousand",
        "{name} reports values around {observed:.1f} while the rest of your "
        "system sits near {expected:.1f}. That is the signature of a sensor "
        "publishing one unit while declaring another — watts labelled as "
        "kilowatts, or watt-hours labelled as kilowatt-hours.",
        "Correct the unit on the source sensor. In an MQTT or template sensor "
        "that is the unit_of_measurement; on a Modbus integration it is usually "
        "a scale factor on the register.",
    ),
    Code.CUMULATIVE_IN_PERIODIC: (
        "{name} is a lifetime total, not a daily figure",
        "{name} reads {observed:,.0f} and only ever increases. That is the total "
        "since your system was installed. Its daily increase — about "
        "{daily:,.1f} — is the number this slot needs.",
        "Map a daily or hourly sensor instead. Most inverter integrations expose "
        "both; if yours does not, a utility_meter helper with a daily cycle will "
        "derive one.",
    ),
    Code.SIGNED_NET_IN_DEDICATED: (
        "{name} is a single net meter, not a one-way sensor",
        "{name} goes negative for part of most sunny days. That means it "
        "measures net flow — positive one way, negative the other — rather than "
        "only one direction. Mapped as one-way, everything on the other side is "
        "being counted as a negative.",
        "Map only one of the two. A single meter that swings both ways belongs "
        "in the import slot with export left empty — Solar Sanity reads the "
        "negatives as export. Mapping a second sensor alongside it counts the "
        "same energy twice.",
    ),
    Code.SIGNED_NET_BATTERY: (
        "{name} measures both directions at once",
        "{name} goes negative for part of most days. A sensor mapped to one "
        "battery direction should only ever report that direction; one that "
        "swings both ways is a net figure, so charging and discharging cancel "
        "each other out inside a single channel and neither is counted "
        "properly.",
        "Map your battery's charge and discharge sensors separately — most "
        "inverters expose both. If yours only publishes the net figure, leave "
        "the other slot empty rather than mapping a second sensor alongside it, "
        "or the same energy is counted twice.",
    ),
    Code.CHANNELS_SWAPPED: (
        "{name} and {other} look swapped",
        "{name} records most of its energy at night, when generation is zero. "
        "That pattern belongs to the other slot.",
        "Swap the two entities in the Solar Sanity configuration.",
    ),
    Code.SIMULTANEOUS_FLOW: (
        "Two sensors report opposite flows at the same moment",
        "{name} and {other} both showed substantial flow simultaneously on "
        "{count} occasions over {days} days. That cannot happen at a single "
        "connection point — one of them is not measuring what it is mapped to.",
        "Check where each current clamp is physically installed. A clamp placed "
        "downstream of where solar joins measures house consumption, not grid flow.",
    ),
    Code.MISSING_STORAGE: (
        "Something is storing energy that nothing measures",
        "About {daily:,.1f} kWh a day builds up while you have surplus, then "
        "drains again overnight. It stops at the same level on every sunny day, "
        "which is how a battery behaves, and nothing in your configuration "
        "measures it.",
        "Map your battery's charge and discharge sensors, or its single net "
        "power sensor if that is what your inverter exposes.",
    ),
    Code.MISSING_EXPORT: (
        "You appear to be exporting, but nothing measures it",
        "Whenever generation exceeds consumption, energy goes missing — and at "
        "no other time. That is export leaving the house unmeasured.",
        "Map a grid export sensor. If your meter reports a single signed value, "
        "map that to the net-grid slot instead.",
    ),
    Code.MISSING_GENERATION: (
        "Something is generating that nothing measures",
        "Energy appears during daylight only, following the shape of a "
        "generation curve, and it does not track the array you have mapped.",
        "If you have a second array or a second inverter, map it too.",
    ),
    Code.PARTIAL_COVERAGE: (
        "{name} sees about {fraction} of what it should",
        "The missing energy tracks {name} almost exactly, in a fixed ratio. That "
        "is the signature of a current clamp on some but not all of the live "
        "conductors.",
        "Check how many conductors your supply has and how many are clamped. A "
        "split-phase or three-phase service needs a clamp on each.",
    ),
    Code.LOAD_BOUNDARY: (
        "Something is being consumed that {name} does not see",
        "About {daily:,.1f} kWh a day is used that {name} does not record, at "
        "all hours including overnight.",
        "This usually means the sensor covers a backup or critical-loads panel "
        "rather than the whole house.",
    ),
    Code.SUBMETER_IN_PARENT: (
        "{name} is already included in {other}",
        "Every time {name} draws power, {other} rises by the same amount. It is "
        "measured inside the larger total, so counting it separately doubles it.",
        "Remove {name} from the balance. You can still track it separately for "
        "your own interest — it just must not be added twice.",
    ),
    Code.STUCK: (
        "{name} has stopped changing",
        "{name} has reported exactly {observed:g} for {hours:.0f} hours while "
        "everything else moved. A sensor that never changes is not measuring.",
        "Check the device is still reachable. Until it recovers, Solar Sanity "
        "cannot check anything else.",
    ),
    Code.STALE: (
        "{name} has stopped updating",
        "{name} last reported {hours:.0f} hours ago. Everything else is current.",
        "Check the integration that provides it.",
    ),
    Code.PV_MEASURED_DC: (
        "Your generation sensor reads before the inverter",
        "About {loss:.0f}% of what {name} reports never reaches your house. "
        "That is normal conversion loss, and it means the sensor measures the "
        "panels directly rather than the inverter's output. Accounted for; "
        "nothing to fix.",
        "",
    ),
    Code.BATTERY_MEASURED_DC: (
        "Your battery sensors read on the DC side",
        "Charging and discharging both lose a few percent against the rest of "
        "the system. That is what round-trip efficiency looks like when it is "
        "measured before conversion. Accounted for; nothing to fix.",
        "",
    ),
    Code.UNMETERED_STANDBY: (
        "About {watts:.0f} W flows continuously that nothing measures",
        "That is the scale of an inverter's own power supply, and it is steady "
        "day and night. Normal for this equipment, and now part of the model.",
        "",
    ),
    Code.CORRECTION_NOW_HARMFUL: (
        "A correction on {name} is no longer needed",
        "The adjustment applied to {name} now makes the numbers worse rather "
        "than better. That usually means the underlying sensor has been fixed.",
        "Remove the correction in the Solar Sanity settings.",
    ),
    Code.UNEXPLAINED: (
        "The numbers do not add up, and I cannot say why yet",
        "About {pct:.0f}% of your energy is unaccounted for, but the pattern "
        "does not match anything I can name with confidence. Patterns usually "
        "declare themselves given another week or two.",
        "",
    ),
}


def render(code: str, **fields: object) -> tuple[str, str, str]:
    """Return ``(headline, detail, source_fix)`` for a fault code.

    Missing template fields raise, deliberately — a half-rendered sentence with
    a stray ``{name}`` in it is worse than a crash in CI.
    """
    headline, detail, fix = _TEMPLATES[code]
    return (
        headline.format(**fields),
        detail.format(**fields),
        fix.format(**fields) if fix else "",
    )


def known_codes() -> frozenset[str]:
    return frozenset(_TEMPLATES)
