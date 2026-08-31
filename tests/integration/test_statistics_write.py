"""The ids and metadata this integration writes, handed to a real recorder.

``tests/test_statistic_ids.py`` already checks the *format* of these ids against
the recorder's documented pattern, purely and without Home Assistant. It is a
good test and it is not this one. A pattern match asserts that a string looks
acceptable; only the recorder can say whether it is accepted, and the difference
between those two is where this project lost a fortnight of forecast history.

What happened: config entry ids minted since Home Assistant 2023.4 are ULIDs,
which are uppercase. The recorder validates external statistic ids against
``[\\da-z_]+:[\\da-z_]+`` — lowercase only. Every write was rejected, and the
only trace was a debug log nobody reads. ``_statistic_key`` lowercases for
exactly that reason, and until now nothing had ever put the result in front of
the thing that does the rejecting.

``recorder_mock`` is requested **before** ``hass`` in every signature here. That
is not style. ``recorder_db_url`` opens with a bare ``assert not
hass_fixture_setup`` and the ``hass`` fixture appends to that list as its second
statement, so the other order fails with ``assert not [True]`` — which names
neither fixture.
"""

from __future__ import annotations

import pytest
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    statistics_during_period,
)

from custom_components.solar_sanity import statistics_source

#: The shape Home Assistant has minted since 2023.4, and the reason this file
#: exists. Uppercase, and the recorder will not have it.
ULID_ENTRY_ID = "01M115AWKC3N083Y1YANVMB7CZ"


def _series(statistic_id: str, name: str = "Solar Sanity forecast"):
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    metadata = statistics_source._metadata(statistic_id, name)
    rows = [{"start": start, "sum": 1.5, "state": 1.5}]
    return metadata, rows


async def test_a_ulid_entry_id_produces_an_id_the_recorder_accepts_on_write(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """The headline. A pure format test cannot reach this.

    The id is built the way production builds it, the metadata is built by the
    same helper production uses, and the recorder is the real one. If any part
    of that contract drifts — the pattern, the required ``mean_type`` and
    ``unit_class``, the rule that ``source`` must match the half before the
    colon — this fails on the write rather than on somebody's dashboard.
    """
    statistic_id = statistics_source.forecast_statistic_id(ULID_ENTRY_ID)
    assert statistic_id.islower(), statistic_id

    metadata, rows = _series(statistic_id)
    async_add_external_statistics(hass, metadata, rows)
    await async_wait_recording_done(hass)

    stored = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        rows[0]["start"],
        None,
        {statistic_id},
        "hour",
    )

    assert statistic_id in stored, (
        f"the recorder accepted nothing for {statistic_id!r} — the id, the "
        f"metadata, or the source half of the contract has drifted"
    )


async def test_the_raw_uppercase_id_is_the_thing_that_would_have_been_refused(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """Proves the lowercasing is load-bearing rather than decorative.

    Without this, ``_statistic_key`` could become the identity function and
    every other test here would still pass. The recorder is what says no, so the
    recorder is what has to be asked.
    """
    refused = f"solar_sanity:forecast_{ULID_ENTRY_ID}"
    metadata, rows = _series(refused)

    with pytest.raises((HomeAssistantError, ValueError)):
        async_add_external_statistics(hass, metadata, rows)
        await async_wait_recording_done(hass)


async def test_the_metadata_carries_what_2026_11_will_require(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """``mean_type`` and ``unit_class`` are deprecated to omit now and a hard
    error in Home Assistant 2026.11, and custom integrations are not exempt.

    CI greps for the two names in the source, which catches deletion but not a
    wrong value. This reads what the helper actually returns.
    """
    from homeassistant.components.recorder.models import StatisticMeanType

    metadata = statistics_source._metadata("solar_sanity:forecast_x", "x")

    assert metadata["mean_type"] is StatisticMeanType.NONE
    assert metadata["unit_class"] == "energy"
    assert metadata["has_sum"] is True
    assert metadata["source"] == "solar_sanity", (
        "the recorder requires source to equal the half before the colon"
    )
