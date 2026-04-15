"""Numerical helpers shared by the feature pipeline (plan §7).

Kept dependency-light: only ``numpy`` is required, ``scipy`` is not
used. All helpers tolerate empty inputs and NaN/∞ gracefully so the
publish quality gate never sees garbage.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np


def clip01(x: float) -> float:
    """Clamp ``x`` to ``[0, 1]``. NaN/∞ collapse to 0."""

    if x is None:
        return 0.0
    if not math.isfinite(float(x)):
        return 0.0
    return max(0.0, min(1.0, float(x)))


def sigmoid_normalize(
    x: float, midpoint: float = 1.0, steepness: float = 4.0
) -> float:
    """Logistic squashing into ``(0, 1)``.

    ``midpoint`` is the value that maps to 0.5; ``steepness`` controls
    the slope. Non-finite inputs collapse to 0.
    """

    if x is None or not math.isfinite(float(x)):
        return 0.0
    try:
        return 1.0 / (1.0 + math.exp(-steepness * (float(x) - midpoint)))
    except OverflowError:  # pragma: no cover - numerical safety
        return 0.0 if x < midpoint else 1.0


def percentile_rank(values: list[float]) -> list[float]:
    """Return the percentile rank in ``[0, 1]`` for each element.

    Ties share the **average** rank (scipy ``rankdata(method='average')``
    equivalent) so equal inputs collapse to identical output. If every
    value is the same, every rank is ``0.5`` — intentional, because that
    is what "no discriminating information" means. Leaking argsort
    positions as a fake linear ranking (the previous bug) silently
    turned empty density signals into spurious scores.
    """

    if not values:
        return []
    arr = np.asarray(list(values), dtype=float)
    # Replace NaN/∞ with 0 before ranking.
    arr = np.where(np.isfinite(arr), arr, 0.0)
    n = len(arr)
    if n == 1:
        return [0.0]
    order = arr.argsort(kind="stable")
    ranks = np.empty(n, dtype=float)
    i = 0
    while i < n:
        j = i + 1
        while j < n and arr[order[j]] == arr[order[i]]:
            j += 1
        avg_rank = (i + j - 1) / 2.0
        for k in range(i, j):
            ranks[order[k]] = avg_rank
        i = j
    return (ranks / (n - 1)).tolist()


def weighted_avg(pairs: Iterable[tuple[float, float]]) -> float:
    """Safe weighted average of ``[(value, weight), ...]``.

    Empty iterables or zero total weight return 0.0. NaN/∞ values are
    dropped rather than poisoning the result.
    """

    total = 0.0
    weight_sum = 0.0
    for value, weight in pairs:
        if value is None or weight is None:
            continue
        v = float(value)
        w = float(weight)
        if not (math.isfinite(v) and math.isfinite(w)):
            continue
        total += v * w
        weight_sum += w
    if weight_sum <= 0:
        return 0.0
    return total / weight_sum
