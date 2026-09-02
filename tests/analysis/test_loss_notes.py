"""The loss model, said out loud rather than only subtracted.

The engine fits an expected-loss model on every run and, until now, threw all of
it away on the one verdict most people ever see. A healthy installation got the
word OK and nothing else, while the analysis behind it had measured a continuous
draw nobody's sensors account for.

All three terms are spoken. The two DC notes were held back while the fit could
not tell a conversion loss from a continuous draw it would have been reassuring
about; separating the two directions of the battery settled that, and
`TestTheDcNotesAreSpokenNow` holds the measurements that decided it.
"""

from __future__ import annotations

import pytest
from analysis.engine import analyse
from analysis.faults import Code
from analysis.model import Answer, DeclaredTopology, LossModel, Status
from analysis.residual import build_days
from analysis.topology import fit_loss_model, joint_loss_fit

from tests.synth import house
from tests.synth.adapt import specs_for, to_request

DECLARED = DeclaredTopology(
    has_battery=Answer.YES,
    grid_is_single_net_sensor=Answer.NO,
    load_covers_whole_house=Answer.YES,
)

DAYS = 30


def _report(series):
    return analyse(to_request(series, declared=DECLARED))


def _request(series):
    return to_request(series, specs=specs_for(), declared=DECLARED)


def _fit(series) -> LossModel:
    specs = specs_for()
    request = to_request(series, specs=specs, declared=DECLARED)
    provisional = build_days(request.buckets, specs, LossModel(), request.utc_offset_hours)
    return fit_loss_model(provisional, specs, None)


def _standby_note(report) -> str | None:
    return next((n for n in report.notes if "flows continuously" in n), None)


class TestItSaysWhatItMeasured:
    @pytest.mark.parametrize("watts", [15.0, 25.0, 35.0, 45.0])
    def test_a_continuous_draw_is_reported_on_a_healthy_house(self, watts: float) -> None:
        report = _report(house.add_standby(house.build(days=DAYS, seed=0), watts))

        assert report.status is Status.OK
        note = _standby_note(report)
        assert note is not None, f"{watts:.0f} W was fitted and never mentioned"
        assert f"{watts:.0f} W" in note

    @pytest.mark.parametrize("watts", [15.0, 35.0, 45.0])
    def test_it_reports_the_draw_without_claiming_the_cause(self, watts: float) -> None:
        """The fit cannot tell an inverter idling from a circuit outside the
        clamp — they are the same signal on night residual, and this file's own
        fixture models the "standby" draw as `load = [v - watts]`, which is an
        unmetered circuit.

        So the note may say where a figure like this usually comes from and may
        not call it normal or say there is nothing to fix. A real 90 W circuit
        is 790 kWh a year, and this was the only place the product would have
        told somebody in writing to ignore it.
        """
        note = _standby_note(_report(house.add_standby(house.build(days=DAYS, seed=0), watts)))

        assert note is not None
        assert "nothing to fix" not in note.lower()
        assert "normal for this equipment" not in note.lower()
        assert "worth finding" in note.lower()

    def test_the_figure_is_the_one_that_was_fitted(self) -> None:
        """Not recomputed for the sentence — the same number the residual was
        corrected by, or the note would describe a different installation."""
        series = house.add_standby(house.build(days=DAYS, seed=0), 35.0)

        assert f"{_fit(series).standby_w:.0f} W" in _standby_note(_report(series))

    def test_the_sentence_is_finished(self) -> None:
        report = _report(house.add_standby(house.build(days=DAYS, seed=0), 35.0))
        note = _standby_note(report)

        assert "{" not in note and "}" not in note
        assert "nan" not in note.lower()
        assert note.endswith(".")

    @pytest.mark.parametrize("seed", range(4))
    def test_it_does_not_move_between_runs(self, seed: int) -> None:
        series = house.add_standby(house.build(days=DAYS, seed=seed), 35.0)

        assert _report(series).notes == _report(series).notes


class TestItStaysQuietWhenItShould:
    @pytest.mark.parametrize("seed", range(4))
    def test_a_house_with_nothing_unmeasured_hears_nothing(self, seed: int) -> None:
        report = _report(house.build(days=DAYS, seed=seed))

        assert report.status is Status.OK
        assert _standby_note(report) is None

    @pytest.mark.parametrize("seed", range(3))
    @pytest.mark.parametrize("noise", [0.03, 0.05])
    def test_meter_noise_is_not_a_continuous_draw(self, seed: int, noise: float) -> None:
        report = _report(house.add_noise(house.build(days=DAYS, seed=seed), noise, seed=seed))

        assert _standby_note(report) is None

    @pytest.mark.parametrize(
        "name,corrupt",
        [
            ("halve load", lambda c: house.halve(c, "load")),
            ("halve pv", lambda c: house.halve(c, "pv")),
            ("invert discharge", lambda c: house.invert(c, "battery_discharge")),
            ("freeze load", lambda c: house.freeze(c, "load", 200)),
            ("mis-scaled pv", lambda c: house.scale(c, "pv", 0.92)),
            ("import in kW", lambda c: house.scale(c, "grid_import", 1000.0)),
        ],
    )
    @pytest.mark.parametrize("seed", range(3))
    def test_a_broken_house_is_never_told_the_loss_is_normal(
        self, name: str, corrupt, seed: int
    ) -> None:
        """The note's whole risk. It says a number is expected and there is
        nothing to fix, so it must never appear beside a fault — the residual a
        fault produces is exactly what a loss term would absorb if it could."""
        report = _report(corrupt(house.build(days=DAYS, seed=seed)))

        assert _standby_note(report) is None, f"{name}/{seed}: reassured a broken house"

    def test_a_draw_too_large_to_be_standby_is_not_called_standby(self) -> None:
        """Above what an inverter idles at it is a load, not a power supply, and
        `_draw_note` is what says so."""
        report = _report(house.add_standby(house.build(days=DAYS, seed=0), 300.0))

        assert _standby_note(report) is None


class TestTheTermsAreNowSeparable:
    """The measurements that used to say the opposite.

    Each of these asserted, before the joint fit, that the loss model could not
    tell a real DC measurement from an unmetered draw. They now assert that it
    can, and they are kept in that shape deliberately: if the fit ever regresses
    to estimating one term at a time, these fail rather than the notes quietly
    becoming wrong again.
    """

    def test_an_unmetered_draw_is_not_read_as_dc_measurement(self) -> None:
        """80 W of draw on a house whose sensors both read AC.

        Fitted one term at a time this came back as `pv_dc` above 0.04, and an
        installation would have been told its sensors read DC-side and the loss
        was normal. The flat column now takes it, to the watt.
        """
        drawing = _fit(house.add_standby(house.build(days=DAYS, seed=0), 80.0))

        assert not drawing.established("pv_dc")
        assert not drawing.established("battery_dc")

    def test_the_draw_lands_in_the_flat_term_where_it_belongs(self) -> None:
        request = _request(house.add_standby(house.build(days=DAYS, seed=0), 80.0))
        specs = specs_for()
        days = build_days(request.buckets, specs, LossModel(), request.utc_offset_hours)

        fit = joint_loss_fit(days, specs)

        assert fit is not None
        assert fit["flat"] == pytest.approx(80.0, abs=5.0)
        assert abs(fit["pv_dc"]) < 0.005
        # Two battery columns, not one: the directions lose different fractions.
        assert abs(fit["battery_charge"]) < 0.005
        assert abs(fit["battery_discharge"]) < 0.005

    def test_a_genuine_dc_battery_is_still_found(self) -> None:
        """The separation has to cost the true case nothing."""
        genuine = _fit(house.measure_battery_dc(house.build(days=DAYS, seed=0)))

        assert genuine.established("battery_dc")
        assert 0.02 <= genuine.battery_dc_gamma <= 0.10

    def test_all_three_are_recovered_at_once(self) -> None:
        """The case that was impossible: a DC-measured house that also has an
        unmetered draw. One term at a time, each contaminated the others.

        Four columns now rather than three. A battery metered on its DC side
        does not lose the same fraction both ways — the charge side is
        ``gamma / (1 - gamma)`` against the discharge side's ``gamma`` — so the
        two are fitted apart and both come back exact.
        """
        from analysis.model import LossModel
        from analysis.residual import build_days
        from analysis.topology import joint_loss_fit

        series = house.add_standby(
            house.measure_battery_dc(house.measure_pv_dc(house.build(days=DAYS, seed=0))), 80.0
        )
        specs = specs_for()
        request = _request(series)
        days = build_days(request.buckets, specs, LossModel(), request.utc_offset_hours)

        fit = joint_loss_fit(days, specs)

        assert fit is not None
        assert fit["pv_dc"] == pytest.approx(0.041, abs=0.01)
        assert fit["battery_discharge"] == pytest.approx(0.05, abs=0.01)
        assert fit["battery_charge"] == pytest.approx(0.0526, abs=0.01)
        assert fit["flat"] == pytest.approx(80.0, abs=10.0)

    @pytest.mark.parametrize("seed", range(4))
    def test_a_draw_never_fits_a_dc_term_on_any_seed(self, seed: int) -> None:
        """It lands in the term that is actually about it, and only that one.

        This used to assert that an 80 W draw fitted *nothing*, which was true
        and was true for the wrong reason: the standby term was capped at a
        fifth of night load, so 80 W was refused on this house and the empty
        tuple proved only that the cap had fired. With the cap replaced by a
        test of shape, the draw is absorbed where it belongs — and the two DC
        terms it used to be confused with come back at exactly nought, which is
        the thing this class exists to demonstrate.
        """
        fitted = _fit(house.add_standby(house.build(days=DAYS, seed=seed), 80.0))

        assert fitted.fitted_terms == ("standby",)
        assert fitted.standby_w == pytest.approx(80.0, abs=1.0)
        assert fitted.pv_dc_gamma == 0.0
        assert fitted.battery_dc_gamma == 0.0


class TestTheDcNotesAreSpokenNow:
    """The decision that used to be pinned the other way, and why it moved.

    `pv_measured_dc` and `battery_measured_dc` had finished copy and no producer.
    They stayed unraised because the loss model's three terms were not
    identifiable from one another: fitted one at a time, an unmetered draw read
    as DC conversion at a *larger* figure than a genuinely DC-measured battery
    produced, so no threshold could separate them, and the copy's "normal
    conversion loss, nothing to fix" would have been said about a draw somebody
    was paying for.

    The joint fit and the battery's two-direction check ended that — see
    `TestTheTermsAreNowSeparable`. What remained was a product decision, and it
    has been taken: **absorb, and say so.**

    The argument that settled it is that the quiet option was never the cautious
    one. A generation coefficient inside the window was already being subtracted
    without a word, so a sensor reading a tenth high produced "no problem found"
    and the assumption behind that answer was shown to nobody. A note costs a
    correctly configured house one sentence; its absence costs a
    mis-configured one the only chance it had of being told.
    """

    @pytest.mark.parametrize("seed", range(3))
    def test_each_term_is_named_when_it_is_taken(self, seed: int) -> None:
        clean = house.build(days=DAYS, seed=seed)

        assert "before the inverter" in " ".join(_report(house.measure_pv_dc(clean)).notes)
        assert "DC side" in " ".join(_report(house.measure_battery_dc(clean)).notes)
        assert "continuously" in " ".join(_report(house.add_standby(clean, 80.0)).notes)

    @pytest.mark.parametrize("seed", range(3))
    def test_a_house_with_no_loss_is_told_nothing(self, seed: int) -> None:
        """The note is about something that was done. On a house where nothing
        was subtracted there is nothing to disclose, and a reassurance nobody
        needed is still a sentence somebody has to read."""
        assert _report(house.build(days=DAYS, seed=seed)).notes == ()

    @pytest.mark.parametrize("seed", range(3))
    def test_the_figure_in_the_note_is_the_figure_that_was_subtracted(self, seed: int) -> None:
        """A disclosure that does not match what was done is worse than none.

        Both readings of it appear, because a sensor a share of whose output
        never arrives is a sensor reading that much high, and the data cannot
        say which description is the true one.
        """
        report = _report(house.measure_pv_dc(house.build(days=DAYS, seed=seed), 0.90))
        note = " ".join(report.notes)

        assert report.loss_model.pv_dc_gamma == pytest.approx(0.10, abs=0.005)
        assert "10%" in note, note
        assert "11%" in note, note

    def test_a_loss_too_large_to_be_conversion_is_neither_taken_nor_excused(self) -> None:
        """The bound is a judgement about inverters, not a discrimination.

        Nothing in the data separates a sensor before the inverter from one
        reading high by the same factor — they are the same series. So beyond
        what conversion can plausibly account for, the engine stops assuming and
        goes back to saying it cannot explain the difference.
        """
        report = _report(house.measure_pv_dc(house.build(days=DAYS, seed=0), 0.80))

        assert report.loss_model.pv_dc_gamma == 0.0
        assert report.notes == ()

    def test_their_copy_still_renders(self) -> None:
        from analysis import faults

        assert faults.render(Code.PV_MEASURED_DC, loss=4.0, over=4.2, name="Solar")[0]
        assert faults.render(Code.BATTERY_MEASURED_DC, loss=5.0)[0]
