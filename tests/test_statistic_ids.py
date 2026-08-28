"""Every archive id must be one the recorder will actually accept.

External statistic ids are validated against ``[\\da-z_]+:[\\da-z_]+`` — lowercase
only. Config entry ids created since Home Assistant 2023.4 are ULIDs, which are
uppercase, so an id built from one verbatim is refused on every write. Capture
then fails silently for the entire life of the installation, and forecast
history is the one record that cannot be rebuilt afterwards.

The reference installation did not hit this: its Forecast.Solar entry predates
ULIDs and its id is lowercase hex. Almost every new installation would.

Checked against Home Assistant's own validator where it is importable, so this
cannot drift from the rule it is protecting.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("homeassistant", reason="Home Assistant not installed")

from custom_components.solar_sanity.statistics_source import (
    dayahead_statistic_id,
    forecast_statistic_id,
)

#: The pattern the recorder applies, quoted from its source. Used only when the
#: real function cannot be imported, so a rename upstream shows up as a change
#: here rather than as a silently weaker test.
DOCUMENTED = re.compile(r"^(?!.+__)(?!_)[\da-z_]+(?<!_):(?!_)[\da-z_]+(?<!_)$")


def _is_valid(statistic_id: str) -> bool:
    try:
        from homeassistant.components.recorder.statistics import valid_statistic_id
    except ImportError:
        return bool(DOCUMENTED.match(statistic_id))
    return bool(valid_statistic_id(statistic_id))


#: Both real shapes: ULIDs as minted today, and the legacy hex form still in the
#: field. All genuinely distinct, so the collision tests below mean something —
#: one ULID written twice in different cases is one entry, not two.
ENTRY_IDS = [
    "01M115AWKC3N083Y1YANVMB7CZ",
    "01K3XQ9M4BFT8YV2R7WDNZ0EQH",
    "01JQ7ZBP4WY6M8XKDNR2VHT5GA",
    "87d3c61d99cb949a46b998013b2aee40",
]


@pytest.mark.parametrize("entry_id", ENTRY_IDS)
def test_the_rolling_archive_id_is_acceptable(entry_id: str) -> None:
    statistic_id = forecast_statistic_id(entry_id)

    assert _is_valid(statistic_id), f"the recorder would refuse {statistic_id!r}"


@pytest.mark.parametrize("entry_id", ENTRY_IDS)
def test_the_day_ahead_archive_id_is_acceptable(entry_id: str) -> None:
    statistic_id = dayahead_statistic_id(entry_id)

    assert _is_valid(statistic_id), f"the recorder would refuse {statistic_id!r}"


def test_a_ulid_would_have_been_refused_verbatim() -> None:
    """The defect itself, so nobody quietly removes the lowercasing."""
    assert not _is_valid("solar_sanity:dayahead_01M115AWKC3N083Y1YANVMB7CZ")


def test_the_two_archives_never_collide() -> None:
    entry_id = ENTRY_IDS[0]

    assert forecast_statistic_id(entry_id) != dayahead_statistic_id(entry_id)


def test_two_providers_never_collide() -> None:
    """Lowercasing must not merge two distinct entries into one archive."""
    ids = {forecast_statistic_id(entry_id) for entry_id in ENTRY_IDS}

    assert len(ids) == len(ENTRY_IDS)


def test_a_legacy_hex_id_is_unchanged() -> None:
    """So no archive already accumulating in the field is orphaned."""
    legacy = "87d3c61d99cb949a46b998013b2aee40"

    assert forecast_statistic_id(legacy).endswith(legacy)
