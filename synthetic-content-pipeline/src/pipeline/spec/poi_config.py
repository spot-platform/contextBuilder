"""POI 카탈로그 yaml 로더 + 검증.

POI-anchored pipeline §Phase 0.

`config/poi/skill_to_tag.yaml` 와 `config/poi/role_distance_rules.yaml` 을
로드한다. yaml 파일의 키 / 값이 다음 불변식을 위반하면 ``ValueError`` 를 발생.

불변식
------
1. ``skill_to_tag.yaml`` 의 키 set 은 ``spot-simulator/config/skills_catalog.yaml``
   의 skill 키 set 의 superset (``__default__`` 제외).
2. ``primary_tags / secondary_tags / avoid_tags`` 의 모든 태그는 9개 valid is_*
   컬럼 중 하나.
3. ``role_hint.{meetup,main,wrapup}`` 도 9개 valid 태그 중 하나.
4. ``role_distance_rules.yaml`` 의 6 키 (max_total_walking_meters,
   meetup_to_main_max_meters, main_to_secondary_max_meters,
   secondary_to_wrapup_max_meters, prefer_same_road_address,
   min_mapping_confidence) 모두 존재.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

# place_normalized 의 9개 boolean 태그 — 매핑 카탈로그가 이 set 외 값을 쓰면 reject.
_VALID_TAGS: frozenset[str] = frozenset({
    "is_food",
    "is_cafe",
    "is_activity",
    "is_park",
    "is_culture",
    "is_nightlife",
    "is_lesson",
    "is_night_friendly",
    "is_group_friendly",
})

_REQUIRED_DISTANCE_KEYS: frozenset[str] = frozenset({
    "max_total_walking_meters",
    "meetup_to_main_max_meters",
    "main_to_secondary_max_meters",
    "secondary_to_wrapup_max_meters",
    "prefer_same_road_address",
    "min_mapping_confidence",
})

_REQUIRED_ROLE_HINT_KEYS: frozenset[str] = frozenset({"meetup", "main", "wrapup"})

# 기본 경로 (cwd 무관 절대경로 fallback).
_DEFAULT_SKILL_TO_TAG_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "poi" / "skill_to_tag.yaml"
)
_DEFAULT_DISTANCE_RULES_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "poi" / "role_distance_rules.yaml"
)
# Skills catalog: ``synthetic-content-pipeline`` 와 ``spot-simulator`` 가
# 워크스페이스 형제 디렉토리. ``parents[4]`` 가 워크스페이스 루트.
_DEFAULT_SKILLS_CATALOG_PATH = (
    Path(__file__).resolve().parents[4] / "spot-simulator" / "config" / "skills_catalog.yaml"
)


def _validate_tag_list(tags: Any, *, ctx: str) -> list[str]:
    if not isinstance(tags, list):
        raise ValueError(f"{ctx}: must be a list, got {type(tags).__name__}")
    invalid = [t for t in tags if t not in _VALID_TAGS]
    if invalid:
        raise ValueError(
            f"{ctx}: invalid tag(s) {invalid}. Allowed: {sorted(_VALID_TAGS)}"
        )
    return list(tags)


def _validate_role_hint(role_hint: Any, *, ctx: str) -> Dict[str, str]:
    if not isinstance(role_hint, dict):
        raise ValueError(f"{ctx}: role_hint must be a dict")
    missing = _REQUIRED_ROLE_HINT_KEYS - role_hint.keys()
    if missing:
        raise ValueError(
            f"{ctx}: role_hint missing keys {sorted(missing)}"
        )
    for role, tag in role_hint.items():
        if role not in _REQUIRED_ROLE_HINT_KEYS:
            continue  # 추가 role 은 무시 (앞으로 secondary 같은 게 추가될 수 있음)
        if tag not in _VALID_TAGS:
            raise ValueError(
                f"{ctx}: role_hint[{role!r}]={tag!r} not in valid tags"
            )
    return {k: role_hint[k] for k in _REQUIRED_ROLE_HINT_KEYS}


def _load_skills_catalog_keys(path: Optional[Path] = None) -> set[str]:
    """spot-simulator/config/skills_catalog.yaml 의 skill 키 set 반환."""
    p = path or _DEFAULT_SKILLS_CATALOG_PATH
    if not p.exists():
        # CI / 패키징에서 spot-simulator 가 없을 수 있음 — 검증을 skip 하기 위해
        # 빈 set 반환. caller (검증 함수) 에서 빈 set 면 superset 검사를 무시.
        return set()
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return set()
    return {str(k) for k in data.keys()}


def load_skill_to_tag(
    path: Optional[Path] = None,
    *,
    skills_catalog_path: Optional[Path] = None,
    strict_superset: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """skill_to_tag.yaml 로드 + 검증.

    Args:
        path: yaml 경로. None 이면 default.
        skills_catalog_path: 검증용 skills_catalog.yaml 경로.
        strict_superset: True 면 catalog 의 모든 skill 이 매핑에 존재해야 함.
            CI 에서 spot-simulator 가 없는 환경 대비로 catalog 가 비어있으면
            superset 검사를 자동 skip.

    Returns:
        ``{skill_name: {primary_tags, secondary_tags, avoid_tags, role_hint}}``
    """
    p = path or _DEFAULT_SKILL_TO_TAG_PATH
    if not p.exists():
        raise FileNotFoundError(f"skill_to_tag.yaml not found: {p}")

    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{p}: top-level must be a mapping")

    if "__default__" not in raw:
        raise ValueError(f"{p}: '__default__' key is required as fallback")

    out: Dict[str, Dict[str, Any]] = {}
    for skill, cfg in raw.items():
        ctx = f"skill_to_tag[{skill!r}]"
        if not isinstance(cfg, dict):
            raise ValueError(f"{ctx}: must be a dict")
        primary = _validate_tag_list(cfg.get("primary_tags", []), ctx=f"{ctx}.primary_tags")
        secondary = _validate_tag_list(
            cfg.get("secondary_tags", []), ctx=f"{ctx}.secondary_tags"
        )
        avoid = _validate_tag_list(cfg.get("avoid_tags", []), ctx=f"{ctx}.avoid_tags")
        role_hint = _validate_role_hint(cfg.get("role_hint", {}), ctx=ctx)
        out[skill] = {
            "primary_tags": primary,
            "secondary_tags": secondary,
            "avoid_tags": avoid,
            "role_hint": role_hint,
        }

    if strict_superset:
        catalog_keys = _load_skills_catalog_keys(skills_catalog_path)
        if catalog_keys:
            mapped_keys = set(out.keys()) - {"__default__"}
            missing = catalog_keys - mapped_keys
            if missing:
                raise ValueError(
                    f"skill_to_tag.yaml is missing mapping for skills: "
                    f"{sorted(missing)}. catalog requires {len(catalog_keys)} "
                    f"skills, got {len(mapped_keys)} mapped."
                )

    return out


def load_distance_rules(path: Optional[Path] = None) -> Dict[str, Any]:
    """role_distance_rules.yaml 로드 + 검증."""
    p = path or _DEFAULT_DISTANCE_RULES_PATH
    if not p.exists():
        raise FileNotFoundError(f"role_distance_rules.yaml not found: {p}")

    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{p}: top-level must be a mapping")

    missing = _REQUIRED_DISTANCE_KEYS - raw.keys()
    if missing:
        raise ValueError(f"{p}: missing required keys {sorted(missing)}")

    # 타입 검증
    for k in (
        "max_total_walking_meters",
        "meetup_to_main_max_meters",
        "main_to_secondary_max_meters",
        "secondary_to_wrapup_max_meters",
    ):
        if not isinstance(raw[k], int) or raw[k] <= 0:
            raise ValueError(f"{p}: {k} must be a positive int")
    if not isinstance(raw["prefer_same_road_address"], bool):
        raise ValueError(f"{p}: prefer_same_road_address must be bool")
    if not isinstance(raw["min_mapping_confidence"], (int, float)):
        raise ValueError(f"{p}: min_mapping_confidence must be number")
    if not 0.0 <= float(raw["min_mapping_confidence"]) <= 1.0:
        raise ValueError(f"{p}: min_mapping_confidence must be in [0, 1]")

    return dict(raw)


__all__ = [
    "load_skill_to_tag",
    "load_distance_rules",
]
