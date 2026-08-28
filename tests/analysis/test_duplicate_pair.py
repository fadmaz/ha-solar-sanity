"""One flow counted twice, named as a pair rather than guessed at.

Two channels carrying the same energy each look entirely spurious on their own,
so both snap to DOUBLE_COUNTED with identical evidence, the margin between first
and second place is nothing, and the margin gate rejects them both. The engine
said nothing at all about a house whose numbers were out by a third. That is the
right instinct — it genuinely cannot tell which of the two to blame — expressed
as the wrong answer, because the answer is the pair.

``Code.DUPLICATE_CHANNEL`` was written for exactly this, with finished copy, and
was emitted by nothing.

## Why the test is a counterfactual and not a resemblance

Measured on the synthetic house, over twenty-one days:

    case                            pearson   ratio med   ratio IQR   residual
    duplicate, identical            1.0000      1.000       0.000       38.4%
    duplicate, DC vs AC sensor      1.0000      0.960       0.000       37.4%
    duplicate, +/-3% device noise   0.9970      0.965       0.053       38.4%
    two real strings, equal         1.0000      1.000       0.000        0.0%
    two real strings, 60/40         1.0000      0.667       0.000        0.0%
    east + west, 1h apart           0.8350      1.002       0.660        0.0%

Two real strings of equal size on one roof are identical to a duplicated sensor
on every statistic of the channels themselves — to four decimal places. No
threshold on correlation or ratio can separate those two rows. The only column
that differs is the last one, and it differs completely.

So the question asked is the one that has an answer: would dropping this channel,
on its own, settle the whole installation? Drop one of two real strings and half
the generation goes missing. Drop one of two sensors watching the same string and
the balance closes. When that is true of two channels neither can be singled out,
and the pair is the finding.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from analysis.engine import analyse
from analysis.faults import Code
from analysis.model import Answer, DeclaredTopology, Role, Status

from tests.synth import house
from tests.synth.adapt import extra_spec, specs_for, to_request

DECLARED = DeclaredTopology(
    has_battery=Answer.YES,
    grid_is_single_net_sensor=Answer.NO,
    load_covers_whole_house=Answer.YES,
)

DAYS = 21

#: The roles where duplicating the channel puts the house far enough out for the
#: engine to go looking for a cause at all. Grid import is the exception and has
#: its own test below — on this house it is small enough that duplicating it
#: leaves the residual at 8%, which does not clear the existing bar for
#: attribution. That bar is not this feature's business.
DUPLICATED_ROLES = [
    ("pv", Role.PV),
    ("load", Role.LOAD),
    ("grid_export", Role.GRID_EXPORT),
    ("battery_charge", Role.BATTERY_CHARGE),
    ("battery_discharge", Role.BATTERY_DISCHARGE),
]


def _analyse(series: house.Series, *extra):
    return analyse(to_request(series, specs=specs_for() + tuple(extra), declared=DECLARED))


def _duplicated(source: str, role: Role, *, seed: int = 0, scale: float = 1.0):
    """A second sensor watching a flow that is already measured."""
    clean = house.build(days=DAYS, seed=seed)
    copy = [value * scale for value in clean.data[source]]
    series = clean.copy_with(**{f"{source}_b": copy})
    return series, extra_spec(f"{source}_b", role, "Second sensor")


class TestAPairIsNamedRatherThanGuessedAt:
    @pytest.mark.parametrize(("source", "role"), DUPLICATED_ROLES)
    def test_every_role_a_duplicate_can_land_in(self, source: str, role: Role) -> None:
        report = _analyse(*_duplicated(source, role))

        assert report.finding is not None, f"{source}: a duplicated channel went unnamed"
        assert report.finding.code == Code.DUPLICATE_CHANNEL
        assert report.status is Status.FAULT_FOUND

    def test_a_duplicate_too_small_to_be_actionable_is_left_alone(self) -> None:
        """Not a gap in this detector — the bar in front of it.

        Duplicating grid import on this house leaves an 8% residual with only
        two actionable days in the last seven, and the engine declines to
        attribute a cause until five of the last seven are actionable. A
        duplicate that never pushes the house past that bar stays unreported,
        which is the same answer it would give for any other fault of that size.
        """
        report = _analyse(*_duplicated("grid_import", Role.GRID_IMPORT))

        assert report.status is Status.INVESTIGATING
        assert report.finding is None

    def test_both_channels_are_named(self) -> None:
        series, spec = _duplicated("pv", Role.PV)

        report = _analyse(series, spec)

        assert set(report.finding.channel_keys) == {"pv", "pv_b"}
        assert "Solar production" in report.finding.headline
        assert "Second sensor" in report.finding.headline

    def test_a_dc_sensor_beside_an_ac_one_is_still_the_same_flow(self) -> None:
        """The realistic shape: one reads before the inverter, one after, so the
        pair differs by conversion efficiency rather than being identical."""
        report = _analyse(*_duplicated("pv", Role.PV, scale=0.96))

        assert report.finding.code == Code.DUPLICATE_CHANNEL

    def test_the_same_flow_under_two_different_roles(self) -> None:
        """Nothing requires the pair to share a role. Generation also mapped as
        battery discharge is one flow counted twice just as much."""
        clean = house.build(days=DAYS, seed=0)
        series = clean.copy_with(shadow=list(clean.data["pv"]))

        report = _analyse(series, extra_spec("shadow", Role.BATTERY_DISCHARGE, "Shadow"))

        assert report.finding.code == Code.DUPLICATE_CHANNEL
        assert set(report.finding.channel_keys) == {"pv", "shadow"}

    def test_no_correction_is_offered(self) -> None:
        """Dropping either would close the balance, so an override here would be
        the engine picking which of the user's sensors to silence on a coin toss.
        """
        report = _analyse(*_duplicated("pv", Role.PV))

        assert report.finding.offered_correction is None
        assert report.finding.source_fix, "a fault with nothing to do about it"

    def test_the_tracking_figure_is_real_and_rendered(self) -> None:
        report = _analyse(*_duplicated("pv", Role.PV, scale=0.96))

        # The copy quotes it as a percentage, so it has to be a fraction.
        assert "%" in report.finding.detail
        assert "{correlation" not in report.finding.detail


class TestTwoRealChannelsAreNeverAPair:
    """The adversary, and the reason resemblance was not enough.

    Every case here has two channels that a correlation-and-ratio test would
    accept, and a balance that closes. Being silent on them is the whole product.
    """

    @pytest.mark.parametrize("tilt", [0.0, 0.2, 0.4, 0.6, 0.8])
    @pytest.mark.parametrize("seed", range(3))
    def test_two_aspects_of_one_array(self, tilt: float, seed: int) -> None:
        """``tilt=0.0`` is the hard one: two halves with byte-identical curves,
        correlating at 1.0000 with a ratio of exactly one — the same numbers a
        duplicated sensor produces."""
        series = house.two_aspects(house.build(days=DAYS, seed=seed), "pv", "pv_west", tilt=tilt)

        report = _analyse(series, extra_spec("pv_west", Role.PV, "Solar west"))

        assert report.finding is None, (
            f"tilt={tilt} seed={seed}: two real arrays were called a duplicate — {report.finding}"
        )

    @pytest.mark.parametrize("share", [0.5, 0.6, 0.75, 0.9])
    def test_two_strings_of_different_sizes_on_one_aspect(self, share: float) -> None:
        clean = house.build(days=DAYS, seed=1)
        pv = clean.data["pv"]
        series = clean.copy_with(pv=[v * share for v in pv], pv_b=[v * (1.0 - share) for v in pv])

        report = _analyse(series, extra_spec("pv_b", Role.PV, "Solar B"))

        assert report.finding is None, f"share={share}: {report.finding}"

    def test_the_house_still_balances_in_every_adversarial_fixture(self) -> None:
        """Otherwise the silence above would prove nothing — a fixture that does
        not balance is silent for the wrong reason."""
        series = house.two_aspects(house.build(days=DAYS, seed=0), "pv", "pv_west")

        original = house.build(days=DAYS, seed=0).data["pv"]
        worst = max(
            abs(series.data["pv"][i] + series.data["pv_west"][i] - original[i])
            for i in range(series.hours)
        )

        assert worst < 1e-6, f"the split arrays do not sum to the original: {worst} Wh"


class TestItOnlySpeaksWhenThePairIsTheWholeStory:
    def test_three_copies_of_one_flow_stay_silent(self) -> None:
        """Not merely unhandled — unreachable, and correctly so.

        With three channels carrying one flow, dropping any single one still
        leaves the house out by a third, so no *pair* is the answer. Once the
        user removes one, two remain and this speaks.
        """
        clean = house.build(days=DAYS, seed=0)
        pv = list(clean.data["pv"])
        series = clean.copy_with(pv_b=list(pv), pv_c=list(pv))

        report = _analyse(
            series,
            extra_spec("pv_b", Role.PV, "Solar B"),
            extra_spec("pv_c", Role.PV, "Solar C"),
        )

        assert report.finding is None or report.finding.code != Code.DUPLICATE_CHANNEL

    def test_removing_one_of_three_leaves_a_pair_that_is_named(self) -> None:
        clean = house.build(days=DAYS, seed=0)
        series = clean.copy_with(pv_b=list(clean.data["pv"]))

        report = _analyse(series, extra_spec("pv_b", Role.PV, "Solar B"))

        assert report.finding.code == Code.DUPLICATE_CHANNEL

    def test_a_duplicate_beside_an_unrelated_fault_is_not_a_pair(self) -> None:
        """Self-gating. If anything else is also wrong, dropping one channel does
        not leave a clean house, and this says nothing — the other fault gets
        reported on its own terms.
        """
        clean = house.build(days=DAYS, seed=0)
        series = house.freeze(clean.copy_with(pv_b=list(clean.data["pv"])), "load", from_hour=200)

        report = _analyse(series, extra_spec("pv_b", Role.PV, "Solar B"))

        assert report.finding is not None
        assert report.finding.code == Code.STUCK

    def test_dropping_either_one_settles_the_house(self) -> None:
        """The claim the finding rests on, checked directly."""
        clean = house.build(days=DAYS, seed=0)
        series = clean.copy_with(pv_b=list(clean.data["pv"]))
        spec = extra_spec("pv_b", Role.PV, "Solar B")

        assert _analyse(series, spec).finding.code == Code.DUPLICATE_CHANNEL
        for dropped in ("pv", "pv_b"):
            settled = series.copy_with(**{dropped: [0.0] * series.hours})
            assert _analyse(settled, spec).status is Status.OK, (
                f"dropping {dropped} was supposed to settle the house"
            )


class TestDeterminism:
    def test_the_order_the_channels_are_configured_in_does_not_matter(self) -> None:
        """The pair is symmetric, so which name leads must come from the data
        and not from the order the user happened to map them in."""
        clean = house.build(days=DAYS, seed=0)
        series = clean.copy_with(pv_b=list(clean.data["pv"]))
        spec = extra_spec("pv_b", Role.PV, "Solar B")

        ordered = (*specs_for(), spec)
        forward = analyse(to_request(series, specs=ordered, declared=DECLARED))
        reversed_specs = tuple(reversed(ordered))
        backward = analyse(to_request(series, specs=reversed_specs, declared=DECLARED))

        assert forward.finding.headline == backward.finding.headline
        assert forward.finding.channel_keys == backward.finding.channel_keys

    def test_the_same_house_twice_gives_the_same_report(self) -> None:
        clean = house.build(days=DAYS, seed=0)
        series = clean.copy_with(pv_b=list(clean.data["pv"]))
        spec = extra_spec("pv_b", Role.PV, "Solar B")

        assert _analyse(series, spec) == _analyse(series, spec)


class TestItStaysWithinBudget:
    """The counterfactual is the most expensive thing the engine does.

    Deciding this by physics rather than by resemblance costs a loss-model refit
    per channel — about 160 ms for a thirty-day house with seven channels in the
    balance, against the project's 500 ms budget. A deliberate trade: the
    alternative was a correlation threshold, and no threshold on correlation or
    ratio can separate a duplicated sensor from two real strings of equal size
    (see this module's docstring).

    Both tests here count *work*, not milliseconds. A wall-clock assertion was
    written first and thrown away: under coverage instrumentation the same call
    takes 2072 ms rather than 160, so the test measured the runner rather than
    the code and would have failed in CI for no reason anybody could act on.
    Counting refits is deterministic, and it catches the regression that
    actually matters — running this somewhere it does not belong.
    """

    @staticmethod
    def _counting_analyse(request):
        """Analyse, and report how many counterfactuals it took."""
        from analysis import engine

        calls = 0
        original = engine._closes_without

        def counting(*args, **kwargs):
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)

        engine._closes_without = counting
        try:
            return engine.analyse(request), calls
        finally:
            engine._closes_without = original

    def test_a_healthy_house_never_pays_for_it(self) -> None:
        """The guard that matters most.

        A clean house returns before attribution is reached, so it runs not one
        counterfactual. If that ever stops being true, every ordinary
        installation starts paying this on every run — which is the whole reason
        the cost is acceptable.
        """
        _, calls = self._counting_analyse(
            to_request(house.build(days=30, seed=0), declared=DECLARED)
        )

        assert calls == 0, f"a clean house ran {calls} counterfactuals"

    def test_a_fault_caught_earlier_never_pays_for_it_either(self) -> None:
        """Screens return at Stage A, long before attribution."""
        _, calls = self._counting_analyse(
            to_request(
                house.scale(house.build(days=30, seed=0), "grid_import", 0.001), declared=DECLARED
            )
        )

        assert calls == 0, f"a screened fault ran {calls} counterfactuals"

    def test_a_dismissed_finding_costs_nothing_to_not_report(self) -> None:
        """Suppression is checked before the counterfactuals, not after.

        A user who has dismissed this has also declined to pay for the seven
        loss-model refits behind it, every night, for as long as it stays
        dismissed.
        """
        clean = house.build(days=30, seed=0)
        request = to_request(
            clean.copy_with(pv_b=list(clean.data["pv"])),
            specs=(*specs_for(), extra_spec("pv_b", Role.PV, "Solar B")),
            declared=DECLARED,
        )

        report, calls = self._counting_analyse(
            replace(request, suppressed_codes=(Code.DUPLICATE_CHANNEL,))
        )

        assert calls == 0
        assert report.finding is None or report.finding.code != Code.DUPLICATE_CHANNEL

    def test_the_worst_path_is_bounded_by_the_channel_count(self) -> None:
        """One refit per channel in the balance, and not one more.

        The bound is what stops this becoming quadratic if somebody later
        compares pairs rather than channels.
        """
        clean = house.build(days=30, seed=0)
        series = clean.copy_with(pv_b=list(clean.data["pv"]))
        specs = (*specs_for(), extra_spec("pv_b", Role.PV, "Solar B"))
        in_balance = sum(1 for spec in specs if spec.role.in_balance)

        report, calls = self._counting_analyse(to_request(series, specs=specs, declared=DECLARED))

        assert report.finding.code == Code.DUPLICATE_CHANNEL
        assert calls <= in_balance, f"{calls} refits for {in_balance} channels"


class TestNoOtherFaultIsMistakenForAPair:
    """The guarantee the counterfactual rests on, swept rather than argued.

    The worry worth having about this design is the loss-model refit inside
    ``_closes_without``. It has free parameters — DC-side gammas and a standby
    term — and if dropping almost any channel let the refit absorb whatever was
    left, two channels would "close" the house by accident and a healthy pair
    would be accused.

    They do not. Across every corruption in the taxonomy, on four seeds, not one
    channel closes the house when dropped. A duplicate is the only shape that
    produces the signature, which is what makes naming the pair honest.
    """

    FAULTS = [
        ("halve load", lambda c: house.halve(c, "load")),
        ("halve pv", lambda c: house.halve(c, "pv")),
        ("invert discharge", lambda c: house.invert(c, "battery_discharge")),
        ("import as kW", lambda c: house.scale(c, "grid_import", 1000.0)),
        ("export unmeasured", lambda c: house.drop(c, "grid_export")),
        ("frozen load", lambda c: house.freeze(c, "load", 200)),
        ("unmetered standby", lambda c: house.add_standby(c, 300.0)),
        ("pv measured DC-side", house.measure_pv_dc),
        ("five per cent meter noise", lambda c: house.add_noise(c, 0.05, seed=3)),
    ]

    @pytest.mark.parametrize(("name", "corrupt"), FAULTS, ids=[f[0] for f in FAULTS])
    @pytest.mark.parametrize("seed", range(4))
    def test_no_channel_closes_the_house_on_its_own(self, name, corrupt, seed) -> None:
        from analysis import engine

        specs = specs_for()
        request = to_request(corrupt(house.build(days=DAYS, seed=seed)), declared=DECLARED)

        buckets = engine._apply_corrections(request.buckets, request.active_corrections)
        closing = [
            spec.key
            for spec in specs
            if spec.role.in_balance and engine._closes_without(request, specs, buckets, spec.key)
        ]

        assert not closing, f"{name} seed={seed}: {closing} looked interchangeable"

    @pytest.mark.parametrize(("name", "corrupt"), FAULTS, ids=[f[0] for f in FAULTS])
    def test_and_so_none_of_them_is_reported_as_a_duplicate(self, name, corrupt) -> None:
        report = _analyse(corrupt(house.build(days=DAYS, seed=0)))

        if report.finding is not None:
            assert report.finding.code != Code.DUPLICATE_CHANNEL, name

    @pytest.mark.parametrize("seed", range(3))
    def test_a_dark_december_cannot_manufacture_a_pair(self, seed: int) -> None:
        """Two kilowatt-hours a day, where tiny absolute residuals become huge
        percentages and the absolute floors are the only thing holding."""
        clean = house.build(days=DAYS, seed=seed, kwp=0.6)
        series = clean.copy_with(
            pv_b=[v * 0.5 for v in clean.data["pv"]], pv=[v * 0.5 for v in clean.data["pv"]]
        )

        report = _analyse(series, extra_spec("pv_b", Role.PV, "Solar B"))

        if report.finding is not None:
            assert report.finding.code != Code.DUPLICATE_CHANNEL, f"seed={seed}"
