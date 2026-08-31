"""Which query the backfill runs, decided by the recorder rather than by state.

This is the defect that made the whole backfill a no-op, and it is the one most
worth a real recorder. Sum-backed statistics answer ``change`` exactly;
mean-backed ones answer only ``mean``, and asking the wrong question returns
nothing at all. So the classification is not a detail — it decides whether an
installation gets a month of history at setup or waits a week for its own.

Classifying from the entity's live state looked equivalent and was not. An
MQTT-backed inverter publishes its entities *after* Home Assistant starts, so at
setup the state machine knew nothing, every channel classified as neither, and
the backfill silently did nothing on exactly the installations it was written
for.

**The test for that is a channel with statistics and no state at all.** A stub
cannot express the difference: it only has whatever the test puts in it.

``recorder_mock`` before ``hass`` in every signature — see the note in
``conftest.py``.
"""

from __future__ import annotations

from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.models import StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.solar_sanity.statistics_source import async_classify_statistics

RECORDED = "sensor.records_its_own_history"
UNRECORDED = "sensor.has_no_state_class"


def _metadata(entity_id: str, *, has_sum: bool) -> StatisticMetaData:
    """Metadata for an *entity*-backed series.

    ``source`` must be ``"recorder"`` for these, unlike our own external series
    where it is the half before the colon.

    ``unit_class`` is required and its omission here is how this test first
    failed — with ``RuntimeError: Detected code that doesn't specify unit_class``.
    The production helper in ``statistics_source`` has carried it all along, and
    CI greps for the name; the test writing the fixture had no such guard. Worth
    recording, because a fixture that cannot be written is a better outcome than
    one written wrongly and asserted about.
    """
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE if has_sum else StatisticMeanType.ARITHMETIC,
        has_sum=has_sum,
        name=None,
        source="recorder",
        statistic_id=entity_id,
        unit_class="energy" if has_sum else "power",
        unit_of_measurement="kWh" if has_sum else "W",
    )


async def _seed(hass: HomeAssistant, entity_id: str, *, has_sum: bool) -> None:
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    row = {"start": start, "sum": 5.0} if has_sum else {"start": start, "mean": 500.0}
    async_import_statistics(hass, _metadata(entity_id, has_sum=has_sum), [row])
    await async_wait_recording_done(hass)


async def test_a_channel_with_statistics_but_no_state_is_still_classified(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """The defect, stated as directly as it can be.

    Nothing is ever written to the state machine for this entity — exactly the
    situation an MQTT-backed inverter is in at setup. If the classification ever
    goes back to asking the state machine, this is what says so, and it says it
    on the installations that were actually affected rather than in the abstract.
    """
    await _seed(hass, RECORDED, has_sum=True)
    assert hass.states.get(RECORDED) is None, "the point is that there is no state"

    sum_backed, _mean, absent = await async_classify_statistics(hass, {RECORDED})

    assert RECORDED in sum_backed
    assert RECORDED not in absent


async def test_sum_and_mean_backed_channels_are_told_apart(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """Asking the wrong one of these two returns nothing, silently."""
    await _seed(hass, RECORDED, has_sum=True)
    await _seed(hass, "sensor.a_power_reading", has_sum=False)

    sum_backed, mean_backed, absent = await async_classify_statistics(
        hass, {RECORDED, "sensor.a_power_reading"}
    )

    assert sum_backed == {RECORDED}
    assert mean_backed == {"sensor.a_power_reading"}
    assert not absent


async def test_a_channel_the_recorder_holds_nothing_for_is_absent(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """``absent`` is a third answer, not a failure.

    It means the source sensor carries no ``state_class``, so nobody is keeping
    its history and Solar Sanity must collect its own — which is a week's wait
    the owner is warned about rather than a silence they have to work out.
    """
    sum_backed, mean_backed, absent = await async_classify_statistics(hass, {UNRECORDED})

    assert absent == {UNRECORDED}
    assert not sum_backed
    assert not mean_backed


async def test_classification_survives_a_recorder_that_is_not_there(
    hass: HomeAssistant,
) -> None:
    """No ``recorder_mock`` in this signature, deliberately.

    A recorder is not guaranteed — someone may have removed it from their
    configuration — and the honest answer then is that every channel is
    unrecorded, not an exception out of setup.
    """
    sum_backed, mean_backed, absent = await async_classify_statistics(hass, {RECORDED})

    assert absent == {RECORDED}
    assert not sum_backed
    assert not mean_backed
