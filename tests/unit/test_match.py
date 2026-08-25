"""Whole-word keyword matching, tested without Home Assistant.

The bug this guards against is subtle and would not surface as a fault: mapping
a discharge sensor into the charge slot swaps the two battery directions, and on
a symmetric day the balance still roughly closes. The product would report
nothing while being quietly wrong.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "solar_sanity" / "_match.py"
)
_spec = importlib.util.spec_from_file_location("solar_sanity_match", _PATH)
_match = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_match)


class TestWholeWordMatching:
    def test_charge_does_not_match_discharge(self) -> None:
        """The whole reason this module exists."""
        assert not _match.matches("charge", "Siseli Calculated Battery Discharge Energy")

    def test_discharge_matches_discharge(self) -> None:
        assert _match.matches("discharge", "Siseli Calculated Battery Discharge Energy")

    def test_charge_matches_charge(self) -> None:
        assert _match.matches("charge", "Siseli Calculated Battery Charge Energy")

    @pytest.mark.parametrize(
        ("keyword", "haystack"),
        [
            ("battery charge", "Siseli Calculated Battery Charge Energy"),
            ("grid import", "Siseli Calculated Grid Import Power"),
            ("output active", "Siseli Output Active Power"),
            ("production", "sensor.energy_production_today"),
        ],
    )
    def test_real_entity_names(self, keyword: str, haystack: str) -> None:
        assert _match.matches(keyword, haystack)

    def test_multi_word_must_be_consecutive(self) -> None:
        assert not _match.matches("grid import", "Grid Power, Import Total")

    def test_separators_are_not_significant(self) -> None:
        """`sensor.grid_import_power` and "Grid Import Power" are the same thing."""
        assert _match.matches("grid import", "sensor.grid_import_power")

    def test_empty_keyword_never_matches(self) -> None:
        assert not _match.matches("", "anything at all")
