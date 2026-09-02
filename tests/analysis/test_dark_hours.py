"""The battery's loss fraction, measured where nothing else can reach it.

The two directions of a DC battery are locked together by one efficiency, so the
pair is one parameter and not two. Fitting them as free columns and checking
their agreement afterwards was never a test: charging *is* the surplus, so on a
self-consumption house the charge column is nearly collinear with generation,
and at the meter noise this project already calls healthy the fitted charge
coefficient carried a bias larger than the fault it had to separate.

The way out is not a better estimator. It is a subset of hours where the
collinear column does not exist: in the dark there is no charging, so there is
one column and one intercept and nothing that can be traded between them.

Every test here is about that premise or about what it buys. The most important
one is the first, because if the dark hours ever stop being free of charging the
whole estimator is unsound and nothing else would notice.
"""

from __future__ import annotations

import pytest
from analysis.model import Answer, DeclaredTopology, LossModel
from analysis.residual import build_days
from analysis.topology import (
    DARK_CHARGE_TOLERANCE,
    DC_BATTERY_GAMMA_WINDOW,
    MAX_LOAD_PROPORTIONAL_SHARE,
    _dark_hours_battery,
    fit_loss_model,
)

from tests.synth import house
from tests.synth.adapt import specs_for, to_request

DECLARED = DeclaredTopology(
    has_battery=Answer.YES,
    grid_is_single_net_sensor=Answer.NO,
    load_covers_whole_house=Answer.YES,
)
DAYS = 30
NO_EXPORT = ("pv", "grid_import", "battery_charge", "battery_discharge", "load")


def _dark(series, channels=None):
    """Run the estimator over a synthetic house, as ``fit_loss_model`` does."""
    specs = specs_for(channels) if channels else specs_for()
    request = to_request(series, specs=specs, declared=DECLARED)
    days = build_days(request.buckets, specs, LossModel(), request.utc_offset_hours)
    return _dark_hours_battery(days, specs)


class TestThePremise:
    """If charging ever reaches the dark hours, none of the rest holds."""

    @pytest.mark.parametrize("seed", range(3))
    def test_the_dark_hours_contain_no_charging(self, seed: int) -> None:
        """The one fact the whole estimator rests on.

        Charge is non-zero only when generation exceeds consumption, so it
        cannot happen in the dark. That is a property of the house rather than
        of the fit, which is why it is asserted directly: a change to the
        synthetic house's dispatch that put charging into the night would make
        the estimator unsound while every other test still passed.
        """
        _gamma, measured = _dark(house.measure_battery_dc(house.build(days=DAYS, seed=seed), 0.90))

        assert measured["dark_charge_wh"] == 0.0
        assert measured["dark_discharge_wh"] > 100_000.0

    def test_a_battery_charged_from_the_grid_overnight_is_refused(self) -> None:
        """The ordinary installation whose premise fails.

        A cheap overnight tariff is not a fault, and the estimator has no
        business measuring a slope on hours where the column it assumes absent
        is the largest thing moving. The ratio is checked directly rather than
        inferred, and such a house is handed back to the day fit.
        """
        series = house.measure_battery_dc(house.build(days=DAYS, seed=0), 0.90)
        # 3 kW into the battery for four hours every night, taken from the grid,
        # so the house still balances and only the premise is broken.
        into = list(series.data["battery_charge"])
        taken = list(series.data["grid_import"])
        for hour in range(series.hours):
            if hour % 24 in (1, 2, 3, 4):
                into[hour] += 3000.0
                taken[hour] += 3000.0
        charged = series.copy_with(battery_charge=into, grid_import=taken)

        gamma, measured = _dark(charged)

        assert measured["dark_charge_wh"] > DARK_CHARGE_TOLERANCE * measured["dark_discharge_wh"]
        assert gamma is None, "a grid-charged battery was measured on hours it charges in"


class TestWhatTheDarkCannotSee:
    """Every fault the day fit's agreement check existed to refuse."""

    @pytest.mark.parametrize("seed", range(3))
    def test_a_charge_channel_reading_low_is_invisible_in_the_dark(self, seed: int) -> None:
        """The trap that defeats a constrained day fit.

        Profiled over daylight this fault lands inside the acceptance band and
        is swallowed as conversion loss. In the dark the charge channel is zero
        whatever it reads, so the fault is not there to be found: the slope is a
        healthy house's slope and the floor refuses it.
        """
        series = house.scale(house.build(days=DAYS, seed=seed), "battery_charge", 0.90)

        gamma, measured = _dark(series)

        assert gamma is None
        assert abs(measured["dark_gamma"]) < DC_BATTERY_GAMMA_WINDOW[0]

    @pytest.mark.parametrize("seed", range(3))
    def test_unmapped_export_does_not_inflate_the_absorbed_loss(self, seed: int) -> None:
        """The failure the direction tolerance was built for.

        A fit that has energy leaving the house unaccounted for will take a
        spurious battery coefficient to help explain it, which drops the
        residual below the band that would have named the missing channel. The
        amount subtracted here is fixed on hours that contain none of the day's
        surplus, so a real battery beside an open boundary reads exactly itself.
        """
        series = house.drop(
            house.measure_battery_dc(house.build(days=DAYS, seed=seed), 0.95), "grid_export"
        )

        gamma, _measured = _dark(series, channels=NO_EXPORT)

        assert gamma is not None
        assert gamma == pytest.approx(0.05, abs=0.005), "the export inflated the battery term"


class TestThePartnerScreens:
    """The band alone admits two faults; these are what refuse them."""

    @pytest.mark.parametrize(
        "channel,share_key",
        [("load", "dark_load_share"), ("grid_import", "dark_import_share")],
    )
    def test_a_sensor_reading_low_is_refused_by_its_partner_share(
        self, channel: str, share_key: str
    ) -> None:
        """Both screens are load-bearing. Deleting either is the regression.

        A consumption or grid-import sensor reading a tenth low fits a dark
        slope squarely inside the band. What separates it from a battery is that
        its residual moves with that channel rather than with discharge.
        """
        series = house.scale(house.build(days=DAYS, seed=0), channel, 0.90)

        gamma, measured = _dark(series)

        assert abs(measured[share_key]) > MAX_LOAD_PROPORTIONAL_SHARE
        assert gamma is None, f"a {channel} sensor reading low was absorbed as battery loss"

    @pytest.mark.parametrize("seed", range(3))
    def test_a_healthy_battery_puts_almost_nothing_in_either_partner(self, seed: int) -> None:
        series = house.measure_battery_dc(house.build(days=DAYS, seed=seed), 0.90)

        gamma, measured = _dark(series)

        assert gamma is not None
        assert abs(measured["dark_load_share"]) < MAX_LOAD_PROPORTIONAL_SHARE
        assert abs(measured["dark_import_share"]) < MAX_LOAD_PROPORTIONAL_SHARE


class TestItSaysWhatItSaw:
    """A refusal that reports nothing is a refusal nobody can check."""

    @pytest.mark.parametrize("efficiency", [0.75, 0.70])
    def test_a_refusal_below_the_band_still_publishes_the_slope(self, efficiency: float) -> None:
        """The number is what tells a reader the estimator worked and the policy
        refused, rather than the estimator having found nothing."""
        gamma, measured = _dark(
            house.measure_battery_dc(house.build(days=DAYS, seed=0), efficiency)
        )

        assert gamma is None
        assert measured["dark_gamma"] == pytest.approx(1.0 - efficiency, abs=0.01)
        assert "dark_gamma_half_width" in measured
        assert "dark_blocks" in measured


class TestItDoesNotMove:
    """Determinism, for arithmetic the AST gates cannot check."""

    def test_the_slope_does_not_depend_on_the_order_of_the_specs(self) -> None:
        series = house.measure_battery_dc(house.build(days=DAYS, seed=0), 0.88)
        specs = specs_for()
        request = to_request(series, specs=specs, declared=DECLARED)
        days = build_days(request.buckets, specs, LossModel(), request.utc_offset_hours)

        forward, _ = _dark_hours_battery(days, specs)
        backward, _ = _dark_hours_battery(days, tuple(reversed(specs)))

        assert forward == backward

    def test_the_same_house_twice_is_the_same_number(self) -> None:
        series = house.measure_battery_dc(house.build(days=DAYS, seed=1), 0.88)

        assert _dark(series)[0] == _dark(series)[0]


class TestTheModelItFeeds:
    """What ``fit_loss_model`` does with it, end to end."""

    @pytest.mark.parametrize("efficiency", [0.95, 0.90, 0.85, 0.82])
    def test_the_term_is_established_and_exact(self, efficiency: float) -> None:
        series = house.measure_battery_dc(house.build(days=DAYS, seed=0), efficiency)
        specs = specs_for()
        request = to_request(series, specs=specs, declared=DECLARED)
        days = build_days(request.buckets, specs, LossModel(), request.utc_offset_hours)

        model = fit_loss_model(days, specs, None)

        assert model.established("battery_dc")
        assert model.battery_dc_gamma == pytest.approx(1.0 - efficiency, abs=0.002)
        assert not model.established("pv_dc"), "a generation term was fabricated beside it"

    @pytest.mark.parametrize("seed", range(3))
    def test_a_house_with_no_battery_loss_establishes_no_term(self, seed: int) -> None:
        series = house.build(days=DAYS, seed=seed)
        specs = specs_for()
        request = to_request(series, specs=specs, declared=DECLARED)
        days = build_days(request.buckets, specs, LossModel(), request.utc_offset_hours)

        model = fit_loss_model(days, specs, None)

        assert not model.established("battery_dc")
        assert model.battery_dc_gamma == 0.0
