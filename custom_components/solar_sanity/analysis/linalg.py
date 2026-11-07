"""Small numeric helpers. Standard library only — no numpy, no scipy.

The outlook and the scoring work on at most a few thousand values (30 days x 24 buckets x
a handful of channels), so hand-rolled implementations are fast enough and keep
the integration installable on a Raspberry Pi with zero wheels to build.

Nothing here uses randomness or the clock; see the determinism invariant test.
"""

from __future__ import annotations

from collections.abc import Sequence


def median(values: Sequence[float]) -> float | None:
    """Median, or ``None`` for an empty sequence.

    Used everywhere in preference to the mean: one freak cloudburst must not
    move a headline.
    """
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Linear-interpolated percentile. ``pct`` is 0-100."""
    if not values:
        return None
    if pct <= 0:
        return min(values)
    if pct >= 100:
        return max(values)
    ordered = sorted(values)
    pos = (len(ordered) - 1) * (pct / 100.0)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def iqr(values: Sequence[float]) -> float | None:
    """Interquartile range — our measure of spread, and of consistency."""
    if len(values) < 2:
        return None
    q1 = percentile(values, 25)
    q3 = percentile(values, 75)
    if q1 is None or q3 is None:
        return None
    return q3 - q1


def mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation, or ``None`` if undefined (constant input)."""
    n = min(len(xs), len(ys))
    if n < 3:
        return None
    mx = mean(xs[:n])
    my = mean(ys[:n])
    if mx is None or my is None:
        return None
    num = 0.0
    dx2 = 0.0
    dy2 = 0.0
    for i in range(n):
        dx = xs[i] - mx
        dy = ys[i] - my
        num += dx * dy
        dx2 += dx * dx
        dy2 += dy * dy
    if dx2 <= 1e-12 or dy2 <= 1e-12:
        return None
    return num / ((dx2 * dy2) ** 0.5)


def safe_ratio(numerator: float, denominator: float) -> float | None:
    """Divide, or return ``None`` when the denominator is negligible.

    Deliberately returns ``None`` rather than 0.0 or infinity — a ratio we
    cannot compute is not a ratio of zero.
    """
    if abs(denominator) < 1e-9:
        return None
    return numerator / denominator
