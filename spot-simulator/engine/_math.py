"""Shared tiny math utilities for the engine package.

Kept deliberately minimal so `decision.py`, `executors.py`, and future
`lifecycle.py` / `settlement.py` modules can all import the same `clamp`
without circular-import gymnastics.
"""

from __future__ import annotations


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp `x` into the closed interval [lo, hi].

    Defaults to the [0, 1] probability range used throughout plan §2.6.
    """
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x
