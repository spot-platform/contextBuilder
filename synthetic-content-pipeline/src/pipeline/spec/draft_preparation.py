"""preparation deterministic draft.

POI-anchored pipeline §Phase 3c.

``skill_topic`` × ``venue_type`` 로부터 ``Preparation`` (host_provides /
partner_brings / weather_contingency / safety_notes) 1차 draft.

LLM 호출 0회. preparation generator (Phase 4d) 가 자연어 표현만 다듬는다.

룰 매핑은 in-code 표. 카탈로그가 30 skill 로 작아 yaml 분리 불필요. 향후
스킬이 늘어나면 ``config/poi/preparation_catalog.yaml`` 로 분리.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from pipeline.spec.models import Preparation


def build_preparation_draft(
    *,
    skill_topic: Optional[str],
    venue_type: Optional[str],
) -> Preparation:
    host_provides, partner_brings = _provide_split(skill_topic)
    weather = _weather_contingency(venue_type, skill_topic)
    safety = _safety_notes(skill_topic, venue_type)
    return Preparation(
        host_provides=host_provides,
        partner_brings=partner_brings,
        weather_contingency=weather,
        safety_notes=safety,
    )


# ---------------------------------------------------------------------------
# host_provides / partner_brings 매핑 — host 가 가져오는 것 vs partner 가 챙길 것
# ---------------------------------------------------------------------------

_PROVIDE_TABLE: dict[str, Tuple[List[str], List[str]]] = {
    # 음악 / 악기
    "기타":          (["기타 대여 가능"], ["편한 복장"]),
    "우쿨렐레":      (["우쿨렐레 대여 가능"], ["편한 복장"]),
    "피아노 기초":   (["연습실 + 악보"], ["편한 복장"]),
    "오카리나":      (["악보 출력본"], ["오카리나 (있으신 분)"]),
    # 요리 / 베이킹
    "홈쿡":          (["식재료 + 도구 일체"], ["앞치마 (있으신 분)"]),
    "홈베이킹":      (["베이킹 재료 + 오븐"], ["앞치마"]),
    "핸드드립":      (["원두 + 드립 도구"], ["좋아하는 머그컵 (선택)"]),
    "다도":          (["찻잎 + 다구"], ["편한 복장"]),
    "김치 담그기":   (["배추 + 양념 + 통"], ["앞치마", "용기 가져오시면 담아 가실 수 있어요"]),
    "홈카페 라떼아트":(["우유 + 원두 + 머신"], ["편한 복장"]),
    # 운동 / 신체
    "러닝":          (["가벼운 러닝 가이드"], ["러닝화", "물 한 병"]),
    "요가 입문":     (["요가매트 대여 가능"], ["편한 운동복", "물 한 병"]),
    "볼더링":        (["입장권"], ["편한 운동복", "클라이밍 슈즈는 대여 가능"]),
    "가벼운 등산":   (["트레일 가이드"], ["굽 낮은 운동화", "물 한 병", "간식"]),
    "필라테스 스트레칭":(["매트 대여 가능"], ["편한 운동복"]),
    "배드민턴":      (["라켓 + 셔틀콕"], ["편한 운동복", "실내화"]),
    "탁구":          (["라켓 + 공"], ["편한 운동복"]),
    # 창작 / 예술
    "드로잉":        (["종이 + 연필 + 파스텔"], ["관심 있는 레퍼런스 (사진 등)"]),
    "스마트폰 사진": (["촬영 가이드"], ["스마트폰 (충전 충분히)", "편한 신발"]),
    "캘리그라피":    (["붓펜 + 한지"], ["편한 복장"]),
    "수채화":        (["물감 + 종이 + 붓"], ["앞치마 (옷 묻을 수 있어요)"]),
    "도예 기초":     (["점토 + 도구 + 유약"], ["옷 묻어도 되는 복장"]),
    # 언어 / 학습
    "영어 프리토킹": (["진행 시트"], ["가벼운 노트"]),
    "코딩 입문":     (["기본 자료"], ["노트북 (충전기 포함)"]),
    "일본어 회화":   (["진행 시트"], ["가벼운 노트"]),
    "중국어 회화":   (["진행 시트"], ["가벼운 노트"]),
    # 생활
    "원예":          (["씨앗 + 흙 + 화분"], ["편한 복장 (옷 묻을 수 있어요)"]),
    "보드게임":      (["보드게임 종류 다양"], ["편하게 오시면 됩니다"]),
    "타로":          (["타로 카드"], ["편하게 오시면 됩니다"]),
    "뜨개질":        (["실 + 바늘 소모분"], ["좋아하는 색 실 (있으신 분)"]),
}


def _provide_split(skill_topic: Optional[str]) -> Tuple[List[str], List[str]]:
    if not skill_topic:
        return ([], [])
    return _PROVIDE_TABLE.get(
        skill_topic,
        (["진행 자료"], ["편하게 오시면 됩니다"]),
    )


# ---------------------------------------------------------------------------
def _weather_contingency(venue_type: Optional[str], skill_topic: Optional[str]) -> Optional[str]:
    if venue_type == "park":
        return "강수 30% 이상이면 다음 회차로 미루거나 인근 카페로 변경해요"
    if skill_topic in ("러닝", "가벼운 등산", "스마트폰 사진"):
        return "비 예보가 강하면 일정 조정 가능합니다"
    return None


def _safety_notes(skill_topic: Optional[str], venue_type: Optional[str]) -> List[str]:
    notes: List[str] = []
    if skill_topic in ("볼더링",):
        notes.append("팔 / 손가락 부상 이력 있으시면 미리 알려주세요")
    if skill_topic in ("가벼운 등산",):
        notes.append("성곽길 일부 구간이 미끄러우니 주의해 주세요")
    if skill_topic in ("김치 담그기", "홈쿡", "홈베이킹"):
        notes.append("매운 양념 / 알레르기 식재료 미리 알려주세요")
    return notes


__all__ = ["build_preparation_draft"]
