"""The storage version is a promise to every installation already in the field.

``Store`` calls ``_async_migrate_func`` whenever the stored version differs from
the one asked for, and its default implementation raises. So moving either
constant is not a bookkeeping change — it is a statement that every file written
by every previous release can still be read, and something has to be true for
that statement to hold.

This runs in the pure suite, without Home Assistant, so it fails in the pull
request that moves the number rather than three minutes later in CI. Its
companion in ``tests/integration/test_storage_round_trip.py`` proves the
behaviour; this one proves somebody thought about it.
"""

from __future__ import annotations

from const import STORAGE_MINOR_VERSION, STORAGE_VERSION

#: Every ``(version, minor_version)`` pair this integration has ever written,
#: oldest first. Append to it in the same commit that moves the constants, and
#: add a fixture at the old pair to
#: ``tests/integration/test_storage_round_trip.py`` proving it still loads.
#:
#: The list is what makes a bump deliberate. Without it the constants are two
#: numbers nobody has a reason to look at, and the first person to increment one
#: finds out what it costs from a user rather than from a test.
HISTORICAL_STORAGE_VERSIONS: tuple[tuple[int, int], ...] = ((1, 1),)


def test_the_current_version_is_the_most_recent_one_recorded() -> None:
    """Moving the constants without recording the old pair fails here.

    The failure is the point: it asks the author to write down what the previous
    shape was, which is the only way the round-trip test can go on proving that
    a file in that shape still loads.
    """
    assert HISTORICAL_STORAGE_VERSIONS[-1] == (STORAGE_VERSION, STORAGE_MINOR_VERSION), (
        "the storage version moved without being recorded — append the previous "
        "pair to HISTORICAL_STORAGE_VERSIONS and add a fixture at that pair to "
        "tests/integration/test_storage_round_trip.py"
    )


def test_the_recorded_history_only_ever_moves_forward() -> None:
    """A list that is not sorted is a list somebody edited carelessly."""
    assert list(HISTORICAL_STORAGE_VERSIONS) == sorted(HISTORICAL_STORAGE_VERSIONS)
    assert len(set(HISTORICAL_STORAGE_VERSIONS)) == len(HISTORICAL_STORAGE_VERSIONS)


def test_a_major_bump_requires_somewhere_for_a_migration_to_live() -> None:
    """Below version 2 there is nothing to migrate *from*, so nothing to write.

    At version 2 there is, and the default ``_async_migrate_func`` raises. This
    is here so that the person who bumps it is told, rather than shipping a
    release that refuses to start for everybody who upgrades.
    """
    if STORAGE_VERSION == 1:
        return

    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "custom_components"
        / "solar_sanity"
        / "coordinator.py"
    ).read_text(encoding="utf-8")

    assert "_async_migrate_func" in source, (
        f"STORAGE_VERSION is {STORAGE_VERSION}, so files written at earlier "
        f"versions exist and Store's default migration raises on them — the "
        f"coordinator needs a Store subclass defining _async_migrate_func"
    )
