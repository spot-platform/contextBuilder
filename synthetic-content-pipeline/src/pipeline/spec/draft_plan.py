"""plan_steps deterministic draft.

POI-anchored pipeline §Phase 3c.

``Schedule`` + ``venue_anchors`` 로부터 시간/장소/활동/의도가 분해된 PlanStep
리스트를 생성. LLM 호출 0회. plan generator (Phase 4b) 가 ``activity`` /
``intent`` 자연어만 다듬는다.

룰
---
- meetup 이 있으면 첫 step (시작시각).
- main 은 meetup +20분 또는 시작시각.
- secondary (있으면) 는 main + 60% 시점.
- wrapup 은 종료 −30분.
- step 의 ``intent`` 는 deterministic 한 첫 시안 (LLM 이 회수해서 다듬음).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from pipeline.spec.models import PlanStep, ResolvedPlace, Schedule


def build_plan_steps_draft(
    *,
    schedule: Schedule,
    venue_anchors: List[ResolvedPlace],
    skill_topic: Optional[str],
    teach_mode: Optional[str],
) -> List[PlanStep]:
    """schedule × anchors 로 PlanStep 리스트 1차 draft."""

    if not venue_anchors:
        return []

    start_dt = _parse_dt(schedule.date, schedule.start_time)
    end_dt = start_dt + timedelta(minutes=schedule.duration_minutes)

    by_role = {a.role: a for a in venue_anchors}
    skill_label = skill_topic or "활동"

    steps: List[PlanStep] = []

    # ── meetup (첫 step) ─────────────────────────────────────────────
    if "meetup" in by_role:
        steps.append(
            PlanStep(
                time=_fmt_time(start_dt),
                place=by_role["meetup"],
                activity=f"가볍게 인사하고 오늘 흐름 소개",
                intent=_intent_for_meetup(skill_label),
            )
        )
        main_offset = 20
    else:
        main_offset = 0

    # ── main ─────────────────────────────────────────────────────────
    if "main" in by_role:
        main_t = start_dt + timedelta(minutes=main_offset)
        steps.append(
            PlanStep(
                time=_fmt_time(main_t),
                place=by_role["main"],
                activity=f"{skill_label} 본 활동",
                intent=_intent_for_main(skill_label, by_role["main"]),
            )
        )
    elif not steps:
        # main 도 meetup 도 없으면 첫 anchor 를 main 으로 fallback
        first = venue_anchors[0]
        steps.append(
            PlanStep(
                time=_fmt_time(start_dt),
                place=first,
                activity=f"{skill_label} 진행",
                intent=f"{first.name}에서 본 활동을 진행해요",
            )
        )

    # ── secondary (workshop 보너스 — 60% 시점에 휴식/식사) ──────────
    if "secondary" in by_role:
        sec_t = start_dt + timedelta(minutes=int(schedule.duration_minutes * 0.6))
        steps.append(
            PlanStep(
                time=_fmt_time(sec_t),
                place=by_role["secondary"],
                activity="잠깐 쉬면서 간식/식사 같이 해요",
                intent="활동이 길면 중간에 한 번 환기하면 좋아요",
            )
        )

    # ── wrapup (종료 −30분) ──────────────────────────────────────────
    if "wrapup" in by_role and schedule.duration_minutes >= 60:
        wrap_t = end_dt - timedelta(minutes=30)
        steps.append(
            PlanStep(
                time=_fmt_time(wrap_t),
                place=by_role["wrapup"],
                activity="마무리 정리 + 후기 한 줄씩 나누기",
                intent="끝에 가볍게 후기 나누면 다음 모임 기대감이 생겨요",
            )
        )

    # 시간 순 정렬 보장
    steps.sort(key=lambda s: s.time)
    return steps


# ---------------------------------------------------------------------------
def _parse_dt(date_iso: str, time_hhmm: str) -> datetime:
    return datetime.strptime(f"{date_iso} {time_hhmm}", "%Y-%m-%d %H:%M")


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _intent_for_meetup(skill_label: str) -> str:
    return f"처음 모이는 사이라 카페에서 {skill_label} 흐름을 짧게 안내해요"


def _intent_for_main(skill_label: str, place: ResolvedPlace) -> str:
    if place.primary_category in ("park", "activity"):
        return f"{place.name}에서 본격적으로 {skill_label} 해보기 좋아요"
    if place.primary_category in ("cafe",):
        return f"{place.name}이 자리 넓고 조용해서 {skill_label} 집중하기 좋아요"
    return f"{place.name}에서 {skill_label}를 같이 해봐요"


__all__ = ["build_plan_steps_draft"]
