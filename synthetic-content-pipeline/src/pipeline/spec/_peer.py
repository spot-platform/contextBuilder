"""Phase Peer-D content_spec builder (peer marketplace).

spot-simulator 가 Phase Peer-A~C 로 재작성한 event_log 를 읽어 ContentSpec 을
생성한다. 새 이벤트 타입 (CREATE_TEACH_SPOT, JOIN_TEACH_SPOT, SUPPORTER_RESPONDED,
BOND_UPDATED, POCKET_MONEY_EARNED, REPUTATION_UPDATED, ...) 의 payload 에서
skill_topic / fee_breakdown / origination_mode / bonded_partner_count 같은 peer
도메인 필드를 추출한다.

설계 원칙:
    1. event_log.jsonl 을 **딱 한 번** 스캔하며 필요한 모든 정보를 수집한다.
       (target spot 의 이벤트 + CREATE_SKILL_REQUEST 인덱스를 동시 수집)
    2. 모든 결정적인 선택은 ``spot_id`` 기반 deterministic Random 을 사용.
    3. simulator 가 특정 필드를 payload 에 넣지 않은 경우, skills_catalog 등
       external config 로 fallback / 추정한다. 모두 fallback 이 실패하면 None.
    4. LLM 생성/검증/프롬프트 로직은 **건드리지 않는다** — 이 모듈은 순수
       구조화 빌더.
"""
from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from pipeline.spec._legacy import (
    DEFAULT_REGION_FEATURES,
    MINUTES_PER_TICK,  # noqa: F401  (contract re-export)
    PERSONA_TYPE_FALLBACK,
    SIMULATION_START_DATE,  # noqa: F401
    TICKS_PER_DAY,  # noqa: F401
    _deterministic_random,
    _resolve_sentiment,
    _tick_to_schedule,
)
from pipeline.spec.models import (
    ActivityConstraints,
    ActivityResult,
    Budget,
    ContentSpec,
    FeeBreakdownSpec,
    HostPersona,
    Participants,
    Schedule,
)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

#: skills_catalog.yaml 기본 경로 (cwd 기준 상대).
DEFAULT_SKILLS_CATALOG = Path("../spot-simulator/config/skills_catalog.yaml")

#: 기본 세션 길이 (분) — simulator 가 duration 을 기록하지 않으므로 상수.
DEFAULT_SESSION_DURATION_MIN = 120

#: 카페 venue 의 기본 대관료 총액 (원). suggest_fee_breakdown 규칙과 동일.
CAFE_VENUE_RENTAL_TOTAL = 2000

#: price_band 역산 경계 (1인당 fee 기준, 원).
PRICE_BAND_THRESHOLDS: List[Tuple[int, int]] = [
    (5000, 1),
    (9000, 2),
    (15000, 3),
    (25000, 4),
    (1_000_000_000, 5),
]

#: skill_topic → category 상위 클래스 (참고용). peer mode 는 기본적으로
#: ``category = skill_topic`` 원값을 그대로 사용하고, 이 매핑은 legacy 호환
#: 필드가 필요한 테스트나 다운스트림 fallback 용.
SKILL_CATEGORY_CLASS: Dict[str, str] = {
    "기타": "music",
    "우쿨렐레": "music",
    "피아노 기초": "music",
    "홈쿡": "cooking",
    "홈베이킹": "cooking",
    "핸드드립": "cooking",
    "러닝": "exercise",
    "요가 입문": "exercise",
    "볼더링": "exercise",
    "가벼운 등산": "nature",
    "드로잉": "art",
    "스마트폰 사진": "art",
    "캘리그라피": "art",
    "영어 프리토킹": "language",
    "코딩 입문": "study",
    "원예": "nature",
    "보드게임": "culture",
    "타로": "culture",
}


# ---------------------------------------------------------------------------
# 공용 yaml 로더 (skills_catalog)
# ---------------------------------------------------------------------------


def _load_skills_catalog(
    path: Optional[Path],
) -> Dict[str, Dict[str, Any]]:
    """skills_catalog.yaml 로드. 누락 시 빈 dict (모든 fallback 비활성)."""
    catalog_path = path or DEFAULT_SKILLS_CATALOG
    if not catalog_path.exists():
        return {}
    try:
        import yaml  # lazy — tests 가 yaml 없어도 import 단계 안 깨지게

        with catalog_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:  # pragma: no cover — 방어적
        return {}


def _load_region_features(path: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    """_legacy._load_region_features 와 동일 동작. peer path 에서만 쓰므로 재선언."""
    p = path or DEFAULT_REGION_FEATURES
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# 이벤트 수집 (1 pass)
# ---------------------------------------------------------------------------


# target spot 에만 달리는 이벤트 타입 (spot_id 로 필터)
SPOT_SCOPED_EVENT_TYPES = frozenset(
    {
        "CREATE_TEACH_SPOT",
        "JOIN_TEACH_SPOT",
        "CANCEL_JOIN",
        "SPOT_MATCHED",
        "SPOT_CONFIRMED",
        "SPOT_STARTED",
        "SPOT_SETTLED",
        "SPOT_COMPLETED",
        "SETTLE",
        "FORCE_SETTLED",
        "SPOT_DISPUTED",
        "SPOT_TIMEOUT",
        "CHECK_IN",
        "NO_SHOW",
        "WRITE_REVIEW",
        "SUPPORTER_RESPONDED",
        "BOND_UPDATED",
        "FRIEND_UPGRADE",
        "REFERRAL_SENT",
        "POCKET_MONEY_EARNED",
        "REPUTATION_UPDATED",
        "SKILL_TRANSFER",
        "EQUIPMENT_LENT",
        "COUNTER_OFFER_SENT",
        "COUNTER_OFFER_ACCEPTED",
        "COUNTER_OFFER_REJECTED",
        "SPOT_RENEGOTIATED",
    }
)


def _collect_events_single_pass(
    event_log_path: Path, target_spot_id: str
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """파일을 **한 번** 스캔하면서 두 가지를 동시에 수집:

    1. ``spot_events``: ``spot_id == target_spot_id`` 인 이벤트 (등장 순)
    2. ``request_index``: ``CREATE_SKILL_REQUEST`` 이벤트를 ``request_id`` 키로
       인덱싱. request_matched 경로에서 originating request 를 역조회.
    """
    spot_events: List[Dict[str, Any]] = []
    request_index: Dict[str, Dict[str, Any]] = {}

    with event_log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = evt.get("event_type")
            sid = evt.get("spot_id")

            if etype == "CREATE_SKILL_REQUEST":
                payload = evt.get("payload") or {}
                rid = payload.get("request_id")
                if rid:
                    request_index[rid] = evt

            if sid == target_spot_id and etype in SPOT_SCOPED_EVENT_TYPES:
                spot_events.append(evt)

    return spot_events, request_index


# ---------------------------------------------------------------------------
# 헬퍼 — 각 도메인 필드 추출
# ---------------------------------------------------------------------------


def _find_create_teach_spot(
    events: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    for evt in events:
        if evt.get("event_type") == "CREATE_TEACH_SPOT":
            return evt
    return None


def _count_joins(events: List[Dict[str, Any]]) -> Tuple[int, set, set]:
    """(join_count, joined_agents, cancelled_agents)."""
    joined: set = set()
    cancelled: set = set()
    for evt in events:
        if evt.get("event_type") == "JOIN_TEACH_SPOT":
            aid = evt.get("agent_id")
            if aid:
                joined.add(aid)
        elif evt.get("event_type") == "CANCEL_JOIN":
            aid = evt.get("agent_id")
            if aid:
                cancelled.add(aid)
    return len(joined), joined, cancelled


def _find_settle(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """SPOT_SETTLED / FORCE_SETTLED / SETTLE 중 가장 먼저 나오는 것."""
    for evt in events:
        if evt.get("event_type") in ("SPOT_SETTLED", "FORCE_SETTLED", "SETTLE"):
            return evt.get("payload") or {}
    return None


def _resolve_scheduled_tick(
    events: List[Dict[str, Any]], fallback_create_tick: int
) -> int:
    """SPOT_MATCHED > SPOT_CONFIRMED > SPOT_STARTED > create_tick+4 순서."""
    prefer = {"SPOT_MATCHED": None, "SPOT_CONFIRMED": None, "SPOT_STARTED": None}
    for evt in events:
        t = evt.get("event_type")
        if t in prefer and prefer[t] is None:
            prefer[t] = evt.get("tick")
    for t in ("SPOT_MATCHED", "SPOT_CONFIRMED", "SPOT_STARTED"):
        if prefer[t] is not None:
            return int(prefer[t])
    return int(fallback_create_tick) + 4


def _infer_peer_host_persona(
    skill_topic: Optional[str],
    teach_mode: Optional[str],
    rng: random.Random,
) -> HostPersona:
    """또래 강사 기본 persona. content-generator-engineer 가 Phase E 에서 교체 가능.

    Phase D 책임 범위는 "값이 들어있고 deterministic" 뿐이다. LLM 톤 튜닝은
    content-generator-engineer 영역.
    """
    # skill_category_class 기반 tone 선택 (legacy fallback 테이블 재사용).
    cat = SKILL_CATEGORY_CLASS.get(skill_topic or "", "cafe")
    # Phase 1 의 PERSONA_TYPE_FALLBACK 은 카테고리 기반이라 일부만 매칭된다.
    persona_tuple = PERSONA_TYPE_FALLBACK.get(
        cat, ("supporter_neutral", "친근하고 편안한", "또래에게 편하게 말하는")
    )
    type_, tone, style = persona_tuple
    # 또래 강사 tone override — 프로 강사스러운 fallback tone 을 peer tone 으로 치환.
    if teach_mode == "1:1":
        tone = "가볍고 집중된 또래 톤"
        style = "편하게 한 명한테 설명해주는 친구 말투"
    elif teach_mode == "workshop":
        tone = "에너지 있고 북돋는 또래 톤"
        style = "여럿이 함께 해보자고 이끄는 친구 말투"
    else:
        tone = "친근하고 가벼운 또래 톤"
        style = "같이 해보자는 제안형 친구 말투"
    return HostPersona(type=type_, tone=tone, communication_style=style)


def _price_band_from_fee_per_partner(fee_per_partner: int) -> int:
    for threshold, band in PRICE_BAND_THRESHOLDS:
        if fee_per_partner <= threshold:
            return band
    return 5  # unreachable — 마지막 경계가 1e9


def _estimate_fee_breakdown(
    fee_per_partner: int,
    partner_count: int,
    skill_topic: Optional[str],
    venue_type: Optional[str],
    skills_catalog: Mapping[str, Dict[str, Any]],
) -> Optional[FeeBreakdownSpec]:
    """event_log payload 에 fee_breakdown 원본이 없을 때 **추정**.

    Phase Peer-B 현재 simulator 는 CREATE_TEACH_SPOT.payload 에 ``fee`` (1인당)
    만 기록한다. 2층 구조를 복원하려면 skills_catalog 에서 material/venue/equipment
    실비를 읽어와 합성해야 한다.

    계산 순서:
        material_cost (총액) = catalog.material_cost_per_partner × partner_count
        venue_rental (총액)   = venue 규칙 (home/park=0, cafe=2000, studio/gym=catalog)
        equipment_rental     = catalog.equipment_rental_per_partner × partner_count
                               (host equipment 보유 여부는 event_log 에 없음 → 보수적
                                으로 0 을 기본값으로 선택. peer_tone 톤 왜곡 방지)
        peer_labor_fee = max(0, fee_total - material - venue - equipment)

    catalog 가 없거나 skill 항목이 빠져 있으면 전부 0 (peer_labor_fee=total).
    반환값이 None 인 경우는 fee_per_partner 가 0 일 때 (invalid).
    """
    if fee_per_partner <= 0 or partner_count <= 0:
        return None

    fee_total = fee_per_partner * partner_count
    material = 0
    venue = 0
    equipment = 0

    entry = skills_catalog.get(skill_topic) if skill_topic else None
    if isinstance(entry, dict):
        material_per = int(entry.get("material_cost_per_partner", 0) or 0)
        material = material_per * partner_count

        # venue_rental 총액 계산
        vt = (venue_type or entry.get("default_venue") or "").lower()
        if vt in ("home", "park"):
            venue = 0
        elif vt == "cafe":
            venue = CAFE_VENUE_RENTAL_TOTAL
        elif vt == "studio":
            venue = int(entry.get("studio_rental_total", 0) or 0)
        elif vt == "gym":
            venue = int(entry.get("gym_rental_total", 0) or 0)
        else:
            venue = 0
        # equipment: host 보유 여부 모름 → 보수적 0
        equipment = 0
        # 단, entry 에 equipment_rental_per_partner 가 있고 fee_total 에서
        # material/venue 빼고도 여유가 크면 equipment 가 포함되었을 가능성이
        # 높다. 이 추정은 content-generator 쪽에서 해석하므로 여기선 0 유지.

    # passthrough 가 이미 fee_total 을 초과하면 labor 0 으로 clamp
    passthrough = material + venue + equipment
    if passthrough > fee_total:
        # catalog 가 실제보다 높게 설정된 경우 (rare). labor 0 + material/venue 비율 조정.
        peer_labor = 0
        # 비례 scale-down
        if passthrough > 0:
            scale = fee_total / passthrough
            material = int(material * scale)
            venue = int(venue * scale)
            equipment = int(equipment * scale)
    else:
        peer_labor = fee_total - passthrough

    return FeeBreakdownSpec(
        peer_labor_fee=peer_labor,
        material_cost=material,
        venue_rental=venue,
        equipment_rental=equipment,
    )


def _summarize_request(
    request_event: Dict[str, Any],
) -> Tuple[str, Optional[int], Optional[str]]:
    """CREATE_SKILL_REQUEST → (summary_text, max_fee, preferred_venue)."""
    payload = request_event.get("payload") or {}
    skill = payload.get("skill", "무언가")
    max_fee = payload.get("max_fee")
    venue = payload.get("venue")
    mode = payload.get("mode")
    parts = [f"{skill} 배우고 싶어요"]
    if max_fee is not None:
        parts.append(f"(예산 {int(max_fee):,}원")
        if venue:
            parts.append(f"{venue} 선호)")
        else:
            parts.append(")")
    elif venue:
        parts.append(f"({venue} 선호)")
    if mode:
        parts.append(f"— {mode}")
    # 한 줄로 합치기
    summary = " ".join(parts).replace(" )", ")").replace("( ", "(")
    max_fee_int: Optional[int] = int(max_fee) if max_fee is not None else None
    return summary, max_fee_int, venue


# ---------------------------------------------------------------------------
# Public (module-private) entrypoint
# ---------------------------------------------------------------------------


def build_peer_content_spec(
    event_log_path: str | Path,
    spot_id: str,
    *,
    region_features_path: Optional[str | Path] = None,
    skills_catalog_path: Optional[str | Path] = None,
) -> ContentSpec:
    """Phase Peer-D peer builder.

    Args:
        event_log_path: ``spot-simulator/output/event_log.jsonl`` (peer 포맷).
        spot_id: target spot id (예: ``"S_0001"``).
        region_features_path: 지역 이름 조회용 region_features.json 경로.
        skills_catalog_path: fee_breakdown 역산에 쓸 skills_catalog.yaml 경로.

    Returns:
        ContentSpec — peer 필드가 채워진 상태. legacy 필드 (region/category/
        host_persona/participants/schedule/budget/activity_constraints/plan_outline)
        도 모두 채워진다.

    Raises:
        FileNotFoundError: event_log 파일 부재.
        ValueError: 해당 spot_id 의 CREATE_TEACH_SPOT 이벤트가 없을 때.
    """
    log_path = Path(event_log_path)
    if not log_path.exists():
        raise FileNotFoundError(f"event log not found: {log_path}")

    region_features = _load_region_features(
        Path(region_features_path) if region_features_path else None
    )
    skills_catalog = _load_skills_catalog(
        Path(skills_catalog_path) if skills_catalog_path else None
    )

    spot_events, request_index = _collect_events_single_pass(log_path, spot_id)
    if not spot_events:
        raise ValueError(f"no events found for spot_id={spot_id}")

    create_evt = _find_create_teach_spot(spot_events)
    if create_evt is None:
        raise ValueError(f"CREATE_TEACH_SPOT event missing for spot_id={spot_id}")

    rng = _deterministic_random(spot_id)
    create_payload: Dict[str, Any] = create_evt.get("payload") or {}
    create_tick = int(create_evt.get("tick") or 0)

    # ── 기본 필드 ────────────────────────────────────────────────────
    host_agent_id = create_evt.get("agent_id")
    region_id = create_evt.get("region_id")
    region_name = "알 수 없음"
    region_center_lat: Optional[float] = None
    region_center_lng: Optional[float] = None
    if region_id and region_id in region_features:
        rfeat = region_features[region_id]
        region_name = rfeat.get("region_name", region_id)
        if rfeat.get("center_lat") is not None:
            region_center_lat = float(rfeat["center_lat"])
        if rfeat.get("center_lng") is not None:
            region_center_lng = float(rfeat["center_lng"])
    elif region_id:
        region_name = region_id

    # ── spot 핀 좌표: region center + deterministic jitter (±0.003° ≈ ±330m) ─
    spot_latitude: Optional[float] = None
    spot_longitude: Optional[float] = None
    if region_center_lat is not None and region_center_lng is not None:
        # spot_id 기반 독립 RNG 로 deterministic jitter. 같은 spot_id → 같은 핀.
        geo_rng = _deterministic_random(f"geo:{spot_id}")
        lat_jitter = geo_rng.uniform(-0.003, 0.003)
        lng_jitter = geo_rng.uniform(-0.003, 0.003)
        spot_latitude = round(region_center_lat + lat_jitter, 6)
        spot_longitude = round(region_center_lng + lng_jitter, 6)

    # ── peer 핵심 payload ─────────────────────────────────────────────
    skill_topic: Optional[str] = create_payload.get("skill")
    teach_mode: Optional[str] = create_payload.get("teach_mode")
    venue_type: Optional[str] = create_payload.get("venue_type")
    fee_per_partner: int = int(create_payload.get("fee", 0) or 0)
    host_skill_level: Optional[int] = create_payload.get("host_skill_level")
    if host_skill_level is not None:
        host_skill_level = int(host_skill_level)

    # ── category: skill_topic 원값을 그대로. 다운스트림에서 SKILL_CATEGORY_CLASS 로 fallback.
    category = skill_topic or SKILL_CATEGORY_CLASS.get("", "casual")

    # ── 참가자 ──────────────────────────────────────────────────────
    join_count, joined_agents, cancelled_agents = _count_joins(spot_events)
    final_joined = joined_agents - cancelled_agents
    expected_count = max(2, join_count + 1)  # + host 본인

    participants = Participants(expected_count=expected_count, persona_mix=[])

    # ── 일정 ─────────────────────────────────────────────────────────
    scheduled_tick = _resolve_scheduled_tick(spot_events, create_tick)
    schedule = _tick_to_schedule(scheduled_tick)
    if DEFAULT_SESSION_DURATION_MIN != schedule.duration_minutes:
        schedule = Schedule(
            date=schedule.date,
            start_time=schedule.start_time,
            duration_minutes=DEFAULT_SESSION_DURATION_MIN,
        )

    # ── fee_breakdown: payload 에 전체 dict 가 있으면 그걸 우선. ───────
    # Phase Peer-F (2025-04-15): simulator 가 CREATE_TEACH_SPOT.payload 에
    # fee_breakdown dict 전체를 기록하도록 확장됐다. 과거 fee 한 개만 있던
    # event_log 와의 하위 호환을 위해 없으면 catalog 역산으로 fallback.
    partner_count_for_fee = max(1, expected_count - 1)
    payload_fb = create_payload.get("fee_breakdown")
    if isinstance(payload_fb, dict) and payload_fb:
        try:
            fee_breakdown = FeeBreakdownSpec(
                peer_labor_fee=int(payload_fb.get("peer_labor_fee", 0) or 0),
                material_cost=int(payload_fb.get("material_cost", 0) or 0),
                venue_rental=int(payload_fb.get("venue_rental", 0) or 0),
                equipment_rental=int(payload_fb.get("equipment_rental", 0) or 0),
            )
        except (TypeError, ValueError):
            fee_breakdown = _estimate_fee_breakdown(
                fee_per_partner=fee_per_partner,
                partner_count=partner_count_for_fee,
                skill_topic=skill_topic,
                venue_type=venue_type,
                skills_catalog=skills_catalog,
            )
    else:
        fee_breakdown = _estimate_fee_breakdown(
            fee_per_partner=fee_per_partner,
            partner_count=partner_count_for_fee,
            skill_topic=skill_topic,
            venue_type=venue_type,
            skills_catalog=skills_catalog,
        )

    # fee_per_partner 재계산: fee_breakdown 이 있으면 capacity 로 나눈 값 우선
    if fee_breakdown and fee_breakdown.total > 0:
        capacity_for_div = int(create_payload.get("capacity", 0) or 0) or expected_count
        effective_fee_per_partner = max(
            fee_per_partner, fee_breakdown.total // max(1, capacity_for_div)
        )
    else:
        effective_fee_per_partner = fee_per_partner if fee_per_partner > 0 else 9000

    price_band = _price_band_from_fee_per_partner(effective_fee_per_partner)
    budget = Budget(
        price_band=price_band,
        expected_cost_per_person=effective_fee_per_partner,
    )

    # ── host_persona / plan_outline / constraints (peer 톤 기본값) ──
    host_persona = _infer_peer_host_persona(skill_topic, teach_mode, rng)
    plan_outline = [
        "가볍게 인사하고 오늘 배울 내용 간단 소개",
        f"{skill_topic or '활동'} 함께 해보며 중간 피드백",
        "마무리 정리하고 다음 세션 제안 혹은 인사",
    ]
    constraints = ActivityConstraints(
        indoor=(venue_type not in ("park",)),
        beginner_friendly=True,
        supporter_required=True,
    )

    # ── origination (offer vs request_matched) ───────────────────────
    origination_mode = str(create_payload.get("origination_mode") or "offer")
    originating_request_id = create_payload.get("originating_request_id")
    supporter_responded_evt: Optional[Dict[str, Any]] = None
    for evt in spot_events:
        if evt.get("event_type") == "SUPPORTER_RESPONDED":
            supporter_responded_evt = evt
            break
    if supporter_responded_evt is not None:
        if origination_mode != "request_matched":
            origination_mode = "request_matched"
        sp_payload = supporter_responded_evt.get("payload") or {}
        if originating_request_id is None:
            originating_request_id = sp_payload.get("request_id")

    is_request_matched = origination_mode == "request_matched"
    originating_voice = "learner" if is_request_matched else "host"
    responded_at_tick: Optional[int] = (
        int(supporter_responded_evt["tick"])
        if supporter_responded_evt is not None and supporter_responded_evt.get("tick") is not None
        else None
    )

    originating_request_summary: Optional[str] = None
    if is_request_matched and originating_request_id:
        req_evt = request_index.get(originating_request_id)
        if req_evt is not None:
            originating_request_summary, _max_fee, _pref_venue = _summarize_request(req_evt)

    # ── counter-offer / 재협상 ──────────────────────────────────────
    had_renegotiation = False
    original_target_partner_count: Optional[int] = None
    final_partner_count: Optional[int] = None
    renegotiation_history: List[Dict[str, Any]] = []
    for evt in spot_events:
        t = evt.get("event_type")
        p = evt.get("payload") or {}
        if t == "COUNTER_OFFER_SENT":
            original_target_partner_count = p.get("from_count")
            renegotiation_history.append(
                {
                    "tick": evt.get("tick"),
                    "type": "sent",
                    "from_count": p.get("from_count"),
                    "to_count": p.get("to_count"),
                    "original_total": p.get("original_total"),
                    "new_total": p.get("new_total"),
                }
            )
        elif t == "COUNTER_OFFER_ACCEPTED":
            renegotiation_history.append(
                {
                    "tick": evt.get("tick"),
                    "type": "accepted",
                    "partner_id": p.get("partner_id"),
                    "new_fee": p.get("new_fee"),
                }
            )
        elif t == "COUNTER_OFFER_REJECTED":
            renegotiation_history.append(
                {
                    "tick": evt.get("tick"),
                    "type": "rejected",
                    "partner_id": p.get("partner_id"),
                    "reason": p.get("reason"),
                }
            )
        elif t == "SPOT_RENEGOTIATED":
            had_renegotiation = True
            final_partner_count = p.get("final_partner_count")
            renegotiation_history.append(
                {
                    "tick": evt.get("tick"),
                    "type": "renegotiated",
                    "renegotiation_count": p.get("renegotiation_count"),
                    "final_total": p.get("final_total"),
                    "final_partner_count": p.get("final_partner_count"),
                }
            )
    if final_partner_count is None and had_renegotiation:
        final_partner_count = len(final_joined) + 1

    # ── 관계 이벤트 (BOND_UPDATED / FRIEND_UPGRADE / REFERRAL_SENT) ──
    bond_updates_at_settlement: List[Dict[str, Any]] = []
    bonded_partner_ids: set = set()
    friend_upgrades: List[Dict[str, Any]] = []
    referrals_triggered: List[Dict[str, Any]] = []
    BONDED_TYPES = {"regular", "mentor_bond", "friend"}
    for evt in spot_events:
        t = evt.get("event_type")
        p = evt.get("payload") or {}
        if t == "BOND_UPDATED":
            partner_id = p.get("other_agent_id")
            to_type = p.get("to")
            bond_updates_at_settlement.append(
                {
                    "partner_id": partner_id,
                    "from": p.get("from"),
                    "to": to_type,
                    "sessions": p.get("sessions"),
                    "affinity": p.get("affinity"),
                    "avg_sat": p.get("avg_sat"),
                }
            )
            if to_type in BONDED_TYPES and partner_id:
                bonded_partner_ids.add(partner_id)
        elif t == "FRIEND_UPGRADE":
            friend_upgrades.append(
                {
                    "partner_id": p.get("other_agent_id") or p.get("partner_id"),
                    "skill": p.get("skill"),
                    "sessions": p.get("sessions"),
                    "avg_sat": p.get("avg_sat"),
                }
            )
        elif t == "REFERRAL_SENT":
            referrals_triggered.append(
                {
                    "from": evt.get("agent_id"),
                    "to": p.get("target_agent_id") or p.get("to"),
                    "host": p.get("host") or host_agent_id,
                    "reason": p.get("reason"),
                    "skill": p.get("skill"),
                }
            )

    # ── 평판 & 수익 ─────────────────────────────────────────────────
    host_reputation_after: Optional[float] = None
    host_reputation_before: Optional[float] = None
    host_earn_from_this_spot: Optional[int] = None
    for evt in spot_events:
        t = evt.get("event_type")
        p = evt.get("payload") or {}
        if t == "REPUTATION_UPDATED":
            if p.get("new_score") is not None:
                host_reputation_after = float(p["new_score"])
                if p.get("delta") is not None:
                    try:
                        host_reputation_before = round(
                            host_reputation_after - float(p["delta"]), 4
                        )
                    except (TypeError, ValueError):
                        host_reputation_before = None
        elif t == "POCKET_MONEY_EARNED":
            if p.get("amount") is not None:
                try:
                    host_earn_from_this_spot = int(p["amount"])
                except (TypeError, ValueError):
                    pass

    # ── activity_result ────────────────────────────────────────────
    settle_payload = _find_settle(spot_events)
    activity_result: Optional[ActivityResult] = None
    if settle_payload is not None:
        completed = int(settle_payload.get("completed", len(final_joined) + 1))
        noshow = int(settle_payload.get("noshow", 0))
        avg_sat = settle_payload.get("avg_sat")
        jitter = rng.randint(-30, 10)
        duration_actual = max(60, DEFAULT_SESSION_DURATION_MIN + jitter)
        activity_result = ActivityResult(
            actual_participants=completed,
            no_show_count=noshow,
            duration_actual_minutes=duration_actual,
            issues=[],
            overall_sentiment=_resolve_sentiment(
                float(avg_sat) if avg_sat is not None else None
            ),
        )

    from pipeline.spec.taste_profile import generate_taste_profile

    _hour = int(schedule.start_time.split(":", 1)[0])
    if 0 <= _hour < 5:
        _slot = "dawn"
    elif _hour < 9:
        _slot = "morning"
    elif _hour < 11:
        _slot = "late_morning"
    elif _hour < 14:
        _slot = "lunch"
    elif _hour < 17:
        _slot = "afternoon"
    elif _hour < 21:
        _slot = "evening"
    else:
        _slot = "night"
    _day_type = (
        "weekend"
        if date.fromisoformat(schedule.date).weekday() >= 5
        else "weekday"
    )

    taste_facets, recent_obsession, curiosity_hooks = generate_taste_profile(
        spot_id=spot_id,
        skill_topic=skill_topic,
        category=category,
        region_label=region_name,
        host_skill_level=host_skill_level,
        teach_mode=teach_mode,
        venue_type=venue_type,
        schedule_time_slot=_slot,
        schedule_day_type=_day_type,
        host_persona_tone=host_persona.tone,
        host_persona_style=host_persona.communication_style,
        originating_request_summary=originating_request_summary,
    )

    return ContentSpec(
        spot_id=spot_id,
        region=region_name,
        category=category,
        spot_type="casual_meetup",
        host_persona=host_persona,
        participants=participants,
        schedule=schedule,
        budget=budget,
        activity_constraints=constraints,
        plan_outline=plan_outline,
        activity_result=activity_result,
        # peer 확장 ──────────────────────────────────────────────────
        skill_topic=skill_topic,
        host_skill_level=host_skill_level,
        teach_mode=teach_mode,
        venue_type=venue_type,
        fee_breakdown=fee_breakdown,
        origination_mode=origination_mode,
        originating_voice=originating_voice,
        originating_request_summary=originating_request_summary,
        responded_at_tick=responded_at_tick,
        is_request_matched=is_request_matched,
        had_renegotiation=had_renegotiation,
        renegotiation_history=renegotiation_history,
        original_target_partner_count=original_target_partner_count,
        final_partner_count=final_partner_count,
        bonded_partner_count=len(bonded_partner_ids),
        bond_updates_at_settlement=bond_updates_at_settlement,
        friend_upgrades=friend_upgrades,
        referrals_triggered=referrals_triggered,
        host_reputation_before=host_reputation_before,
        host_reputation_after=host_reputation_after,
        host_earn_from_this_spot=host_earn_from_this_spot,
        latitude=spot_latitude,
        longitude=spot_longitude,
        peer_tone_required=True,
        taste_facets=taste_facets,
        recent_obsession=recent_obsession,
        curiosity_hooks=curiosity_hooks,
    )


def _iter_events(event_log_path: Path) -> Iterable[Dict[str, Any]]:
    """호환용 — 외부 도구 디버깅에 쓰기 좋은 재사용 제너레이터."""
    with event_log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
