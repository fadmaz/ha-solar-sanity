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
