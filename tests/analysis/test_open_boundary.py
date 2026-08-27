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

    def test_the_grid_case_still_offers_its_correction(self) -> None:
        series = house.merge_to_net(house.build(days=10, seed=1))
        report = analyse(to_request(series, declared=DECLARED))

        assert report.finding.code == Code.SIGNED_NET_IN_DEDICATED
        assert report.finding.offered_correction is not None


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
    def test_a_healthy_house_gets_a_verdict_instead_of_a_wait(self, seed: int) -> None:
        report = self._report(house.build(days=30, seed=seed))

        assert report.status is Status.OK
        assert report.notes, "an OK covering only half the hours must say so"

    def test_the_note_says_what_was_not_checked(self) -> None:
        report = self._report(house.build(days=30, seed=1))
        joined = " ".join(report.notes)

        assert "no generation" in joined
        assert "generation sensor is not covered" in joined

    def test_the_note_quantifies_the_unexplained_surplus(self) -> None:
        """A number the user can check against their own meter or bill."""
        report = self._report(house.build(days=30, seed=1))

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
