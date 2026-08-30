"""An unmeasured path across the boundary is not a fault, and must not read as one.

Every scenario here is a *correctly wired* house whose configuration is
incomplete: a real energy path exists that nothing is mapped to. The residual
that produces is large, daily and one-signed — indistinguishable from a
miscounted sensor if you only look at its size.

This suite exists because a real installation sat at a 40% residual reporting
"the numbers do not add up, but no explanation is convincing yet" while the
actual answer — no export sensor is mapped — was already knowable from the
channel list before a single bucket was read.
"""

from __future__ import annotations

import pytest
from analysis.engine import analyse
from analysis.faults import Code
from analysis.model import Answer, DeclaredTopology, LossModel, Role, Status
from analysis.residual import build_days
from analysis.topology import Closure, check_closure, fit_loss_model

from tests.synth import house
from tests.synth.adapt import specs_for, to_request

#: The user's own shape: import measured, export not, battery measured.
NO_EXPORT = ("pv", "grid_import", "battery_charge", "battery_discharge", "load")

DECLARED = DeclaredTopology(
    has_battery=Answer.YES,
    grid_is_single_net_sensor=Answer.NO,
    load_covers_whole_house=Answer.YES,
)


def _self_consumed(series):
    """The same house with nowhere for surplus to go but the battery.

    Not ``house.drop("grid_export")`` — that removes the *measurement* and
    leaves the export happening, which is the fault case, not the control.
    Here the energy genuinely never leaves, so the identity still closes while
    surplus hours remain plentiful.
    """
    charge = [
        c + e
        for c, e in zip(series.data["battery_charge"], series.data["grid_export"], strict=True)
    ]
    return series.copy_with(battery_charge=charge, grid_export=[0.0] * series.hours)


class TestClosure:
    """A boundary with an unmeasured path is open, whatever else is mapped."""

    def test_an_unmapped_export_path_is_open(self) -> None:
        result = check_closure(specs_for(NO_EXPORT), DECLARED)

        assert result.state is Closure.OPEN
        assert "leaving the house" in result.reason

    def test_a_single_net_meter_closes_it(self) -> None:
        """One signed sensor carries both directions, so import alone is whole."""
        declared = DeclaredTopology(
            has_battery=Answer.YES,
            grid_is_single_net_sensor=Answer.YES,
        )
        result = check_closure(specs_for(NO_EXPORT), declared)

        assert result.state is Closure.CLOSED

    def test_a_fully_mapped_house_stays_closed(self) -> None:
        assert check_closure(specs_for(), DECLARED).state is Closure.CLOSED


class TestThePartialLoadSensorTheOwnerToldUsAbout:
    """``load_covers_whole_house`` was asked at setup, stored, and never read.

    A load sensor on part of the house leaves the rest of the consumption
    outside every channel there is. The identity cannot close, and the energy it
    misses arrives in the residual looking like a fault — so the engine was
    reporting these houses as fully measured on their owner's own word that they
    are not, and then hunting for a cause of the gap that word explains.
    """

    @staticmethod
    def _declared(covers: Answer, net: Answer = Answer.NO) -> DeclaredTopology:
        return DeclaredTopology(
            has_battery=Answer.YES,
            grid_is_single_net_sensor=net,
            load_covers_whole_house=covers,
        )

    def test_a_partial_load_sensor_opens_an_otherwise_closed_house(self) -> None:
        result = check_closure(specs_for(), self._declared(Answer.NO))

        assert result.state is Closure.OPEN
        assert "whole house" in result.reason

    @pytest.mark.parametrize("covers", [Answer.YES, Answer.UNKNOWN])
    def test_anything_but_a_flat_no_leaves_the_verdict_alone(self, covers: Answer) -> None:
        """``UNKNOWN`` is the stored default, so treating it as a partial sensor
        would open the boundary on every installation that skipped the question."""
        assert check_closure(specs_for(), self._declared(covers)).state is Closure.CLOSED

    def test_it_does_not_take_the_night_hours_away_from_a_house_with_no_export(
        self,
    ) -> None:
        """The ordering property, and the whole reason this branch is last.

        ``check_closure`` returns on first match. Answering ahead of the export
        branch would report the boundary open — correctly — but with
        ``unmeasured_export`` unset, and that flag is the sole thing that earns
        a house the restricted night-hours verdict. Every no-export house would
        have silently lost the only real answer available to it, as a result of
        the engine being told *more* about the installation.
        """
        result = check_closure(specs_for(NO_EXPORT), self._declared(Answer.NO))

        assert result.state is Closure.OPEN
        assert result.unmeasured_export is True

    def test_the_house_still_gets_a_verdict_rather_than_a_shrug(self) -> None:
        """Opening the boundary must not cost the owner their analysis."""
        report = analyse(
            to_request(
                house.build(days=30, seed=0),
                specs=specs_for(),
                declared=self._declared(Answer.NO),
            )
        )

        assert report.status is not Status.INSUFFICIENT_DATA
        assert report.residual.valid_days > 0


class TestMissingExport:
    """The finding whose copy has always existed and could never be reached."""

    @staticmethod
    def _report(seed: int, days: int = 30):
        series = house.build(days=days, seed=seed)
        return analyse(to_request(series, specs=specs_for(NO_EXPORT), declared=DECLARED))

    @pytest.mark.parametrize("seed", range(12))
    def test_nothing_else_is_ever_named(self, seed: int) -> None:
        """Silence is allowed. Blaming a sensor for the missing channel is not."""
        report = self._report(seed)

        if report.finding is not None:
            assert report.finding.code == Code.MISSING_EXPORT, (
                f"seed={seed}: unmeasured export was attributed to "
                f"{report.finding.code} — {report.finding.headline}"
            )

    def test_it_is_usually_named(self) -> None:
        """The upstream band gate keeps some seeds quiet; most must not be."""
        found = sum(
            1
            for seed in range(12)
            if (f := self._report(seed).finding) is not None and f.code == Code.MISSING_EXPORT
        )

        assert found >= 8, f"only {found}/12 exporting houses were told why"

    def test_the_fix_is_the_one_the_user_can_act_on(self) -> None:
        report = self._report(0)

        assert report.finding is not None
        assert "export sensor" in report.finding.source_fix

    @pytest.mark.parametrize("noise_pct", [0.0, 0.03, 0.05])
    @pytest.mark.parametrize("seed", range(6))
    def test_a_house_that_does_not_export_is_silent(self, seed: int, noise_pct: float) -> None:
        """The discrimination is *when*, not *how much*.

        A house whose export channel is unmapped but which never exports has no
        unexplained energy, and must not be told it is exporting.
        """
        series = _self_consumed(house.build(days=30, seed=seed))
        series.assert_closes(tolerance=1e-6)
        if noise_pct:
            series = house.add_noise(series, noise_pct, seed=seed + 200)
        report = analyse(to_request(series, specs=specs_for(NO_EXPORT), declared=DECLARED))

        assert report.finding is None, (
            f"seed={seed} noise={noise_pct}: false positive {report.finding.code} "
            "on a house that exports nothing"
        )


class TestAConversionLossIsNotAnExportPath:
    """The false accusation this discriminator was rewritten to stop.

    A self-consumption house — surplus into the battery, nothing ever leaving,
    no export sensor because there is nothing to measure — with its generation
    metered before the inverter. The conversion loss sits above what
    ``DC_MEASUREMENT_WINDOW`` will absorb, so nothing is subtracted, and what is
    left is large, daily, one-signed and concentrated in the sunny hours.

    Which is what unmeasured export looks like. The engine said so, with
    ``Confidence.HIGH``, to a house measured to export exactly 0.0 Wh in a
    month: *"You appear to be exporting, but nothing measures it."*

    The hours that tell the two apart are the lit ones with no surplus. Nothing
    can leave in them, so export claims nothing; a loss proportional to
    generation is present in proportion to generation. Night is silent under
    both and is most of the deficit bucket, which is why averaging over all of
    it hid the difference.
    """

    @staticmethod
    def _report(series, seed_specs=NO_EXPORT):
        return analyse(to_request(series, specs=specs_for(seed_specs), declared=DECLARED))

    @pytest.mark.parametrize("efficiency", [0.90, 0.85, 0.80, 0.75])
    @pytest.mark.parametrize("seed", range(6))
    def test_a_house_that_never_exports_is_never_told_that_it_does(
        self, efficiency: float, seed: int
    ) -> None:
        series = house.measure_pv_dc(_self_consumed(house.build(days=30, seed=seed)), efficiency)

        report = self._report(series)

        assert report.finding is None, (
            f"eff={efficiency} seed={seed}: {report.finding.code} on a house that "
            f"exports nothing — {report.finding.headline}"
        )

    @pytest.mark.parametrize("efficiency", [1.0, 0.96, 0.90, 0.85, 0.80])
    @pytest.mark.parametrize("seed", range(6))
    def test_a_house_that_does_export_is_still_told_so(self, efficiency: float, seed: int) -> None:
        """The half that matters more, and the harder half.

        Both stories are true at once here: the export really is unmapped *and*
        the inverter really is metered on its DC side. A veto that could not
        tell the difference would take a correct, actionable finding away from
        every hybrid installation that happens to be missing an export sensor.
        """
        series = house.drop(house.build(days=30, seed=seed), "grid_export")
        if efficiency < 1.0:
            series = house.measure_pv_dc(series, efficiency)

        report = self._report(series)

        assert report.finding is not None, f"eff={efficiency} seed={seed}: went unnamed"
        assert report.finding.code == Code.MISSING_EXPORT

    def test_the_rate_is_what_separates_them_not_the_loudness(self) -> None:
        """A rented roof is loud in exactly those hours too, and must not be silenced.

        Its whole output leaves unmeasured, so the residual is generation-shaped
        there as well — at a coefficient of 1.00 against 0.15 to 0.25 for a
        conversion loss. Requiring only loudness would have silenced it, and
        silence hands the verdict to "generation is counted twice", whose remedy
        is to delete the one sensor telling the truth.
        """
        from analysis import hypotheses

        low, _high = hypotheses.DC_MEASUREMENT_WINDOW

        assert low <= 0.25 <= hypotheses.MAX_GENERATION_LOSS_COEFFICIENT, (
            "an inverter at 75% must still be recognised as a conversion loss"
        )
        assert hypotheses.MAX_GENERATION_LOSS_COEFFICIENT < 0.99, (
            "a roof exporting its entire output must not be mistaken for one"
        )


class TestTheCounterfactualMayNotOverruleAnUnmeasuredPath:
    """A rented roof, where the array serves almost none of the metered load.

    Generation goes to the grid, the load comes back from the grid, and the
    export meter is not mapped. Nothing is broken — the wiring is right and the
    configuration is incomplete, which is exactly what MISSING_EXPORT says.

    "Generation is counted twice" and "energy leaves by a path nothing measures"
    explain that residual equally well, and the first won by about 0.011 of
    explained fraction. Its remedy is to delete the generation channel. Follow
    it and the engine says OK, having talked the user into throwing away the one
    sensor that was telling the truth, while MISSING_EXPORT was passing every
    gate on its own.

    The counterfactual could only ever test one of the two: MISSING_EXPORT names
    no channel, so it can never appear among the channels that close the house.
    A test only one side can sit is not evidence about which side is right.
    """

    @staticmethod
    def _rented_roof(self_use: float, days: int = 30, seed: int = 0):
        clean = house.build(days=days, seed=seed)
        pv = list(clean.data["pv"])
        load = list(clean.data["load"])
        used = [min(p * self_use, drawn) for p, drawn in zip(pv, load, strict=True)]
        series = clean.copy_with(
            pv=pv,
            grid_export=[p - u for p, u in zip(pv, used, strict=True)],
            grid_import=[drawn - u for drawn, u in zip(load, used, strict=True)],
            battery_charge=[0.0] * clean.hours,
            battery_discharge=[0.0] * clean.hours,
            load=load,
        )
        specs = tuple(s for s in specs_for() if s.role is not Role.GRID_EXPORT)
        return to_request(
            series,
            specs=specs,
            declared=DeclaredTopology(
                has_battery=Answer.NO,
                grid_is_single_net_sensor=Answer.NO,
                load_covers_whole_house=Answer.YES,
            ),
        )

    @pytest.mark.parametrize("self_use", [0.0, 0.005, 0.01])
    def test_it_never_offers_to_delete_the_generation_channel(self, self_use: float) -> None:
        """The destructive outcome, pinned. This returned drop_channel on pv."""
        report = analyse(self._rented_roof(self_use))

        offered = report.finding.offered_correction if report.finding else None
        assert offered is None or offered.channel_key != "pv", (
            f"offered to delete the user's generation channel at {self_use:.1%} self-use"
        )

    @pytest.mark.parametrize("self_use", [0.0, 0.005, 0.01])
    def test_it_does_not_call_a_correctly_wired_roof_double_counted(self, self_use: float) -> None:
        report = analyse(self._rented_roof(self_use))

        assert report.finding is None or report.finding.code != Code.DOUBLE_COUNTED

    @pytest.mark.parametrize("seed", range(3))
    def test_it_holds_across_seeds(self, seed: int) -> None:
        report = analyse(self._rented_roof(0.0, seed=seed))

        assert report.finding is None or report.finding.code != Code.DOUBLE_COUNTED


class TestUnattributedIsStillAProblem:
    """ "We cannot say why" is not "there is nothing wrong"."""

    def test_a_proven_imbalance_sets_identity_fails(self) -> None:
        series = house.build(days=30, seed=0)
        report = analyse(to_request(series, specs=specs_for(NO_EXPORT), declared=DECLARED))

        assert report.identity_fails is True

    def test_a_clean_house_does_not(self) -> None:
        report = analyse(to_request(house.build(days=30, seed=3)))

        assert report.identity_fails is False

    def test_the_closure_caveat_reaches_the_reason(self) -> None:
        """An open boundary was computed, used, and then thrown away."""
        series = house.add_noise(house.build(days=10, seed=5), 0.03, seed=9)
        report = analyse(to_request(series, specs=specs_for(NO_EXPORT), declared=DECLARED))

        if report.status is Status.INVESTIGATING:
            assert "leaving the house" in report.reason


class TestLossModelIsIdempotent:
    """Fitting must converge, not alternate.

    The fit ran against the loss-corrected residual, so the second run estimated
    the loss that *remained* rather than the loss that was there — near zero.
    Carried forward as the next prior, that alternates forever and the reported
    status flips with it on every refresh.
    """

    def test_refitting_against_its_own_output_changes_nothing(self) -> None:
        specs = specs_for()
        series = house.measure_pv_dc(house.build(days=30, seed=2), efficiency=0.96)
        request = to_request(series, declared=DECLARED)

        base = LossModel()
        first = fit_loss_model(build_days(request.buckets, specs, base), specs, None)
        second = fit_loss_model(build_days(request.buckets, specs, first), specs, first)

        assert first.pv_dc_gamma == pytest.approx(second.pv_dc_gamma, abs=1e-9)
        assert first.battery_dc_gamma == pytest.approx(second.battery_dc_gamma, abs=1e-9)
        assert first.standby_w == pytest.approx(second.standby_w, abs=1e-9)

    def test_a_rejected_fit_is_not_reported_as_fitted(self) -> None:
        """All-zero terms used to be indistinguishable from a genuine result."""
        specs = specs_for()
        series = house.build(days=30, seed=4)
        request = to_request(series)
        model = fit_loss_model(build_days(request.buckets, specs, LossModel()), specs, None)

        if not model.fitted_terms:
            assert model.fitted is False
            assert model.established("battery_dc") is False


class TestSignedBatteryChannel:
    """A net battery sensor in a one-way slot, decided from ordinary hours."""

    @staticmethod
    def _net_in_charge_slot(seed: int):
        series = house.build(days=10, seed=seed)
        net = [
            series.data["battery_charge"][i] - series.data["battery_discharge"][i]
            for i in range(series.hours)
        ]
        return series.copy_with(battery_charge=net)

    @pytest.mark.parametrize("seed", range(5))
    def test_it_is_caught(self, seed: int) -> None:
        report = analyse(to_request(self._net_in_charge_slot(seed), declared=DECLARED))

        assert report.finding is not None, f"seed={seed}: signed battery channel not caught"
        assert report.finding.code == Code.SIGNED_NET_BATTERY

    def test_it_names_the_channel_and_offers_no_silent_override(self) -> None:
        report = analyse(to_request(self._net_in_charge_slot(0), declared=DECLARED))

        assert "Battery charging" in report.finding.headline
        # There is no net-battery slot to reinterpret into, and an internal
        # override would quietly discard half the channel.
        assert report.finding.offered_correction is None

    def test_the_grid_case_offers_no_override_either(self) -> None:
        """Both sides of the pair have to be unmapped by hand.

        This used to offer a "reinterpret_as_net" correction. Nothing
        implemented it: accepting it stored a correction, counted it in
        `corrections_active`, and changed not one number — the residual stayed
        exactly as it was and the same finding came back the next night.
        """
        series = house.net_meter_beside_export(house.build(days=10, seed=1))
        report = analyse(to_request(series, declared=DECLARED))

        assert report.finding.code == Code.SIGNED_NET_IN_DEDICATED
        assert report.finding.offered_correction is None

    def test_a_lone_net_meter_is_left_alone(self) -> None:
        series = house.merge_to_net(house.build(days=10, seed=1))
        report = analyse(to_request(series, declared=DECLARED))

        assert report.finding is None


class TestRolesNotScreenedForSign:
    """PV and load are deliberately excluded, and that has to stay deliberate."""

    def test_a_negative_generation_hour_is_not_a_screen_hit(self) -> None:
        series = house.build(days=10, seed=6)
        pv = list(series.data["pv"])
        for hour in range(0, len(pv), 24):
            pv[hour] = -40.0
        report = analyse(to_request(series.copy_with(pv=pv), declared=DECLARED))

        if report.finding is not None:
            assert report.finding.code != Code.SIGNED_NET_BATTERY
            assert report.finding.code != Code.SIGNED_NET_IN_DEDICATED


def test_every_magnitude_role_screened_has_copy() -> None:
    """A screen that fires with no template raises at render time, in CI."""
    from analysis.faults import render
    from analysis.screen import _MAGNITUDE_ROLES

    for role, code in _MAGNITUDE_ROLES.items():
        assert isinstance(role, Role)
        headline, detail, fix = render(code, name="Test sensor")
        assert "Test sensor" in headline or "Test sensor" in detail
        assert fix


class TestVerifiableHoursOnly:
    """When the open path cannot be closed, check the hours it cannot reach.

    A house with no export meter has no measurement that separates energy that
    left from energy a sensor over-reported — in a surplus hour those are the
    same number, and no amount of waiting produces a third one. Telling that
    user "still looking" is a promise that cannot be kept.

    The hours with no generation are ordinary arithmetic, and checking them is
    the difference between a verdict about part of the system and none at all.
    """

    @staticmethod
    def _report(series, seed_specs=NO_EXPORT):
        return analyse(to_request(series, specs=specs_for(seed_specs), declared=DECLARED))

    @pytest.mark.parametrize("seed", [1, 3])
    def test_the_missing_export_meter_is_named_rather_than_called_healthy(self, seed: int) -> None:
        """These two seeds used to report ``OK`` on a house a fifth out.

        The full-day residual is 20.60% and 20.77%. The restricted pass checked
        the night hours, found them clean, and returned ``OK`` with the whole
        daytime discrepancy demoted to a note — so the headline verdict on a
        house missing an entire channel was "no problem found", and the reason
        it never got further was a verdict window narrow enough that six
        agreeable days out of seven ended the analysis.

        The house is healthy. The *mapping* is not, and that is a thing its
        owner can fix, so it is now said in the finding rather than the
        footnote.
        """
        report = self._report(house.build(days=30, seed=seed))

        assert report.status is Status.FAULT_FOUND
        assert report.finding is not None
        assert report.finding.code == Code.MISSING_EXPORT

    def test_the_note_says_what_was_not_checked(self) -> None:
        """Read on a house that still takes the restricted path.

        A healthy no-export house now has its export named outright, so the
        restricted pass is reached by the houses it was written for: the ones
        where attribution over the full day comes back with nothing it can
        stand behind, and the verifiable hours are all that is left.
        """
        report = self._report(house.halve(house.build(days=30, seed=1), "load"))
        joined = " ".join(report.notes)

        assert "no generation" in joined
        assert "generation sensor is not covered" in joined

    def test_the_note_quantifies_the_unexplained_surplus(self) -> None:
        """A number the user can check against their own meter or bill."""
        report = self._report(house.halve(house.build(days=30, seed=1), "load"))

        assert any("kWh a day is unaccounted for" in note for note in report.notes)

    @pytest.mark.parametrize("seed", [1, 3])
    def test_a_fault_visible_at_night_is_still_found(self, seed: int) -> None:
        """The point of the exercise: half a system checked beats none."""
        series = house.halve(house.build(days=30, seed=seed), "load")
        report = self._report(series)

        assert report.finding is not None
        assert report.finding.code == Code.PARTIAL_COVERAGE
        assert report.notes

    @pytest.mark.parametrize("noise_pct", [0.0, 0.03, 0.05])
    @pytest.mark.parametrize("seed", range(6))
    def test_a_healthy_house_is_never_blamed(self, seed: int, noise_pct: float) -> None:
        """The restricted pass must not become a new false-positive surface."""
        series = house.build(days=30, seed=seed)
        if noise_pct:
            series = house.add_noise(series, noise_pct, seed=seed + 300)
        report = self._report(series)

        assert report.finding is None or report.finding.code == Code.MISSING_EXPORT, (
            f"seed={seed} noise={noise_pct}: {report.finding.code} on a healthy house"
        )

    def test_a_closed_boundary_gets_no_notes(self) -> None:
        """Nothing is restricted when everything is measured."""
        report = analyse(to_request(house.build(days=30, seed=1), declared=DECLARED))

        assert report.notes == ()


class TestLocalDayGrouping:
    """A resolved date on the bucket beats an offset applied to the window.

    ``build_days`` used to add one flat offset — captured once, from whatever the
    zone happened to be that afternoon — to every hour in a thirty-day window.
    Twice a year that window contains a transition, and on the wrong side of it
    every hour near local midnight lands on the neighbouring day.
    """

    @staticmethod
    def _with_dates(request, mapper):
        from analysis.model import AnalysisRequest, Bucket

        buckets = tuple(
            Bucket(
                start_utc=b.start_utc,
                seconds=b.seconds,
                wh=b.wh,
                quality=b.quality,
                source=b.source,
                solar_elevation_deg=b.solar_elevation_deg,
                is_dst_transition=mapper(b)[1],
                local_date=mapper(b)[0],
            )
            for b in request.buckets
        )
        return AnalysisRequest(
            now_utc=request.now_utc,
            specs=request.specs,
            buckets=buckets,
            declared=request.declared,
            loss_model=request.loss_model,
        )

    def test_the_resolved_date_wins_over_the_offset(self) -> None:
        """Every bucket labelled one day must group into exactly one day."""
        from datetime import date

        from analysis.residual import build_days

        request = to_request(house.build(days=4, seed=1))
        forced = date(2001, 1, 1)
        stamped = self._with_dates(request, lambda b: (forced, False))
        days = build_days(stamped.buckets, stamped.specs, LossModel(), utc_offset_hours=11.0)

        assert [d.day for d in days] == [forced], "the flat offset still decided the grouping"

    def test_transition_days_are_dropped(self) -> None:
        """The guard has always existed; nothing ever set the flag."""
        from analysis.residual import build_days

        request = to_request(house.build(days=4, seed=1))
        stamped = self._with_dates(
            request, lambda b: (b.start_utc.date(), b.start_utc.day % 2 == 0)
        )
        days = build_days(stamped.buckets, stamped.specs, LossModel())

        assert days, "everything was dropped"
        assert all(d.day.day % 2 == 1 for d in days), "a transition day survived"

    def test_the_offset_still_applies_when_no_date_is_resolved(self) -> None:
        """Synthetic input carries no zone, and must keep working."""
        from analysis.residual import build_days

        request = to_request(house.build(days=4, seed=1))
        without = build_days(request.buckets, request.specs, LossModel())

        assert len(without) >= 3
