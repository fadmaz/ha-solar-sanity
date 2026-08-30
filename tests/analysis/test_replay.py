"""A real installation's diagnostics must replay to the same verdict.

The engine is a pure function of its buckets, so a diagnostics file carrying
them is a reproducible test case. That is the point of the whole exercise: three
diagnoses of one installation were made confidently and retracted, because every
question had to be settled by asking its owner to run something and wait a day.

If the round trip is lossy, a replay is not evidence — it is a second opinion
about slightly different data, which is worse than none.
"""

from __future__ import annotations

import pytest
from analysis.engine import analyse
from analysis.model import Answer, BucketSource, DeclaredTopology, Quality

from tests.synth import house, replay
from tests.synth.adapt import specs_for, to_request

DECLARED = DeclaredTopology(
    has_battery=Answer.YES,
    grid_is_single_net_sensor=Answer.NO,
    load_covers_whole_house=Answer.YES,
)

CASES = {
    "clean": lambda c: c,
    "halved load": lambda c: house.halve(c, "load"),
    "halved generation": lambda c: house.halve(c, "pv"),
    "inverted discharge": lambda c: house.invert(c, "battery_discharge"),
    "import in kilowatts": lambda c: house.scale(c, "grid_import", 1000.0),
    "five percent noise": lambda c: house.add_noise(c, 0.05, seed=9),
    "standby draw": lambda c: house.add_standby(c, 60.0),
    "generation measured dc": house.measure_pv_dc,
}


def _round_trip(request):
    return analyse(replay.request_from(replay.payload_from(request)))


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_verdict_survives_the_round_trip(name: str) -> None:
    request = to_request(
        CASES[name](house.build(days=30, seed=0)), specs=specs_for(), declared=DECLARED
    )

    before, after = analyse(request), _round_trip(request)

    assert after.status == before.status
    assert after.identity_fails == before.identity_fails
    assert (after.finding.code if after.finding else None) == (
        before.finding.code if before.finding else None
    )


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_measured_figure_survives_the_round_trip(name: str) -> None:
    """Not just the verdict — the numbers under it.

    Rounding the exported values to milliwatt-hours was enough to move a
    replayed residual from 5e-16 to 3e-06. Harmless in itself, and precisely the
    drift that stops a replay being evidence, so the export carries full floats
    and this asserts it.
    """
    request = to_request(
        CASES[name](house.build(days=30, seed=0)), specs=specs_for(), declared=DECLARED
    )

    before, after = analyse(request), _round_trip(request)

    assert after.residual == before.residual
    assert after.measurements == before.measurements
    assert after.loss_model == before.loss_model
    assert after.notes == before.notes


class TestTheFormatCarriesWhatTheEngineReads:
    def test_a_hole_stays_a_hole(self) -> None:
        """`None` must not come back as zero. It is the distinction the whole
        engine is built around."""
        request = to_request(
            house.build(days=30, seed=0),
            specs=specs_for(),
            declared=DECLARED,
            missing={"pv": {100, 101, 102}},
        )

        restored = replay.request_from(replay.payload_from(request))
        holed = [b for b in restored.buckets if b.wh["pv"] is None]

        assert len(holed) == 3
        assert all(b.quality["pv"] is Quality.MISSING for b in holed)

    def test_provenance_survives(self) -> None:
        """A mean-derived hour restored as our own measurement would launder the
        one distinction that decides whether the band is 10% or 16%."""
        request = to_request(
            house.build(days=30, seed=0),
            specs=specs_for(),
            declared=DECLARED,
            source=BucketSource.LTS_MEAN,
        )

        restored = replay.request_from(replay.payload_from(request))

        assert all(
            source is BucketSource.LTS_MEAN
            for bucket in restored.buckets
            for source in bucket.source.values()
        )

    def test_the_declaration_survives(self) -> None:
        """`load_covers_whole_house` is about to acquire its first reader, so a
        replay that silently defaulted it would answer a different question."""
        declared = DeclaredTopology(
            has_battery=Answer.NO,
            grid_is_single_net_sensor=Answer.YES,
            load_covers_whole_house=Answer.NO,
        )
        request = to_request(house.build(days=5, seed=0), specs=specs_for(), declared=declared)

        assert replay.request_from(replay.payload_from(request)).declared == declared

    def test_a_partial_hour_is_still_partial(self) -> None:
        """`build_days` drops anything not exactly 3600 s, so the seconds column
        is load-bearing rather than decorative."""
        from dataclasses import replace as dataclass_replace

        request = to_request(house.build(days=5, seed=0), specs=specs_for(), declared=DECLARED)
        buckets = list(request.buckets)
        buckets[0] = dataclass_replace(buckets[0], seconds=1800)
        request = dataclass_replace(request, buckets=tuple(buckets))

        restored = replay.request_from(replay.payload_from(request))

        assert restored.buckets[0].seconds == 1800

    def test_a_channel_the_house_does_not_have_is_absent(self) -> None:
        keys = ("pv", "grid_import", "battery_charge", "battery_discharge", "load")
        request = to_request(
            house.drop(house.build(days=5, seed=0), "grid_export"),
            specs=specs_for(keys),
            declared=DECLARED,
        )

        restored = replay.request_from(replay.payload_from(request))

        assert tuple(s.key for s in restored.specs) == keys
        assert "grid_export" not in restored.buckets[0].wh


class TestItRefusesRatherThanGuesses:
    def test_a_file_without_a_window_says_so(self) -> None:
        with pytest.raises(ValueError, match="no window block"):
            replay.request_from({"data": {"window": None}})

    def test_an_unknown_quality_code_raises(self) -> None:
        """Silently mapping an unrecognised code to OK would produce a confident
        wrong answer, which is the failure this module exists to prevent."""
        with pytest.raises(KeyError):
            replay.buckets_from(
                {
                    "keys": ["pv"],
                    "rows": [["2026-08-01T00:00:00+00:00", 3600, [1.0], "Z", "i"]],
                }
            )

    def test_a_row_whose_codes_do_not_match_its_channels_raises(self) -> None:
        with pytest.raises(ValueError, match="codes"):
            replay.buckets_from(
                {
                    "keys": ["pv", "load"],
                    "rows": [["2026-08-01T00:00:00+00:00", 3600, [1.0, 2.0], "O", "ii"]],
                }
            )
