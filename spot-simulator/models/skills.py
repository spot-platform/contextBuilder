"""Peer pivot — skill / asset / relationship / fee dataclasses.

spot-simulator-peer-pivot-plan.md §2 기반. 기존 `agent.py` / `spot.py` 를
건드리지 않고 여기에 모든 신규 도메인 타입을 모아둔다. `agent.py` /
`spot.py` 는 이 모듈을 import 해서 필드만 append-only 로 확장한다.

Phase Peer-A 에서 추가되는 타입:
  - SkillTopic       : 18 스킬 토픽 enum (plan §2-1). value 는 한국어 문자열
                       로 유지해 yaml / event payload 에 그대로 기록된다.
  - SkillProfile     : agent 별 (skill → level, teach/learn appetite, ...).
  - Assets           : 7 차원 개인 자산 (wallet, time budget, equipment,
                       space, social capital, reputation).
  - Relationship     : agent 쌍의 단골/친구 관계 (first_meet → regular →
                       mentor_bond → friend 전이).
  - FeeBreakdown     : 2 층 fee 구조 (peer_labor + passthrough).
                       plan §3-4.
  - 상한 상수         : LABOR_CAP_PER_PARTNER / SOFT_CAP_PER_PARTNER /
                       HARD_CAP_PER_PARTNER — plan §3-4 상한 규칙.

모든 dataclass 는 `__post_init__` 에서 numeric / set 필드를 clamp 하여
persona yaml 이 부정한 값을 넘기더라도 도메인 불변식을 유지한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional


# ---------------------------------------------------------------------------
# SkillTopic — plan §2-1 (18 MVP)
# ---------------------------------------------------------------------------
#
# 값은 한국어 문자열 그대로. persona_templates.yaml / skills_catalog.yaml /
# event_log payload 에 이 value 가 그대로 기록된다. sim-data-integrator 가
# yaml 로드 시점에 "yaml 의 skill key 가 SkillTopic value 집합에 속하는가"
# 를 검증한다.


class SkillTopic(StrEnum):
    """plan §2-1 18 스킬 토픽.

    6 카테고리 × 2~3 스킬 = 18. Phase B 에서 persona 별 초기 분포를 확정할 때
    이 enum 을 재사용한다. value 가 한국어라는 점은 의도적이다 — yaml 파일
    / event_log / prompt 가 모두 사람이 읽을 수 있는 라벨로 흐른다.
    """

    # 음악/악기
    GUITAR = "기타"
    UKULELE = "우쿨렐레"
    PIANO_BASIC = "피아노 기초"
    # 요리/베이킹
    HOMECOOK = "홈쿡"
    BAKING = "홈베이킹"
    COFFEE = "핸드드립"
    # 운동/신체
    RUNNING = "러닝"
    YOGA_BASIC = "요가 입문"
    CLIMBING = "볼더링"
    HIKING = "가벼운 등산"
    # 창작/예술
    DRAWING = "드로잉"
    PHOTO = "스마트폰 사진"
    CALLIGRAPHY = "캘리그라피"
    # 언어/학습
    ENGLISH_TALK = "영어 프리토킹"
    CODING_BASIC = "코딩 입문"
    # 생활
    GARDENING = "원예"
    BOARDGAME = "보드게임"
    TAROT = "타로"


# ---------------------------------------------------------------------------
# SkillProfile — plan §2-1
# ---------------------------------------------------------------------------


@dataclass
class SkillProfile:
    """Agent 가 특정 스킬에 대해 갖는 프로파일 (plan §2-1).

    Level 해석 (plan §2-1 주석):
      0 — 없음
      1 — 입문자
      2 — 기초
      3 — 숙련
      4 — 가르칠 수 있음
      5 — 자신 있게 전수

    대부분 agent 는 18 스킬 중 **non-zero 인 엔트리만 2~6 개** 보유. 나머지
    스킬은 dict 에서 생략되거나 level=0 / appetite=0 으로 채워진다.
    """

    level: int = 0
    years_exp: float = 0.0
    teach_appetite: float = 0.0  # 0~1 — 가르칠 동기
    learn_appetite: float = 0.0  # 0~1 — 배울 동기

    def __post_init__(self) -> None:
        # Clamp numeric fields so persona yaml / random seeding cannot drift
        # out of the documented ranges.
        self.level = max(0, min(5, int(self.level)))
        self.teach_appetite = max(0.0, min(1.0, float(self.teach_appetite)))
        self.learn_appetite = max(0.0, min(1.0, float(self.learn_appetite)))
        self.years_exp = max(0.0, float(self.years_exp))


# ---------------------------------------------------------------------------
# Assets — plan §2-2
# ---------------------------------------------------------------------------


@dataclass
class Assets:
    """페르소나 개인 자산 7 차원 (plan §2-2 / §8).

    plan §8 에서 설명한 대로 기존 `budget_level:int` 1차원 필드로는 또래
    강사 marketplace 에서의 "지갑 / 시간 / 장비 / 공간 / 평판" 같은
    결정 요인을 표현하지 못한다. Phase Peer-A 는 `Assets` 로 교체가 아닌
    **보강**을 하며, `AgentState.budget_level` 은 legacy 결정 경로 호환을
    위해 그대로 유지된다 (append-only 원칙).
    """

    # --- 금전 ------------------------------------------------------------
    # 월 여가 예산 (원). persona band 에 따라 6,000 ~ 60,000 범위.
    # 25,000 은 기본 persona 의 medium 값.
    wallet_monthly: int = 25_000
    # 0~1 — 용돈 벌이 동기. host 결정 시 host_score 보강에 쓰인다.
    pocket_money_motivation: float = 0.5
    # 누적 획득 / 지출. Phase D 이후 실제로 증가한다.
    earn_total: int = 0
    spent_total: int = 0

    # --- 시간 ------------------------------------------------------------
    # 주중/주말 참여 가능 tick 수. 기본값은 회사원 페르소나 기준
    # (주중 3 tick, 주말 10 tick).
    time_budget_weekday: int = 3
    time_budget_weekend: int = 10

    # --- 장비 / 공간 -----------------------------------------------------
    # 보유 장비. value 는 SkillTopic value 의 subset (문자열 set).
    # suggest_fee_breakdown 에서 `skill not in host.assets.equipment` 체크에
    # 쓰이므로 set 타입이 중요하다.
    equipment: set[str] = field(default_factory=set)
    # 0 없음 / 1 카페 미팅만 / 2 집 초대 가능 / 3 작은 스튜디오.
    space_level: int = 1
    # "none" | "cafe" | "home" | "studio" | "park" | "gym". venue_rental
    # 계산 시 참고되는 공간 정체성.
    space_type: str = "cafe"

    # --- 소셜 ------------------------------------------------------------
    # 0~1. 친구/팔로워 수 프록시. 추천(REFERRAL_SENT) 영향력을 결정한다.
    social_capital: float = 0.5
    # 0~1. 누적 평판 EMA. 매 세션 완료 시 REPUTATION_UPDATED 이벤트로
    # 갱신된다.
    reputation_score: float = 0.5

    def __post_init__(self) -> None:
        self.wallet_monthly = max(0, int(self.wallet_monthly))
        self.pocket_money_motivation = max(
            0.0, min(1.0, float(self.pocket_money_motivation))
        )
        self.earn_total = max(0, int(self.earn_total))
        self.spent_total = max(0, int(self.spent_total))
        self.time_budget_weekday = max(0, min(7, int(self.time_budget_weekday)))
        self.time_budget_weekend = max(0, min(14, int(self.time_budget_weekend)))
        self.social_capital = max(0.0, min(1.0, float(self.social_capital)))
        self.reputation_score = max(0.0, min(1.0, float(self.reputation_score)))
        self.space_level = max(0, min(3, int(self.space_level)))
        # `equipment` 는 yaml 에서 list 로 들어오는 경우가 많으므로 항상
        # set 으로 정규화한다.
        if not isinstance(self.equipment, set):
            self.equipment = set(self.equipment) if self.equipment else set()


# ---------------------------------------------------------------------------
# Relationship — plan §2-3
# ---------------------------------------------------------------------------


@dataclass
class Relationship:
    """agent 쌍 사이의 단골/친구 관계 (plan §2-3).

    agent 는 `relationships: dict[other_agent_id, Relationship]` 를 가진다.
    첫 만남에서 entry 가 생성되고, 이후 세션/상호작용마다 업데이트된다.

    관계 전이 (plan §2-3):
      first_meet  ─(session_count >= 2 & avg_sat >= 0.70)─> regular
      regular     ─(session_count >= 4 & avg_sat >= 0.80)─> mentor_bond
      mentor_bond ─(session_count >= 6 & avg_sat >= 0.85)─> friend
                                                           (evolved_to_friend=True)
    """

    other_agent_id: str
    # "first_meet" | "regular" | "mentor_bond" | "friend"
    rel_type: str = "first_meet"
    # 관계를 형성시킨 주 스킬. None 이면 "일반 친구" (skill-agnostic friend).
    skill_topic: Optional[str] = None
    session_count: int = 0
    total_satisfaction: float = 0.0
    last_interaction_tick: int = -1
    # 0~1. 다음 세션 호의도 (match_score 가산에 쓰인다).
    affinity: float = 0.5
    # FRIEND_UPGRADE 이벤트가 한 번 emit 되었는지 idempotency 플래그.
    evolved_to_friend: bool = False

    @property
    def avg_satisfaction(self) -> float:
        """누적 만족도 평균. session_count == 0 이면 0.0 (첫 만남 전)."""

        if self.session_count <= 0:
            return 0.0
        return self.total_satisfaction / self.session_count


# ---------------------------------------------------------------------------
# FeeBreakdown — plan §3-4
# ---------------------------------------------------------------------------


@dataclass
class FeeBreakdown:
    """또래 강사료 + 실비 2 층 구조 (plan §3-4).

    `Spot.fee_breakdown` 필드로 저장되며, 기존 Phase 1~3 의 `fee` 대응은
    `Spot.fee_per_partner` property 로 파생된다
    (`fee_breakdown.total // capacity`).
    """

    # 또래 강사의 "시간/노동" 대가 (순마진). LABOR_CAP_PER_PARTNER (10,000원)
    # 이 상한.
    peer_labor_fee: int = 0
    # 재료비 실비 (식재료, 물감, 씨앗 등). skills_catalog.yaml
    # material_cost_per_partner 에서 주입.
    material_cost: int = 0
    # 장소 대관료 실비 (클라이밍장, 스튜디오 등).
    venue_rental: int = 0
    # 장비 대여료 실비. host 가 장비 미보유 시에만 청구.
    equipment_rental: int = 0

    @property
    def total(self) -> int:
        """네 항목 합 — `Spot.fee_per_partner` 는 이 값을 capacity 로 나눈다."""

        return (
            self.peer_labor_fee
            + self.material_cost
            + self.venue_rental
            + self.equipment_rental
        )

    @property
    def passthrough_total(self) -> int:
        """실비 합 (labor 제외). SOFT_CAP 초과 허용 여부를 판정할 때 쓴다."""

        return self.material_cost + self.venue_rental + self.equipment_rental


# ---------------------------------------------------------------------------
# 상한 상수 — plan §3-4
# ---------------------------------------------------------------------------
#
# suggest_fee_breakdown (engine/fee.py, Phase C) 가 clip 용도로 import 하고,
# validator (synthetic-content-pipeline 의 feed rules, Phase E) 가 reject
# 임계값으로 재사용한다. 두 코드베이스 모두 여기 모듈을 truth source 로
# 참조해야 숫자가 분산되지 않는다.

#: `peer_labor_fee` 단독 상한 (원). 10,000 → 18,000 상향
#: (2025-04-15 cold-start tuning). 또래 강사라도 책임감 유지를 위해 1:1 L4
#: 세션 기준 12k~18k 가 나올 수 있어야 한다는 사용자 피드백 반영.
LABOR_CAP_PER_PARTNER: int = 18_000

#: `total` 일반 상한 (원). passthrough 없이 초과 시 reject; breakdown 명세
#: 가 있으면 HARD_CAP 까지 허용. 15,000 → 25,000 상향.
SOFT_CAP_PER_PARTNER: int = 25_000

#: `total` 절대 상한 (원). 실비 포함해도 이 이상은 또래 강사 아님.
#: 30,000 → 45,000 상향 (클라이밍장 대관+장비 대여 1:1 같은 고비용 케이스 허용).
HARD_CAP_PER_PARTNER: int = 45_000


# ---------------------------------------------------------------------------
# SkillRequest — plan §3-request (Offer vs Request dual path)
# ---------------------------------------------------------------------------
#
# 학생(learner)이 "OO 배우고 싶어요" 요청을 먼저 게시하고, 호스트가 그에
# 응답해 teach-spot 이 생성되는 경로. 매칭 이후 lifecycle 은 offer 경로와
# 동일하지만 `Spot.origination_mode == "request_matched"` 로 기록되어
# content pipeline 이 리뷰/메시지의 voice(능동 주체) 를 구분한다.
#
# 이 dataclass 는 Phase B 의 `engine/request_lifecycle.py` 가 사용하고,
# synthetic-content-pipeline 의 `content_spec_builder` (Phase D) 가 event_log
# 에서 origination_request_id 로 역조회해 content_spec 에 주입한다.


@dataclass
class SkillRequest:
    """Pre-spot 학생 요청. `Spot` 이 생성되기 전 단계의 "배우고 싶어요" 게시글.

    상태 머신:
        OPEN → MATCHED (호스트 응답 → Spot 생성)
        OPEN → EXPIRED (wait_deadline_tick 도달까지 응답 없음)
        OPEN → CANCELED (learner 가 취소, Phase C 확장용)
    """

    #: 요청 고유 id ("R_0001" 형식). engine 이 할당.
    request_id: str
    #: 학생 agent id — origination_agent_id 의 source of truth.
    learner_agent_id: str
    #: 배우고 싶은 SkillTopic value (한국어 enum value).
    skill_topic: str
    #: 요청이 올라온 지역.
    region_id: str
    #: 요청 게시 tick.
    created_at_tick: int
    #: 학생 지갑 기반 fee 상한. host 가 제시하려는 fee 가 이 값을 넘으면
    #: `p_respond_to_request` 는 0 을 반환한다.
    max_fee_per_partner: int
    #: 선호 teach_mode ("1:1" | "small_group" | "workshop").
    preferred_teach_mode: str = "small_group"
    #: 선호 venue_type ("cafe" | "home" | "park" | "studio" | "gym" | "online").
    preferred_venue: str = "cafe"
    #: 마감 tick. 이 tick 까지 응답 없으면 EXPIRED 전이.
    wait_deadline_tick: int = -1

    # --- 상태 머신 --------------------------------------------------------
    #: "OPEN" | "MATCHED" | "EXPIRED" | "CANCELED"
    status: str = "OPEN"
    #: MATCHED 일 때 생성된 Spot id.
    matched_spot_id: str | None = None
    #: MATCHED tick.
    matched_at_tick: int | None = None
    #: MATCHED 일 때 응답한 host agent_id.
    respondent_agent_id: str | None = None
    #: 중복 응답 거절 이력 (Phase C 확장 — 학생이 먼저 host 를 거절한 경우).
    rejected_respondent_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.max_fee_per_partner = max(0, int(self.max_fee_per_partner))
        if self.status not in ("OPEN", "MATCHED", "EXPIRED", "CANCELED"):
            self.status = "OPEN"
