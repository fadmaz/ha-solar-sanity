"""A year's production against the figure on the quote.

The one question every owner has and no dashboard answers. Home Assistant knows
what the array produced, the quote is a number the owner can type in, and
nothing joined them.

**A note, never a finding.** Falling short is a conversation with an installer,
not a fault in the data — and this integration's promise is that when it accuses
something, it is right. A short year has honest causes it cannot tell apart: the
weather, shading that grew, a panel derating exactly as its warranty allows.

The refusals matter more than the arithmetic here, because the failure mode is
somebody telephoning an installer about a number this made up.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.const import (
    DOMAIN,
    OPT_CORRECTIONS,
    OPT_GUARANTEED_ANNUAL_KWH,
)
from custom_components.solar_sanity.yield_check import (
    YEAR_DAYS,
    YieldAgainstPromise,
    async_yield_against_promise,
)

PROMISED = 6000.0


async def _coordinator(hass: HomeAssistant, entry: MockConfigEntry):
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data.coordinator


def _entry(entry_data: dict, *, promised: float | None = PROMISED) -> MockConfigEntry:
    """Seeded into ``options``, which is the only store a user can write.

    Seeding ``data`` is what kept this suite green while the feature could not
    be switched on at all.
    """
    options: dict = {}
    if promised is not None:
        options[OPT_GUARANTEED_ANNUAL_KWH] = promised
    return MockConfigEntry(domain=DOMAIN, data=dict(entry_data), options=options)


def _energy(*, year: float, oldest_month: float = 400.0):
    """``async_energy_between`` answering the two questions the module asks."""

    async def fake(_hass, _statistic_id, start, end):
        return oldest_month if (end - start).days <= 31 else year

    return fake


class TestItAnswers:
    async def test_a_full_year_is_compared_to_the_promise(
        self, hass: HomeAssistant, enable_custom_integrations: None, entry_data: dict
    ) -> None:
        coordinator = await _coordinator(hass, _entry(entry_data))

        with patch(
            "custom_components.solar_sanity.yield_check.async_energy_between",
            _energy(year=5400.0),
        ):
            result = await async_yield_against_promise(hass, coordinator)

        assert result is not None
        assert result.produced_kwh == pytest.approx(5400.0)
        assert result.share == pytest.approx(0.9)

    async def test_a_shortfall_says_whose_conversation_it_is(self) -> None:
        note = YieldAgainstPromise(produced_kwh=5400.0, promised_kwh=PROMISED).note

        assert "90%" in note
        assert "installed it" in note
        assert "fault" in note

    async def test_meeting_the_promise_says_so_without_the_caveat(self) -> None:
        note = YieldAgainstPromise(produced_kwh=6300.0, promised_kwh=PROMISED).note

        assert "105%" in note
        assert "installer" not in note

    async def test_the_window_is_a_year_and_not_a_day_less(self) -> None:
        """Never annualised from a partial year.

        Scaling nine months up by four thirds compares three summer months to an
        average one — it flatters an installation checked in autumn and damns
        one checked in spring, and the second is somebody on the telephone about
        a number this invented.
        """
        assert YEAR_DAYS == 365


class TestItRefuses:
    async def test_no_guarantee_configured_means_no_note(
        self, hass: HomeAssistant, enable_custom_integrations: None, entry_data: dict
    ) -> None:
        coordinator = await _coordinator(hass, _entry(entry_data, promised=None))

        with patch(
            "custom_components.solar_sanity.yield_check.async_energy_between",
            _energy(year=5400.0),
        ):
            assert await async_yield_against_promise(hass, coordinator) is None

    @pytest.mark.parametrize("promised", [0.0, -100.0, "lots"])
    async def test_a_nonsense_guarantee_is_not_divided_by(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry_data: dict,
        promised,
    ) -> None:
        coordinator = await _coordinator(hass, _entry(entry_data, promised=promised))

        with patch(
            "custom_components.solar_sanity.yield_check.async_energy_between",
            _energy(year=5400.0),
        ):
            assert await async_yield_against_promise(hass, coordinator) is None

    async def test_an_archive_that_does_not_reach_back_a_year_is_refused(
        self, hass: HomeAssistant, enable_custom_integrations: None, entry_data: dict
    ) -> None:
        """The most damaging thing this module could say.

        A recorder purged to ninety days answers a 365-day query without
        complaint and returns ninety days of energy — which reads as a
        two-thirds shortfall on a roof with nothing wrong with it.
        """
        coordinator = await _coordinator(hass, _entry(entry_data))

        with patch(
            "custom_components.solar_sanity.yield_check.async_energy_between",
            _energy(year=1800.0, oldest_month=0.0),
        ):
            assert await async_yield_against_promise(hass, coordinator) is None

    async def test_a_recorder_that_cannot_answer_is_not_a_shortfall(
        self, hass: HomeAssistant, enable_custom_integrations: None, entry_data: dict
    ) -> None:
        """``None`` is a question that could not be answered, never nought."""
        coordinator = await _coordinator(hass, _entry(entry_data))

        async def unanswerable(*_args, **_kwargs):
            return None

        with patch(
            "custom_components.solar_sanity.yield_check.async_energy_between",
            unanswerable,
        ):
            assert await async_yield_against_promise(hass, coordinator) is None


class TestItReachesTheUser:
    """The only path a user has, driven end to end.

    Everything above seeds the figure into the entry directly, which is what let
    the feature ship, be documented and be tested while nobody could switch it
    on: the flow wrote ``entry.options`` and the check read ``entry.data``, and
    the miss was silent because ``None`` also means "no guarantee configured".
    """

    async def test_a_figure_typed_into_the_options_is_what_gets_compared(
        self, hass: HomeAssistant, enable_custom_integrations: None, entry_data: dict
    ) -> None:
        entry = MockConfigEntry(domain=DOMAIN, data=dict(entry_data))
        coordinator = await _coordinator(hass, entry)
        assert entry.options.get(OPT_GUARANTEED_ANNUAL_KWH) is None

        result = await hass.config_entries.options.async_init(entry.entry_id)
        done = await hass.config_entries.options.async_configure(
            result["flow_id"], {OPT_GUARANTEED_ANNUAL_KWH: PROMISED}
        )
        await hass.async_block_till_done()

        assert done["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options[OPT_GUARANTEED_ANNUAL_KWH] == PROMISED

        with patch(
            "custom_components.solar_sanity.yield_check.async_energy_between",
            _energy(year=5400.0),
        ):
            note = await async_yield_against_promise(hass, coordinator)

        assert note is not None, "the options flow is the only way in, and it did not arrive"
        assert note.promised_kwh == pytest.approx(PROMISED)

    async def test_a_figure_typed_by_mistake_can_be_taken_back(
        self, hass: HomeAssistant, enable_custom_integrations: None, entry_data: dict
    ) -> None:
        """A blanked ``vol.Optional`` is absent from ``user_input``, so a plain
        merge puts the old number back. Now that the check reads this store, a
        mistyped figure would otherwise be a note nobody could switch off.
        """
        entry = MockConfigEntry(
            domain=DOMAIN, data=dict(entry_data), options={OPT_GUARANTEED_ANNUAL_KWH: PROMISED}
        )
        coordinator = await _coordinator(hass, entry)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

        assert OPT_GUARANTEED_ANNUAL_KWH not in entry.options

        with patch(
            "custom_components.solar_sanity.yield_check.async_energy_between",
            _energy(year=5400.0),
        ):
            assert await async_yield_against_promise(hass, coordinator) is None

    async def test_clearing_a_field_leaves_settings_the_form_never_offered(
        self, hass: HomeAssistant, enable_custom_integrations: None, entry_data: dict
    ) -> None:
        """``corrections`` is written by Repairs and is never a row on this form.
        Whatever the form does with blanks, it must not reach a key it never
        showed.
        """
        corrections = [{"code": "grid_export_sign", "kind": "sign_flip"}]
        entry = MockConfigEntry(
            domain=DOMAIN, data=dict(entry_data), options={OPT_CORRECTIONS: corrections}
        )
        await _coordinator(hass, entry)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            result["flow_id"], {OPT_GUARANTEED_ANNUAL_KWH: PROMISED}
        )
        await hass.async_block_till_done()

        assert entry.options[OPT_CORRECTIONS] == corrections
        assert entry.options[OPT_GUARANTEED_ANNUAL_KWH] == PROMISED


class TestItReachesTheCard:
    async def test_the_note_joins_the_others_the_card_renders(
        self, hass: HomeAssistant, enable_custom_integrations: None, entry_data: dict
    ) -> None:
        """Appended to ``notes`` rather than given an attribute of its own.

        It is the same kind of sentence as the rest — something true beside the
        verdict rather than part of it — and a reader should not have to know
        that one of them came from a different module.
        """
        entry = _entry(entry_data)
        coordinator = await _coordinator(hass, entry)
        coordinator.yield_note = YieldAgainstPromise(produced_kwh=5400.0, promised_kwh=PROMISED)
        coordinator.async_update_listeners()
        await hass.async_block_till_done()

        state = next(
            state
            for state in hass.states.async_all("sensor")
            if state.attributes.get("notes") is not None
        )

        assert any("5,400 kWh" in note for note in state.attributes["notes"])
