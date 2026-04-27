"""POI routing 알고리즘 — POI-anchored pipeline §Phase 3a.

skill_topic + venue_type + region_emd + duration → ResolvedPlace 리스트.

핵심 원칙
---------
1. **알고리즘 결정적, LLM 미관여**. spot_id 기반 deterministic seed 로 같은 spot
   은 같은 POI 선택 → reproducibility / golden test 가능.
2. ``mapping_confidence < min_confidence`` POI 자동 배제.
3. ``avoid_tags`` 매칭 POI 자동 배제 (러닝 ↔ nightlife 같은 카테고리 모순 방지).
4. ``venue_type`` 별 role 할당 규칙 명시:
   - ``cafe / studio / gym`` → main 1 + (선택) wrapup 1
   - ``park`` → main(park) + meetup(cafe) + (선택) wrapup(cafe)
   - ``home`` → meetup(cafe) + (선택) wrapup(cafe). main 은 가상 (집)
   - ``online / unknown`` → meetup(cafe) 1개 (FE 핀 표시용)
5. ``workshop`` + duration ≥ 180min 이면 secondary (식사/카페) 1개 보너스.
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from pipeline.poi.models import PoiRecord
from pipeline.poi.repository import PoiRepository
from pipeline.spec.models import ResolvedPlace

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutingResult:
    """assign_poi_roles 반환값."""

    venue_anchors: List[ResolvedPlace]
    primary_pin: Optional[ResolvedPlace]
    fallback_reason: Optional[str]  # None | "region_empty" | "no_match" | "skill_unmapped"


def assign_poi_roles(
    *,
    spot_id: str,
    skill_topic: Optional[str],
    venue_type: Optional[str],
    region_emd: str,
    teach_mode: Optional[str],
    duration_minutes: int,
    repo: PoiRepository,
    skill_to_tag: dict,
    distance_rules: dict,
    rng: Optional[random.Random] = None,
) -> RoutingResult:
    """skill / venue / region 으로부터 venue_anchors + primary_pin 결정.

    spot_id 기반 deterministic RNG 가 들어오면 같은 입력 → 같은 출력.
    region_pool 이 비어있거나 매칭 0건이면 fallback_reason 채우고 빈 결과 반환.
    """

    if rng is None:
        rng = _deterministic_rng(spot_id)

    skill_cfg = skill_to_tag.get(skill_topic) if skill_topic else None
    if skill_cfg is None:
        skill_cfg = skill_to_tag.get("__default__")
        if skill_cfg is None:
            return RoutingResult([], None, "skill_unmapped")
        logger.debug(
            "routing(%s): skill %r not in catalog — using __default__",
            spot_id, skill_topic,
        )

    primary_tags: List[str] = list(skill_cfg.get("primary_tags") or [])
    secondary_tags: List[str] = list(skill_cfg.get("secondary_tags") or [])
    avoid_tags: List[str] = list(skill_cfg.get("avoid_tags") or [])
    role_hint: dict = dict(skill_cfg.get("role_hint") or {})

    min_confidence = float(distance_rules.get("min_mapping_confidence", 0.7))
    region_pool = repo.list_by_region(region_emd)
    if not region_pool:
        return RoutingResult([], None, "region_empty")

    def excluded(p: PoiRecord) -> bool:
        if p.mapping_confidence < min_confidence:
            return True
        for t in avoid_tags:
            if getattr(p, t, False):
                return True
        return False

    pool = [p for p in region_pool if not excluded(p)]
    primary_pool = [p for p in pool if (not primary_tags) or p.has_any_tag(primary_tags)]
    secondary_pool = [p for p in pool if (not secondary_tags) or p.has_any_tag(secondary_tags)]

    venue = (venue_type or "cafe").lower()

    anchors: List[ResolvedPlace] = []
    primary: Optional[ResolvedPlace] = None
    fallback_reason: Optional[str] = None

    main_to_secondary_max = int(distance_rules.get("main_to_secondary_max_meters", 600))
    meetup_to_main_max = int(distance_rules.get("meetup_to_main_max_meters", 800))
    secondary_to_wrapup_max = int(distance_rules.get("secondary_to_wrapup_max_meters", 600))

    if venue == "home":
        # 집에서 활동. meetup 만 외부 카페 (집결지로). main 은 spec 의 "host home"
        # 으로 표현 — 합성 콘텐츠에서는 primary_pin = meetup 으로 두면 프론트 핀이
        # 카페에 박혀 자연스럽다.
        meetup_tag = role_hint.get("meetup", "is_cafe")
        meetup_p = _pick_top_k(secondary_pool, tag_filter=meetup_tag, rng=rng) \
            or _pick_top_k(primary_pool, tag_filter=None, rng=rng)
        if meetup_p:
            anchors.append(_to_resolved(meetup_p, role="meetup"))
            wrap_p = _pick_nearest(
                _exclude(secondary_pool, anchors),
                anchor=meetup_p,
                max_meters=secondary_to_wrapup_max,
                rng=rng,
            )
            if wrap_p:
                anchors.append(_to_resolved(wrap_p, role="wrapup"))
            primary = anchors[0]
        else:
            fallback_reason = "no_match"

    elif venue == "park":
        main_tag = role_hint.get("main", "is_park")
        meetup_tag = role_hint.get("meetup", "is_cafe")
        main_p = _pick_top_k(primary_pool, tag_filter=main_tag, rng=rng)
        if main_p is None:
            # park 가 없으면 secondary 에서라도 활동 가능한 곳으로 fallback
            main_p = _pick_top_k(primary_pool, tag_filter=None, rng=rng)
        if main_p is not None:
            anchors.append(_to_resolved(main_p, role="main"))
            primary = anchors[-1]
            meetup_p = _pick_nearest(
                [p for p in secondary_pool if getattr(p, meetup_tag, False)] or secondary_pool,
                anchor=main_p,
                max_meters=meetup_to_main_max,
                rng=rng,
            )
            if meetup_p and not _has_place_id(anchors, meetup_p.place_id):
                anchors.append(_to_resolved(meetup_p, role="meetup"))
            wrap_p = _pick_nearest(
                _exclude(secondary_pool, anchors),
                anchor=main_p,
                max_meters=main_to_secondary_max,
                rng=rng,
            )
            if wrap_p:
                anchors.append(_to_resolved(wrap_p, role="wrapup"))
        else:
            fallback_reason = "no_match"

    elif venue in ("cafe", "studio", "gym"):
        main_tag = role_hint.get("main", "is_cafe" if venue == "cafe" else None)
        main_p = _pick_top_k(primary_pool, tag_filter=main_tag, rng=rng)
        if main_p is None:
            main_p = _pick_top_k(primary_pool, tag_filter=None, rng=rng)
        if main_p is not None:
            anchors.append(_to_resolved(main_p, role="main"))
            primary = anchors[-1]
            wrap_p = _pick_nearest(
                _exclude(secondary_pool, anchors),
                anchor=main_p,
                max_meters=main_to_secondary_max,
                rng=rng,
            )
            if wrap_p:
                anchors.append(_to_resolved(wrap_p, role="wrapup"))
        else:
            fallback_reason = "no_match"

    else:  # online / unknown
        meetup_p = _pick_top_k(secondary_pool, tag_filter=role_hint.get("meetup", "is_cafe"), rng=rng) \
            or _pick_top_k(primary_pool, tag_filter=None, rng=rng)
        if meetup_p is not None:
            anchors.append(_to_resolved(meetup_p, role="meetup"))
            primary = anchors[-1]
        else:
            fallback_reason = "no_match"

    # workshop + 장시간이면 secondary (식사/카페) 보너스
    if (
        teach_mode == "workshop"
        and duration_minutes >= 180
        and primary is not None
        and not _has_role(anchors, "secondary")
    ):
        food_or_cafe = [
            p for p in pool
            if (p.is_food or p.is_cafe) and p.place_id != primary.place_id
        ]
        food_or_cafe = _exclude(food_or_cafe, anchors)
        sec_p = _pick_nearest(
            food_or_cafe,
            anchor=_resolved_to_record(primary, region_pool),
            max_meters=main_to_secondary_max,
            rng=rng,
        )
        if sec_p:
            anchors.append(_to_resolved(sec_p, role="secondary"))

    # role 순서로 재정렬 (meetup → main → secondary → wrapup)
    anchors = _sort_by_role(anchors)

    if primary is None and anchors:
        # 위 분기에서 못 채웠는데 anchors 만 채워진 케이스 보호
        primary = anchors[0]

    if not anchors:
        return RoutingResult([], None, fallback_reason or "no_match")

    return RoutingResult(anchors, primary, fallback_reason)


# ---------------------------------------------------------------------------
# Helpers — pickers / distance / sorting
# ---------------------------------------------------------------------------


_ROLE_ORDER = ["meetup", "main", "secondary", "wrapup"]


def _deterministic_rng(spot_id: str) -> random.Random:
    seed = hash("poi:" + spot_id) & 0xFFFFFFFF
    return random.Random(seed)


def _pick_top_k(
    pool: Sequence[PoiRecord],
    *,
    tag_filter: Optional[str],
    rng: random.Random,
    k: int = 5,
) -> Optional[PoiRecord]:
    """tag_filter 가 있으면 그 태그가 True 인 후보만. mapping_confidence 내림차순
    Top-K 중 deterministic shuffle 로 1개 선택 (분산 + 재현성)."""
    if not pool:
        return None
    cands: List[PoiRecord] = []
    if tag_filter:
        cands = [p for p in pool if getattr(p, tag_filter, False)]
        if not cands:
            cands = list(pool)  # 태그 없으면 전체 풀
    else:
        cands = list(pool)
    cands.sort(key=lambda p: (-p.mapping_confidence, p.place_id))
    top = cands[: min(k, len(cands))]
    rng.shuffle(top)
    return top[0]


def _pick_nearest(
    pool: Sequence[PoiRecord],
    *,
    anchor: Optional[PoiRecord],
    max_meters: float,
    rng: random.Random,
    top_n: int = 3,
) -> Optional[PoiRecord]:
    """anchor 와의 거리 ≤ max_meters 후보 중 가까운 top_n 에서 deterministic
    shuffle 로 1개. 후보 없으면 None."""
    if anchor is None or not pool:
        return None
    scored: List[Tuple[float, PoiRecord]] = []
    for p in pool:
        if p.place_id == anchor.place_id:
            continue
        d = _haversine_meters(anchor.lat, anchor.lng, p.lat, p.lng)
        if d <= max_meters:
            scored.append((d, p))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0])
    top = [p for _, p in scored[: min(top_n, len(scored))]]
    rng.shuffle(top)
    return top[0]


def _haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Earth-radius haversine. 두 좌표 사이의 m 거리."""
    R = 6371000.0  # meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _to_resolved(p: PoiRecord, *, role: str) -> ResolvedPlace:
    return ResolvedPlace(
        place_id=p.place_id,
        name=p.name,
        primary_category=p.primary_category,
        role=role,  # type: ignore[arg-type]
        lat=p.lat,
        lng=p.lng,
        address=p.address_for_display() or p.region_emd,
        road_address=p.road_address_name,
        confidence=p.mapping_confidence,
    )


def _sort_by_role(anchors: List[ResolvedPlace]) -> List[ResolvedPlace]:
    return sorted(
        anchors,
        key=lambda a: (_ROLE_ORDER.index(a.role) if a.role in _ROLE_ORDER else 99, a.place_id),
    )


def _exclude(pool: Iterable[PoiRecord], anchors: List[ResolvedPlace]) -> List[PoiRecord]:
    used = {a.place_id for a in anchors}
    return [p for p in pool if p.place_id not in used]


def _has_place_id(anchors: List[ResolvedPlace], place_id: int) -> bool:
    return any(a.place_id == place_id for a in anchors)


def _has_role(anchors: List[ResolvedPlace], role: str) -> bool:
    return any(a.role == role for a in anchors)


def _resolved_to_record(
    anchor: ResolvedPlace, pool: Sequence[PoiRecord]
) -> Optional[PoiRecord]:
    """ResolvedPlace 의 place_id 로 원본 PoiRecord 를 찾아 반환 (haversine 호출용)."""
    for p in pool:
        if p.place_id == anchor.place_id:
            return p
    # pool 에 없으면 합성 PoiRecord 를 임시 생성 (좌표만 필요).
    return PoiRecord(
        place_id=anchor.place_id,
        region_emd="",
        name=anchor.name,
        primary_category=anchor.primary_category,
        sub_category=None,
        lat=anchor.lat,
        lng=anchor.lng,
        address_name=anchor.address,
        road_address_name=anchor.road_address,
        is_food=False, is_cafe=False, is_activity=False, is_park=False,
        is_culture=False, is_nightlife=False, is_lesson=False,
        is_night_friendly=False, is_group_friendly=False,
        mapping_confidence=anchor.confidence,
    )


__all__ = ["assign_poi_roles", "RoutingResult"]
