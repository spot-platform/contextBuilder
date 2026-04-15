"""Real-data blending weights (plan §8 STEP 6, v1.1 interface).

``get_weights`` decides how much the Kakao-derived score should trust
live user-activity aggregates. The logic is shared by every processor
so that a single tuning change ripples everywhere.

MVP keeps this as a tiny pure function. A full v1.1 implementation can
expand the signature to accept per-region tuning overrides.
"""

from __future__ import annotations

from typing import Optional


def get_weights(real_agg) -> tuple[float, float]:
    """Return ``(alpha, beta)`` where ``alpha`` weights Kakao and
    ``beta`` weights the real-activity blend.

    ``real_agg`` is an optional :class:`RealActivityAgg` row (or any
    object exposing ``real_spot_count``). Passing ``None`` always
    falls back to Kakao-only. The thresholds mirror plan §8-6 exactly:

    - <5 spots → (1.0, 0.0)
    - <20     → (0.8, 0.2)
    - <50     → (0.6, 0.4)
    - else    → (0.4, 0.6)
    """

    if real_agg is None:
        return (1.0, 0.0)
    spot_count: Optional[int] = getattr(real_agg, "real_spot_count", None)
    if spot_count is None or spot_count < 5:
        return (1.0, 0.0)
    if spot_count < 20:
        return (0.8, 0.2)
    if spot_count < 50:
        return (0.6, 0.4)
    return (0.4, 0.6)
