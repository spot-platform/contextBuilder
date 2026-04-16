"""peer 모드 CREATE_SKILL_REQUEST 샘플링 분포 테스트.

Phase F 관측 버그: `preferred_teach_mode` 가 `small_group` 로 하드코딩,
`preferred_venue` 가 `default_venue` 단일값 → 모든 request 가 결정론적.

수정 후: `pick_teach_mode` / `pick_venue` 가 catalog distribution 에서
샘플링 → 한 combo 의 점유율이 50% 이하가 되어야 한다 (diversity 확보).
"""

from __future__ import annotations

import random

import pytest

from engine.peer_decision import pick_teach_mode, pick_venue


CATALOG_UNIFORM = {
    "mixed_skill": {
        "material_cost_per_partner": 0,
        "default_venue": "cafe",
        "teach_mode_distribution": {"1:1": 0.34, "small_group": 0.33, "workshop": 0.33},
        "venue_distribution": {"cafe": 0.4, "home": 0.4, "park": 0.2},
    },
}

CATALOG_LEGACY = {
    "legacy_skill": {
        "material_cost_per_partner": 0,
        "default_venue": "home",
        "teach_mode_distribution": {"small_group": 0.7, "workshop": 0.3},
    },
}


def test_pick_teach_mode_honours_distribution_not_hardcoded():
    rng = random.Random(123)
    counts: dict[str, int] = {}
    for _ in range(2000):
        mode = pick_teach_mode("mixed_skill", CATALOG_UNIFORM, rng)
        counts[mode] = counts.get(mode, 0) + 1

    top_share = max(counts.values()) / sum(counts.values())
    assert top_share <= 0.50, (
        f"teach_mode top combo share {top_share:.2f} > 0.50 — distribution not uniform"
    )
    # All three modes should appear (no 0 counts).
    assert set(counts.keys()) == {"1:1", "small_group", "workshop"}


def test_pick_venue_uses_distribution_when_present():
    rng = random.Random(42)
    counts: dict[str, int] = {}
    for _ in range(2000):
        v = pick_venue("mixed_skill", CATALOG_UNIFORM, rng)
        counts[v] = counts.get(v, 0) + 1

    top_share = max(counts.values()) / sum(counts.values())
    assert top_share <= 0.55, (
        f"venue top share {top_share:.2f} > 0.55 — venue_distribution ignored?"
    )
    assert set(counts.keys()) == {"cafe", "home", "park"}


def test_pick_venue_falls_back_to_default_venue_when_no_distribution():
    """기존 30개 catalog skill 는 venue_distribution 이 없으므로 default_venue 고정."""

    rng = random.Random(7)
    for _ in range(500):
        assert pick_venue("legacy_skill", CATALOG_LEGACY, rng) == "home"


def test_pick_venue_fallback_to_cafe_when_skill_missing():
    rng = random.Random(9)
    assert pick_venue("unknown_skill", {}, rng) == "cafe"
    assert pick_venue("unknown_skill", CATALOG_LEGACY, rng) == "cafe"


def test_combo_diversity_mode_x_venue():
    """실제 버그 시나리오: (mode, venue) combo 분포가 한 점에 쏠리면 안 됨."""

    rng = random.Random(1000)
    combos: dict[tuple[str, str], int] = {}
    for _ in range(3000):
        m = pick_teach_mode("mixed_skill", CATALOG_UNIFORM, rng)
        v = pick_venue("mixed_skill", CATALOG_UNIFORM, rng)
        combos[(m, v)] = combos.get((m, v), 0) + 1

    top_share = max(combos.values()) / sum(combos.values())
    assert top_share <= 0.25, (
        f"top (mode,venue) combo share {top_share:.2f} > 0.25 — bias suspected"
    )
    # Should see at least 6 of 9 possible combos.
    assert len(combos) >= 6


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
