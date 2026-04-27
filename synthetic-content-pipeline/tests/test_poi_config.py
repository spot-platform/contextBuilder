"""Phase 0 — POI 카탈로그 yaml 로드 + 검증 단위 테스트."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pipeline.spec.poi_config import (
    load_distance_rules,
    load_skill_to_tag,
)


def test_load_distance_rules_default():
    rules = load_distance_rules()
    assert rules["max_total_walking_meters"] == 1500
    assert rules["meetup_to_main_max_meters"] == 800
    assert rules["main_to_secondary_max_meters"] == 600
    assert rules["secondary_to_wrapup_max_meters"] == 600
    assert rules["prefer_same_road_address"] is True
    assert 0.0 <= rules["min_mapping_confidence"] <= 1.0


def test_load_skill_to_tag_default_has_default_key():
    cfg = load_skill_to_tag(strict_superset=False)
    assert "__default__" in cfg
    assert "primary_tags" in cfg["__default__"]


def test_load_skill_to_tag_superset_with_catalog():
    """catalog 의 모든 skill 이 매핑에 있어야 한다."""
    cfg = load_skill_to_tag(strict_superset=True)  # raises if catalog 와 불일치
    # 30 skill + __default__
    assert len(cfg) >= 30
    # 핵심 skill 샘플 검증
    for skill in ["기타", "러닝", "홈베이킹", "보드게임", "도예 기초"]:
        assert skill in cfg, f"missing skill: {skill}"


def test_skill_to_tag_all_tags_valid():
    cfg = load_skill_to_tag(strict_superset=False)
    valid_tags = {
        "is_food", "is_cafe", "is_activity", "is_park",
        "is_culture", "is_nightlife", "is_lesson",
        "is_night_friendly", "is_group_friendly",
    }
    for skill, c in cfg.items():
        for key in ("primary_tags", "secondary_tags", "avoid_tags"):
            for t in c[key]:
                assert t in valid_tags, f"{skill}.{key}: invalid tag {t}"
        for role, t in c["role_hint"].items():
            if role in {"meetup", "main", "wrapup"}:
                assert t in valid_tags, f"{skill}.role_hint[{role}]: invalid {t}"


def test_skill_to_tag_role_hint_complete():
    cfg = load_skill_to_tag(strict_superset=False)
    for skill, c in cfg.items():
        rh = c["role_hint"]
        assert "meetup" in rh
        assert "main" in rh
        assert "wrapup" in rh


def test_load_skill_to_tag_missing_default_key(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("기타:\n  primary_tags: [is_cafe]\n  secondary_tags: []\n  avoid_tags: []\n  role_hint: {meetup: is_cafe, main: is_cafe, wrapup: is_cafe}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="__default__"):
        load_skill_to_tag(bad, strict_superset=False)


def test_load_skill_to_tag_invalid_tag_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "__default__:\n"
        "  primary_tags: [is_cafe]\n"
        "  secondary_tags: []\n"
        "  avoid_tags: []\n"
        "  role_hint: {meetup: is_cafe, main: is_cafe, wrapup: is_cafe}\n"
        "기타:\n"
        "  primary_tags: [is_xxxxxx]\n"
        "  secondary_tags: []\n"
        "  avoid_tags: []\n"
        "  role_hint: {meetup: is_cafe, main: is_cafe, wrapup: is_cafe}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid tag"):
        load_skill_to_tag(bad, strict_superset=False)


def test_load_skill_to_tag_invalid_role_hint_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "__default__:\n"
        "  primary_tags: [is_cafe]\n"
        "  secondary_tags: []\n"
        "  avoid_tags: []\n"
        "  role_hint: {meetup: is_cafe, main: is_xxxxxx, wrapup: is_cafe}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="role_hint"):
        load_skill_to_tag(bad, strict_superset=False)


def test_load_distance_rules_missing_key(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("max_total_walking_meters: 1000\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required keys"):
        load_distance_rules(bad)


def test_load_distance_rules_invalid_confidence(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "max_total_walking_meters: 1500\n"
        "meetup_to_main_max_meters: 800\n"
        "main_to_secondary_max_meters: 600\n"
        "secondary_to_wrapup_max_meters: 600\n"
        "prefer_same_road_address: true\n"
        "min_mapping_confidence: 1.5\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="min_mapping_confidence"):
        load_distance_rules(bad)
