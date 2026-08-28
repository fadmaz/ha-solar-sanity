"""The night identity, one channel at a time.

The reference installation has run for a month at "still looking" with a night
residual of about -326 Wh an hour and nothing able to explain it. The night fit
reported medians — middle-hour load, middle-hour discharge, middle-hour residual
— and medians do not compose: they are three different hours, so no arithmetic
over them says whether the channels reconcile.

Totals over one agreed set of hours do reconcile, exactly. That turns "the
numbers do not add up at night" from a summary into a subtraction anyone can
check a line at a time, and the size of each line says which channel is short.
"""

from __future__ import annotations

import pytest
from analysis.model import Answer, DeclaredTopology, LossModel, Role
from analysis.residual import build_days
from analysis.topology import fit_loss_model, night_fit_raw, night_ledger

from tests.synth import house
from tests.synth.adapt import specs_for, to_request

DECLARED = DeclaredTopology(
    has_battery=Answer.YES,
    grid_is_single_net_sensor=Answer.NO,
    load_covers_whole_house=Answer.YES,
)

DAYS = 21


def _ledger(series, specs=None) -> dict[str, float]:
    specs = specs or specs_for()
    request = to_request(series, specs=specs, declared=DECLARED)
    provisional = build_days(request.buckets, specs, LossModel(), request.utc_offset_hours)
    loss = fit_loss_model(provisional, specs, None)
    days = build_days(request.buckets, specs, loss, request.utc_offset_hours)
    return night_ledger(days, specs)


class TestItReconciles:
    """The property the whole thing rests on."""

    @pytest.mark.parametrize("seed", range(4))
    def test_a_healthy_night_adds_up_to_nothing(self, seed: int) -> None:
        ledger = _ledger(house.build(days=DAYS, seed=seed))

        assert ledger["night_sources_minus_sinks_wh"] == pytest.approx(0.0, abs=1.0)

    @pytest.mark.parametrize(
        "name,corrupt",
        [
            ("clean", lambda c: c),
            ("halve load", lambda c: house.halve(c, "load")),
            ("halve discharge", lambda c: house.halve(c, "battery_discharge")),
            ("standby 80 W", lambda c: house.add_standby(c, 80.0)),
            ("no import measured", lambda c: house.drop(c, "grid_import")),
        ],
    )
    def test_the_identity_matches_the_residual_it_explains(self, name: str, corrupt) -> None:
        """Two different computations of the same quantity — one from the
        buckets, one from the residual the engine had already built. They must
        agree, or the ledger is explaining a number nobody else is using."""
        ledger = _ledger(corrupt(house.build(days=DAYS, seed=0)))

        assert ledger["night_sources_minus_sinks_wh"] == pytest.approx(
            ledger["night_total_residual_wh"], abs=1e-6
        ), name


class TestItNamesTheShortChannel:
    def test_an_injected_draw_comes_back_exactly(self) -> None:
        """80 W across every night hour, recovered to the watt-hour. This is the
        measurement that would tell the reference installation whether its
        missing energy is a continuous draw or a mis-reading channel."""
        ledger = _ledger(house.add_standby(house.build(days=DAYS, seed=0), 80.0))
        hours = ledger["night_ledger_hours"]

        assert ledger["night_sources_minus_sinks_wh"] == pytest.approx(80.0 * hours, rel=1e-6)

    def test_a_halved_channel_shows_up_as_half(self) -> None:
        clean = _ledger(house.build(days=DAYS, seed=0))
        halved = _ledger(house.halve(house.build(days=DAYS, seed=0), "load"))

        # Not exact: both totals are rounded to the milliwatt-hour before being
        # reported, so half of one is not bit-identical to the other. The
        # tolerance is still four orders of magnitude below a meter's precision.
        assert halved["night_total_load_wh"] == pytest.approx(
            clean["night_total_load_wh"] / 2.0, rel=1e-6
        )

    def test_the_other_channels_are_untouched_by_one_being_wrong(self) -> None:
        clean = _ledger(house.build(days=DAYS, seed=0))
        halved = _ledger(house.halve(house.build(days=DAYS, seed=0), "load"))

        for key in ("night_total_grid_import_wh", "night_total_battery_discharge_wh"):
            assert halved[key] == pytest.approx(clean[key], rel=1e-6), key


class TestItInventsNothing:
    def test_a_channel_that_is_not_configured_gets_no_number(self) -> None:
        """The reference installation has no export sensor. Reporting 0 Wh of
        export would state that nothing was exported, which is exactly the thing
        nobody can know without the meter."""
        without_export = tuple(s for s in specs_for() if s.key != "grid_export")

        ledger = _ledger(house.build(days=DAYS, seed=0), specs=without_export)

        assert "night_total_grid_export_wh" not in ledger
        assert "night_total_grid_import_wh" in ledger

    def test_no_pv_channel_means_night_cannot_be_identified(self) -> None:
        """Without generation there is no way to know which hours are night."""
        without_pv = tuple(s for s in specs_for() if s.role is not Role.PV)

        assert _ledger(house.build(days=DAYS, seed=0), specs=without_pv) == {}

    def test_no_days_produces_nothing_rather_than_zeroes(self) -> None:
        specs = specs_for()

        assert night_ledger((), specs) == {}


class TestTheHoursAreHonest:
    def test_it_says_how_many_hours_it_used(self) -> None:
        ledger = _ledger(house.build(days=DAYS, seed=0))

        assert ledger["night_ledger_hours"] > 0

    def test_the_count_is_reported_beside_the_fits_own(self) -> None:
        """So a gap between them reads as coverage rather than as physics."""
        series = house.build(days=DAYS, seed=0)
        specs = specs_for()
        request = to_request(series, specs=specs, declared=DECLARED)
        provisional = build_days(request.buckets, specs, LossModel(), request.utc_offset_hours)
        loss = fit_loss_model(provisional, specs, None)
        days = build_days(request.buckets, specs, loss, request.utc_offset_hours)

        raw = night_fit_raw(days, specs)

        assert "night_hours" in raw
        assert "night_ledger_hours" in raw
        assert raw["night_ledger_hours"] <= raw["night_hours"]

    def test_daylight_is_not_counted(self) -> None:
        """Generation is zero across every hour the ledger used, or the totals
        describe something other than night."""
        ledger = _ledger(house.build(days=DAYS, seed=0))

        assert ledger["night_total_pv_wh"] == pytest.approx(0.0, abs=1.0)


class TestItIsStillPure:
    @pytest.mark.parametrize("seed", range(3))
    def test_the_same_input_gives_the_same_ledger(self, seed: int) -> None:
        series = house.build(days=DAYS, seed=seed)

        assert _ledger(series) == _ledger(series)

    def test_the_order_channels_were_mapped_in_does_not_matter(self) -> None:
        series = house.build(days=DAYS, seed=0)
        forward = specs_for()
        backward = tuple(reversed(forward))

        assert _ledger(series, specs=forward) == _ledger(series, specs=backward)
