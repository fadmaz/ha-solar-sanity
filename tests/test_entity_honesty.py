"""What the entities say, in every state the engine can be in.

The binary sensor is the entity most likely to end up in somebody else's
automation, and nothing exercised it. A real installation whose energy balance
had been shown to miss by 40% on five of the last seven days reported "OK",
because no single sensor could yet be blamed and the entity judged only on
whether a finding existed.

These need Home Assistant importable and are skipped when it is not, so they run
in CI and are absent when working on the pure engine locally.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("homeassistant", reason="Home Assistant not installed")

from custom_components.solar_sanity.analysis.model import (
    AnalysisReport,
    Confidence,
    Finding,
    Role,
    Severity,
    Status,
)
from custom_components.solar_sanity.binary_sensor import _data_healthy
from custom_components.solar_sanity.discovery import (
    KEYWORDS,
    Candidate,
    _also_matches_opposite,
)

NET_BATTERY = (
    "sensor.energy_battery_inverter_calculated_battery_discharge_power_"
    "inverter_calculated_battery_charge_power_net_power"
)


def _finding(severity: Severity) -> Finding:
    return Finding(
        code="test",
        severity=severity,
        confidence=Confidence.HIGH,
        channel_keys=("pv",),
        headline="h",
        detail="d",
        source_fix="f",
    )


class TestDataHealthy:
    """Every Status, so a new one cannot be added without deciding this."""

    def test_no_report_is_unknown(self) -> None:
        assert _data_healthy(None) is None

    @pytest.mark.parametrize("status", [Status.INSUFFICIENT_DATA, Status.NOT_CHECKABLE])
    def test_nothing_to_judge_is_unknown(self, status: Status) -> None:
        assert _data_healthy(AnalysisReport(status=status)) is None

    def test_ok_is_healthy(self) -> None:
        assert _data_healthy(AnalysisReport(status=Status.OK)) is True

    def test_investigating_without_a_proven_imbalance_is_healthy(self) -> None:
        """ "The numbers move around" is not yet a problem."""
        report = AnalysisReport(status=Status.INVESTIGATING, identity_fails=False)

        assert _data_healthy(report) is True

    def test_investigating_with_a_proven_imbalance_is_a_problem(self) -> None:
        """The regression this file exists for."""
        report = AnalysisReport(status=Status.INVESTIGATING, identity_fails=True)

        assert _data_healthy(report) is False

    def test_a_fault_is_a_problem(self) -> None:
        report = AnalysisReport(
            status=Status.FAULT_FOUND, finding=_finding(Severity.FAULT), identity_fails=True
        )

        assert _data_healthy(report) is False

    def test_a_note_is_not_a_problem(self) -> None:
        """Calibration observations must never alarm."""
        report = AnalysisReport(status=Status.OK, finding=_finding(Severity.NOTE))

        assert _data_healthy(report) is True

    def test_every_status_is_covered(self) -> None:
        """A new Status must not silently default to "fine"."""
        for status in Status:
            result = _data_healthy(AnalysisReport(status=status))
            assert result in (True, False, None)


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


class TestIssueOwnership:
    """A removed installation must not leave a repair card behind."""

    def test_issue_ids_are_matched_by_entry(self) -> None:
        """Entry ids are ULIDs, so a suffix test cannot collide."""
        from custom_components.solar_sanity.const import DOMAIN
        from custom_components.solar_sanity.repairs import issue_ids_for_entry

        mine = "01M0X6H534EZXNCW0X86RXE1D3"
        theirs = "01M113N4N74WEFYB26341HJ8W4"
        issues = {
            f"signed_net_battery_slot_{mine}": DOMAIN,
            f"missing_export_channel_{mine}": DOMAIN,
            f"signed_net_battery_slot_{theirs}": DOMAIN,
            f"something_{mine}": "other_domain",
        }

        class _Issue:
            def __init__(self, issue_id: str, domain: str) -> None:
                self.issue_id = issue_id
                self.domain = domain

        registry = SimpleNamespace(
            issues={k: _Issue(k, v) for k, v in issues.items()},
        )
        with patch(
            "custom_components.solar_sanity.repairs.ir.async_get",
            return_value=registry,
        ):
            found = issue_ids_for_entry(object(), mine)

        assert found == {
            f"signed_net_battery_slot_{mine}",
            f"missing_export_channel_{mine}",
        }

    def test_removal_is_wired_up(self) -> None:
        """Home Assistant calls this by name; a rename would silently disable it."""
        from custom_components.solar_sanity import async_remove_entry

        assert callable(async_remove_entry)


class TestOrphanedIssuesAreSweptUp:
    """Cards left by entries that no longer exist.

    Removal has been handled since v0.3.2, but only at the moment an entry goes.
    Anything orphaned before that shipped is stranded forever, because every
    other path reconciles against *this* entry's id and an old card does not
    carry it. The reference installation has one, for an entry deleted half an
    hour before the removal hook existed, and its Fix button leads to a flow
    that can only abort.
    """

    @staticmethod
    def _registry(issue_ids: dict[str, str]):
        class _Issue:
            def __init__(self, issue_id: str, domain: str) -> None:
                self.issue_id = issue_id
                self.domain = domain

        return SimpleNamespace(issues={k: _Issue(k, v) for k, v in issue_ids.items()})

    async def _sweep(self, issues: dict[str, str], live: set[str]) -> list[str]:
        from custom_components.solar_sanity.repairs import async_sweep_orphans

        deleted: list[str] = []
        with (
            patch(
                "custom_components.solar_sanity.repairs.ir.async_get",
                return_value=self._registry(issues),
            ),
            patch(
                "custom_components.solar_sanity.repairs.ir.async_delete_issue",
                side_effect=lambda _hass, _domain, issue_id: deleted.append(issue_id),
            ),
        ):
            await async_sweep_orphans(object(), live)
        return sorted(deleted)

    async def test_the_reference_installations_stranded_card_is_removed(self) -> None:
        from custom_components.solar_sanity.const import DOMAIN

        gone = "01M0X6H534EZXNCW0X86RXE1D3"
        live = "01M115AWKC3N083Y1YANVMB7CZ"

        deleted = await self._sweep(
            {
                f"signed_net_battery_slot_{gone}": DOMAIN,
                f"signed_net_battery_slot_{live}": DOMAIN,
            },
            {live},
        )

        assert deleted == [f"signed_net_battery_slot_{gone}"]

    async def test_a_live_entrys_cards_are_left_alone(self) -> None:
        from custom_components.solar_sanity.const import DOMAIN

        live = "01M115AWKC3N083Y1YANVMB7CZ"

        deleted = await self._sweep(
            {
                f"signed_net_battery_slot_{live}": DOMAIN,
                f"missing_export_channel_{live}": DOMAIN,
            },
            {live},
        )

        assert deleted == []

    async def test_every_live_entry_counts_not_just_one(self) -> None:
        """Two installations, and sweeping for one must not take the other's."""
        from custom_components.solar_sanity.const import DOMAIN

        first = "01M115AWKC3N083Y1YANVMB7CZ"
        second = "01M113N4N74WEFYB26341HJ8W4"

        deleted = await self._sweep(
            {
                f"signed_net_battery_slot_{first}": DOMAIN,
                f"signed_net_battery_slot_{second}": DOMAIN,
            },
            {first, second},
        )

        assert deleted == []

    async def test_another_integrations_cards_are_never_touched(self) -> None:
        deleted = await self._sweep(
            {"signed_net_battery_slot_01M0X6H534EZXNCW0X86RXE1D3": "other_domain"},
            {"01M115AWKC3N083Y1YANVMB7CZ"},
        )

        assert deleted == []

    async def test_no_live_entries_sweeps_all_of_ours(self) -> None:
        """The last installation removed, and its card outliving it."""
        from custom_components.solar_sanity.const import DOMAIN

        deleted = await self._sweep(
            {"signed_net_battery_slot_01M0X6H534EZXNCW0X86RXE1D3": DOMAIN}, set()
        )

        assert deleted == ["signed_net_battery_slot_01M0X6H534EZXNCW0X86RXE1D3"]
