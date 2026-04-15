"""Unit tests for scoring utilities used by ``build_region_features``.

Full pipeline testing lives in integration-qa; these tests pin down
the pure math so refactors can not silently drift the percentile
ranking or weighted average semantics.
"""

from __future__ import annotations

import math

from app.services.scoring_service import (
    clip01,
    percentile_rank,
    sigmoid_normalize,
    weighted_avg,
)


def test_percentile_rank_empty():
    assert percentile_rank([]) == []


def test_percentile_rank_single_element():
    assert percentile_rank([3.14]) == [0.0]


def test_percentile_rank_monotonic_sort():
    ranks = percentile_rank([10.0, 20.0, 30.0, 40.0, 50.0])
    # Endpoints pinned to [0, 1]; interior values strictly increasing.
    assert ranks[0] == 0.0
    assert ranks[-1] == 1.0
    assert all(ranks[i] < ranks[i + 1] for i in range(len(ranks) - 1))


def test_percentile_rank_ordering_preserved_under_shuffle():
    values = [5.0, 1.0, 3.0, 4.0, 2.0]
    ranks = percentile_rank(values)
    # The minimum (1.0 at idx 1) must have rank 0.
    assert ranks[1] == 0.0
    # The maximum (5.0 at idx 0) must have rank 1.
    assert ranks[0] == 1.0


def test_percentile_rank_handles_nan_and_inf():
    # NaN / inf are replaced with 0 before ranking, so they behave
    # like the smallest value.
    ranks = percentile_rank([float("nan"), 10.0, 20.0])
    assert ranks[0] == 0.0
    assert ranks[-1] == 1.0
    for r in ranks:
        assert math.isfinite(r)


def test_clip01_bounds():
    assert clip01(-1.0) == 0.0
    assert clip01(0.0) == 0.0
    assert clip01(0.5) == 0.5
    assert clip01(1.0) == 1.0
    assert clip01(2.5) == 1.0
    assert clip01(float("nan")) == 0.0
    assert clip01(float("inf")) == 0.0


def test_sigmoid_normalize_midpoint():
    assert abs(sigmoid_normalize(1.0, midpoint=1.0) - 0.5) < 1e-9


def test_sigmoid_normalize_monotonic():
    low = sigmoid_normalize(0.0, midpoint=1.0)
    high = sigmoid_normalize(2.0, midpoint=1.0)
    assert low < 0.5 < high


def test_weighted_avg_basic():
    result = weighted_avg([(1.0, 0.5), (0.0, 0.5)])
    assert abs(result - 0.5) < 1e-9


def test_weighted_avg_empty_returns_zero():
    assert weighted_avg([]) == 0.0


def test_weighted_avg_all_zero_weight_returns_zero():
    assert weighted_avg([(1.0, 0.0), (2.0, 0.0)]) == 0.0


def test_weighted_avg_drops_nan():
    # NaN value should be dropped rather than poisoning the result.
    result = weighted_avg([(float("nan"), 1.0), (1.0, 1.0)])
    assert result == 1.0


def test_region_feature_formulas_shape():
    """Simulate plan §7 spot-suitability formulas over tiny inputs.

    Just a sanity check that the composition of percentile_rank +
    weighted_avg + clip01 lives in ``[0, 1]`` for every output.
    """

    # 5 regions, density values for food/cafe/activity/nightlife/lesson.
    food = [1, 3, 5, 7, 9]
    cafe = [9, 7, 5, 3, 1]
    park_count = [0, 1, 2, 3, 5]

    food_norm = percentile_rank(food)
    cafe_norm = percentile_rank(cafe)

    for i in range(5):
        park_access = clip01(min(1.0, park_count[i] / 3.0))
        casual_meetup = clip01(
            weighted_avg(
                [
                    (food_norm[i], 0.4),
                    (cafe_norm[i], 0.35),
                    (park_access, 0.25),
                ]
            )
        )
        assert 0.0 <= casual_meetup <= 1.0
