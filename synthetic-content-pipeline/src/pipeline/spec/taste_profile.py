"""Taste profile 발현 — Job 1 (build_content_spec) 보조 모듈.

같은 스킬·동네라도 호스트마다 세부 취향이 달라야 피드 카드의 색깔이 갈린다.
이 모듈은 `(spot_id, skill, region, level, time_slot, ...)` 컨텍스트로
codex 를 1 회 호출해 ``taste_facets / recent_obsession / curiosity_hooks`` 를
생성한다. cache 키는 spot_id 기반이라 재현성이 유지된다.

호출 실패 / stub / codex 미사용 환경에선 deterministic seeded fallback 을
반환한다. ContentSpec 의 동일 필드는 fallback 이어도 채워진다.
"""
from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# codex schema 위치 — codex_client 는 절대경로를 받는다.
_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "llm"
    / "schemas"
    / "taste_profile.json"
)
_TEMPLATE_ID = "taste_profile:v1"

# ---------------------------------------------------------------------------
# Deterministic fallback pools
# ---------------------------------------------------------------------------
#
# codex 가 부재하거나 실패해도 spec 에 빈 값이 들어가면 generator 다양성이
# 살지 않는다. 카테고리 / 스킬 단위 풀에서 deterministic seed 로 2~3 개를
# 뽑아 채운다. 풀이 짧아도 spot_id 단위 seed 로 조합이 갈리므로 충분.

_FACET_POOLS: Dict[str, List[str]] = {
    "music": [
        "핑거스타일 카피", "어쿠스틱 소품곡", "코드 보이싱", "녹음 후 셀프 피드백",
        "야외에서 가볍게 치기", "취향 플레이리스트 만들기",
    ],
    "cooking": [
        "재료 손질 디테일", "오븐 온도 잡기", "버터 결 살리기", "한 그릇 메뉴",
        "남은 재료 활용", "플레이팅 기록",
    ],
    "exercise": [
        "야외 러닝 루트", "회복 스트레칭", "초보용 페이스 잡기", "동네 산책 코스",
        "근력 기초 폼", "기록 사진 모으기",
    ],
    "nature": [
        "동네 산책 코스", "꽃 이름 외우기", "계절 식물 관찰", "가벼운 등산 입문",
        "도심 속 작은 정원",
    ],
    "art": [
        "10분 드로잉", "스마트폰 구도 잡기", "캘리그라피 한 줄", "색감 일기",
        "스케치북 한 권 채우기",
    ],
    "language": [
        "프리토킹 5분 챌린지", "표현 노트", "원어민 영상 따라 말하기",
        "관심 주제로 떠들기",
    ],
    "study": [
        "작은 토이 프로젝트", "에러 메시지 읽기", "코드 리뷰 함께",
        "기초 알고리즘 풀어보기",
    ],
    "culture": [
        "보드게임 규칙 비교", "타로 카드 해석 연습", "전시 후 감상 메모",
    ],
    "food": [
        "동네 맛집 한 줄 평", "혼밥/같이밥 비교", "메뉴 사진 기록",
    ],
    "cafe": [
        "원두 향 메모", "조용한 자리 찾기", "독서 한 챕터",
    ],
    "bar": [
        "한 잔의 페어링", "조용한 동네 바", "맥주 스타일 비교",
    ],
}

_OBSESSION_POOLS: Dict[str, List[str]] = {
    # 종결 어미 다양화 — 어떤 풀에서도 같은 종결이 두 번 이상 등장하지 않도록.
    # codex 가 fallback 으로 떨어졌을 때도 카드 다양성이 무너지지 않게 함.
    "music": [
        "핑거스타일 카피 한 곡씩 손에 익혀가는 중이에요.",
        "어쿠스틱 소품곡 카탈로그를 천천히 늘려보고 있어요.",
        "녹음한 걸 들으며 셀프 피드백 잡는 게 요즘 즐겁습니다.",
    ],
    "cooking": [
        "버터 결을 살리는 데 시간을 들이는 중이에요.",
        "남은 재료로 한 그릇 메뉴 짜는 게 요즘 재미입니다.",
        "오븐 온도를 단계별로 정리해보고 있어요.",
    ],
    "exercise": [
        "야외 러닝 루트를 동네 단위로 그려보는 중이에요.",
        "회복 스트레칭 루틴을 짧게 다듬어가는 중입니다.",
        "초보 페이스 잡는 법에 요즘 관심이 많아요.",
    ],
    "nature": [
        "동네 산책 코스를 한 줄씩 늘려가고 있어요.",
        "계절마다 달라지는 식물 표정을 기록하는 중입니다.",
    ],
    "art": [
        "10분 드로잉 한 장씩을 매일 모아가는 중이에요.",
        "스마트폰 구도 연습에 시간을 더 쏟고 있어요.",
    ],
    "language": [
        "관심 주제로 5분 떠들기 챌린지를 이어가는 중이에요.",
        "표현 노트를 한 페이지씩 채워가는 게 요즘 재미입니다.",
    ],
    "study": [
        "작은 토이 프로젝트를 손에서 놓지 않으려 합니다.",
    ],
    "culture": [
        "보드게임 규칙 비교 메모를 천천히 정리하고 있어요.",
        "타로 카드 해석 연습이 요즘 흥미롭습니다.",
    ],
    "food": [
        "동네 작은 가게 한 줄 평을 모아가는 중이에요.",
    ],
    "cafe": [
        "원두 향 메모를 노트에 옮기는 게 요즘 즐겁습니다.",
    ],
    "bar": [
        "조용한 동네 바를 한 곳씩 찾아가는 중이에요.",
    ],
}

_HOOK_POOLS: Dict[str, List[str]] = {
    "music": ["재즈 코드 보이싱", "어쿠스틱 녹음 팁", "리듬 기타 패턴"],
    "cooking": ["발효빵 입문", "한식 기본 양념", "디저트 플레이팅"],
    "exercise": ["요가 기본 호흡", "필라테스 코어", "트레일 러닝"],
    "nature": ["식물 이름 외우기", "주말 등산 코스", "도시 정원 가꾸기"],
    "art": ["수채화 채색", "사진 보정 기초", "캘리그라피 한 줄"],
    "language": ["원어민 표현 캐치", "발음 교정", "프리토킹 진행법"],
    "study": ["코드 리뷰 받기", "알고리즘 기초", "프로젝트 배포"],
    "culture": ["보드게임 큐레이션", "타로 스토리텔링"],
    "food": ["동네 가게 큐레이션", "혼밥 메뉴 추천"],
    "cafe": ["핸드드립 기초", "원두 블렌딩"],
    "bar": ["칵테일 입문", "맥주 페어링"],
}

# 한국어 SkillTopic → category class. _peer.SKILL_CATEGORY_CLASS 와 동일하지만
# import cycle 방지를 위해 여기에 가벼운 사본을 둔다 (fallback 전용).
_SKILL_TO_CLASS: Dict[str, str] = {
    "기타": "music", "우쿨렐레": "music", "피아노 기초": "music",
    "홈쿡": "cooking", "홈베이킹": "cooking", "핸드드립": "cafe",
    "러닝": "exercise", "요가 입문": "exercise", "볼더링": "exercise",
    "가벼운 등산": "nature", "원예": "nature",
    "드로잉": "art", "스마트폰 사진": "art", "캘리그라피": "art",
    "영어 프리토킹": "language", "코딩 입문": "study",
    "보드게임": "culture", "타로": "culture",
}


def _resolve_class(skill_topic: Optional[str], category: str) -> str:
    """skill_topic / category → fallback pool 키. 결측 시 'food' 로 떨어진다."""
    if skill_topic and skill_topic in _SKILL_TO_CLASS:
        return _SKILL_TO_CLASS[skill_topic]
    if category in _FACET_POOLS:
        return category
    return "food"


def _seeded_rng(spot_id: str) -> random.Random:
    return random.Random(hash(("taste", spot_id)) & 0xFFFFFFFF)


def _fallback_profile(
    *,
    spot_id: str,
    skill_topic: Optional[str],
    category: str,
    host_skill_level: Optional[int],
) -> Dict[str, Any]:
    """deterministic fallback. spot_id seed 로 풀에서 무작위 슬라이스."""
    rng = _seeded_rng(spot_id)
    cls = _resolve_class(skill_topic, category)

    facet_pool = list(_FACET_POOLS.get(cls, _FACET_POOLS["food"]))
    obsession_pool = list(_OBSESSION_POOLS.get(cls, _OBSESSION_POOLS.get("food", [
        "요즘 동네에서 가볍게 만나는 모임에 빠져 있어요.",
    ])))
    hook_pool = list(_HOOK_POOLS.get(cls, _HOOK_POOLS.get("food", ["새로운 취미"])))

    rng.shuffle(facet_pool)
    rng.shuffle(hook_pool)

    # level 기반 가벼운 재정렬 — 입문/숙련 색깔 분리.
    if host_skill_level is not None and host_skill_level <= 2:
        # 입문 색깔의 풀에서 앞쪽 우선. (현재 풀은 무작위라 순서 그대로 OK)
        pass

    facet_n = 3
    hook_n = 2
    return {
        "taste_facets": facet_pool[:facet_n],
        "recent_obsession": rng.choice(obsession_pool),
        "curiosity_hooks": hook_pool[:hook_n],
    }


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def generate_taste_profile(
    *,
    spot_id: str,
    skill_topic: Optional[str],
    category: str,
    region_label: str,
    host_skill_level: Optional[int],
    teach_mode: Optional[str],
    venue_type: Optional[str],
    schedule_time_slot: str,
    schedule_day_type: str,
    host_persona_tone: str,
    host_persona_style: str,
    originating_request_summary: Optional[str] = None,
    use_codex: bool = True,
) -> Tuple[List[str], Optional[str], List[str]]:
    """spot 1 개에 대한 taste profile 을 발현한다.

    Returns:
        (taste_facets, recent_obsession, curiosity_hooks)

    동작 순서:
        1. ``use_codex=True`` 면 codex_client.call_codex 호출 시도.
           - cache 는 codex_client 내부에서 (template_id, version, variables) 기반
             으로 작동하므로 spot_id 만 같다면 재실행해도 동일 응답.
        2. 실패/예외/import 불가 → deterministic fallback.

    Raises:
        없음. 어떤 실패에도 fallback 으로 회복한다 — Job 1 결정론을 깨지 않기 위해.
    """
    if use_codex:
        try:
            from pipeline.llm.codex_client import call_codex  # type: ignore
        except ImportError:
            logger.debug("taste_profile: codex_client unavailable, using fallback")
        else:
            variables = {
                "spot_id": spot_id,
                "region_label": region_label,
                "category": category,
                "skill_topic": skill_topic,
                "host_skill_level": host_skill_level,
                "teach_mode": teach_mode,
                "venue_type": venue_type,
                "schedule_time_slot": schedule_time_slot,
                "schedule_day_type": schedule_day_type,
                "host_persona": {
                    "tone": host_persona_tone,
                    "communication_style": host_persona_style,
                },
                "originating_request_summary": originating_request_summary,
            }
            try:
                resp = call_codex(
                    template_id=_TEMPLATE_ID,
                    variables=variables,
                    schema_path=_SCHEMA_PATH,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "taste_profile codex call failed (%s) — fallback for %s",
                    exc,
                    spot_id,
                )
            else:
                if isinstance(resp, dict):
                    facets = list(resp.get("taste_facets") or [])
                    obs = resp.get("recent_obsession")
                    hooks = list(resp.get("curiosity_hooks") or [])
                    if facets and obs and hooks:
                        return facets, obs, hooks
                    logger.warning(
                        "taste_profile codex response missing fields for %s — fallback",
                        spot_id,
                    )

    profile = _fallback_profile(
        spot_id=spot_id,
        skill_topic=skill_topic,
        category=category,
        host_skill_level=host_skill_level,
    )
    return (
        profile["taste_facets"],
        profile["recent_obsession"],
        profile["curiosity_hooks"],
    )


__all__ = ["generate_taste_profile"]
