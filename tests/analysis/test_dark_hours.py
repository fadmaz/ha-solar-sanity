"""The battery's loss fraction, measured on the hours after dark.

The two directions of a DC battery are locked together by one efficiency, so
the pair is one parameter and not two. Fitting them as free columns and
checking their agreement afterwards was never a test: charging *is* the
surplus, so over daylight the charge column is nearly collinear with
generation, and at the meter noise this project calls healthy the fitted charge
coefficient carried a bias larger than the fault it had to separate.

After dark that collinearity is gone — the charge column tracks discharge at
-0.53 to -0.26 there, because grid charging happens in the cheap hours when
discharge is suppressed, against +0.48 to +0.70 against generation in daylight.
So the two directions are fitted as the single column the physics says they
are: ``discharge + charge/(1-gamma)``, whose slope is gamma.

0.26.0 assumed the charge column away instead, and refused any house that
charged after dark. That is an ordinary installation on a cheap overnight
tariff, and the reference installation is one. Keeping those hours is also what
keeps the screens alive: drop them and the survivors satisfy
``load = discharge - residual`` identically, so the load screen returns its own
input and the import column is a constant zero.

What is delicate here is not charge against discharge. It is load against
discharge, which after dark runs 0.70 to 0.98, and `MIN_LOAD_INDEPENDENCE` is
the gate on it — the only thing standing between this estimator and absorbing a
consumption sensor reading ten per cent low.
"""

from __future__ import annotations

import pytest
from analysis.model import Answer, DeclaredTopology, LossModel
from analysis.residual import build_days
from analysis.topology import (
    DC_BATTERY_GAMMA_WINDOW,
    MAX_LOAD_PROPORTIONAL_SHARE,
    MIN_LOAD_INDEPENDENCE,
    MIN_NIGHT_BLOCKS,
    _block_slope,
    _dark_blocks,
    _dark_hours_battery,
    fit_loss_model,
    night_fit_raw,
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


def _grid_charged(series, kwh_per_night: float, efficiency: float):
    """A battery filled from the grid overnight, metered on its DC side.

    ``E`` leaves the grid and ``E * efficiency`` is metered into the battery, so
    the shortfall is the conversion loss the lock has to explain rather than
    something the fixture has hidden in another channel. An earlier version of
    this helper added the shortfall to consumption, which balances the identity
    exactly and leaves the charge hours with no residual to measure — the fixture
    silently testing nothing.
    """
    data = {key: list(values) for key, values in series.data.items()}
    per_hour = kwh_per_night * 1000.0 / 4.0
    for hour in range(series.hours):
        if hour % 24 in (1, 2, 3, 4):
            data["grid_import"][hour] += per_hour
            data["battery_charge"][hour] += per_hour * efficiency
    return series.copy_with(**data)


class TestTheLock:
    """One parameter for two directions, and what that buys."""

    @pytest.mark.parametrize("efficiency", [0.95, 0.90, 0.85])
    @pytest.mark.parametrize("kwh", [5.0, 12.0])
    def test_a_grid_charged_battery_is_measured_rather_than_refused(
        self, efficiency: float, kwh: float
    ) -> None:
        """The houses 0.26.0 turned away.

        A cheap overnight tariff is not a fault. Under the lock the charge hours
        carry information, so the figure comes back exact on clean data at charge
        ratios from 0.4 to 1.2 — more energy into the battery overnight than out
        of it, and still answered.
        """
        series = _grid_charged(
            house.measure_battery_dc(house.build(days=DAYS, seed=0), efficiency),
            kwh,
            efficiency,
        )

        gamma, measured = _dark(series)

        assert measured["dark_charge_wh"] > 0.0, "the fixture did not charge in the dark"
        assert gamma is not None, "a healthy grid-charged battery was refused"
        assert gamma == pytest.approx(1.0 - efficiency, abs=0.002)

    @pytest.mark.parametrize("efficiency", [0.95, 0.90, 0.85])
    def test_without_dark_charging_it_is_the_plain_slope_exactly(self, efficiency: float) -> None:
        """Bit-identical, not merely close.

        Where nothing charges after dark the locked column *is* the discharge
        column for every candidate gamma, so the search is skipped rather than
        approximated. This is what makes the lock free on the houses the
        estimator already answered.
        """
        series = house.measure_battery_dc(house.build(days=DAYS, seed=0), efficiency)
        specs = specs_for()
        request = to_request(series, specs=specs, declared=DECLARED)
        days = build_days(request.buckets, specs, LossModel(), request.utc_offset_hours)

        blocks = _dark_blocks(days, specs)
        assert blocks is not None
        xs, cs, ys, *_ = blocks
        assert not any(charge > 0.0 for charge in cs)

        _gamma, measured = _dark(series)

        assert measured["dark_gamma"] == _block_slope(xs, ys)[0]

    def test_the_search_does_not_depend_on_where_it_started(self) -> None:
        """Bisection rather than iteration, and the reason is recorded.

        A plain fixed point two-cycles on some houses, which makes the answer at
        any step cap arbitrary between two values. Halving a bracket has one
        root and no convergence question, so the same house gives the same
        number every time.
        """
        series = _grid_charged(
            house.measure_battery_dc(house.build(days=DAYS, seed=1), 0.90), 8.0, 0.90
        )

        first, _ = _dark(series)
        second, _ = _dark(series)

        assert first == second


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


class TestTheReportCarriesThem:
    """Measuring something and publishing it are two different things.

    The estimator returned its numbers from the first commit and the report
    dropped them, because the call sat below ``night_fit_raw``'s two-hundred-hour
    gate. The houses past that gate are exactly the houses the dark hours exist
    to answer, so the disclosure was missing wherever it was worth having — the
    reference installation refused a gamma four separate ways and said none of
    them. The comment above that gate warns about this precise trap for the
    night ledger; the fix is to sit beside the ledger.
    """

    def _raw(self, series, channels=None):
        specs = specs_for(channels) if channels else specs_for()
        request = to_request(series, specs=specs, declared=DECLARED)
        days = build_days(request.buckets, specs, LossModel(), request.utc_offset_hours)
        return night_fit_raw(days, specs)

    def test_a_house_the_night_fit_cannot_speak_about_still_reports_the_dark_hours(
        self,
    ) -> None:
        """Ten days is under two hundred night hours and over sixty blocks."""
        raw = self._raw(house.measure_battery_dc(house.build(days=10, seed=0), 0.88))

        assert "night_slope" not in raw, "this fixture is meant to be past the night gate"
        assert raw["dark_blocks"] >= MIN_NIGHT_BLOCKS
        assert raw["dark_gamma"] == pytest.approx(0.12, abs=0.01)

    @pytest.mark.parametrize("efficiency", [0.88, 0.70])
    def test_the_figures_are_published_whether_or_not_the_gamma_is_taken(
        self, efficiency: float
    ) -> None:
        raw = self._raw(house.measure_battery_dc(house.build(days=DAYS, seed=0), efficiency))

        for key in ("dark_blocks", "dark_gamma", "dark_gamma_half_width", "dark_charge_wh"):
            assert key in raw, f"{key} missing at efficiency {efficiency}"


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


class TestTheScreenThatKeepsItHonest:
    """`MIN_LOAD_INDEPENDENCE`, and the fault it is the only guard against."""

    @pytest.mark.parametrize("efficiency", [0.95, 0.90])
    @pytest.mark.parametrize("channels", [None, NO_EXPORT])
    def test_a_consumption_sensor_reading_low_is_refused_on_a_charging_house(
        self, efficiency: float, channels
    ) -> None:
        """The decisive case for the whole design.

        Admitting the charging hours admits more houses, and the question that
        decides whether that is safe is not whether the figure is recovered but
        whether this fault is still turned away. It is: the slope it produces is
        half again the truth, and the load screen sees it at roughly +0.11
        against a limit of 0.04 — a measurement rather than the identity it
        would be if the charging hours had been dropped instead.

        Run on both boundary shapes, because the open one is the reference
        installation's and is where dropping hours breaks the screen.
        """
        base = _grid_charged(
            house.measure_battery_dc(house.build(days=DAYS, seed=0), efficiency), 5.0, efficiency
        )
        if channels is not None:
            base = house.drop(base, "grid_export")

        healthy, _ = _dark(base, channels)
        gamma, measured = _dark(house.scale(base, "load", 0.90), channels)

        assert healthy is not None, "the healthy house was refused, so this proves nothing"
        assert gamma is None, "a consumption sensor reading a tenth low was absorbed"
        assert abs(measured["dark_load_share"]) > MAX_LOAD_PROPORTIONAL_SHARE

    @pytest.mark.parametrize("efficiency", [0.95, 0.90])
    def test_the_screen_is_a_measurement_and_not_an_identity(self, efficiency: float) -> None:
        """What the independence figure exists to certify.

        The load screen can fail silently: on a house where consumption is the
        residual's mirror image it returns its own input, and a fault then lands
        on the *safer* side of it. This figure is computed from the design
        columns alone — the residual never enters — so no fault can flatter it,
        and it must clear the floor before the screen above is believed.
        """
        series = _grid_charged(
            house.measure_battery_dc(house.build(days=DAYS, seed=0), efficiency), 5.0, efficiency
        )

        gamma, measured = _dark(series)

        assert gamma is not None
        assert measured["dark_load_independence"] >= MIN_LOAD_INDEPENDENCE

    def test_the_independence_figure_is_published_even_when_it_refuses(self) -> None:
        """A refusal on this gate has to say so, like every other refusal here."""
        series = house.measure_battery_dc(house.build(days=DAYS, seed=0), 0.90)

        _gamma, measured = _dark(series)

        assert "dark_load_independence" in measured
