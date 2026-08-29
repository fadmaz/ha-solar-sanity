"""The loss model, said out loud rather than only subtracted.

The engine fits an expected-loss model on every run and, until now, threw all of
it away on the one verdict most people ever see. A healthy installation got the
word OK and nothing else, while the analysis behind it had measured a continuous
draw nobody's sensors account for.

Only the standby term is spoken. Copy exists for the two DC terms as well and is
deliberately still silent — see `TestTheDcNotesStaySilent` for the measurements
that decided it.
"""

from __future__ import annotations

import pytest
from analysis.engine import analyse
from analysis.faults import Code
from analysis.model import Answer, DeclaredTopology, LossModel, Status
from analysis.residual import build_days
from analysis.topology import fit_loss_model

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


class TestTheDcNotesStaySilent:
    """Pinning a decision, so it is not quietly reversed as an oversight.

    `pv_measured_dc` and `battery_measured_dc` have finished copy telling the
    reader that a few per cent of loss is normal conversion and there is nothing
    to fix. Both stay unraised because the loss model's three terms are not
    identifiable from one another, and the measurements below are why.
    """

    def test_an_unmetered_draw_masquerades_as_dc_measurement(self) -> None:
        """80 W of draw on a house whose sensors both read AC. If the DC notes
        were emitted from `established()`, this installation would be told its
        sensors read DC-side and the loss was normal."""
        drawing = _fit(house.add_standby(house.build(days=DAYS, seed=0), 80.0))

        assert drawing.established("pv_dc")
        assert drawing.pv_dc_gamma > 0.04

    def test_the_spurious_fit_can_exceed_the_genuine_one(self) -> None:
        """Which is why no threshold separates them: the false case is larger
        than the true one."""
        genuine = _fit(house.measure_battery_dc(house.build(days=DAYS, seed=0)))
        spurious = _fit(house.add_standby(house.build(days=DAYS, seed=0), 80.0))

        assert genuine.established("battery_dc") and spurious.established("battery_dc")
        assert spurious.battery_dc_gamma > genuine.battery_dc_gamma

    @pytest.mark.parametrize("seed", range(3))
    def test_neither_dc_note_is_ever_spoken(self, seed: int) -> None:
        for corrupt in (
            house.measure_pv_dc,
            house.measure_battery_dc,
            lambda c: house.add_standby(c, 80.0),
        ):
            report = _report(corrupt(house.build(days=DAYS, seed=seed)))
            joined = " ".join(report.notes)

            assert "conversion loss" not in joined
            assert "DC side" not in joined

    def test_their_copy_is_still_written_and_still_renders(self) -> None:
        """So the day the fit can separate the terms, the sentences are ready."""
        from analysis import faults

        assert faults.render(Code.PV_MEASURED_DC, loss=4.0, name="Solar")[0]
        assert faults.render(Code.BATTERY_MEASURED_DC)[0]
