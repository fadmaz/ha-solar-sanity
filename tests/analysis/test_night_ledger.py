"""The night identity, one channel at a time.

The reference installation has run for a month at "still looking" with a night
residual of about -326 Wh an hour and nothing able to explain it. The night fit
reported medians — middle-hour load, middle-hour discharge, middle-hour residual
— and medians do not compose: they are three different hours, so no arithmetic
over them says whether the channels reconcile.

Totals over one agreed set of hours do reconcile, exactly. That turns "the
numbers do not add up at night" from a summary into a subtraction anyone can
check a line at a time, and the size of each line says which channel is short.

The reconciliation is an identity rather than a cross-check: the residual is
what the lines sum to under the sign convention, by construction. Saying so
matters, because the first version of this file published that sum twice under
two names and described the second as verifying the first.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from analysis.model import Answer, BucketSource, DeclaredTopology, LossModel, Role
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


def _raw(series) -> dict[str, float]:
    """The measurements the engine would publish for this house."""
    specs = specs_for()
    request = to_request(series, specs=specs, declared=DECLARED)
    provisional = build_days(request.buckets, specs, LossModel(), request.utc_offset_hours)
    loss = fit_loss_model(provisional, specs, None)
    return night_fit_raw(build_days(request.buckets, specs, loss, request.utc_offset_hours), specs)


class TestItReconciles:
    """The property the whole thing rests on."""

    @pytest.mark.parametrize("seed", range(4))
    def test_a_healthy_night_adds_up_to_nothing(self, seed: int) -> None:
        ledger = _ledger(house.build(days=DAYS, seed=seed))

        assert ledger["night_total_residual_wh"] == pytest.approx(0.0, abs=1.0)

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
    def test_the_totals_sum_to_the_residual(self, name: str, corrupt) -> None:
        """The property the ledger is for: the reported residual is exactly what
        the reported lines add up to under the sign convention, so a reader can
        do the subtraction by hand and land on the same number.

        This is an identity, not a cross-check. An earlier version of this test
        compared the sum against a separately published key and called them
        "two different computations" — they were the same sum reassociated, and
        the difference was exactly zero on every input. A test that cannot fail
        is worse than no test, because it is counted as coverage.
        """
        ledger = _ledger(corrupt(house.build(days=DAYS, seed=0)))
        lines = {k: v for k, v in ledger.items() if k.startswith("night_total_")}
        del lines["night_total_residual_wh"]
        by_hand = sum(
            next(r.sign for r in Role if r.key == key[len("night_total_") : -len("_wh")]) * value
            for key, value in lines.items()
        )

        assert by_hand == pytest.approx(ledger["night_total_residual_wh"], abs=0.01), name


class TestItNamesTheShortChannel:
    def test_an_injected_draw_comes_back_exactly(self) -> None:
        """80 W across every night hour, recovered to the watt-hour. This is the
        measurement that would tell the reference installation whether its
        missing energy is a continuous draw or a mis-reading channel."""
        ledger = _ledger(house.add_standby(house.build(days=DAYS, seed=0), 80.0))
        hours = ledger["night_ledger_hours"]

        assert ledger["night_total_residual_wh"] == pytest.approx(80.0 * hours, rel=1e-6)

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

    def test_the_count_equals_the_fits_own_and_that_is_not_a_signal(self) -> None:
        """Pinned as equality on purpose.

        `build_days` has already discarded any bucket missing a balance channel,
        so by the time an hour reaches the ledger every channel has reported and
        the two counts cannot diverge. This was once asserted as `<=` and
        documented as a coverage signal — an inequality that passes only by
        equality, describing a diagnostic that cannot fire. If a change to
        `bucket_is_valid` ever makes them differ, this fails and the docstring
        gets rewritten rather than quietly becoming true again.
        """
        series = house.build(days=DAYS, seed=0)
        specs = specs_for()
        request = to_request(series, specs=specs, declared=DECLARED)
        provisional = build_days(request.buckets, specs, LossModel(), request.utc_offset_hours)
        loss = fit_loss_model(provisional, specs, None)
        days = build_days(request.buckets, specs, loss, request.utc_offset_hours)

        raw = night_fit_raw(days, specs)

        assert raw["night_ledger_hours"] == raw["night_hours"]

    def test_daylight_is_not_counted(self) -> None:
        """Generation is zero across every hour the ledger used, or the totals
        describe something other than night."""
        ledger = _ledger(house.build(days=DAYS, seed=0))

        assert ledger["night_total_pv_wh"] == pytest.approx(0.0, abs=1.0)


class TestItReachesTheHousesThatNeedItMost:
    """The ledger and the night fit answer different questions, and the fit's
    preconditions were gating both.

    `_night_samples` wants a discharge channel and two hundred night hours. So a
    house with no battery got nothing at all, and every house got nothing for
    its first sixteen days — which is exactly the window where somebody is
    looking at "still looking" and wanting to know why. The ledger needs neither
    of those things to total up what each channel reported.
    """

    @staticmethod
    def _raw(series, specs, declared):
        request = to_request(series, specs=specs, declared=declared)
        provisional = build_days(request.buckets, specs, LossModel(), request.utc_offset_hours)
        loss = fit_loss_model(provisional, specs, None)
        days = build_days(request.buckets, specs, loss, request.utc_offset_hours)
        return night_fit_raw(days, specs)

    @staticmethod
    def _no_battery(days: int, seed: int = 0):
        clean = house.build(days=days, seed=seed)
        series = clean.copy_with(
            battery_charge=[0.0] * clean.hours,
            battery_discharge=[0.0] * clean.hours,
            load=[
                consumed + out - into
                for consumed, out, into in zip(
                    clean.data["load"],
                    clean.data["battery_discharge"],
                    clean.data["battery_charge"],
                    strict=True,
                )
            ],
        )
        specs = tuple(
            s for s in specs_for() if s.role not in (Role.BATTERY_CHARGE, Role.BATTERY_DISCHARGE)
        )
        return series, specs

    def test_a_house_with_no_battery_still_gets_a_ledger(self) -> None:
        series, specs = self._no_battery(30)

        raw = self._raw(
            series,
            specs,
            DeclaredTopology(
                has_battery=Answer.NO,
                grid_is_single_net_sensor=Answer.NO,
                load_covers_whole_house=Answer.YES,
            ),
        )

        assert raw["night_ledger_hours"] > 0
        # The fit genuinely cannot run here, and does not pretend to.
        assert "night_hours" not in raw

    @pytest.mark.parametrize("days", [7, 10, 14])
    def test_it_is_there_before_the_fit_can_speak(self, days: int) -> None:
        raw = self._raw(house.build(days=days, seed=0), specs_for(), DECLARED)

        assert raw["night_ledger_hours"] > 0
        assert "night_hours" not in raw
        assert "night_total_load_wh" in raw

    def test_the_fit_still_arrives_once_it_has_enough(self) -> None:
        """So moving the ledger out did not move the fit out with it."""
        raw = self._raw(house.build(days=21, seed=0), specs_for(), DECLARED)

        assert "night_hours" in raw
        assert raw["night_ledger_hours"] == raw["night_hours"]


class TestTheSplitSaysWhenTheGapHappens:
    """A whole-night total says how much is missing. It cannot say when.

    On the reference installation the night was short by 298 W on average. In
    the hours the battery ran the house alone — generation, import and charging
    all exactly zero — the arithmetic closed to within 6% of discharge, which is
    what a DC-measured battery feeding an AC load should look like. The deficit
    was entirely in the hours the grid was involved, and that points at one
    channel instead of three.

    Finding that took diffing two diagnostics downloads by hand across a
    two-hour window, which is not a thing the product should require.
    """

    @staticmethod
    def _split(series):
        raw = _raw(series)
        quiet, active = raw.get("night_grid_quiet_hours"), raw.get("night_grid_active_hours")
        if not quiet or not active:
            return None
        return (
            raw["night_grid_quiet_residual_wh"] / quiet,
            raw["night_grid_active_residual_wh"] / active,
        )

    def test_a_clean_house_is_quiet_in_both_halves(self) -> None:
        quiet, active = self._split(house.build(days=DAYS, seed=0))

        assert abs(quiet) < 15.0
        assert abs(active) < 15.0

    def test_an_under_reading_grid_meter_shows_only_in_grid_hours(self) -> None:
        """The signature the reference installation matches."""
        quiet, active = self._split(house.scale(house.build(days=DAYS, seed=0), "grid_import", 0.6))

        assert abs(quiet) < 15.0, "the grid was not involved in these hours"
        assert active < -100.0

    def test_an_under_reading_battery_shows_where_the_battery_works_hardest(self) -> None:
        """A different shape, and the reason the split is worth having: this is
        what the reference installation would look like if discharge were the
        culprit, and it does not."""
        quiet, active = self._split(
            house.scale(house.build(days=DAYS, seed=0), "battery_discharge", 0.8)
        )

        assert quiet < -100.0
        assert quiet < active

    def test_an_over_reading_load_shows_in_both_halves(self) -> None:
        """Consumption is drawn in every hour, so it cannot hide in half of them."""
        quiet, active = self._split(house.scale(house.build(days=DAYS, seed=0), "load", 1.3))

        assert quiet < -100.0
        assert active < -100.0

    def test_the_halves_add_up_to_the_whole(self) -> None:
        """They are computed by the same path over disjoint hours, so anything
        else would mean the split is counting different columns."""
        raw = _raw(house.scale(house.build(days=DAYS, seed=0), "grid_import", 0.6))

        assert (
            raw["night_grid_quiet_hours"] + raw["night_grid_active_hours"]
            == raw["night_ledger_hours"]
        )
        assert raw["night_grid_quiet_residual_wh"] + raw[
            "night_grid_active_residual_wh"
        ] == pytest.approx(raw["night_total_residual_wh"], abs=0.01)
        for role in ("load", "grid_import", "battery_discharge", "battery_charge", "pv"):
            halves = raw[f"night_grid_quiet_{role}_wh"] + raw[f"night_grid_active_{role}_wh"]
            assert halves == pytest.approx(raw[f"night_total_{role}_wh"], abs=0.01), role

    def test_a_grid_that_never_rests_gets_no_split(self) -> None:
        """One half would be the whole night and the other empty, and
        republishing the same totals under a second name is how a reader comes
        to believe two numbers agreed when only one was computed."""
        clean = house.build(days=DAYS, seed=0)
        always = clean.copy_with(
            grid_import=[v + 500.0 for v in clean.data["grid_import"]],
            load=[v + 500.0 for v in clean.data["load"]],
        )
        raw = _raw(always)

        assert "night_grid_quiet_hours" not in raw
        assert "night_grid_active_hours" not in raw
        assert raw["night_ledger_hours"] > 0


class TestItTellsBlindApartFromMisScaled:
    """The one question a total cannot answer.

    A month whose night is short by 500 W looks identical whether every hour is
    short by 500 W or a fifth of the hours are short by everything — and those
    have different causes and different fixes. Scaling a channel cannot rescue
    an hour where every source reads zero, because there is nothing to multiply.
    So an hour drawing real power with nothing measured supplying it is proof
    that something stopped reporting rather than that something reports the
    wrong amount, and the absence of such hours is equally strong the other way.
    """

    @staticmethod
    def _blind(series, every: int = 6):
        """Every Nth night hour the supply sensors report a hard zero while the
        house keeps drawing. Zero, not absent — an absent channel is dropped by
        `build_days` long before it reaches here, which is the whole reason a
        sensor that lies about being idle is worth detecting separately."""
        imports = list(series.data["grid_import"])
        discharge = list(series.data["battery_discharge"])
        pv = series.data["pv"]
        seen = 0
        for index in range(len(imports)):
            if pv[index] <= 0.0 and (imports[index] > 0 or discharge[index] > 0):
                seen += 1
                if seen % every == 0:
                    imports[index] = 0.0
                    discharge[index] = 0.0
        return series.copy_with(grid_import=imports, battery_discharge=discharge)

    def test_a_healthy_house_has_none(self) -> None:
        assert _raw(house.build(days=DAYS, seed=0))["night_hours_with_no_supply"] == 0.0

    @pytest.mark.parametrize(
        "name,corrupt",
        [
            ("battery out reads half", lambda c: house.scale(c, "battery_discharge", 0.5)),
            ("consumption reads double", lambda c: house.scale(c, "load", 2.0)),
            ("battery out reads a third", lambda c: house.scale(c, "battery_discharge", 0.33)),
        ],
    )
    def test_a_mis_scaled_channel_produces_none_however_large_the_gap(
        self, name: str, corrupt
    ) -> None:
        """This is the point. These have residuals of hundreds of watts and
        still no hour without a supply, because a wrong number is still a
        number."""
        raw = _raw(corrupt(house.build(days=DAYS, seed=0)))

        assert raw["night_total_residual_wh"] < -10_000.0, f"{name}: nothing to detect"
        assert raw["night_hours_with_no_supply"] == 0.0, name

    def test_a_sensor_going_blind_is_counted(self) -> None:
        raw = _raw(self._blind(house.build(days=DAYS, seed=0)))

        assert raw["night_hours_with_no_supply"] > 0.0
        assert raw["night_unsupplied_draw_wh"] > 0.0

    def test_the_draw_reported_is_the_draw_in_those_hours(self) -> None:
        """So the figure can be turned into a rate rather than only a count."""
        raw = _raw(self._blind(house.build(days=DAYS, seed=0)))
        per_hour = raw["night_unsupplied_draw_wh"] / raw["night_hours_with_no_supply"]

        assert 100.0 < per_hour < 5000.0

    @pytest.mark.parametrize("every", [4, 8, 12])
    def test_more_blind_hours_are_counted_as_more(self, every: int) -> None:
        raw = _raw(self._blind(house.build(days=DAYS, seed=0), every=every))
        expected = _raw(self._blind(house.build(days=DAYS, seed=0), every=every * 2))

        assert raw["night_hours_with_no_supply"] > expected["night_hours_with_no_supply"]

    def test_the_key_is_always_present_so_zero_means_zero(self) -> None:
        """An absent key and a zero are different facts, and the reader of a
        diagnostics file cannot tell which they are looking at unless the key is
        always there."""
        assert "night_hours_with_no_supply" in _raw(house.build(days=DAYS, seed=0))


class TestItSeparatesWhatWeMeasuredFromWhatWeWereTold:
    """The control the ledger did not have.

    An hourly arithmetic mean over a sensor that reports on change over-weights
    the busy part of the hour, so a power channel read that way sits high while
    an energy counter beside it is exact. That is a night which does not add up
    with nothing whatever wrong — and in a total it is indistinguishable from a
    sensor that genuinely under-reports.

    Our own integration weights every reading by how long it stood, so it is the
    control. If the deficit lives in the hours taken from statistics and the
    hours we integrated ourselves close, the estimator is the fault and the
    house is fine.
    """

    @staticmethod
    def _sourced(request, own_from=None, inflate: float = 1.0):
        """Older hours from statistics, recent ones our own — and optionally the
        statistics-derived POWER channels inflated, which is what the arithmetic
        mean does to an event-reporting sensor."""
        power = {"pv", "load", "grid_import"}
        buckets = []
        for bucket in request.buckets:
            own = own_from is not None and bucket.start_utc.date() >= own_from
            source = {
                key: (
                    BucketSource.OWN_INTEGRAL
                    if own
                    else (BucketSource.LTS_MEAN if key in power else BucketSource.LTS_SUM)
                )
                for key in bucket.source
            }
            wh = dict(bucket.wh)
            if not own and inflate != 1.0:
                for key in power:
                    if wh.get(key) is not None:
                        wh[key] = wh[key] * inflate
            buckets.append(replace(bucket, source=source, wh=wh))
        return replace(request, buckets=tuple(buckets))

    @staticmethod
    def _measure(request):
        specs = specs_for()
        provisional = build_days(request.buckets, specs, LossModel(), request.utc_offset_hours)
        loss = fit_loss_model(provisional, specs, None)
        return night_fit_raw(
            build_days(request.buckets, specs, loss, request.utc_offset_hours), specs
        )

    def _request(self):
        return to_request(house.build(days=DAYS, seed=0), specs=specs_for(), declared=DECLARED)

    def test_a_biased_estimator_is_confined_to_the_hours_it_touched(self) -> None:
        request = self._request()
        cut = sorted({b.start_utc.date() for b in request.buckets})[-5]

        raw = self._measure(self._sourced(request, own_from=cut, inflate=1.8))

        measured = raw["night_measured_residual_wh"] / raw["night_measured_hours"]
        told = raw["night_from_statistics_residual_wh"] / raw["night_from_statistics_hours"]
        assert abs(measured) < 15.0, "the hours we integrated ourselves should close"
        assert told < -100.0, "the hours we were told should carry the whole deficit"

    def test_a_real_fault_shows_in_both_kinds(self) -> None:
        """So the split cannot be read as exonerating a house that is genuinely
        wrong — a mis-scaled sensor is mis-scaled whoever did the arithmetic."""
        request = to_request(
            house.scale(house.build(days=DAYS, seed=0), "battery_discharge", 0.5),
            specs=specs_for(),
            declared=DECLARED,
        )
        cut = sorted({b.start_utc.date() for b in request.buckets})[-5]

        raw = self._measure(self._sourced(request, own_from=cut))

        assert raw["night_measured_residual_wh"] / raw["night_measured_hours"] < -100.0
        assert (
            raw["night_from_statistics_residual_wh"] / raw["night_from_statistics_hours"] < -100.0
        )

    def test_the_halves_add_up_to_the_whole(self) -> None:
        request = self._request()
        cut = sorted({b.start_utc.date() for b in request.buckets})[-5]

        raw = self._measure(self._sourced(request, own_from=cut, inflate=1.8))

        assert (
            raw["night_measured_hours"] + raw["night_from_statistics_hours"]
            == raw["night_ledger_hours"]
        )
        assert raw["night_measured_residual_wh"] + raw[
            "night_from_statistics_residual_wh"
        ] == pytest.approx(raw["night_total_residual_wh"], abs=0.01)

    def test_a_fresh_installation_gets_no_split(self) -> None:
        """Everything is backfilled on day one, and one half being the whole
        night republishes it under a second name."""
        raw = self._measure(self._sourced(self._request(), own_from=None))

        assert "night_measured_hours" not in raw
        assert "night_from_statistics_hours" not in raw
        assert raw["night_ledger_hours"] > 0


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
