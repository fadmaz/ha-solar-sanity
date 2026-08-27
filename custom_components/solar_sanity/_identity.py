"""Whether two installations are describing the same house.

Not a unique id. An id derived from the mapping changes the moment the mapping
does, so remapping a single channel minted a new house and the duplicate went
uncaught — while the user, whose whole reason for adding a second entry was that
they needed to change something, got no warning at all.

What identifies an installation is what it measures. That is stable under
renaming, survives a remap of every other channel, and is checkable against
entries that already exist without storing anything new.

Kept free of Home Assistant imports so the rule can be tested without one, the
same reason ``_match``, ``_forecast_plan`` and ``_local_time`` exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .analysis.model import Role
from .const import CONF_CHANNELS, CONF_ENTITY_ID, CONF_ROLE


@dataclass(frozen=True, slots=True)
class Overlap:
    """An entity this configuration shares with one that already exists."""

    title: str
    role_key: str
    entity_id: str
    #: Consumption is the one the whole balance is defined around, so sharing it
    #: is never right. Anything else can be legitimate.
    decisive: bool


def find_overlap(
    entries: list[Any], channels: dict[str, str], ignore_entry_id: str | None = None
) -> Overlap | None:
    """An entity already monitored by another installation, if there is one.

    Two entries watching one house is not a hypothetical: it is what a user does
    when the thing they need to change cannot be changed, and it goes wrong
    quietly — both write the same forecast archive, each resuming its running
    total from what the other left, and neither ever reports a problem.

    A shared *consumption* sensor is decisive. The identity is defined around
    load, and two installations claiming the same one describe the same house by
    construction. Everything else is reported but not refused: one grid meter
    serving two sub-systems is a real arrangement, and a flat refusal would push
    the user straight back to the workaround this exists to remove.
    """
    for entry in entries:
        if ignore_entry_id is not None and entry.entry_id == ignore_entry_id:
            continue
        theirs = {
            record[CONF_ENTITY_ID]: record[CONF_ROLE]
            for record in entry.data.get(CONF_CHANNELS, [])
        }
        for role_key, entity_id in channels.items():
            if entity_id not in theirs:
                continue
            return Overlap(
                title=entry.title or "another installation",
                role_key=role_key,
                entity_id=entity_id,
                decisive=role_key == Role.LOAD.key and theirs[entity_id] == Role.LOAD.key,
            )
    return None
