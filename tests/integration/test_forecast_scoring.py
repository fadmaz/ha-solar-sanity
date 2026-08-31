"""Scoring a forecast provider against what the roof actually produced.

``analysis/forecast.py`` could answer this all along — 367 lines and thirty
tests — and had no caller. Those tests still carry the arithmetic; these carry
the join, which is where a number a person repeats to their installer can go
quietly wrong.

Three ways it can, and one of them is silent:

- **Units.** The archive is kWh, the buckets are Wh. A missed division gives a
  bias of -99.9%, which rounds to -100 and reads as a finding.
- **The unresolvable day.** ``local_day`` returns ``None`` with no zone, and
  ``forecast.build_days`` keys its days on that return. Every hour would land
  under one key, merging the window into a single "day" long enough to pass
  every eligibility test.
- **A missing hour.** One absent midday hour is most of a day's energy, and
  filling it with a zero to keep the day is how a provider gets blamed for the
  recorder's gap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_sanity.analysis.model import Bucket, BucketSource, Quality
from custom_components.solar_sanity.const import CONF_FORECAST_ENTRIES, DOMAIN
from custom_components.solar_sanity.scoring import (
    WH_PER_KWH,
    _actual_kwh_by_hour,
    _local_date_or_refuse,
    async_score_providers,
)

KEYS = ("pv", "load", "grid_import", "battery_charge", "battery_discharge")
NOON = datetime(2026, 8, 1, 12, tzinfo=UTC)


def _bucket(start: datetime, pv_wh: float | None, *, seconds: int = 3600) -> Bucket:
    return Bucket(
        start_utc=start,
        seconds=seconds,
        wh={key: (pv_wh if key == "pv" else 0.0) for key in KEYS},
        quality=dict.fromkeys(KEYS, Quality.OK if pv_wh is not None else Quality.MISSING),
        source=dict.fromkeys(KEYS, BucketSource.OWN_INTEGRAL),
        local_date=start.date(),
        is_dst_transition=False,
    )


async def _coordinator(hass: HomeAssistant, entry: MockConfigEntry):
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data.coordinator


class TestTheUnitsAreConverted:
    """The most damaging thing here and the least visible."""

    async def test_watt_hours_become_kilowatt_hours(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
    ) -> None:
        coordinator = await _coordinator(hass, entry)
        coordinator._buckets = [_bucket(NOON, 4500.0)]

        actual = _actual_kwh_by_hour(coordinator)

        assert actual == {NOON: pytest.approx(4.5)}

    async def test_the_divisor_is_the_one_everybody_means(self) -> None:
        """Pinned so that a well-meant 'tidy-up' to 1024 or 100 fails loudly."""
        assert WH_PER_KWH == 1000.0

    async def test_a_forecast_and_a_measurement_of_the_same_size_score_level(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry_data: dict,
    ) -> None:
        """End to end on the conversion.

        If the division were missed, 4.5 kWh forecast against 4500 "kWh"
        measured is a bias of +99,900%, and the snap that follows would report
        something confident and absurd.

        A real provider entry has to exist in ``hass`` for this to reach the
        arithmetic at all — ``async_score_providers`` skips an id it cannot
        resolve, which is what it should do and what made the first version of
        this test pass vacuously in CI.

        Thirty days rather than twenty because the pure module wants
        twenty-one before it will put a number to anything, and says so:
        "20 comparable days so far; a figure needs 21."
        """
        provider = MockConfigEntry(domain="forecast_solar", title="Forecast.Solar")
        provider.add_to_hass(hass)
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={**entry_data, CONF_FORECAST_ENTRIES: [provider.entry_id]},
        )
        coordinator = await _coordinator(hass, entry)
        coordinator._buckets = [_bucket(NOON + timedelta(days=day), 4500.0) for day in range(30)]
        forecast = {NOON + timedelta(days=day): 4.5 for day in range(30)}

        with patch(
            "custom_components.solar_sanity.scoring.async_forecast_series",
            return_value=forecast,
        ):
            scores = await async_score_providers(hass, coordinator)

        assert scores, "no provider was scored"
        assert scores[0].name == "Forecast.Solar"
        assert scores[0].bias.value == pytest.approx(0.0, abs=0.01)


class TestAnUnresolvableZoneIsRefused:
    """Passing it through is worse than returning nothing.

    ``build_days`` keys days on the resolver's return, so a resolver that yields
    ``None`` merges sixty days into one entry — which then passes every size
    test there is and produces a confident figure about nothing.
    """

    async def test_no_zone_means_no_resolver(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
    ) -> None:
        coordinator = await _coordinator(hass, entry)

        with patch.object(type(coordinator), "time_zone", property(lambda _self: None)):
            assert _local_date_or_refuse(coordinator) is None

    async def test_no_zone_means_no_scores(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
        entry_data: dict,
    ) -> None:
        coordinator = await _coordinator(hass, entry)
        coordinator._buckets = [_bucket(NOON, 4500.0)]

        with patch.object(type(coordinator), "time_zone", property(lambda _self: None)):
            assert await async_score_providers(hass, coordinator) == []

    async def test_a_resolvable_zone_gives_a_resolver_that_answers(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
    ) -> None:
        coordinator = await _coordinator(hass, entry)

        resolve = _local_date_or_refuse(coordinator)

        assert resolve is not None
        assert resolve(NOON) == NOON.astimezone(coordinator.time_zone).date()


class TestWhatIsNotMeasuredIsNotScored:
    async def test_a_partial_hour_is_left_out(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
    ) -> None:
        """Three-quarters of an hour under-reads by a quarter, and there is
        nothing in the figure to say so."""
        coordinator = await _coordinator(hass, entry)
        coordinator._buckets = [_bucket(NOON, 4500.0, seconds=2700)]

        assert _actual_kwh_by_hour(coordinator) == {}

    async def test_an_hour_with_no_reading_is_left_out(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
    ) -> None:
        """Not filled with a zero. A hole is not a quiet hour, and
        ``forecast.build_days`` drops the whole day for exactly this reason."""
        coordinator = await _coordinator(hass, entry)
        coordinator._buckets = [_bucket(NOON, None)]

        assert _actual_kwh_by_hour(coordinator) == {}


class TestItRefusesRatherThanBlames:
    """A provider must never be reported as biased because we could not read."""

    @pytest.mark.parametrize("archive", [None, {}])
    async def test_a_failed_or_empty_archive_scores_nothing(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
        archive,
    ) -> None:
        """``None`` is a failed query, ``{}`` is an empty archive, and neither is
        a provider that forecasts badly."""
        coordinator = await _coordinator(hass, entry)
        coordinator._buckets = [_bucket(NOON, 4500.0)]

        with patch(
            "custom_components.solar_sanity.scoring.async_forecast_series",
            return_value=archive,
        ):
            assert await async_score_providers(hass, coordinator) == []

    async def test_no_configured_provider_scores_nothing(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
    ) -> None:
        coordinator = await _coordinator(hass, entry)
        coordinator._buckets = [_bucket(NOON, 4500.0)]

        assert entry.data.get(CONF_FORECAST_ENTRIES) in (None, [])
        assert await async_score_providers(hass, coordinator) == []

    async def test_a_provider_entry_that_no_longer_exists_is_skipped(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry_data: dict,
    ) -> None:
        """Someone deletes their forecast integration; we do not raise about it."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={**entry_data, CONF_FORECAST_ENTRIES: ["01M0000000000000000000GONE"]},
        )
        coordinator = await _coordinator(hass, entry)
        coordinator._buckets = [_bucket(NOON, 4500.0)]

        assert await async_score_providers(hass, coordinator) == []


class TestTheSensorExistsBecauseAProviderDoes:
    """Gated on configuration, not on having earned a figure.

    Earned-ness changes with the weather. An entity that appeared and vanished
    with it would break every automation and history graph pointing at it, and
    would do so silently — so the sensor is created when a provider is
    configured and reports ``unknown`` until there is something to say.
    """

    async def test_a_configured_provider_gets_a_sensor_before_it_has_a_figure(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry_data: dict,
    ) -> None:
        provider = MockConfigEntry(domain="forecast_solar", title="Forecast.Solar")
        provider.add_to_hass(hass)
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={**entry_data, CONF_FORECAST_ENTRIES: [provider.entry_id]},
        )
        await _coordinator(hass, entry)

        states = [
            state
            for state in hass.states.async_all("sensor")
            if "accuracy" in (state.attributes.get("friendly_name") or "").lower()
        ]

        assert states, "no bias sensor was created for a configured provider"
        assert states[0].state in ("unknown", "unavailable")
        assert states[0].attributes.get("reason")

    async def test_no_provider_means_no_sensor(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
    ) -> None:
        await _coordinator(hass, entry)

        assert not [
            state
            for state in hass.states.async_all("sensor")
            if "accuracy" in (state.attributes.get("friendly_name") or "").lower()
        ]

    async def test_it_carries_no_state_class(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry_data: dict,
    ) -> None:
        """A judgement, not a measurement.

        It is recomputed over a rolling window, snapped to five points, and may
        return to unknown when the days behind it stop qualifying. Recording it
        as a statistic would produce a history graph of an opinion changing its
        mind.
        """
        provider = MockConfigEntry(domain="forecast_solar", title="Forecast.Solar")
        provider.add_to_hass(hass)
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={**entry_data, CONF_FORECAST_ENTRIES: [provider.entry_id]},
        )
        await _coordinator(hass, entry)

        state = next(
            state
            for state in hass.states.async_all("sensor")
            if "accuracy" in (state.attributes.get("friendly_name") or "").lower()
        )

        assert state.attributes.get("state_class") is None


class TestTomorrowCorrectedByWhatTheProviderActuallyDoes:
    """The decision about *when* a bias is worth applying is not made here.

    ``Bias`` separates the figure it measured from the figure it will state, and
    only the second is used. A bias computed from eleven days is real arithmetic
    and not yet an answer, and multiplying tomorrow by it would launder that
    distinction into a number people plan around.
    """

    async def test_no_stateable_bias_means_no_corrected_figure(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
    ) -> None:
        coordinator = await _coordinator(hass, entry)

        assert coordinator.expected_tomorrow_corrected_kwh is None

    async def test_a_stateable_bias_moves_tomorrow_by_exactly_it(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
    ) -> None:
        from custom_components.solar_sanity.analysis.forecast import Bias
        from custom_components.solar_sanity.scoring import ProviderScore

        coordinator = await _coordinator(hass, entry)
        coordinator._expected_tomorrow_kwh = 20.0
        coordinator.forecast_scores = (
            ProviderScore(
                entry_id="x",
                name="Provider",
                bias=Bias(days=40, value=-0.1, reportable_pct=-10),
            ),
        )

        if coordinator.expected_tomorrow_kwh is None:
            pytest.skip("no forecast to correct on this fixture")
        assert coordinator.expected_tomorrow_corrected_kwh == pytest.approx(
            coordinator.expected_tomorrow_kwh * 0.9
        )

    async def test_a_measured_but_unstateable_bias_is_not_applied(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
    ) -> None:
        """``value`` without ``reportable_pct`` is the estimator saying "I know
        this and I will not stand behind it". Tomorrow must not borrow it."""
        from custom_components.solar_sanity.analysis.forecast import Bias
        from custom_components.solar_sanity.scoring import ProviderScore

        coordinator = await _coordinator(hass, entry)
        coordinator.forecast_scores = (
            ProviderScore(
                entry_id="x",
                name="Provider",
                bias=Bias(days=11, value=-0.34, reportable_pct=None),
            ),
        )

        assert coordinator.expected_tomorrow_corrected_kwh is None


class TestTheRescoreService:
    async def test_it_is_registered_and_answers(
        self,
        hass: HomeAssistant,
        enable_custom_integrations: None,
        entry: MockConfigEntry,
    ) -> None:
        """Registered is not the same as working, and this one returns a
        response, so it can be asked."""
        await _coordinator(hass, entry)

        assert hass.services.has_service(DOMAIN, "rescore_forecasts")

        response = await hass.services.async_call(
            DOMAIN, "rescore_forecasts", {}, blocking=True, return_response=True
        )

        assert response is not None
        assert "providers" in response
