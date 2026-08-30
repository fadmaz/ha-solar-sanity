"""Small numeric helpers. Standard library only — no numpy, no scipy.

The whole engine works on at most a few thousand values (30 days x 24 buckets x
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


def coefficient_of_variation(values: Sequence[float]) -> float | None:
    """Standard deviation over |mean|. ``None`` when the mean is ~zero.

    Used to require that a fault estimate is *stable* across days, not merely
    correct on average.
    """
    if len(values) < 2:
        return None
    m = mean(values)
    if m is None or abs(m) < 1e-9:
        return None
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return (var**0.5) / abs(m)


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


def theil_sen_slope(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Robust slope: the median of pairwise slopes.

    A deterministic stride is used to bound the pair count rather than sampling
    randomly, because the engine must produce byte-identical output for
    identical input.
    """
    n = min(len(xs), len(ys))
    if n < 3:
        return None
    stride = 1 if n <= 60 else n // 60
    slopes: list[float] = []
    for i in range(0, n, stride):
        for j in range(i + 1, n, stride):
            dx = xs[j] - xs[i]
            if abs(dx) < 1e-12:
                continue
            slopes.append((ys[j] - ys[i]) / dx)
    return median(slopes)


def theil_sen_intercept(xs: Sequence[float], ys: Sequence[float], slope: float) -> float | None:
    """The robust companion to the slope: ``median(y - slope * x)``.

    A least-squares intercept would be dragged by the same outliers Theil-Sen
    exists to ignore, so the pair has to be taken together.
    """
    if not xs or len(xs) != len(ys):
        return None
    return median([y - slope * x for x, y in zip(xs, ys, strict=True)])


def sum_squares(values: Sequence[float]) -> float:
    return sum(v * v for v in values)


def safe_ratio(numerator: float, denominator: float) -> float | None:
    """Divide, or return ``None`` when the denominator is negligible.

    Deliberately returns ``None`` rather than 0.0 or infinity — a ratio we
    cannot compute is not a ratio of zero.
    """
    if abs(denominator) < 1e-9:
        return None
    return numerator / denominator


def least_squares(
    columns: Sequence[Sequence[float]], target: Sequence[float]
) -> list[float] | None:
    """Coefficients minimising the squared error of ``Σ cᵢ·columnᵢ`` against ``target``.

    Exists because estimating one coefficient at a time is biased whenever the
    columns overlap, and on a DC-coupled hybrid they always do: generation and
    battery throughput rise together, so a median-of-ratios attributed to one
    term carries a share of the other. Measured against a known 96%-efficient
    inverter, that bias reads the generation term 62% high — enough to push a
    healthy installation's loss outside the window that would have absorbed it,
    at which point nothing is subtracted at all.

    Solved through the normal equations, which are adequate here and only here:
    three columns, and each is first scaled to unit root-mean-square so their
    wildly different magnitudes (watt-hours against a column of ones) cannot
    wreck the conditioning. Returns ``None`` rather than guessing when a column
    is empty or the system is singular — two identical columns have no unique
    answer, and inventing one would put a fault's energy into a loss term.
    """
    if not columns or not target:
        return None
    width = len(columns)
    rows = len(target)
    if any(len(column) != rows for column in columns) or rows <= width:
        return None

    scales: list[float] = []
    for column in columns:
        rms = (sum(v * v for v in column) / rows) ** 0.5
        if rms <= 1e-12:
            return None
        scales.append(rms)

    # A is the scaled design matrix; solve AᵀA x = Aᵀb.
    normal = [
        [
            sum(columns[i][r] * columns[j][r] for r in range(rows)) / (scales[i] * scales[j])
            for j in range(width)
        ]
        + [sum(columns[i][r] * target[r] for r in range(rows)) / scales[i]]
        for i in range(width)
    ]

    for pivot in range(width):
        best = max(range(pivot, width), key=lambda r: abs(normal[r][pivot]))
        if abs(normal[best][pivot]) < 1e-9:
            return None
        normal[pivot], normal[best] = normal[best], normal[pivot]
        head = normal[pivot]
        for row in normal[pivot + 1 :]:
            factor = row[pivot] / head[pivot]
            for col in range(pivot, width + 1):
                row[col] -= factor * head[col]

    solved = [0.0] * width
    for i in reversed(range(width)):
        total = normal[i][width] - sum(normal[i][j] * solved[j] for j in range(i + 1, width))
        solved[i] = total / normal[i][i]
    return [value / scale for value, scale in zip(solved, scales, strict=True)]
