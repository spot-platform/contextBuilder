# Spot Simulator — Peer-Instructor Pivot Plan (Tier 3)

> spot-simulator 의 근본 도메인 모델을 **venue 기반 meetup** 에서
> **또래 강사(peer-instructor) 스킬 마켓플레이스** 로 피벗한다.
> 동시에 페르소나에게 **개인 자산(스킬/지갑/시간/장비/공간/관계)** 을
> 주입하여 supporter ↔ partner 관계 다이나믹스를 event_log 에 기록한다.

---

## 1. 배경 & 문제

### 1-1. 제품 DNA (재확인)

이 플랫폼은 **또래 강사 marketplace** 다.

- 호스트(supporter) 는 **프로 강사가 아님**. "나 기타 칠 줄 아는데" 수준의 또래.
- 목적: **용돈 벌이 + 취미 공유 + 친구 만들기**.
- 파트너(participant)는 "비싼 수업 말고 편하게 배우고 싶다" 는 또래.
- 톤: "알려드릴게요" X / "같이 해볼래요" O.

### 1-2. 현재 시뮬레이터의 격차

현재 spot-simulator 는 **venue 기반 meetup** 모델이다:

| 층위 | 현재 | 제품 DNA 격차 |
|------|------|-------------|
| persona.preferred_categories | `["food","cafe","bar","exercise","nature","culture"]` | venue 타입이지 **스킬 토픽이 아님** |
| p_create / p_join | host_score × region_affinity × category_match | "가르칠 의향" 개념 없음 |
| Spot | host_agent_id, category, capacity | **skill_topic, fee, host_skill_level 부재** |
| EventLog payload | 대부분 `{}` | 관계/용돈/장비 정보 0 |
| Relationship | **없음** | supporter↔partner 단골 관계 모델 부재 |
| Personal assets | budget_level 1 필드만 | 지갑/장비/공간/시간/소셜자본 부재 |

이 격차 때문에 content-pipeline 에 기록되는 ContentSpec.category 가
`food/nature/exercise` 로만 나오고, LLM 은 "저녁 식사 모임" / "자연 산책" 같은
일반 meetup 을 만든다. "수원 화서동 기타 같이 쳐볼 분 5천원" 같은 콘텐츠는
모델이 그 개념을 한 번도 만난 적이 없어서 나올 수 없다.

### 1-3. 해결 전략

**venue category 를 삭제하는 것이 아니라, 스킬 토픽(skill_topic) 이 1축,
장소 타입(venue) 이 보조축으로 공존** 시킨다. 스팟은 `{skill_topic, venue, level}`
의 3-tuple 로 정체성을 가진다. 예:

- `{guitar, home, 1:1 초급}` — 호스트 집에서 1:1 기타 입문
- `{running, park, 그룹 3~5명}` — 공원 러닝 크루 같이 해보기
- `{baking, cafe, 4명 워크숍}` — 카페 한 켠 빌려서 홈베이킹

페르소나는 **스킬별 보유/학습 의향** 을 벡터로 갖고,
**지갑/장비/공간/시간/소셜 자본** 을 별도 자산 구조에 담는다.
**관계 (relationships)** 는 agent 별 dict 로 관리되며 매 세션 후 업데이트된다.

---

## 2. 새 도메인 모델

### 2-1. Skill 시스템

```python
class SkillTopic(StrEnum):
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
```

(MVP 18개. Phase B 에서 persona 별 초기 분포만 확정.)

```python
@dataclass
class SkillProfile:
    level: int             # 0 없음, 1 입문자, 2 기초, 3 숙련, 4 가르칠 수 있음, 5 자신 있게 전수
    years_exp: float        # 경험 연수 (프롬프트에 활용)
    teach_appetite: float   # 0~1 — 이 스킬을 가르치고 싶은 동기
    learn_appetite: float   # 0~1 — 이 스킬을 배우고 싶은 동기
```

각 agent 는 `skills: dict[SkillTopic, SkillProfile]` 를 가진다.
대부분 entry 는 level=0 / 둘 appetite 0, **non-zero 인 엔트리만 2~6개**.

### 2-2. Personal Assets

```python
@dataclass
class Assets:
    # 금전
    wallet_monthly: int              # 월 여가 예산 (원). 6,000 ~ 60,000
    pocket_money_motivation: float   # 용돈 벌이 동기 0~1
    earn_total: int                  # 누적 획득 (Phase D 이후 반영)
    spent_total: int                 # 누적 지출

    # 시간
    time_budget_weekday: int         # 주중 참여 가능 tick 수 (0~7)
    time_budget_weekend: int          # 주말 참여 가능 tick 수 (0~14)

    # 장비 / 공간
    equipment: set[str]               # 보유 장비 (SkillTopic 이름 subset)
    space_level: int                  # 0 없음, 1 카페 미팅만, 2 집 초대 가능, 3 작은 스튜디오
    space_type: str                   # "none" | "cafe" | "home" | "studio" | "park"

    # 소셜
    social_capital: float             # 0~1 — 친구/팔로워 수 프록시. 추천 영향력
    reputation_score: float           # 0~1 — 누적 평판. 매 세션 후 업데이트
```

### 2-3. Relationships

```python
@dataclass
class Relationship:
    other_agent_id: str
    rel_type: str                    # "first_meet" | "regular" | "mentor_bond" | "friend"
    skill_topic: str | None
    session_count: int
    total_satisfaction: float         # 누적 합 (평균 = / session_count)
    last_interaction_tick: int
    affinity: float                   # 0~1 다음 세션 호의도
    evolved_to_friend: bool
```

agent 는 `relationships: dict[other_agent_id, Relationship]` 를 가진다.
첫 만남에서는 entry 가 생성되고, 이후 세션/상호작용마다 업데이트.
**관계 전이**:

```
first_meet  ──(session_count >= 2 + sat >= 0.7)──> regular
regular     ──(session_count >= 4 + sat >= 0.8)──> mentor_bond
mentor_bond ──(session_count >= 6 + sat >= 0.85)──> friend (evolved_to_friend=True)
```

### 2-4. Spot 확장

```python
@dataclass
class Spot:
    # 기존 Phase 1~3 필드 전부 유지 ...

    # 신규 Phase A 필드
    skill_topic: str                 # SkillTopic value
    host_skill_level: int            # supporter 의 해당 스킬 level
    fee_per_partner: int             # partner 1 인 당 받는 용돈 (원)
    required_equipment: list[str]     # 필요 장비 — 부재 시 partner 지참 조건
    venue_type: str                   # "cafe" | "home" | "studio" | "park" | "online"
    is_followup_session: bool         # 단골 partner 대상 후속 세션 여부
    bonded_partner_ids: list[str]     # 이 세션이 mentor_bond / friend 관계의 N 회차인 partner 목록
    teach_mode: str                   # "1:1" | "small_group" | "workshop"
```

### 2-5. AgentState 확장

```python
@dataclass
class AgentState:
    # 기존 필드 전부 유지 (fatigue, social_need, trust_score, home_region_id, ...)

    # 신규
    skills: dict[str, SkillProfile] = field(default_factory=dict)
    assets: Assets = field(default_factory=Assets)
    relationships: dict[str, Relationship] = field(default_factory=dict)
    role_preference: str = "both"   # "prefer_teach" | "prefer_learn" | "both"
```

### 2-6. 새 이벤트 타입

기존 event_type 은 그대로 유지하고 다음을 **append-only** 로 추가:

| Event | 발화 주체 | payload |
|-------|---------|--------|
| `SKILL_SIGNAL` | agent | `{skill, role: "offer"|"request", motivation: 0~1}` |
| `CREATE_TEACH_SPOT` | host | `{skill, fee, teach_mode, venue_type}` (CREATE_SPOT 확장) |
| `JOIN_TEACH_SPOT` | partner | `{skill, is_follower: bool}` |
| `SKILL_TRANSFER` | host→partners | `{skill, level_gain: 0.0~0.3}` — 수업 중 1회 |
| `BOND_UPDATED` | host-partner pair | `{from: rel_type, to: rel_type, sessions: N}` |
| `FRIEND_UPGRADE` | pair | `{skill, sessions: N, avg_sat: 0~1}` |
| `REFERRAL_SENT` | agent_a → agent_b | `{host, skill, reason}` |
| `EQUIPMENT_LENT` | lender→borrower | `{equipment, duration_ticks}` |
| `POCKET_MONEY_EARNED` | host | `{amount, spot_id, partner_count}` |
| `REPUTATION_UPDATED` | agent | `{delta, new_score}` |

event_log.jsonl 스키마는 하위 호환: `agent_id / event_id / event_type / payload / region_id / spot_id / tick` 그대로. **payload 에 실제 값이 들어간다** 는 점만 달라짐.

---

## 3. 의사결정 엔진 피벗

### 3-1. 기존 공식 (Phase 1~3)

```
p_create = host_score × time_weight × region_affinity × fatigue_mod
p_join   = join_score × time_weight × category_match × budget_penalty × fatigue_mod
```

### 3-2. 신규 공식

```
p_teach(skill s, tick t) =
    agent.skills[s].teach_appetite              # 이 스킬 가르칠 동기
    × (agent.skills[s].level / 5)               # 실력 (5 미만은 낮음)
    × agent.assets.pocket_money_motivation      # 용돈 벌이 동기
    × (1 - fatigue / max_fatigue)               # 피로도
    × time_availability(agent, t)               # 주중/주말 예산 잔여
    × space_capability(agent, s)                # 공간 가능 여부 (home 있으면 +20%)
    × region_create_affinity(region, s)         # 지역별 스킬 수요 프록시

p_learn(skill s, tick t) =
    agent.skills[s].learn_appetite              # 이 스킬 배우고 싶은 동기
    × (1 - agent.skills[s].level / 5)           # 이미 잘하면 배울 동기 낮음
    × budget_capability(agent, fee)             # wallet 대비 fee 비율
    × (1 - fatigue / max_fatigue)
    × time_availability(agent, t)

p_join_bonded(partner → host, skill) =
    relationship.affinity × 2.0                 # 단골 관계면 2배 가산
    if relationship.rel_type in {"regular","mentor_bond","friend"}
    else 1.0

p_refer(agent_a, agent_b, host) =
    agent_a.assets.social_capital
    × relationship(agent_a, host).affinity
    × (1 if avg_satisfaction(agent_a, host) >= 0.8 else 0)

p_friend_upgrade(pair) =
    0.3 if (session_count >= 6 AND avg_sat >= 0.85)
    else 0.0
```

### 3-3. 매칭 함수

```python
def find_matchable_spot(learner: AgentState, spots: list[Spot], tick: int) -> Spot | None:
    """learner 가 가장 선호하는 OPEN 상태 teach-spot 을 찾는다.

    우선순위:
      1. bonded relationship 이 있고 그 host 가 연 teach-spot (가산 2x)
      2. learn_appetite 가 높은 skill 의 spot
      3. region 근접 + time 적합
      4. fee 가 wallet_monthly 대비 무리 없음 (< 30%)
      5. required_equipment 모두 agent.assets.equipment 또는 공용 소유
    """
```

### 3-4. Fee 공식 (2층 구조: peer_labor + passthrough)

또래 강사의 핵심 원칙은 "프로 강사료 X, 용돈 벌이 O" 이지만,
**클라이밍장 대관 / 베이킹 재료 / 꽃 / 스튜디오 시간** 같은 실비는
또래 강사라도 피할 수 없다. 이를 반영해 fee 를 **순수 노동료 + 실비 pass-through**
2층으로 분리한다.

```python
@dataclass
class FeeBreakdown:
    peer_labor_fee: int          # 또래 강사의 "시간/노동" 대가 (순마진)
    material_cost: int           # 재료비 실비 (식재료, 물감, 씨앗 등)
    venue_rental: int            # 장소 대관료 실비 (클라이밍장, 스튜디오 등)
    equipment_rental: int        # 장비 대여료 실비 (부재 시에만 청구)

    @property
    def total(self) -> int:
        return (self.peer_labor_fee + self.material_cost
                + self.venue_rental + self.equipment_rental)

    @property
    def passthrough_total(self) -> int:
        return self.material_cost + self.venue_rental + self.equipment_rental
```

Spot 에는 `fee_breakdown: FeeBreakdown` 필드로 전체 내역을 기록하고,
기존 `fee_per_partner` 는 `breakdown.total // partner_count` 로 파생한다.

```python
def suggest_fee_breakdown(
    host: AgentState,
    skill: SkillTopic,
    teach_mode: str,
    venue_type: str,
    expected_partners: int,
) -> FeeBreakdown:
    """또래 강사료 + 실비를 분리 계산."""

    # ── 1. peer_labor_fee (순수 또래 강사 노동료) ──────────────────
    # teach_mode 별 기본값 + level_mod + motivation_mod
    base_labor = {
        "1:1":         7000,   # 집중도 높음
        "small_group": 4000,   # 2~4명
        "workshop":    2500,   # 5명+
    }[teach_mode]

    level_mod = 0.6 + host.skills[skill].level * 0.15     # L3→1.05x, L5→1.35x
    motivation_mod = 0.8 + host.assets.pocket_money_motivation * 0.4
    peer_labor_fee = int(base_labor * level_mod * motivation_mod)
    peer_labor_fee = min(peer_labor_fee, LABOR_CAP_PER_PARTNER)  # 상한 10,000원

    # ── 2. material_cost (스킬별 재료비 실비, skills_catalog.yaml 기준) ──
    material_cost = SKILLS_CATALOG[skill].material_cost_per_partner
    # 예: 홈베이킹 = 4,500원, 드로잉 = 3,000원, 원예 = 5,000원, 기타/러닝 = 0원

    # ── 3. venue_rental (장소 대관료, host.space_level 에 따라) ──────
    if venue_type == "home" or venue_type == "park":
        venue_rental = 0                   # 호스트 집/공원 = 무료
    elif venue_type == "cafe":
        venue_rental = 2000 // expected_partners   # 음료 1잔 수준
    elif venue_type == "studio":
        venue_rental = SKILLS_CATALOG[skill].studio_rental_total // expected_partners
        # 예: 스튜디오 요가 20,000원 / 4명 = 5,000원/인
    elif venue_type == "gym":
        venue_rental = SKILLS_CATALOG[skill].gym_rental_total // expected_partners
        # 예: 볼더링장 14,000원 / 3명 = 4,667원/인

    # ── 4. equipment_rental (장비 대여, host 가 장비 없으면) ──────────
    equipment_rental = 0
    if skill not in host.assets.equipment:
        equipment_rental = SKILLS_CATALOG[skill].equipment_rental_per_partner

    return FeeBreakdown(
        peer_labor_fee=peer_labor_fee,
        material_cost=material_cost,
        venue_rental=venue_rental,
        equipment_rental=equipment_rental,
    )
```

### 상한 규칙 (3단 게이트)

| 상한 | 값 | 대상 | 위반 시 |
|------|---|------|--------|
| `LABOR_CAP_PER_PARTNER` | 10,000원 | `peer_labor_fee` 단독 | simulator 측 suggest 에서 clip. validator 에서 reject |
| `SOFT_CAP_PER_PARTNER` | 15,000원 | `total` | passthrough 없이 초과 시 reject. breakdown 명세 있으면 허용 |
| `HARD_CAP_PER_PARTNER` | 30,000원 | `total` | 초과 시 무조건 reject. 실비 포함해도 이 이상은 또래 강사 아님 |

validator 쪽 규칙 (Phase E):
- `peer_labor_fee > 10,000` → reject (프로 강사 가격)
- `total > 15,000` && `passthrough_total == 0` → reject (실비 없이 노동료만 비쌈)
- `total > 30,000` → reject (장르 이탈)
- `total <= 15,000` → 통과 (가장 흔한 케이스)
- `15,000 < total <= 30,000` && `passthrough_total > 0` → 통과, 단 콘텐츠 프롬프트에서
  "실비 OO원 포함" 명시 강제

### 스킬 카탈로그 (`config/skills_catalog.yaml`) 예시

```yaml
기타:
  material_cost_per_partner: 0
  studio_rental_total: 0
  gym_rental_total: 0
  equipment_rental_per_partner: 3000   # 기타 없는 partner 에게 대여
  default_venue: "cafe"
  teach_mode_distribution: { "1:1": 0.6, "small_group": 0.3, "workshop": 0.1 }

홈베이킹:
  material_cost_per_partner: 4500      # 밀가루 + 버터 + 토핑
  studio_rental_total: 0
  equipment_rental_per_partner: 0
  default_venue: "home"
  teach_mode_distribution: { "1:1": 0.3, "small_group": 0.6, "workshop": 0.1 }

볼더링:
  material_cost_per_partner: 0
  studio_rental_total: 0
  gym_rental_total: 14000              # 시간당 볼더링장 3인 기준
  equipment_rental_per_partner: 2500   # 클라이밍 슈즈 대여
  default_venue: "gym"
  teach_mode_distribution: { "1:1": 0.2, "small_group": 0.7, "workshop": 0.1 }

원예:
  material_cost_per_partner: 5000      # 씨앗 / 흙 / 작은 화분
  studio_rental_total: 0
  equipment_rental_per_partner: 0
  default_venue: "home"

드로잉:
  material_cost_per_partner: 3000      # 종이 / 연필 / 파스텔
  equipment_rental_per_partner: 0
  default_venue: "home"

러닝:
  material_cost_per_partner: 0
  venue_rental: 0                       # 공원
  equipment_rental_per_partner: 0
  default_venue: "park"

영어 프리토킹:
  material_cost_per_partner: 0
  equipment_rental_per_partner: 0
  default_venue: "cafe"
  # 카페 venue_rental 은 공식에서 자동 계산 (2000 // partners)

# ... 18 스킬 전부
```

### Fee 예시 (설계 검증)

| 스팟 | mode | venue | labor | material | venue | equip | total | 판정 |
|------|------|-------|-------|----------|-------|-------|-------|------|
| 기타 1:1 (카페) | 1:1 | cafe | 7,000 | 0 | 2,000 | 3,000 (parter 기타 X) | **12,000** | ✅ soft_cap 내 |
| 기타 1:1 (카페, 본인 기타) | 1:1 | cafe | 7,000 | 0 | 2,000 | 0 | **9,000** | ✅ 쾌적 |
| 홈베이킹 4명 (집) | small_group | home | 4,500 | 4,500 | 0 | 0 | **9,000** | ✅ |
| 볼더링 3명 | small_group | gym | 4,500 | 0 | 4,667 | 2,500 | **11,667** | ✅ |
| 볼더링 1:1 | 1:1 | gym | 7,000 | 0 | 14,000 | 2,500 | **23,500** | ⚠ soft 초과, passthrough 있음 → 허용 |
| 드로잉 5명 워크숍 | workshop | home | 2,500 | 3,000 | 0 | 0 | **5,500** | ✅ |
| 스튜디오 요가 4명 | small_group | studio | 4,000 | 0 | 5,000 | 0 | **9,000** | ✅ |
| 원예 3명 (집) | small_group | home | 4,500 | 5,000 | 0 | 0 | **9,500** | ✅ |

이 표가 Phase B 게이트 분포 검증의 기준이 된다.

### 3-5. Relationship update

매 session settlement 후:

```python
def update_relationship(host, partner, spot, satisfaction):
    rel = host.relationships.get(partner.agent_id) or Relationship(...)
    rel.session_count += 1
    rel.total_satisfaction += satisfaction
    rel.last_interaction_tick = tick
    rel.affinity = _recompute_affinity(rel)

    # 전이
    avg = rel.total_satisfaction / rel.session_count
    if rel.rel_type == "first_meet" and rel.session_count >= 2 and avg >= 0.7:
        rel.rel_type = "regular"
        emit("BOND_UPDATED", {...})
    elif rel.rel_type == "regular" and rel.session_count >= 4 and avg >= 0.8:
        rel.rel_type = "mentor_bond"
        emit("BOND_UPDATED", {...})
    elif rel.rel_type == "mentor_bond" and rel.session_count >= 6 and avg >= 0.85:
        if rng.random() < 0.3:
            rel.rel_type = "friend"
            rel.evolved_to_friend = True
            emit("FRIEND_UPGRADE", {...})

    # 대칭 업데이트: partner 도 host 에 대한 rel 생성
    update_symmetric(partner, host, ...)
```

### 3-counter. Counter-offer (역제안) 플로우 — peer marketplace signature

또래 강사 marketplace 의 핵심 차별점 중 하나는 **모집 인원 유연 협상**. 프로
강사 클래스는 정가제이지만 또래 강사는 "5명 목표였는데 3명 모였네요, 그래도
진행할래요?" 같은 negotiation 을 한다. 이 플로우를 시뮬레이터/로그에 심으면
content pipeline 이 "가격 재조정 후 3명이서 진행" 같은 lived detail review 를
자동 생성할 수 있다.

#### 트리거 조건

```
spot.status == OPEN                                  AND
current_partner_count < spot.target_partner_count    AND
current_partner_count >= spot.min_viable_count       AND
tick >= spot.wait_deadline_tick                      AND
not spot.counter_offer_sent
→ send_counter_offer()
```

#### 재계산 공식

```python
def recompute_fee_for_smaller_group(
    original: FeeBreakdown,
    host: AgentState,
    skill: str,
    teach_mode: str,
    venue_type: str,
    new_partner_count: int,
) -> FeeBreakdown:
    """passthrough 실비는 **총액 고정**. partner 수가 줄어들면 1인당 분담이 늘어남.
    peer_labor_fee 는 host 가 유연 조정 가능 (minor discount 로 가격 부담 완충)."""

    # passthrough_total 은 원본 그대로 (재료/대관은 총액 고정)
    passthrough_total = original.passthrough_total
    # peer_labor 은 약간 할인 (partner 수 줄었으니 host 도 양보)
    new_labor = int(original.peer_labor_fee * 0.85)
    # capacity 분배 변화는 fee_per_partner property 가 자동 처리

    return FeeBreakdown(
        peer_labor_fee=new_labor,
        material_cost=original.material_cost,
        venue_rental=original.venue_rental,
        equipment_rental=original.equipment_rental,
    )
```

partner 1 인당 요금 변화 예:
- 원래: 5명, total 50,000 (labor 25k + passthrough 25k) → 1인 10,000
- 재조정: 3명, total 46,250 (labor 21.25k + passthrough 25k) → 1인 약 15,417

#### Partner 응답 공식

```python
def p_accept_counter_offer(partner: AgentState, spot: Spot) -> float:
    new_per_partner = spot.fee_per_partner
    original_per_partner = (
        spot.original_fee_breakdown.total // max(1, spot.target_partner_count)
    )
    fee_delta_ratio = new_per_partner / original_per_partner  # 보통 1.3~2.0

    affordability = min(1.0, partner.assets.wallet_monthly / (new_per_partner * 3))
    relationship_boost = 0.0
    rel = partner.relationships.get(spot.host_agent_id)
    if rel:
        relationship_boost = rel.affinity * 0.3  # 단골이면 수용 가능성 ↑

    price_penalty = max(0.0, (fee_delta_ratio - 1.0)) * 0.4
    p = affordability * 0.6 + relationship_boost - price_penalty
    return max(0.1, min(0.9, p))
```

#### 응답 수집 → 최종 판정

```python
def finalize_counter_offer(spot: Spot, tick: int, rng):
    """counter_offer_sent_tick + 3 tick 내에 응답 수집 → 재합성."""
    if tick < spot.counter_offer_sent_tick + 3:
        return  # 아직 대기

    accepted, rejected = [], []
    for pid in list(spot.participants):
        partner = get_agent(pid)
        if rng.random() < p_accept_counter_offer(partner, spot):
            accepted.append(pid)
            emit("COUNTER_OFFER_ACCEPTED", {"partner_id": pid, "new_fee": spot.fee_per_partner})
        else:
            rejected.append(pid)
            emit("COUNTER_OFFER_REJECTED", {"partner_id": pid, "reason": "budget"})

    # renegotiation_history 기록
    spot.renegotiation_history.append({
        "tick": tick,
        "from_count": spot.target_partner_count,
        "to_count": len(accepted),
        "from_total": spot.original_fee_breakdown.total,
        "to_total": spot.fee_breakdown.total,
        "accepted_by": accepted,
        "rejected_by": rejected,
    })

    if len(accepted) >= spot.min_viable_count:
        spot.participants = accepted
        spot.capacity = len(accepted)  # 실제 진행 인원 반영
        spot.status = SpotStatus.MATCHED
        emit("SPOT_RENEGOTIATED", {
            "renegotiation_count": len(spot.renegotiation_history),
            "final_total": spot.fee_breakdown.total,
            "final_partner_count": len(accepted),
        })
        # BOND: 수락한 partner 는 affinity +0.05 (호스트와의 관계 강화)
        for pid in accepted:
            bump_affinity(pid, spot.host_agent_id, +0.05)
    else:
        spot.status = SpotStatus.CANCELED
        emit("SPOT_TIMEOUT", {"reason": "counter_offer_rejected"})
```

#### 이벤트 4종 (§2-6 PHASE_PEER_EVENT_TYPES 에 추가)

- `COUNTER_OFFER_SENT` `{from_count, to_count, original_total, new_total}`
- `COUNTER_OFFER_ACCEPTED` `{partner_id, new_fee}`
- `COUNTER_OFFER_REJECTED` `{partner_id, reason}`
- `SPOT_RENEGOTIATED` `{renegotiation_count, final_total, final_partner_count}`

#### 분포 지표 (Phase F 게이트 추가)

| # | 항목 | 목표 |
|---|------|-----|
| C1 | counter_offer 발동률 | 전체 OPEN spot 의 5~15% |
| C2 | 수락률 (accept / sent) | ≥ 50% |
| C3 | 재협상 후 CONFIRMED 비율 | ≥ 70% (수락한 spot 기준) |
| C4 | 평균 fee 상승 폭 | 1.3x~2.0x |

---

### 3-request. Offer vs Request Dual Path — 학생 주도 경로

또래 강사 marketplace 의 또 다른 signature 는 **양방향 origination**. 호스트가
먼저 모집하는 offer 와 학생이 먼저 요청을 올리는 request 두 경로가 공존한다.
매칭 이후 lifecycle 은 동일하지만, **voice (능동 주체)** 가 다르므로 content
pipeline 이 리뷰/메시지 톤을 구분해 생성한다.

#### 두 경로의 차이

| 항목 | Offer 경로 | Request 경로 |
|-----|-----------|-------------|
| 시작 엔티티 | `Spot` (호스트 직접 생성) | `SkillRequest` (학생 게시) |
| 시작 이벤트 | `CREATE_TEACH_SPOT` | `CREATE_SKILL_REQUEST` |
| 매칭 이벤트 | `JOIN_TEACH_SPOT` (partner 참여) | `SUPPORTER_RESPONDED` → `CREATE_TEACH_SPOT` (origination_mode="request_matched") + learner 자동 참여 |
| voice (능동 주체) | 호스트 | 학생 |
| Spot.origination_mode | "offer" | "request_matched" |
| Spot.origination_agent_id | host_agent_id | learner_agent_id |
| 리뷰 톤 | "선생님이 모집하신 거 봤어요" | "제가 배우고 싶다고 올렸는데 선생님이 답해주셨어요" |

#### SkillRequest 상태 머신

```
OPEN ──host 응답 (p_respond_to_request)──> MATCHED → Spot 생성
OPEN ──wait_deadline 도달 (응답 없음)──> EXPIRED
OPEN ──learner 취소──> CANCELED
```

#### 학생 요청 게시 공식

```python
def p_post_request(learner: AgentState, skill: str, tick: int) -> float:
    """학생이 skill 에 대해 SkillRequest 를 올릴 확률.

    role_preference="prefer_learn" 또는 "both" 면 높음, "prefer_teach" 면 낮음.
    wallet > max_fee 조건.
    """
    sp = learner.skills.get(skill)
    if not sp or sp.learn_appetite < 0.3:
        return 0.0
    role_mod = {"prefer_learn": 1.2, "both": 1.0, "prefer_teach": 0.2}[learner.role_preference]
    return (
        sp.learn_appetite
        * role_mod
        * (1 - learner.fatigue / MAX_FATIGUE)
        * time_availability(learner, tick)
    )
```

#### 호스트 응답 공식

```python
def p_respond_to_request(host: AgentState, request: SkillRequest, tick: int) -> float:
    """호스트가 open request 에 응답할 확률. p_teach 와 유사하지만
    budget_capability 는 request.max_fee_per_partner 기반."""

    sp = host.skills.get(request.skill_topic)
    if not sp or sp.level < level_floor_to_teach(request.skill_topic):
        return 0.0

    # 호스트가 제시할 fee 계산 후 learner 예산 초과 체크
    fee_breakdown = suggest_fee_breakdown(
        host, request.skill_topic, request.preferred_teach_mode,
        request.preferred_venue, expected_partners=3,
    )
    proposed_per_partner = fee_breakdown.total // 3
    if proposed_per_partner > request.max_fee_per_partner:
        return 0.0  # 예산 초과 → 응답 불가

    relationship_boost = 1.0
    rel = host.relationships.get(request.learner_agent_id)
    if rel:
        relationship_boost += rel.affinity * 0.5  # 단골 학생이면 1.5배

    return (
        sp.teach_appetite
        * host.assets.pocket_money_motivation
        * relationship_boost
        * (1 - host.fatigue / MAX_FATIGUE)
    )
```

#### tick 루프 확장

```python
def process_open_requests(agents, open_requests, tick, rng):
    """매 tick 호출. 호스트 후보들이 open request 스캔."""
    for request in list(open_requests):
        if request.status != "OPEN":
            continue
        if tick >= request.wait_deadline_tick:
            request.status = "EXPIRED"
            emit("REQUEST_EXPIRED", {"request_id": request.request_id,
                                     "reason": "no_response"})
            continue

        # 호스트 후보 필터링
        candidates = [
            a for a in agents
            if a.agent_id != request.learner_agent_id
            and a.skills.get(request.skill_topic)
            and a.skills[request.skill_topic].teach_appetite > 0
            and a.assets.reputation_score > 0.3
        ]
        # 지역 가중치로 정렬 (learner 와 같은 home_region 우선)
        candidates.sort(
            key=lambda a: 0 if a.home_region_id == request.region_id else 1
        )

        for host in candidates:
            if rng.random() < p_respond_to_request(host, request, tick):
                spot = create_teach_spot_from_request(host, request, tick)
                spot.origination_mode = "request_matched"
                spot.origination_agent_id = request.learner_agent_id
                spot.originating_request_id = request.request_id
                spot.responded_at_tick = tick
                spot.participants.append(request.learner_agent_id)  # 자동 join

                request.status = "MATCHED"
                request.matched_spot_id = spot.spot_id
                request.matched_at_tick = tick
                request.respondent_agent_id = host.agent_id

                emit("SUPPORTER_RESPONDED", {
                    "request_id": request.request_id,
                    "host_agent_id": host.agent_id,
                    "proposed_fee": spot.fee_per_partner,
                    "spot_id": spot.spot_id,
                })
                emit("CREATE_TEACH_SPOT", {
                    "skill": spot.skill_topic,
                    "fee": spot.fee_per_partner,
                    "teach_mode": spot.teach_mode,
                    "venue_type": spot.venue_type,
                    "origination_mode": "request_matched",
                })
                break  # 한 request 에 한 host 만 매칭
```

#### 이벤트 3종 (§2-6 PHASE_PEER_EVENT_TYPES 에 추가)

- `CREATE_SKILL_REQUEST` `{request_id, skill, max_fee, mode, venue, deadline_tick}`
- `SUPPORTER_RESPONDED` `{request_id, host_agent_id, proposed_fee, spot_id}`
- `REQUEST_EXPIRED` `{request_id, reason: "no_response"|"learner_canceled"}`

#### ContentSpec 확장 (Phase D)

```python
class ContentSpec(BaseModel):
    ...
    origination_mode: str                       # "offer" | "request_matched"
    originating_voice: str                       # "host" | "learner"  (파생)
    originating_request_summary: str | None = None
    # request_matched 경로일 때 Learner 의 원 요청 요약
    # (max_fee, preferred_venue 등을 한 문장으로)
```

#### 프롬프트 확장 (Phase E)

detail/plan/messages/review v2 공통 상단에 origination-aware 블록:

```jinja2
{% if origination_mode == "request_matched" %}
## 이 스팟의 출생 배경 (voice: learner)
이 스팟은 **파트너(학생)가 먼저 "배우고 싶어요" 요청을 올렸고**, 호스트가
그에 응답해서 만들어진 수업이다.
- 원 요청 요약: "{{ originating_request_summary }}"
- 응답한 호스트: {{ host_persona.type }}

**voice 가이드**:
- 파트너 쪽 voice ("제가 요청 올렸는데 선생님이 답해주셨어요") 를 review/message
  에 자연스럽게 반영.
- 호스트 쪽 voice ("요청 보고 반가워서 답했어요") 도 messages 에 한두 번.
- "먼저 제안해주셔서 감사" 류 문구는 피하라 — 주도권이 파트너.
{% else %}
## 이 스팟의 출생 배경 (voice: host)
이 스팟은 **호스트가 먼저 모집글을 올린** offer 다. 파트너들은 그 모집글을
보고 신청한 것이므로 voice 는 호스트 주도적.
- "선생님이 모집 올린 수업에 참여했어요" 류 표현 권장.
{% endif %}
```

#### 분포 지표 (Phase F 게이트 추가)

| # | 항목 | 목표 |
|---|------|-----|
| R1 | learner-originated spot 비율 | 15~40% (offer 60~85%) |
| R2 | SkillRequest EXPIRED 비율 | ≤ 40% |
| R3 | request → host 응답 평균 소요 tick | ≤ 12 tick |
| R4 | request 경로의 단골 매칭 비율 | ≥ 20% (bonded learner→host 재응답) |
| R5 | request_matched spot 의 avg_satisfaction | offer 대비 +/-10% 이내 (voice 차이가 품질엔 영향 없어야) |

### 3-6. Reputation & 소셜 자본

매 settlement:

```python
host.assets.reputation_score = 0.9 * host.assets.reputation_score + 0.1 * avg_satisfaction
if rng.random() < rel.affinity * host.assets.social_capital:
    # 추천 발화 — 3rd agent 에게 REFERRAL_SENT 이벤트
    target = pick_friend(partner)
    emit("REFERRAL_SENT", ...)
```

`social_capital` 은 Phase B 에서 초기값만 부여하고, Phase C 에서 실 이벤트 발화 기반 업데이트를 추가.

---

## 4. 페르소나 재정의

기존 5 페르소나는 유지하되, **각자의 스킬 포트폴리오 + 자산 프로파일** 을
`persona_templates.yaml` 에 추가한다. 예시:

```yaml
night_social:
  host_score: 0.70
  join_score: 0.75
  home_region: "emd_yeonmu"
  role_preference: "both"
  skills:
    홈쿡:            { level: 3, years_exp: 2.0, teach: 0.65, learn: 0.20 }
    핸드드립:        { level: 4, years_exp: 4.0, teach: 0.80, learn: 0.10 }
    보드게임:        { level: 3, years_exp: 3.0, teach: 0.55, learn: 0.25 }
    기타:            { level: 0, years_exp: 0.0, teach: 0.00, learn: 0.70 }
    드로잉:          { level: 1, years_exp: 0.5, teach: 0.05, learn: 0.55 }
  assets:
    wallet_monthly: 30000
    pocket_money_motivation: 0.75
    time_budget_weekday: 3
    time_budget_weekend: 10
    equipment: ["핸드드립", "보드게임"]
    space_level: 2
    space_type: "home"
    social_capital: 0.65
    reputation_score: 0.5

weekend_explorer:
  ...
  skills:
    러닝:            { level: 4, teach: 0.75, learn: 0.15 }
    가벼운 등산:     { level: 4, teach: 0.70, learn: 0.20 }
    스마트폰 사진:   { level: 3, teach: 0.55, learn: 0.30 }
    영어 프리토킹:   { level: 2, teach: 0.25, learn: 0.50 }
  assets:
    wallet_monthly: 40000
    pocket_money_motivation: 0.55
    time_budget_weekend: 12
    equipment: ["러닝", "가벼운 등산", "스마트폰 사진"]
    space_level: 1  # 공원/카페 선호
    space_type: "park"
    social_capital: 0.55

planner:
  skills:
    드로잉:          { level: 4, teach: 0.70, learn: 0.10 }
    캘리그라피:      { level: 4, teach: 0.65, learn: 0.15 }
    코딩 입문:       { level: 3, teach: 0.45, learn: 0.25 }
    요가 입문:       { level: 2, teach: 0.10, learn: 0.60 }
  assets:
    wallet_monthly: 50000
    pocket_money_motivation: 0.40  # 돈보다 취향 공유
    equipment: ["드로잉", "캘리그라피", "코딩 입문"]
    space_level: 2
    space_type: "home"
    social_capital: 0.45

spontaneous:
  skills:
    우쿨렐레:        { level: 3, teach: 0.60, learn: 0.25 }
    홈쿡:            { level: 2, teach: 0.25, learn: 0.55 }
    보드게임:        { level: 4, teach: 0.75, learn: 0.20 }
    볼더링:          { level: 2, teach: 0.20, learn: 0.65 }
  assets:
    wallet_monthly: 22000
    pocket_money_motivation: 0.85  # 용돈 벌이 강함
    equipment: ["우쿨렐레", "보드게임"]
    space_level: 1
    space_type: "cafe"
    social_capital: 0.70

homebody:
  skills:
    홈베이킹:        { level: 3, teach: 0.40, learn: 0.30 }
    원예:            { level: 4, teach: 0.55, learn: 0.20 }
    타로:            { level: 3, teach: 0.45, learn: 0.25 }
    요가 입문:       { level: 2, teach: 0.15, learn: 0.50 }
  assets:
    wallet_monthly: 15000
    pocket_money_motivation: 0.50
    equipment: ["홈베이킹", "원예", "타로"]
    space_level: 2
    space_type: "home"
    social_capital: 0.35
```

**설계 원칙**:
- 각 persona 는 **3~5개 non-zero 스킬** (teach 쪽 1~3개, learn 쪽 1~3개 중복 가능)
- 지역별 density 도 skill 기반으로 보강: `region_features.json` 에 `skill_density_*` 추가 (Phase B-2)
- homebody 가 낮은 wallet 이지만 풍부한 장비로 호스트 가능 → 현실감

### 4-1. 페르소나 확장성 (신규 persona 쉽게 추가)

기존 5 persona 는 MVP 에서 유지하되, **추후 persona 1개를 드롭인으로 추가** 할 수
있도록 base template + YAML anchor 패턴으로 작성한다:

```yaml
# persona_templates.yaml

# ── 공통 default (anchor) ──────────────────────────────────────
_base_persona: &base_persona
  role_preference: "both"
  time_budget_weekday: 3
  time_budget_weekend: 10
  reputation_score: 0.5
  social_capital: 0.5
  pocket_money_motivation: 0.50
  wallet_monthly: 25000
  space_level: 1
  space_type: "cafe"
  equipment: []
  skills: {}      # 빈 dict. persona 측에서 override
  # fatigue/social_need 계수는 simulator core 가 별도로 관리

# ── MVP 5 persona ──────────────────────────────────────────────
night_social:
  <<: *base_persona
  host_score: 0.70
  join_score: 0.75
  home_region: "emd_yeonmu"
  wallet_monthly: 30000
  pocket_money_motivation: 0.75
  space_level: 2
  space_type: "home"
  social_capital: 0.65
  equipment: ["핸드드립", "보드게임"]
  skills:
    홈쿡:     { level: 3, years_exp: 2.0, teach: 0.65, learn: 0.20 }
    핸드드립: { level: 4, years_exp: 4.0, teach: 0.80, learn: 0.10 }
    보드게임: { level: 3, years_exp: 3.0, teach: 0.55, learn: 0.25 }
    기타:    { level: 0, years_exp: 0.0, teach: 0.00, learn: 0.70 }

weekend_explorer:
  <<: *base_persona
  host_score: 0.50
  join_score: 0.80
  home_region: "emd_sinchon"
  wallet_monthly: 40000
  space_type: "park"
  social_capital: 0.55
  equipment: ["러닝", "가벼운 등산", "스마트폰 사진"]
  skills:
    러닝:         { level: 4, teach: 0.75, learn: 0.15 }
    가벼운 등산:  { level: 4, teach: 0.70, learn: 0.20 }
    스마트폰 사진:{ level: 3, teach: 0.55, learn: 0.30 }
    영어 프리토킹:{ level: 2, teach: 0.25, learn: 0.50 }

planner:
  <<: *base_persona
  host_score: 0.60
  join_score: 0.40
  home_region: "emd_jangan"
  wallet_monthly: 50000
  pocket_money_motivation: 0.40
  space_level: 2
  space_type: "home"
  equipment: ["드로잉", "캘리그라피", "코딩 입문"]
  skills:
    드로잉:      { level: 4, teach: 0.70, learn: 0.10 }
    캘리그라피:  { level: 4, teach: 0.65, learn: 0.15 }
    코딩 입문:   { level: 3, teach: 0.45, learn: 0.25 }
    요가 입문:   { level: 2, teach: 0.10, learn: 0.60 }

spontaneous:
  <<: *base_persona
  host_score: 0.75
  join_score: 0.65
  home_region: "emd_yeonmu"
  wallet_monthly: 22000
  pocket_money_motivation: 0.85     # 용돈 벌이 강함
  social_capital: 0.70
  space_type: "cafe"
  equipment: ["우쿨렐레", "보드게임"]
  skills:
    우쿨렐레: { level: 3, teach: 0.60, learn: 0.25 }
    홈쿡:     { level: 2, teach: 0.25, learn: 0.55 }
    보드게임: { level: 4, teach: 0.75, learn: 0.20 }
    볼더링:   { level: 2, teach: 0.20, learn: 0.65 }

homebody:
  <<: *base_persona
  host_score: 0.15
  join_score: 0.25
  home_region: "emd_sinchon"
  wallet_monthly: 15000             # 낮지만 장비 풍부
  space_level: 2
  space_type: "home"
  social_capital: 0.35
  equipment: ["홈베이킹", "원예", "타로"]
  skills:
    홈베이킹: { level: 3, teach: 0.40, learn: 0.30 }
    원예:     { level: 4, teach: 0.55, learn: 0.20 }
    타로:     { level: 3, teach: 0.45, learn: 0.25 }
    요가 입문:{ level: 2, teach: 0.15, learn: 0.50 }
```

### 4-2. 새 persona 드롭인 추가 방법

5명 이후에 페르소나를 추가하려면:

**방법 A — 같은 yaml 파일에 추가** (가장 간단):
```yaml
# persona_templates.yaml 맨 아래에 append
side_hustler:                        # 용돈 벌이 집중형 예시
  <<: *base_persona
  host_score: 0.85
  join_score: 0.30
  home_region: "emd_yeonmu"
  wallet_monthly: 18000
  pocket_money_motivation: 0.95      # 극단적 용돈 벌이
  social_capital: 0.40
  space_type: "cafe"
  equipment: ["기타", "우쿨렐레"]
  skills:
    기타:     { level: 4, teach: 0.85, learn: 0.10 }
    우쿨렐레: { level: 3, teach: 0.70, learn: 0.15 }
    영어 프리토킹: { level: 3, teach: 0.60, learn: 0.20 }

multi_learner:                       # 호기심 많은 학습자 예시
  <<: *base_persona
  host_score: 0.25
  join_score: 0.90
  wallet_monthly: 45000
  pocket_money_motivation: 0.15      # 배우는 쪽
  social_capital: 0.60
  skills:
    기타:     { level: 1, teach: 0.00, learn: 0.80 }
    드로잉:   { level: 1, teach: 0.00, learn: 0.75 }
    홈베이킹: { level: 0, teach: 0.00, learn: 0.60 }
    러닝:     { level: 1, teach: 0.05, learn: 0.55 }
    보드게임: { level: 2, teach: 0.15, learn: 0.50 }
```

**방법 B — 별도 파일로 추가** (외부 파일 드롭인):
```yaml
# config/personas/side_hustler.yaml  (신규 디렉토리)
<<: *base_persona
host_score: 0.85
...
```

- `sim-data-integrator` 의 loader 가 `config/persona_templates.yaml` 뿐 아니라
  `config/personas/*.yaml` 전체를 glob 스캔해 merge 하는 코드를 포함
- 신규 persona 파일만 떨어뜨리면 시뮬레이션 재실행 시 자동 인식

**방법 C — 런타임 주입** (실험용):
- `simulation_config.yaml` 의 `personas_override` 키로 특정 persona 비중 또는 추가
  persona 정의 inline 주입

MVP 범위: 방법 A + B 를 Phase A 에서 구현. C 는 선택.

### 4-3. 불변식 (새 persona 추가 시 반드시 지켜야 할 것)

1. `_base_persona` anchor 사용 (필수 필드 누락 방지)
2. `skills` dict 의 key 는 `SkillTopic` enum value 와 정확히 일치 (오타 시 loader 에러)
3. `equipment` list 의 원소는 `SkillTopic` value 중 하나
4. `home_region` 은 `region_features.json` 에 존재하는 `region_id`
5. `skills` 합계 teach_appetite + learn_appetite 는 모든 스킬 각각 0~1 범위
6. non-zero skill 개수 3~6개 권장 (많으면 편향 낮아짐, 적으면 행동 편향 심해짐)
7. `wallet_monthly` 는 10,000~60,000 범위 (시뮬 안정성)
8. `pocket_money_motivation` 0~1

loader 가 실행 시점에 이 불변식을 검증하고, 위반 persona 는 로드 거부 + warning 로그.

---

## 5. Phase 분할 (구현 순서)

총 6 Phase, 각 Phase 마다 에이전트 팀이 병렬/순차로 작업. 각 Phase 끝에
**sim-analyst-qa (또는 pipeline-qa)** 가 게이트 판정.

### Phase A — 도메인 모델 기반 (Day 1~2)
**목표**: 새 dataclass + enum + persona_templates.yaml 확장. 기존 엔진은
임시로 Phase 1~3 공식을 쓰되 새 필드는 empty/default 주입.

**에이전트**: `sim-model-designer` (메인), `sim-data-integrator` (persona yaml)

**산출물**:
- `spot-simulator/models/skills.py` — SkillTopic enum, SkillProfile, Assets, Relationship dataclass
- `spot-simulator/models/agent.py` 확장 (skills/assets/relationships 필드)
- `spot-simulator/models/spot.py` 확장 (skill_topic/fee/... 8개 필드)
- `spot-simulator/config/persona_templates.yaml` — 5 persona × 스킬/자산
- `spot-simulator/config/skills_catalog.yaml` — 18 SkillTopic + 기본 fee band
- `_workspace/sim_02_models/peer_pivot_delta.md`

**게이트**:
- `AgentState()` / `Spot()` default 인스턴스화 가능
- persona_templates 로드 시 5 persona 전부 skills/assets 파싱
- 기존 Phase 1~3 테스트 회귀 0 (새 필드 default 로만)

### Phase B — 의사결정 엔진 피벗 (Day 3~5)
**목표**: p_teach / p_learn / p_join_bonded / suggest_fee / find_matchable_spot 구현.
기존 p_create / p_join 을 호환 모드로 남기고 신규 경로를 우선.

**에이전트**: `sim-engine-engineer` (메인), `sim-data-integrator` (region skill density)

**산출물**:
- `spot-simulator/engine/decision.py` 재작성 — p_teach/p_learn, find_matchable_spot
- `spot-simulator/engine/fee.py` (신규) — suggest_fee, budget_capability
- `spot-simulator/engine/time_availability.py` (신규)
- `spot-simulator/data/region_features.json` 확장 — `skill_density_{topic}` 벡터 (persona 집계 기반)
- `spot-simulator/engine/runner.py` — tick 루프에 skill decision 경로 추가
- `_workspace/sim_03_engine/peer_pivot_probability_table.md`

**게이트**:
- 50 agents × 48 tick × Phase B 공식만으로 시뮬레이션 1회 성공
- 스킬 토픽별 CREATE_TEACH_SPOT 분포 측정 — 상위 5 토픽이 전체의 60~80%
- fee 분포 측정 — 3,000~15,000 범위 95% 이상
- agent 별 role 경향 측정 — prefer_teach / prefer_learn / both 가 극단 쏠림 없음

### Phase C — 관계 다이나믹스 (Day 6~7)
**목표**: Relationship 상태 전이 + BOND_UPDATED / FRIEND_UPGRADE / REFERRAL_SENT
이벤트 발화 + p_join_bonded 반영.

**에이전트**: `sim-engine-engineer`, `sim-model-designer` (이벤트 카탈로그), `sim-analyst-qa`

**산출물**:
- `spot-simulator/engine/relationships.py` (신규) — update_relationship, transition_rules
- `spot-simulator/engine/settlement.py` 확장 — relationship update + referral emission
- `spot-simulator/models/event.py` — 신규 이벤트 타입 카탈로그 추가 (Phase4 dict)
- `_workspace/sim_03_engine/relationship_transitions.md`

**게이트**:
- 3일치 시뮬 후 first_meet → regular 전환률 ≥ 15%
- regular → mentor_bond 전환률 ≥ 5%
- FRIEND_UPGRADE 이벤트 ≥ 1 건 (tune 가능)
- REFERRAL_SENT 이벤트 ≥ 전체 session 의 5%
- p_join_bonded 가산이 실제 join 편향에 반영 (단골 partner 의 반복 참여 비율 ↑)

### Phase D — Event log 포맷 확장 & content_spec_builder 업데이트 (Day 8)
**목표**: event_log.jsonl 에 새 payload 값 실제 기록 + synthetic-content-pipeline
의 content_spec_builder 가 신규 필드를 추출.

**에이전트**: `sim-engine-engineer` (simulator 쪽 emit), `pipeline-infra-architect` (builder 쪽)

**산출물**:
- `spot-simulator/engine/executors.py` — 각 event emit 에 payload 값 주입
- `synthetic-content-pipeline/src/pipeline/spec/models.py` 확장:
  ```python
  class ContentSpec(BaseModel):
      # 기존 필드 유지
      ...
      # 신규
      skill_topic: str
      host_skill_level: int
      fee_per_partner: int
      teach_mode: str
      venue_type: str
      is_followup_session: bool
      bonded_partner_count: int
      peer_tone_required: bool = True
  ```
- `synthetic-content-pipeline/src/pipeline/spec/builder.py` 전면 재작성
  - 새 이벤트 타입 파싱
  - spot 별 payload 추출 (skill_topic, fee 등)
  - 기존 venue category 추론 로직은 legacy path 로 이름만 바꿔 보존

**게이트**:
- 새 event_log 에서 ContentSpec 생성 시 skill_topic 이 non-null 인 비율 ≥ 95%
- fee_per_partner 범위 3,000~15,000 만족
- 기존 Phase 1~3 synthetic-content-pipeline stub 테스트 회귀 0

### Phase E — 프롬프트 / Generator / Validator / Critic 재작성 (Day 9)
**목표**: 콘텐츠 파이프라인 프롬프트 6개에 제품 DNA 주입. persona_tones 재작성.
validator 에 프로톤 금기어 추가. scoring 에 peer_tone_fit 가중치.

**에이전트**: `content-generator-engineer`, `validator-engineer`, `codex-bridge-engineer` (critic 스키마)

**산출물**:
- `config/prompts/feed/v1.j2` → **v2.j2** — 제품 DNA 블록 + skill_topic 변수 사용
- 동일하게 `detail/v2.j2`, `plan/v2.j2`, `messages/v2.j2`, `review/v2.j2`, `critic/v2.j2`
- `src/pipeline/generators/persona_tones.py` 전면 재작성 — 또래 말투 예시 (페르소나 5종 × 3~5 문장)
- `config/rules/feed_rules.yaml` 등 — 금기어 추가 ("전문 강사", "원데이 클래스", "자격증", "수업료")
- `src/pipeline/validators/rules.py` — fee 상한 15,000 체크, peer-tone rule
- `src/pipeline/validators/scoring.py` — 가중치 재조정:
  ```
  0.25 naturalness
  0.20 consistency
  0.15 persona_fit
  0.10 region_fit
  0.05 business_rule_fit
  0.10 diversity
  0.15 peer_tone_fit    # 신규
  ```
- `src/pipeline/llm/schemas/critic.json` — `peer_tone_score` 필드 추가 (strict mode 준수)

**게이트**:
- 6 프롬프트 Jinja2 StrictUndefined 컴파일 통과
- 5 종 generator stub 모드 정상 동작
- 기존 128 passed regression 유지

### Phase F — 검증 / 지표 재측정 / 샘플 확인 (Day 10~11)
**목표**: 시뮬레이터 분포 검증 + pipeline §14 지표 재측정 + live 샘플 1~3건 확인.

**에이전트**: `sim-analyst-qa`, `pipeline-qa`

**산출물**:
- `spot-simulator/analysis/validate_peer.py` (신규) — 스킬 분포 / 관계 전이율 / fee 분포 / 용돈 수익 분포 검증
- `synthetic-content-pipeline/tests/test_end_to_end_peer.py` — peer spec 기반 goldens 3~5개
- `data/goldens/specs/peer_*.json` 신규 goldens
- `_workspace/scp_05_qa/phase_peer_report.md`
- Live 샘플: `기타 / 홈쿡 / 러닝` 3 스킬 각 1 spot 씩 live codex 호출 → 결과 첨부

**게이트**:
- 시뮬레이터 분포 검증 7 항목 (skill/관계/fee/수익/공간/시간/평판) 전부 PASS
- §14 live 지표 ≥ 5/7 PASS (Phase 3 기준 유지)
- Live 3 sample 모두 "또래 강사 톤" 육안 확인

---

## 6. 에이전트 팀 재정립

기존 10 에이전트 중 **8 명이 관여**. 새 에이전트는 추가하지 않는다 (역할 확장만).

### spot-simulator 팀 (5명)

| 에이전트 | Phase | 역할 확장 |
|---------|-------|---------|
| `sim-model-designer` | A, C | SkillTopic/SkillProfile/Assets/Relationship dataclass 신규. AgentState/Spot 필드 append-only 확장. 기존 Phase 1~3 필드는 전부 유지. |
| `sim-engine-engineer` | B, C, D | p_create/p_join 는 legacy 로 유지하되 p_teach/p_learn/p_join_bonded 신규. relationships 업데이트 + 새 event emit. **공식 튜닝은 sim-analyst-qa 피드백 기반**. |
| `sim-data-integrator` | A, B | persona_templates.yaml 에 skills/assets 주입. skills_catalog.yaml 신규. region_features.json 에 `skill_density_*` 확장. |
| `sim-infra-architect` | A | 디렉토리 변경 없음 (기존 `spot-simulator/` 구조 유지). pyproject 업데이트만 (필요 시). |
| `sim-analyst-qa` | B, C, F | `validate_peer.py` 신규. 스킬/관계/fee/수익 분포 게이트. 기존 validate_phase{1,2,3} 은 legacy 로 유지. |

### synthetic-content-pipeline 팀 (4명)

| 에이전트 | Phase | 역할 확장 |
|---------|-------|---------|
| `pipeline-infra-architect` | D | `content_spec_builder` 전면 재작성. ContentSpec 에 peer 필드 추가 (pydantic append-only). DB 스키마는 변경 없음 (기존 6 테이블로 충분). |
| `content-generator-engineer` | E | 6 프롬프트 v2 재작성 + persona_tones 전면 재작성. 또래 말투 가이드 주입. |
| `validator-engineer` | E | rules/feed_rules.yaml 등에 프로톤 금기어 + fee 상한. scoring.py 에 `peer_tone_fit` 가중치. |
| `codex-bridge-engineer` | E | `critic.json` 에 `peer_tone_score` 필드 추가 (strict mode 준수). critic 프롬프트 v2 에 peer_tone 평가 항목. |
| `pipeline-qa` | F | goldens 재작성 + §14 재측정 + live 3 sample. |

### 역할 경계 — 무엇을 건드리지 않는가

- `local-context-builder` — 이번 pivot 에서 **건드리지 않음**. real_spot_count 어댑터 작업은 Post-Pivot 으로 미룸.
- `synthetic-content-pipeline` 의 Publisher/VersionManager — Phase 4 완료 상태 그대로. peer pivot 이후에도 synthetic_* 테이블과 전환 FSM 은 재사용.
- `spot-simulator` Phase 1~3 기존 공식 — legacy path 로 보존. 신규 path 가 기본값이 되고 legacy 는 flag 로 호출 가능.

---

## 7. 마이그레이션 전략

### 7-1. 데이터

- 기존 `spot-simulator/output/event_log.jsonl` (55,338 events) 는
  **`event_log_legacy_v1.jsonl` 로 이름 변경해서 보존**.
- 새 event_log 는 Phase B/C 가 끝난 시점에 재생성.
- `synthetic-content-pipeline/data/goldens/` 의 기존 7 spec + 3 bundle 도
  `goldens_legacy/` 로 이동. Phase F 에서 peer 기반 goldens 새로 작성.

### 7-2. DB

- `content_version_policy` 에 `v1_legacy` 와 `v2_peer` 두 dataset_version 을 유지.
- 기존 synthetic\_\* row 는 `dataset_version='v1_legacy'` 로 남기고
  peer pivot 결과는 `dataset_version='v2_peer'` 로 insert.
- Phase F 게이트 통과 후 VersionManager.activate("v2_peer") 로 자동 전환.

### 7-3. 코드

- **Append-only 원칙**: Phase 1~3 필드/함수 삭제 금지. legacy flag 로 호출 가능하도록.
- `engine/decision.py` 는 `mode="legacy" | "peer"` 파라미터를 받아 분기.
- 테스트는 기존 Phase 1~3 테스트 + 신규 peer 테스트가 **공존**. 둘 다 passed 해야 게이트 통과.

### 7-4. 실험 flag

```python
# spot-simulator/config/simulation_config.yaml
simulation_mode: "peer"   # "legacy" | "peer"
```

legacy 모드로 돌리면 Phase 1~3 공식. peer 모드가 기본값.

---

## 8. 개인 자산 설계 세부 (질문 "개인 자산 넣게 할 수 있어?" 답변)

**결론: 네, `Assets` dataclass 로 구현하면 매우 자연스럽게 들어갑니다.**

### 8-1. 왜 자산이 의미 있는가

현재 simulator 는 `budget_level` 1개 int 필드만 있어서
"persona 가 돈이 많다/적다" 라는 1차원 정보만 있습니다. 하지만 또래 강사 marketplace 에서는:

- **지갑(wallet_monthly)** — 얼마 fee 까지 감당 가능한가 → join 결정
- **용돈 동기(pocket_money_motivation)** — 수익 vs 취미 공유 중 어느 쪽이 강한가 → 호스트 여부
- **시간(time_budget_*)** — 주중/주말 언제 가능한가 → 스케줄링
- **장비(equipment)** — 특정 스킬 호스트 가능 여부 ("기타 없으면 기타 레슨 못 엶")
- **공간(space_level / space_type)** — home / cafe / park / studio. 1:1 수업 가능한지
- **소셜 자본(social_capital)** — 친구/팔로워 프록시. 추천 영향력
- **평판(reputation_score)** — 누적 평가. 후속 join 확률에 영향

이 7 차원이 있으면:
- "homebody 는 wallet 적지만 home 공간 + baking 장비로 호스트"
- "spontaneous 는 wallet 중간이지만 pocket_money_motivation 높아서 적극 호스트"
- "weekend_explorer 는 러닝 전문이지만 실내 스킬은 배움"
같은 **현실적이고 다양한 행동** 이 자동으로 나옵니다.

### 8-2. 관계 시스템이 함께 있어야 하는 이유

자산만 넣으면 단발 세션만 반복됩니다. **관계(Relationship)** 가 있어야:
- "3번째 수업인데 이번엔 친구 됐어요" 류 리뷰 가능
- "단골 partner 에게 다른 스킬도 가르쳐줌" 류 cross-skill 이벤트
- "친구가 이 호스트 추천해줘서 왔어요" 류 REFERRAL 기반 첫 만남
- "수업 후 같이 저녁 먹으러 감" 류 관계 전환 이벤트

이 모든 것이 event_log 에 기록되면 **content-pipeline 이 리뷰/메시지/detail 을 훨씬 다채롭게 생성**할 수 있습니다. 예:

> "벌써 세 번째 기타 수업인데 오늘 처음으로 아주 작은 곡 하나 끝까지 쳤어요.
> 선생님도 또래여서 수업 끝나고 같이 떡볶이 먹으러 갔습니다. 다음엔 친구
> 초대해서 4명이서 같이 하기로 했어요."

이런 review 는 지금 scheme 으로는 불가능하지만, Relationship.session_count +
FRIEND_UPGRADE 이벤트 + REFERRAL_SENT 가 있으면 자연스럽게 나옵니다.

### 8-3. MVP 범위

Phase A/B/C 에서 모든 7 자산 차원을 넣되, **업데이트 로직은 최소화**:

| 자산 | Phase A 초기값 | Phase B 업데이트 | Phase C 업데이트 |
|------|---------------|----------------|----------------|
| wallet_monthly | persona yaml | 세션 참여 시 fee 차감 | 월 리셋 (선택) |
| pocket_money_motivation | persona yaml | 고정 | 고정 |
| time_budget_* | persona yaml | tick 소비 차감 | - |
| equipment | persona yaml | 고정 | EQUIPMENT_LENT 로 일시 추가 |
| space_level | persona yaml | 고정 | - |
| social_capital | persona yaml | - | REFERRAL_SENT 수신 시 +0.02 |
| reputation_score | 0.5 초기값 | 세션 후 EMA 업데이트 | - |

**공식**:
- 세션 참여 시: `partner.wallet -= spot.fee_per_partner`
- 세션 정산 시: `host.wallet += spot.fee_per_partner × len(partners)`
- 세션 정산 시: `host.reputation = 0.9 × host.reputation + 0.1 × avg_sat`
- Referral 수신 시: `target.social_capital += 0.02`
- 월 리셋 (Phase B 선택 사항): 매 336 tick 마다 wallet_monthly 리셋

---

## 9. 리스크 & 완화

| 리스크 | 영향 | 완화 |
|-------|-----|-----|
| 기존 Phase 1~3 테스트 대량 실패 | 회귀 | append-only + legacy flag 유지, CI 에서 두 모드 병행 |
| 새 공식이 분포 편향 (skill 1개에 쏠림) | 게이트 F 미통과 | Phase B 종료 시 sim-analyst-qa 가 분포 검증 → 편향 시 persona_templates 재튜닝 |
| content_spec_builder 이중화 복잡도 | pipeline 코드 증가 | legacy builder 는 별 파일로 분리, mode flag 로 선택 |
| Relationship 이벤트 폭발 (N² 관계) | 성능 | Phase C 에서 `relationships` 에 LRU 제한 (agent 당 20 까지) |
| LLM 프롬프트가 또래 톤을 일관되게 따르지 못함 | §14 지표 하락 | critic.json 에 peer_tone_score 추가 + scoring 가중 0.15 → critic 이 감점 |
| fee 상한 15,000 이 너무 타이트 | 현실감 저하 | Phase F 게이트에서 재조정, persona_templates 에 high_earner 페르소나 추가 검토 |
| local-context-builder 연동 지연 | real_spot_count 없음 | 이번 pivot 범위 외. real_content_threshold 는 수동 튜닝으로 대체 |

---

## 10. 성공 지표 (Phase F 게이트)

### 10-1. 시뮬레이터 분포 검증

| # | 항목 | 목표 |
|---|------|-----|
| 1 | 스킬 토픽 분포 (상위 5개 비중) | 60~80% |
| 2 | persona 별 role 편중 | prefer_teach/learn/both 중 하나가 ≥ 70% 인 persona ≤ 1 개 |
| 3a | `peer_labor_fee` 분포 | 2,500~10,000 범위 ≥ 95% |
| 3b | `total` fee 분포 | `soft_cap(15,000)` 이내 ≥ 80%, `hard_cap(30,000)` 이내 ≥ 99% |
| 3c | passthrough 비중 | 전체 fee 의 0~60% (스킬별 차이 인정) |
| 4 | first_meet → regular 전환률 | ≥ 15% |
| 5 | regular → mentor_bond 전환률 | ≥ 5% |
| 6 | FRIEND_UPGRADE 이벤트 수 | ≥ 1 건 (tune) |
| 7 | REFERRAL_SENT 이벤트 수 | ≥ 세션의 5% |
| 8 | 호스트 누적 수익 (peer_labor 기준) | 상위 30% 호스트가 전체 labor 수익의 60% |
| 9 | wallet 고갈로 인한 join 거절률 | 10~30% |
| 10 | equipment 부재로 인한 create 거절률 | 5~20% |
| 11 | 볼더링/스튜디오 같은 고비용 venue 스팟 | 전체 스팟의 5~15% (지나치게 많으면 장르 이탈) |

### 10-2. 콘텐츠 파이프라인 §14 지표

Phase 3 기준 그대로 유지 (≥ 5/7 PASS):

| 지표 | 목표 |
|------|-----|
| 1차 승인률 | ≥ 70% |
| 최종 승인률 | ≥ 95% |
| 평균 quality | ≥ 0.80 |
| diversity | ≤ 0.60 |
| 호출수 | ≤ 15 |
| 시간 | ≤ 30s (인프라 한계, 현실화 여지) |
| critic 비율 | ≤ 20% |

### 10-3. 육안 검증

Live 모드로 3 스팟 × 5 content = 15 건을 생성한 뒤 **사용자가 직접 review**:

- "기타 1:1" 스팟 → fee 5~8천원, 톤이 "친구끼리" 느낌
- "홈쿡 소그룹" 스팟 → "같이 해볼래요" 톤, host 집 공간 언급
- "러닝 그룹" 스팟 → 공원 만남, 장비 X, 신청 메시지가 가벼움

---

## 11. 일정 요약

| Phase | 내용 | 기간 | 주 에이전트 |
|-------|------|-----|----------|
| A | 도메인 모델 | 1~2일 | model-designer, data-integrator |
| B | 의사결정 엔진 | 2~3일 | engine-engineer, data-integrator |
| C | 관계 다이나믹스 | 1~2일 | engine-engineer, model-designer, analyst-qa |
| D | event_log + spec_builder | 1일 | engine-engineer, pipeline-infra-architect |
| E | 프롬프트 + validator + critic | 1일 | content-generator, validator, codex-bridge |
| F | 검증 + live 샘플 | 2일 | analyst-qa, pipeline-qa |
| **총** | | **~9일** | 8 에이전트 |

실질 자동화 기준 약 3~5일 분량.

---

## 12. 플랜 승인 & 다음 행동

승인 시 구현 진입 순서:

1. 본 플랜 문서 확정 (`spot-simulator-peer-pivot-plan.md`)
2. 기존 `event_log.jsonl` → `event_log_legacy_v1.jsonl` 백업
3. Phase A 에이전트 2명 (model-designer + data-integrator) 병렬 실행
4. Phase A 게이트 통과 후 Phase B 진입
5. 매 Phase 완료 시 분포 지표 report → 사용자 확인 지점

필요하면:
- **페르소나 추가** (현재 5명 외 "용돈벌이 집중형" / "호기심 많은 멀티러너" 등)
- **skill_topic 추가 삭제** (MVP 18개에서 조정)
- **fee 상한 재조정** (15,000 → 20,000 등)

이 3 가지는 Phase A 시작 전 확정해두는 게 좋다.

---

**작성 목적**: Tier 3 peer-instructor pivot 의 스코프/리스크/에이전트 역할을
사용자와 합의하기 위함. 실제 구현은 플랜 승인 후 착수.
