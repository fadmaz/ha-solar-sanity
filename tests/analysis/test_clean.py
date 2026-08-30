"""S1 — the clean suite. A CI gate, not a test.

A healthy system must produce **silence**. Every scenario here asserts no
finding, and there is no tolerance for exceptions: a single false "your sensor
is broken" on a working installation loses that user permanently and gets
written up in a forum thread that outlives the fix.

This suite must be green before any threshold anywhere is changed. If a
threshold tweak makes a real fault detectable but breaks one clean scenario, the
tweak is wrong.
"""

from __future__ import annotations

import pytest
from analysis.engine import analyse
from analysis.model import Role, Status

from tests.synth import house
from tests.synth.adapt import extra_spec, specs_for, to_request

#: Statuses that count as "said nothing alarming".
QUIET = {Status.OK, Status.INSUFFICIENT_DATA, Status.INVESTIGATING, Status.NOT_CHECKABLE}


def _assert_quiet(report, label: str) -> None:
    """Silence, and the absence of an accusation made by other means.

    ``identity_fails`` is a separate channel from the finding: it is what the
    card renders as a data-health problem, and it can be set on a report whose
    ``finding`` is ``None``. A gate that only looked at the finding could
    therefore watch a healthy house be marked as failing its own arithmetic and
    call that silence. Every change to the loss model and the verdict window
    pushes healthy houses along exactly that axis, so it is checked here.
    """
    assert report.finding is None, (
        f"{label}: expected silence on a healthy system, got "
        f"{report.finding.code} — {report.finding.headline}"
    )
    assert report.status in QUIET, f"{label}: unexpected status {report.status}"
    assert report.identity_fails is False, (
        f"{label}: healthy house marked as failing its own identity, status {report.status}"
    )


@pytest.mark.parametrize("seed", range(25))
def test_clean_house_is_silent(seed: int) -> None:
    """The base case, across many different weather and usage patterns."""
    series = house.build(days=30, seed=seed)
    report = analyse(to_request(series))
    _assert_quiet(report, f"clean seed={seed}")


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("noise_pct", [0.0, 0.02, 0.05])
def test_clean_house_with_meter_noise_is_silent(seed: int, noise_pct: float) -> None:
    """Real meters disagree by a few percent. That is not a fault.

    Five percent per channel in quadrature over four channels is roughly the
    worst a correctly-installed system produces, and it must stay under the
    threshold.
    """
    series = house.add_noise(house.build(days=30, seed=seed), noise_pct, seed=seed + 100)
    report = analyse(to_request(series))
    _assert_quiet(report, f"noise={noise_pct} seed={seed}")


@pytest.mark.parametrize("seed", range(8))
def test_dc_measured_pv_is_not_a_fault(seed: int) -> None:
    """Generation measured before the inverter is a topology fact.

    It produces a persistent positive residual of a few percent, which is
    exactly what the loss model exists to absorb.
    """
    series = house.measure_pv_dc(house.build(days=30, seed=seed), efficiency=0.96)
    report = analyse(to_request(series))
    _assert_quiet(report, f"dc pv seed={seed}")


@pytest.mark.parametrize("seed", range(8))
def test_dc_measured_battery_is_not_a_fault(seed: int) -> None:
    """Round-trip loss on a DC-side battery, likewise."""
    series = house.measure_battery_dc(house.build(days=30, seed=seed), efficiency=0.95)
    report = analyse(to_request(series))
    _assert_quiet(report, f"dc battery seed={seed}")


@pytest.mark.parametrize("seed", range(8))
def test_unmetered_standby_is_not_a_fault(seed: int) -> None:
    """An inverter's own power supply draws 15-60 W around the clock."""
    series = house.add_standby(house.build(days=30, seed=seed), watts=25.0)
    report = analyse(to_request(series))
    _assert_quiet(report, f"standby seed={seed}")


@pytest.mark.parametrize("seed", range(6))
def test_all_losses_combined_is_not_a_fault(seed: int) -> None:
    """The realistic DC-coupled hybrid: every loss at once, plus meter noise.

    This is the author's own topology and the one shipped first, so it is the
    single most important row in this suite.
    """
    series = house.build(days=30, seed=seed)
    series = house.measure_pv_dc(series, efficiency=0.96)
    series = house.measure_battery_dc(series, efficiency=0.95)
    series = house.add_standby(series, watts=25.0)
    series = house.add_noise(series, 0.02, seed=seed + 200)
    report = analyse(to_request(series))
    _assert_quiet(report, f"combined seed={seed}")


@pytest.mark.parametrize("seed", range(6))
def test_two_real_arrays_are_not_a_duplicate(seed: int) -> None:
    """Two arrays on different aspects correlate, but are genuinely separate.

    Adversarial: this looks superficially like the duplicate-channel signature.

    This used to call ``house.split_arrays``, which replaced the generation
    curve with a smoothed copy of itself and adjusted nothing else — leaving up
    to 1,121 Wh an hour unaccounted for, and never creating the second channel
    it claimed to. So six scenarios asserted silence about a house that cannot
    exist, using a detector that had nothing to pair. ``two_aspects`` splits the
    array into two halves that sum to the original hour for hour, and both are
    mapped, which is the thing the test says it is testing.
    """
    series = house.two_aspects(house.build(days=30, seed=seed), "pv", "pv_west", tilt=0.4)
    report = analyse(
        to_request(series, specs=(*specs_for(), extra_spec("pv_west", Role.PV, "Solar west")))
    )
    _assert_quiet(report, f"two arrays seed={seed}")


@pytest.mark.parametrize("seed", range(6))
def test_low_sun_month_never_asserts_a_fault(seed: int) -> None:
    """December: 2 kWh a day of throughput.

    Tiny absolute residuals become huge percentages here, which is exactly what
    the absolute floors exist to prevent.
    """
    series = house.build(days=30, seed=seed, kwp=0.6)
    report = analyse(to_request(series))
    _assert_quiet(report, f"low sun seed={seed}")


def test_short_history_never_asserts_a_fault() -> None:
    """Under a week of data, the only honest answer is that we do not know."""
    for days in (1, 2, 3, 4):
        series = house.build(days=days, seed=1)
        report = analyse(to_request(series))
        assert report.status is Status.INSUFFICIENT_DATA
        assert report.finding is None
