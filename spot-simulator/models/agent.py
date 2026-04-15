"""AgentState dataclass — plan §2.3 Phase 1 + §3 Phase 2 + §4 Phase 3
+ peer-pivot §2-5 Phase Peer-A.

Phase 2 appended: trust_score, prev_trust, confirmed_spots, checked_in_spots,
                  noshow_spots, checked_in_for()
Phase 3 appended: trust_threshold, review_spots, saved_spots,
                  satisfaction_history
Phase Peer-A appended: skills, assets, relationships, role_preference
                       (see spot-simulator-peer-pivot-plan.md §2-5)

All additions are append-only; existing Phase 1/2/3 fields must never be
removed or renamed, or earlier phase tests will break.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models.skills import Assets, Relationship, SkillProfile


def _default_assets() -> Assets:
    """dataclass `default_factory` 용 `Assets()` 헬퍼.

    `field(default_factory=Assets)` 를 직접 쓰면 일부 타입 체커가 `Assets`
    을 call 해서 post_init 를 트리거하는 시점을 예측하기 어려워진다.
    별도 헬퍼를 두면 persona yaml 이 `assets` 를 생략한 경우에도 깔끔한
    기본값(`Assets()`) 이 보장된다.
    """

    return Assets()


@dataclass
class AgentState:
    """Per-agent mutable state carried through the tick loop.

    Fields grouped per plan §2.3:
      - identity          : agent_id, persona_type, home_region_id,
                            active_regions, interest_categories
      - dispositions      : host_score, join_score, budget_level
      - dynamic state     : fatigue, social_need, current_state
      - schedule          : schedule_weights keyed as "{day_type}_{time_slot}"
      - tracking          : last_action_tick, hosted_spots, joined_spots

    Phase 2 (plan §3) appended fields:
      - trust_score       : host reliability 0..1, seeded at 0.5
      - prev_trust        : snapshot before settlement delta, used by Phase 3
                            SettlementResult.host_trust_delta; Phase 2 keeps it
                            in sync so trust_score and prev_trust both exist.
      - confirmed_spots   : spots this agent is CONFIRMED into but not yet
                            started (cleared when spot transitions to
                            IN_PROGRESS / CHECK_IN / CANCEL_JOIN).
      - checked_in_spots  : spots this agent has CHECK_IN'd for (set for O(1)
                            membership test by `checked_in_for`).
      - noshow_spots      : spots this agent was marked NO_SHOW in.
    """

    # --- identity ---------------------------------------------------------
    agent_id: str
    persona_type: str
    home_region_id: str
    active_regions: list[str]
    interest_categories: list[str]

    # --- behavioural dispositions (set at init from persona) --------------
    host_score: float  # 0~1, tendency to create spots
    join_score: float  # 0~1, tendency to join spots

    # --- dynamic state (mutates every tick) -------------------------------
    fatigue: float      # 0~1
    social_need: float  # 0~1
    current_state: str  # "idle" | "hosting" | "joined" | "checked_in"

    # --- schedule preference ---------------------------------------------
    # Key format MUST be "{day_type}_{time_slot}", e.g. "weekday_evening".
    # See engine.time_utils.schedule_key for the canonical builder.
    schedule_weights: dict[str, float]

    # --- budget (plan §5, used by engine.budget_penalty) -----------------
    budget_level: int  # 1..3

    # --- tracking (defaults so init_agent_from_persona can omit) ---------
    last_action_tick: int = -1
    hosted_spots: list[str] = field(default_factory=list)
    joined_spots: list[str] = field(default_factory=list)

    # --- Phase 2 lifecycle + trust (append only) -------------------------
    # (Phase 2) host reliability, 0..1, seeded at 0.5 for unseen hosts.
    trust_score: float = 0.5
    # (Phase 2) snapshot of trust_score taken by engine BEFORE settlement
    # so Phase 3 can compute `host_trust_delta = trust_score - prev_trust`.
    # Phase 2 keeps this in sync (equal to trust_score at init).
    prev_trust: float = 0.5
    # (Phase 2) spot_ids the agent has CONFIRMED but not yet started.
    confirmed_spots: list[str] = field(default_factory=list)
    # (Phase 2) spot_ids the agent has CHECK_IN'd for. `set` for O(1) lookup.
    checked_in_spots: set[str] = field(default_factory=set)
    # (Phase 2) spot_ids the agent was marked NO_SHOW in.
    noshow_spots: set[str] = field(default_factory=set)

    # --- Phase 3 settlement / review / save (append only) ----------------
    # (Phase 3) minimum host trust this agent will tolerate when deciding to
    # join. Plan §4.4 calculate_satisfaction uses
    # `abs(agent.trust_threshold - host.trust_score)` as the host-trust fit
    # term. Default 0.5 mirrors the seeded `trust_score` so Phase 1/2 code
    # paths that never read this field stay neutral.
    trust_threshold: float = 0.5
    # (Phase 3) spot_ids this agent has written reviews for (WRITE_REVIEW
    # action target). Append on REVIEW_WRITTEN. List (not set) so order of
    # review writes is preserved for analysis.
    review_spots: list[str] = field(default_factory=list)
    # (Phase 3) spot_ids this agent has SAVE_SPOT'd (bookmark). Distinct from
    # joined_spots / confirmed_spots — purely a "watchlist".
    saved_spots: list[str] = field(default_factory=list)
    # (Phase 3) running log of satisfaction scores from
    # `calculate_satisfaction` (plan §4.4). Append on each settled spot the
    # agent participated in; sim-analyst-qa uses this for distribution
    # checks (mean / variance over time).
    satisfaction_history: list[float] = field(default_factory=list)

    # --- Phase Peer-A: skills / assets / relationships (append only) -----
    # peer-pivot §2-5 — 또래 강사 marketplace 결정 인풋.
    #
    # 모든 필드는 default 가 보장되어 있어 legacy Phase 1~3 코드가
    # `AgentState(agent_id=..., persona_type=..., home_region_id=..., ...)`
    # 식으로 기존 필드만 채워도 그대로 생성된다.
    #
    # - skills            : SkillTopic value(str) → SkillProfile. non-zero
    #                       entry 는 persona 당 2~6 개만 채워지고 나머지는
    #                       dict 에서 생략된다(plan §2-1 주석).
    # - assets            : Assets dataclass. wallet / time / equipment /
    #                       space / social / reputation 7 차원.
    # - relationships     : other_agent_id → Relationship. 첫 만남 시 entry
    #                       생성, 세션 완료마다 session_count /
    #                       total_satisfaction 갱신.
    # - role_preference   : "prefer_teach" | "prefer_learn" | "both". teach /
    #                       learn appetite 와 조합해 p_teach / p_learn 의
    #                       결정 bias 로 쓰인다.
    skills: dict[str, SkillProfile] = field(default_factory=dict)
    assets: Assets = field(default_factory=_default_assets)
    relationships: dict[str, Relationship] = field(default_factory=dict)
    role_preference: str = "both"

    # --- Phase 2 helper methods ------------------------------------------
    def checked_in_for(self, spot_id: str) -> bool:
        """(Phase 2) True iff this agent CHECK_IN'd for the given spot."""
        return spot_id in self.checked_in_spots
