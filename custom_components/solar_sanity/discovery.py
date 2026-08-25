"""Finding the user's energy sensors, and saying how sure we are.

The strategy is inherited from the predecessor and it is the right one: the user
already told Home Assistant the truth when they set up the Energy Dashboard, so
start from the statistics they declared there and expand outward to sibling
entities on the same device and config entry.

Four things are done differently, and each fixes a real defect in the original:

* **Energy sensors are candidates too.** The predecessor gated every match on a
  power unit, which meant the kWh statistics the Energy Dashboard is actually
  built on were collected and then discarded. The balance check is an energy
  identity, so that version could not find its own inputs.
* **Results are ranked, with reasons.** The original returned a bare entity id,
  so a caller could not tell a certain match from the least-bad of two poor
  candidates. For a product about trustworthiness that was the most important
  thing missing.
* **Metadata comes from the registry first.** Reading ``device_class`` off the
  state object skips any entity that happens to be unavailable at setup time.
* **The registry is queried by index**, not walked twice per energy source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from analysis.model import Role
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

POWER_UNITS = {u.value for u in UnitOfPower}
ENERGY_UNITS = {u.value for u in UnitOfEnergy}

#: Keyword hints per role, best first. Earlier keywords score higher.
KEYWORDS: dict[Role, tuple[str, ...]] = {
    Role.PV: ("pv", "solar", "photovoltaic", "generation", "production"),
    Role.LOAD: ("load", "consumption", "house", "home", "usage"),
    Role.GRID_IMPORT: ("grid import", "from grid", "import", "consumed", "grid"),
    Role.GRID_EXPORT: ("grid export", "to grid", "export", "feed", "return"),
    Role.BATTERY_CHARGE: ("battery charge", "charging", "charge"),
    Role.BATTERY_DISCHARGE: ("battery discharge", "discharging", "discharge"),
}

#: A candidate must clear this to be offered at all. Without a floor the
#: original would return whatever scored highest even when nothing matched.
MIN_SCORE = 25


@dataclass(frozen=True, slots=True)
class Candidate:
    """One possible match for a role, with its reasoning attached."""

    entity_id: str
    score: int
    reasons: tuple[str, ...]

    @property
    def confident(self) -> bool:
        return self.score >= 60


@dataclass
class Discovery:
    """Ranked candidates per role."""

    by_role: dict[Role, list[Candidate]] = field(default_factory=dict)

    def best(self, role: Role) -> Candidate | None:
        options = self.by_role.get(role)
        return options[0] if options else None

    def suggestion(self, role: Role) -> str:
        best = self.best(role)
        return best.entity_id if best else ""


async def async_discover(hass: HomeAssistant) -> Discovery:
    """Best-effort discovery. Never raises; an empty result is a valid answer."""
    discovery = Discovery()
    related = await _async_energy_dashboard_entities(hass)
    registry = er.async_get(hass)
    candidates = _sibling_entities(registry, related)

    if not candidates:
        # No Energy Dashboard, or nothing recognisable in it. Fall back to the
        # whole registry rather than giving up entirely.
        candidates = [entry.entity_id for entry in registry.entities.values()]

    claimed: set[str] = set()
    for role in KEYWORDS:
        ranked = _rank(hass, registry, candidates, role, claimed)
        if ranked:
            discovery.by_role[role] = ranked
            if ranked[0].confident:
                claimed.add(ranked[0].entity_id)

    return discovery


async def _async_energy_dashboard_entities(hass: HomeAssistant) -> list[str]:
    """Every statistic id the user declared in the Energy Dashboard.

    Treated as a convenience rather than a dependency: it is wrapped in a broad
    except and degrades to an empty list, because the energy component's data
    structures are not a public API.
    """
    try:
        from homeassistant.components.energy.data import async_get_manager

        manager = await async_get_manager(hass)
    except Exception:
        _LOGGER.debug("energy dashboard unavailable", exc_info=True)
        return []

    data = getattr(manager, "data", None)
    if not data:
        return []

    found: list[str] = []

    def _add(value: Any) -> None:
        if isinstance(value, str) and value and value not in found:
            found.append(value)

    for source in data.get("energy_sources", []):
        for key in ("stat_energy_from", "stat_energy_to", "stat_rate"):
            _add(source.get(key))
        power_config = source.get("power_config") or {}
        for key in (
            "stat_rate",
            "stat_rate_inverted",
            "stat_rate_from",
            "stat_rate_to",
        ):
            _add(power_config.get(key))
        # Nested flow lists, used by older energy schema versions.
        for direction in ("flow_from", "flow_to"):
            for flow in source.get(direction, []) or []:
                for key in ("stat_energy_from", "stat_energy_to", "stat_rate"):
                    _add(flow.get(key))

    # Individually-monitored devices are exactly what explains a load residual
    # that will not close, so they are worth knowing about.
    for device in data.get("device_consumption", []) or []:
        _add(device.get("stat_consumption"))

    return found


def _sibling_entities(registry: er.EntityRegistry, seeds: list[str]) -> list[str]:
    """Entities sharing a device or config entry with a declared statistic.

    Uses the registry's indexed lookups rather than iterating every entity once
    per energy source, which is what the predecessor did.
    """
    device_ids: set[str] = set()
    config_entry_ids: set[str] = set()

    for entity_id in seeds:
        entry = registry.async_get(entity_id)
        if entry is None:
            # External statistics have no entity behind them, which is expected.
            continue
        if entry.device_id:
            device_ids.add(entry.device_id)
        if entry.config_entry_id:
            config_entry_ids.add(entry.config_entry_id)

    ordered: list[str] = []
    seen: set[str] = set()

    for device_id in device_ids:
        for entry in er.async_entries_for_device(registry, device_id, True):
            if entry.entity_id not in seen:
                seen.add(entry.entity_id)
                ordered.append(entry.entity_id)

    for config_entry_id in config_entry_ids:
        for entry in er.async_entries_for_config_entry(registry, config_entry_id):
            if entry.entity_id not in seen:
                seen.add(entry.entity_id)
                ordered.append(entry.entity_id)

    for entity_id in seeds:
        if entity_id not in seen and registry.async_get(entity_id) is not None:
            seen.add(entity_id)
            ordered.append(entity_id)

    return ordered


def _metadata(
    hass: HomeAssistant, registry: er.EntityRegistry, entity_id: str
) -> tuple[str | None, str | None, str]:
    """``(device_class, unit, searchable text)``, registry first.

    Registry first matters: an entity that is merely unavailable right now still
    has a device class and a unit, and skipping it would lose a perfectly good
    candidate because of a momentary outage.
    """
    entry = registry.async_get(entity_id)
    device_class: str | None = None
    unit: str | None = None
    name = entity_id

    if entry is not None:
        device_class = entry.device_class or entry.original_device_class
        unit = entry.unit_of_measurement
        name = entry.name or entry.original_name or entity_id

    state = hass.states.get(entity_id)
    if state is not None:
        device_class = device_class or state.attributes.get("device_class")
        unit = unit or state.attributes.get("unit_of_measurement")
        name = state.attributes.get("friendly_name") or name

    return device_class, unit, f"{entity_id} {name}".lower()


def _rank(
    hass: HomeAssistant,
    registry: er.EntityRegistry,
    candidates: list[str],
    role: Role,
    claimed: set[str],
) -> list[Candidate]:
    """Score every candidate for one role, best first."""
    scored: list[Candidate] = []

    for entity_id in candidates:
        if entity_id in claimed or not entity_id.startswith("sensor."):
            continue

        device_class, unit, haystack = _metadata(hass, registry, entity_id)
        score = 0
        reasons: list[str] = []

        # Both power and energy are usable. The balance runs on energy; the live
        # tripwire runs on power. Rejecting either loses half the inputs.
        if device_class == SensorDeviceClass.POWER or (unit in POWER_UNITS):
            score += 30
            reasons.append("measures power")
        elif device_class == SensorDeviceClass.ENERGY or (unit in ENERGY_UNITS):
            score += 30
            reasons.append("measures energy")
        else:
            continue

        for index, keyword in enumerate(KEYWORDS[role]):
            if keyword in haystack:
                score += max(5, 20 - index * 3)
                reasons.append(f"name mentions {keyword!r}")
                break

        if score < MIN_SCORE or len(reasons) < 2:
            # A unit match alone is not evidence of *which* role this is.
            continue

        scored.append(Candidate(entity_id=entity_id, score=score, reasons=tuple(reasons)))

    scored.sort(key=lambda c: (-c.score, c.entity_id))
    return scored[:5]
