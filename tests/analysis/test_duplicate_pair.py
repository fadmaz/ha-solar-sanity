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


def _counterfactuals(request) -> tuple[object, int]:
    """Analyse, and count how many counterfactuals were actually run.

    Every assertion about the detector *declining* has to be paired with this.
    The first version of the adversarial tests below asserted silence on houses
    that balance — and a house that balances returns at ``_would_be_ok`` long
    before the detector is reached, so they ran zero counterfactuals and proved
    nothing about it. Replacing the whole detector with the naive
    correlation-and-ratio test this design exists to avoid still passed 88 of
    those 89 tests.
    """
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


class TestTwoRealChannelsAreNeverAPair:
    """The adversary, put in front of the detector rather than beside it.

    Two real strings of equal size correlate at 1.0000 with a ratio of exactly
    one — the same numbers a duplicated sensor gives. Asserting silence is only
    worth something if the detector actually looked, so each of these puts the
    house genuinely out by some *other* means and then checks that the pair is
    still not blamed for it.
    """

    @staticmethod
    def _out_of_balance(series: house.Series) -> house.Series:
        """Something is wrong, and it is not the pair.

        Unmetered standby, because no Stage-A screen catches it — a screen hit
        would return before attribution and put the detector out of reach again.
        """
        return house.add_standby(series, 400.0)

    @pytest.mark.parametrize("tilt", [0.0, 0.2, 0.4, 0.6, 0.8])
    @pytest.mark.parametrize("seed", range(3))
    def test_two_aspects_of_one_array(self, tilt: float, seed: int) -> None:
        """``tilt=0.0`` is the hard one: two halves with byte-identical curves,
        correlating at 1.0000 with a ratio of exactly one."""
        series = self._out_of_balance(
            house.two_aspects(house.build(days=DAYS, seed=seed), "pv", "pv_west", tilt=tilt)
        )

        report, ran = _counterfactuals(
            to_request(
                series,
                specs=(*specs_for(), extra_spec("pv_west", Role.PV, "Solar west")),
                declared=DECLARED,
            )
        )

        assert ran > 0, "the detector was never reached — this proves nothing"
        assert report.finding is None or report.finding.code != Code.DUPLICATE_CHANNEL, (
            f"tilt={tilt} seed={seed}: two real arrays were called a duplicate"
        )

    @pytest.mark.parametrize("share", [0.5, 0.6, 0.75, 0.9])
    def test_two_strings_of_different_sizes_on_one_aspect(self, share: float) -> None:
        clean = house.build(days=DAYS, seed=1)
        pv = clean.data["pv"]
        series = self._out_of_balance(
            clean.copy_with(pv=[v * share for v in pv], pv_b=[v * (1.0 - share) for v in pv])
        )

        report, ran = _counterfactuals(
            to_request(
                series,
                specs=(*specs_for(), extra_spec("pv_b", Role.PV, "Solar B")),
                declared=DECLARED,
            )
        )

        assert ran > 0, "the detector was never reached — this proves nothing"
        assert report.finding is None or report.finding.code != Code.DUPLICATE_CHANNEL

    @pytest.mark.parametrize("tilt", [0.0, 0.4])
    def test_neither_real_string_closes_the_house_on_its_own(self, tilt: float) -> None:
        """The discriminator itself, asserted directly rather than through a
        verdict that several other gates also influence."""
        from analysis import engine

        series = self._out_of_balance(
            house.two_aspects(house.build(days=DAYS, seed=0), "pv", "pv_west", tilt=tilt)
        )
        specs = (*specs_for(), extra_spec("pv_west", Role.PV, "Solar west"))
        request = to_request(series, specs=specs, declared=DECLARED)
        buckets = engine._apply_corrections(request.buckets, request.active_corrections)

        for key in ("pv", "pv_west"):
            closes, _ = engine._closes_without(request, specs, buckets, key)
            assert not closes, f"dropping the real array {key} was thought to settle the house"

    def test_a_balanced_house_never_gets_that_far_anyway(self) -> None:
        """Belt and braces, and the reason the cost is acceptable: on a house
        that adds up, none of the above is even asked."""
        series = house.two_aspects(house.build(days=DAYS, seed=0), "pv", "pv_west")

        report, ran = _counterfactuals(
            to_request(
                series,
                specs=(*specs_for(), extra_spec("pv_west", Role.PV, "Solar west")),
                declared=DECLARED,
            )
        )

        assert report.status is Status.OK
        assert ran == 0

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
            if spec.role.in_balance and engine._closes_without(request, specs, buckets, spec.key)[0]
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


class TestTheBandWhereNothingSpeaks:
    """A known gap, pinned so it cannot widen unnoticed.

    A copy that disagrees with its partner by 5 to 10 per cent is close enough that both
    DOUBLE_COUNTED hypotheses score within 0.01 of each other, and the margin
    gate wants 0.15. The correct hypothesis — the copy, correctly identified —
    is top of the list and rejected anyway.

    Only one channel closes the house here, so this is not the pair finding: the
    engine knows which one to blame and still says nothing. Fixing it means
    letting the counterfactual break the tie the margin gate cannot, in the
    attribution path rather than this one.

    The test asserts today's behaviour rather than the behaviour we want, which
    is unusual and deliberate: it fails the moment either edge of the band
    moves, in either direction.
    """

    @staticmethod
    def _closing(scale: float) -> tuple[list[str], object]:
        from analysis import engine

        clean = house.build(days=DAYS, seed=0)
        series = clean.copy_with(pv_b=[v * scale for v in clean.data["pv"]])
        specs = (*specs_for(), extra_spec("pv_b", Role.PV, "Solar B"))
        request = to_request(series, specs=specs, declared=DECLARED)
        buckets = engine._apply_corrections(request.buckets, request.active_corrections)
        closing = [
            key for key in ("pv", "pv_b") if engine._closes_without(request, specs, buckets, key)[0]
        ]
        return closing, engine.analyse(request)

    @pytest.mark.parametrize("scale", [1.00, 0.98, 0.96])
    def test_a_close_copy_is_still_a_pair(self, scale: float) -> None:
        closing, report = self._closing(scale)

        assert closing == ["pv", "pv_b"]
        assert report.finding.code == Code.DUPLICATE_CHANNEL

    @pytest.mark.parametrize("scale", [0.88, 0.85, 0.80])
    def test_a_distant_copy_is_named_on_its_own(self, scale: float) -> None:
        closing, report = self._closing(scale)

        assert closing == ["pv_b"]
        assert report.finding.code == Code.DOUBLE_COUNTED

    @pytest.mark.parametrize("scale", [0.94, 0.92, 0.90])
    def test_and_in_between_nothing_is_said(self, scale: float) -> None:
        """The gap. One channel is singled out by the counterfactual and the
        margin gate rejects the hypothesis that says so."""
        closing, report = self._closing(scale)

        assert closing == ["pv_b"], "the counterfactual knows which one it is"
        assert report.finding is None, (
            f"scale={scale} now names something — good news, and this test and "
            f"the comment in _duplicate_pair both need updating"
        )


class TestTheCounterfactualIsNotEnoughOnItsOwn:
    """The false positive an adversarial sweep found, at one house in sixty.

    Dropping a channel and watching the balance close is a *magnitude* test. It
    asks whether the house is out by about one of these, not whether the missing
    energy *is* one of these — and two real strings both answer yes the moment
    an unrelated fault happens to be roughly their size. The engine then told
    somebody that half their real generation was a duplicate, and to unmap it.

    Correlation does not help: two strings on one roof correlate at 1.00 by
    construction. What separates them is whether the unexplained energy *is*
    that channel, hour for hour — 0.000 to 0.056 for a duplicated sensor even
    with ten per cent of independent error on both devices, and 0.459 to 0.543
    for two real strings beside an unmetered draw.
    """

    @pytest.mark.parametrize("watts", [150.0, 300.0, 400.0, 600.0, 900.0])
    @pytest.mark.parametrize("kwp", [3.0, 6.0, 9.0])
    @pytest.mark.parametrize("seed", range(4))
    def test_two_real_strings_beside_an_unmetered_draw(
        self, seed: int, kwp: float, watts: float
    ) -> None:
        """seed=1, kwp=3.0, 300 W was the one that fired."""
        series = house.add_standby(
            house.two_aspects(
                house.build(days=DAYS, seed=seed, kwp=kwp), "pv", "pv_west", tilt=0.0
            ),
            watts,
        )

        report = _analyse(series, extra_spec("pv_west", Role.PV, "Solar west"))

        assert report.finding is None or report.finding.code != Code.DUPLICATE_CHANNEL, (
            f"seed={seed} kwp={kwp} standby={watts}: half a real array was called a duplicate"
        )

    def test_enough_of_that_grid_actually_reaches_the_detector(self) -> None:
        """Otherwise the sweep above is sixty assertions about an early return.

        A small draw on a large array leaves the house clean, and a clean house
        returns before attribution — so reachability is a property of the grid
        rather than of each cell, and it is checked here once.
        """
        reached = 0
        for seed in range(4):
            for kwp in (3.0, 6.0, 9.0):
                for watts in (150.0, 300.0, 400.0, 600.0, 900.0):
                    series = house.add_standby(
                        house.two_aspects(
                            house.build(days=DAYS, seed=seed, kwp=kwp),
                            "pv",
                            "pv_west",
                            tilt=0.0,
                        ),
                        watts,
                    )
                    _, ran = _counterfactuals(
                        to_request(
                            series,
                            specs=(
                                *specs_for(),
                                extra_spec("pv_west", Role.PV, "Solar west"),
                            ),
                            declared=DECLARED,
                        )
                    )
                    reached += ran > 0

        assert reached >= 40, f"only {reached} of 60 reached the detector"

    def test_the_counterfactual_alone_would_have_fired(self) -> None:
        """Pins the mechanism, so nobody removes the identity test believing the
        counterfactual already covered this."""
        from analysis import engine

        series = house.add_standby(
            house.two_aspects(house.build(days=DAYS, seed=1, kwp=3.0), "pv", "pv_west", tilt=0.0),
            300.0,
        )
        specs = (*specs_for(), extra_spec("pv_west", Role.PV, "Solar west"))
        request = to_request(series, specs=specs, declared=DECLARED)
        buckets = engine._apply_corrections(request.buckets, request.active_corrections)

        closing = [
            key
            for key in ("pv", "pv_west")
            if engine._closes_without(request, specs, buckets, key)[0]
        ]

        assert closing == ["pv", "pv_west"], (
            "both real strings still look interchangeable — the identity test is "
            "the only thing standing between this house and a wrong answer"
        )


class TestAFigureWeCannotComputeIsNeverPrinted:
    """``{correlation:.0%}`` of a NaN renders as ``nan%``.

    Pearson returns NaN for non-finite input, and NaN slips through an ordinary
    ``< threshold`` guard because every comparison against NaN is false. The
    project already treats non-finite readings as a threat worth testing, so
    this was reachable rather than theoretical.
    """

    def test_a_non_finite_reading_does_not_reach_the_copy(self) -> None:
        from dataclasses import replace as _replace

        clean = house.build(days=DAYS, seed=0)
        series = clean.copy_with(pv_b=list(clean.data["pv"]))
        request = to_request(
            series,
            specs=(*specs_for(), extra_spec("pv_b", Role.PV, "Solar B")),
            declared=DECLARED,
        )
        buckets = list(request.buckets)
        poisoned = buckets[60]
        buckets[60] = _replace(poisoned, wh={**poisoned.wh, "pv_b": float("inf")})

        report = analyse(_replace(request, buckets=tuple(buckets)))

        if report.finding is not None:
            assert "nan" not in report.finding.detail.lower()
            assert "inf" not in report.finding.detail.lower()

    def test_the_guard_is_written_so_that_nan_fails_it(self) -> None:
        """A NaN must be rejected, not accepted by default."""
        from analysis.engine import TRACKING_MIN_CORRELATION

        nan = float("nan")

        assert not nan < TRACKING_MIN_CORRELATION, "the naive guard lets NaN past"
        assert not nan >= TRACKING_MIN_CORRELATION, "the guard used must reject it"


class TestTheReportSaysWhatItKnows:
    """A finding that reports nothing about itself is hard to trust or check.

    The first version set none of these. Diagnostics recorded a fault that
    explained 0% of the mismatch over 0 days with no evidence behind it, on a
    house it had just proved was 38% out — and the report said the identity
    held, which is the one thing it demonstrably does not.
    """

    @staticmethod
    def _report():
        clean = house.build(days=DAYS, seed=0)
        return _analyse(
            clean.copy_with(pv_b=list(clean.data["pv"])),
            extra_spec("pv_b", Role.PV, "Solar B"),
        )

    def test_the_identity_is_reported_as_failing(self) -> None:
        """Every other fault says so. This one is 38% out and said otherwise."""
        assert self._report().identity_fails is True

    def test_it_reports_how_much_of_the_mismatch_it_accounts_for(self) -> None:
        report = self._report()

        assert report.finding.explained_fraction > 0.9
        assert report.finding.explained_fraction <= 1.0

    def test_it_reports_the_window_it_was_judged_over(self) -> None:
        """The counterfactual runs over the whole window rather than sampling,
        so every day supports it — but zero was never the honest answer."""
        report = self._report()

        assert report.finding.days_evaluated == DAYS
        assert report.finding.days_supporting == DAYS

    def test_it_carries_numbers_a_user_can_check(self) -> None:
        evidence = self._report().finding.evidence

        assert evidence, "a finding the reader has to take on trust"
        assert all(e.window_days == DAYS for e in evidence)
        assert all(e.unit for e in evidence)

    def test_the_explained_share_is_measured_and_not_assumed(self) -> None:
        """A near-copy accounts for slightly less, and should say so."""
        clean = house.build(days=DAYS, seed=0)
        close = _analyse(
            clean.copy_with(pv_b=[v * 0.96 for v in clean.data["pv"]]),
            extra_spec("pv_b", Role.PV, "Solar B"),
        )
        exact = self._report()

        assert close.finding.explained_fraction < exact.finding.explained_fraction
