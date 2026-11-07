"""What discovery and the entities promise, checked against the code.

Discovery is what pre-fills the setup form, so a sensor matched to the wrong
role is a mapping the user has to notice and undo. A net battery sensor named
after both directions is the case that went wrong on the reference
installation.

These need Home Assistant importable and are skipped when it is not, so they run
in CI and are absent when working on the pure analysis locally.
"""

from __future__ import annotations

import pytest

pytest.importorskip("homeassistant", reason="Home Assistant not installed")

from custom_components.solar_sanity.analysis.model import Role
from custom_components.solar_sanity.discovery import (
    KEYWORDS,
    Candidate,
    _also_matches_opposite,
)

NET_BATTERY = (
    "sensor.energy_battery_inverter_calculated_battery_discharge_power_"
    "inverter_calculated_battery_charge_power_net_power"
)


class TestAmbiguousDiscovery:
    """A name carrying both directions belongs to neither slot on its own."""

    def test_a_net_battery_name_matches_both_roles(self) -> None:
        haystack = NET_BATTERY.replace("_", " ").replace(".", " ")

        assert _also_matches_opposite(Role.BATTERY_CHARGE, haystack) is True
        assert _also_matches_opposite(Role.BATTERY_DISCHARGE, haystack) is True

    def test_a_clean_charge_name_does_not(self) -> None:
        haystack = "sensor.inverter_calculated_battery_charge_power battery charge power".replace(
            "_", " "
        )

        assert _also_matches_opposite(Role.BATTERY_CHARGE, haystack) is False

    def test_pv_and_load_have_no_opposite(self) -> None:
        assert _also_matches_opposite(Role.PV, "solar pv generation") is False
        assert _also_matches_opposite(Role.LOAD, "house consumption load") is False

    def test_grid_directions_oppose_each_other(self) -> None:
        assert _also_matches_opposite(Role.GRID_IMPORT, "grid import and grid export") is True

    def test_the_demotion_drops_a_clean_match_below_confident(self) -> None:
        """50 is what a first-keyword unit match scores; it must stop clearing 45."""
        from custom_components.solar_sanity.discovery import AMBIGUOUS_PENALTY

        assert Candidate(entity_id="x", score=50, reasons=()).confident is True
        assert Candidate(entity_id="x", score=50 - AMBIGUOUS_PENALTY, reasons=()).confident is False

    def test_the_demotion_still_leaves_the_weakest_match_offered(self) -> None:
        """Demoted, not rejected: a real net sensor should still be pickable."""
        from custom_components.solar_sanity.discovery import AMBIGUOUS_PENALTY, MIN_SCORE

        weakest = 30 + max(5, 20 - (len(KEYWORDS[Role.BATTERY_CHARGE]) - 1) * 3)

        assert weakest - AMBIGUOUS_PENALTY >= MIN_SCORE


def test_removal_is_wired_up() -> None:
    """Home Assistant calls this by name; a rename would silently disable it."""
    from custom_components.solar_sanity import async_remove_entry

    assert callable(async_remove_entry)
