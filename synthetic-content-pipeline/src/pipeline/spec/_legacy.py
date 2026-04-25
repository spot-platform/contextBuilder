"""Phase 1 legacy content_spec builder.

Phase Peer-D 이전의 `build_content_spec` 로직을 그대로 보존한다. 이 모듈의
함수들은 **CREATE_SPOT / JOIN_SPOT / SPOT_SETTLED** 같은 legacy 이벤트 타입을
사용하는 event_log (예: ``event_log_legacy_v1.jsonl``) 를 입력으로 받는다.

Phase Peer-D 이후 기본 경로는 ``_peer.py`` 이고, 이 모듈은 ``mode="legacy"`` 로
호출할 때만 동작한다. 내부 로직은 **의도적으로 수정하지 않는다** — Phase 1~4
pytest 회귀를 보장하기 위해서.
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pipeline.spec.models import (
    ActivityConstraints,
    ActivityResult,
    Budget,
    ContentSpec,
    HostPersona,
    Participants,
    Schedule,
)

# ---------------------------------------------------------------------------
# 상수 / 매핑 (Phase 1 그대로)
# ---------------------------------------------------------------------------

SIMULATION_START_DATE = date(2026, 4, 18)
MINUTES_PER_TICK = 30
TICKS_PER_DAY = 48

DENSITY_TO_CATEGORY = {
    "density_food": "food",
    "density_cafe": "cafe",
    "density_bar": "bar",
    "density_exercise": "exercise",
    "density_nature": "nature",
}

CATEGORY_PLAN_OUTLINE: Dict[str, List[str]] = {
    "food": ["가볍게 인사", "식사와 대화", "다음 모임 취향 공유"],
    "cafe": ["가볍게 인사", "음료 주문 후 자기소개", "취향 카드 한 장씩 공유"],
    "bar": ["가볍게 인사", "한 잔 하면서 근황 토크", "마지막 한 잔과 마무리 인사"],
    "exercise": ["스트레칭으로 워밍업", "메인 활동 함께하기", "정리 운동과 후기 공유"],
    "nature": ["만나서 코스 안내", "산책하며 대화", "마무리 사진과 인사"],
    "culture": ["가볍게 인사", "함께 관람/체험", "감상 나누고 마무리"],
}

PERSONA_TYPE_FALLBACK = {
    "food": ("supporter_teacher", "친절하고 실용적", "가볍고 직접적"),
    "cafe": ("supporter_neutral", "차분하고 부드러운", "여유 있는 톤"),
    "bar": ("supporter_neutral", "편안하고 가벼운", "솔직하고 친근한"),
    "exercise": ("supporter_coach", "에너지 있고 격려하는", "리드미컬하고 명확한"),
    "nature": ("supporter_neutral", "느긋하고 따뜻한", "여유롭고 부드러운"),
    "culture": ("supporter_teacher", "차분하고 정돈된", "정중하고 풍부한"),
}

DEFAULT_REGION_FEATURES = Path("../spot-simulator/data/region_features.json")


# ---------------------------------------------------------------------------
# 헬퍼 (Phase 1 그대로, 재사용은 _peer.py 도 함)
# ---------------------------------------------------------------------------


def _load_region_features(
    region_features_path: Optional[Path],
) -> Dict[str, Dict[str, Any]]:
    path = region_features_path or DEFAULT_REGION_FEATURES
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _iter_events(event_log_path: Path) -> Iterable[Dict[str, Any]]:
    with event_log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _collect_spot_events(
    event_log_path: Path, target_spot_id: str
) -> List[Dict[str, Any]]:
    return [
        evt
        for evt in _iter_events(event_log_path)
        if evt.get("spot_id") == target_spot_id
    ]


def _deterministic_random(spot_id: str) -> random.Random:
    return random.Random(hash(spot_id) & 0xFFFFFFFF)


def _infer_category(
    region_id: Optional[str],
    region_features: Dict[str, Dict[str, Any]],
    rng: random.Random,
) -> str:
    if not region_id or region_id not in region_features:
        return "food"
    feats = region_features[region_id]
    density_pairs: List[Tuple[str, float]] = []
    for key in DENSITY_TO_CATEGORY:
        if key in feats:
            density_pairs.append((key, float(feats[key])))
    if not density_pairs:
        return "food"
    density_pairs.sort(key=lambda kv: (-kv[1], kv[0]))
    top = density_pairs[0][0]
    return DENSITY_TO_CATEGORY[top]


def _infer_host_persona(category: str) -> HostPersona:
    type_, tone, style = PERSONA_TYPE_FALLBACK.get(
        category, ("supporter_neutral", "친절하고 실용적", "가볍고 직접적")
    )
    return HostPersona(type=type_, tone=tone, communication_style=style)


def _tick_to_schedule(scheduled_tick: int) -> Schedule:
    day_offset = scheduled_tick // TICKS_PER_DAY
    minute_in_day = (scheduled_tick % TICKS_PER_DAY) * MINUTES_PER_TICK
    spot_date = SIMULATION_START_DATE + timedelta(days=day_offset)
    hh, mm = divmod(minute_in_day, 60)
    return Schedule(
        date=spot_date.isoformat(),
        start_time=f"{hh:02d}:{mm:02d}",
        duration_minutes=120,
    )


def _build_budget(
    region_id: Optional[str], region_features: Dict[str, Dict[str, Any]]
) -> Budget:
    band = 2
    if region_id and region_id in region_features:
        band = int(region_features[region_id].get("budget_avg_level", 2))
    band = max(1, min(5, band))
    return Budget(price_band=band, expected_cost_per_person=band * 9000)


def _resolve_sentiment(avg_sat: Optional[float]) -> str:
    if avg_sat is None:
        return "neutral"
    if avg_sat >= 0.7:
        return "positive"
    if avg_sat >= 0.4:
        return "neutral"
    return "negative"


def _summarize_lifecycle(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "host_agent_id": None,
        "region_id": None,
        "create_tick": None,
        "matched_tick": None,
        "confirmed_tick": None,
        "started_tick": None,
        "settle_payload": None,
        "joined_agents": set(),
        "cancelled_agents": set(),
        "review_count": 0,
    }
    for evt in events:
        etype = evt.get("event_type")
        tick = evt.get("tick")
        if etype == "CREATE_SPOT":
            summary["host_agent_id"] = evt.get("agent_id")
            summary["region_id"] = evt.get("region_id")
            summary["create_tick"] = tick
        elif etype == "JOIN_SPOT":
            agent_id = evt.get("agent_id")
            if agent_id:
                summary["joined_agents"].add(agent_id)
        elif etype == "CANCEL_JOIN":
            agent_id = evt.get("agent_id")
            if agent_id:
                summary["cancelled_agents"].add(agent_id)
        elif etype == "SPOT_MATCHED":
            summary["matched_tick"] = tick
        elif etype == "SPOT_CONFIRMED":
            summary["confirmed_tick"] = tick
        elif etype == "SPOT_STARTED":
            summary["started_tick"] = tick
        elif etype == "SPOT_SETTLED":
            summary["settle_payload"] = evt.get("payload") or {}
        elif etype == "WRITE_REVIEW":
            summary["review_count"] += 1
    return summary


# ---------------------------------------------------------------------------
# Public (module-private) entrypoint
# ---------------------------------------------------------------------------


def build_legacy_content_spec(
    event_log_path: str | Path,
    spot_id: str,
    *,
    region_features_path: Optional[str | Path] = None,
) -> ContentSpec:
    """Phase 1 legacy builder. CREATE_SPOT 기반 event_log 용.

    peer event_log 에 대해 호출하면 CREATE_SPOT 이 없어서 ``ValueError`` 가 난다.
    이 함수는 ``mode="legacy"`` 호출 경로에서만 사용되며, 본문은 Phase 1 버전을
    **수정 없이** 그대로 이동해 왔다.
    """
    log_path = Path(event_log_path)
    if not log_path.exists():
        raise FileNotFoundError(f"event log not found: {log_path}")

    region_features = _load_region_features(
        Path(region_features_path) if region_features_path else None
    )
    events = _collect_spot_events(log_path, spot_id)
    if not events:
        raise ValueError(f"no events found for spot_id={spot_id}")

    summary = _summarize_lifecycle(events)
    if summary["create_tick"] is None:
        raise ValueError(f"CREATE_SPOT event missing for spot_id={spot_id}")

    rng = _deterministic_random(spot_id)

    region_id = summary["region_id"]
    region_name = "알 수 없음"
    if region_id and region_id in region_features:
        region_name = region_features[region_id].get("region_name", region_id)
    elif region_id:
        region_name = region_id

    category = _infer_category(region_id, region_features, rng)
    host_persona = _infer_host_persona(category)

    joined: set = summary["joined_agents"]
    cancelled: set = summary["cancelled_agents"]
    final_joined = joined - cancelled
    expected_count = max(2, len(joined) + 1)
    persona_mix: List[str] = []

    participants = Participants(expected_count=expected_count, persona_mix=persona_mix)

    scheduled_tick = (
        summary["matched_tick"]
        if summary["matched_tick"] is not None
        else summary["confirmed_tick"]
        if summary["confirmed_tick"] is not None
        else (summary["create_tick"] + 4)
    )
    schedule = _tick_to_schedule(scheduled_tick)

    budget = _build_budget(region_id, region_features)

    plan_outline = CATEGORY_PLAN_OUTLINE.get(
        category, ["가볍게 인사", "메인 활동", "마무리 인사"]
    )

    constraints = ActivityConstraints(
        indoor=True, beginner_friendly=True, supporter_required=True
    )

    settle = summary["settle_payload"]
    activity_result: Optional[ActivityResult] = None
    if settle is not None:
        completed = int(settle.get("completed", len(final_joined) + 1))
        noshow = int(settle.get("noshow", 0))
        avg_sat = settle.get("avg_sat")
        jitter = rng.randint(-30, 10)
        duration_actual = max(60, 120 + jitter)
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
        skill_topic=None,
        category=category,
        region_label=region_name,
        host_skill_level=None,
        teach_mode=None,
        venue_type=None,
        schedule_time_slot=_slot,
        schedule_day_type=_day_type,
        host_persona_tone=host_persona.tone,
        host_persona_style=host_persona.communication_style,
        originating_request_summary=None,
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
        taste_facets=taste_facets,
        recent_obsession=recent_obsession,
        curiosity_hooks=curiosity_hooks,
    )
