"""§7-1 페르소나별 톤 예시 (Phase Peer-E 재작성).

`host_persona.type` (ContentSpec) 를 프롬프트의 톤 레퍼런스로 매핑한다.
각 예문은 LLM 프롬프트에 그대로 삽입되며, **모두 또래 존댓말**이 기본이다.

제품 DNA (peer pivot §1):
- 또래끼리 가볍게 취미/스킬을 나누는 플랫폼
- 호스트는 프로 강사가 아닌 "저도 아직 배우는 중" 수준의 또래
- 공개 콘텐츠 (feed/detail/plan/recruiting_intro/join_approval) 는 반드시 존댓말
- 반말/SNS 톤 금지, 프로 강사 어휘 (강좌/수강생/수강료) 금지

예문 3~5개씩 유지. content-generator-engineer 가 단독 소유. validator-engineer
는 읽기 전용.
"""
from __future__ import annotations

from typing import Dict, List

#: 페르소나 → 또래 존댓말 톤 예문.
#:
#: key 는 `ContentSpec.host_persona.type` 과 일치. 피벗 이전 키
#: (``supporter_teacher`` / ``supporter_neutral`` / ``supporter_coach``) 은
#: legacy golden 호환을 위해 유지한다. 신규 5 persona 는 peer pivot plan §4 의
#: 명단 (night_social / weekend_explorer / planner / spontaneous / homebody).
PERSONA_TONE_EXAMPLES: Dict[str, List[str]] = {
    # ── peer pivot 5 persona ──────────────────────────────────────────
    "night_social": [
        "저 요리는 좀 해요, 같이 저녁 한 끼 만들어보실래요?",
        "처음 오셔도 금방 편해지실 거예요. 편하게 오세요~",
        "분위기 조용한 편이니까 초면이어도 부담 없어요.",
        "퇴근 후에 가볍게 한 끼 하면서 이야기 나누는 자리예요.",
    ],
    "weekend_explorer": [
        "저 매주 공원 러닝 나가는데 같이 뛰실 분 계세요?",
        "날씨 좋으면 같이 걷기만 해도 기분 풀려요.",
        "페이스는 편한 대로 맞춰요. 초보도 환영이에요.",
        "사진 찍으면서 천천히 가는 코스라 부담 없어요.",
    ],
    "planner": [
        "드로잉 같이 해보실 분 모집해요. 저도 아직 배우는 중이에요.",
        "도구 챙겨드리니까 빈손으로 오셔도 됩니다.",
        "천천히 진행해서 처음이신 분도 따라올 수 있어요.",
        "참가비는 재료비 실비만 나눠 내면 돼요.",
    ],
    "spontaneous": [
        "오늘 저녁 같이 홈쿡 해볼 분~ 재료는 같이 사러 가요!",
        "정해진 커리큘럼 없이 그때그때 즉흥으로 해요.",
        "분위기 가볍고 편해요. 말 편하게 거셔도 돼요.",
        "큰 계획 없이 모여서 같이 해봐요.",
    ],
    "homebody": [
        "집에서 조용히 베이킹 하는 모임이에요. 3명이 딱 좋아요.",
        "재료는 제가 준비할게요. 실비만 나눠 내면 돼요.",
        "천천히 만들면서 수다 떠는 분위기 좋아하시면 잘 맞아요.",
        "큰 소음 없이 조용히 같이 시간 보내요.",
    ],
    # ── legacy supporter_* persona (Phase 1 골든 호환) ────────────────
    "supporter_teacher": [
        "저도 이 동네에서 몇 년째 지내고 있는 또래예요. 편하게 오세요.",
        "처음 오시는 분도 환영해요. 자리 잡고 가볍게 인사부터 시작할게요.",
        "참여 확정되면 위치랑 도착 안내 한 번 더 드릴게요.",
    ],
    "supporter_neutral": [
        "가볍게 한 잔 하면서 근황 나누는 자리예요.",
        "딱 두 시간, 무리 없이 마무리해요.",
        "처음이어도 부담 없이 오세요. 분위기 편해요.",
    ],
    "supporter_coach": [
        "시작 전에 가볍게 몸 풀고 본격적으로 진행해요.",
        "초보도 환영이에요. 페이스 맞춰서 같이 가요.",
        "끝나고 가볍게 정리 운동까지 함께 해요.",
    ],
    # ── default fallback — 또래 존댓말 기본 톤 ────────────────────────
    "default": [
        "편하게 같이 해볼 분 찾고 있어요.",
        "저도 아직 배우는 중이라 부담 갖지 마시고 오세요.",
        "처음 오시는 분도 환영이에요.",
        "거창한 자리는 아니고 같이 시간 보내는 모임이에요.",
    ],
}


def tone_examples_for(persona_type: str) -> List[str]:
    """persona type → 톤 예시 리스트. 매칭 실패 시 default 반환.

    프롬프트 변수 `tone_examples` 에 직접 주입한다.
    """
    return PERSONA_TONE_EXAMPLES.get(persona_type, PERSONA_TONE_EXAMPLES["default"])


__all__ = ["PERSONA_TONE_EXAMPLES", "tone_examples_for"]
