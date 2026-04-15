# Spot Agent-Based Simulation Runtime — 구현 계획서

## 0. 설계 원칙

이 계획은 하나의 핵심 원칙을 따른다: **최소 동작 루프를 먼저 증명하고, 점진적으로 현실성을 높인다.**

전체 시스템을 한 번에 만들지 않는다. Phase마다 "돌아가는 시뮬레이션"이 존재하고, 각 Phase가 이전 Phase의 로그 품질을 검증한 뒤에만 다음으로 넘어간다.

---

## 1. 스케일 파라미터 확정 (Phase 진입 전 필수)

구현 방식은 에이전트 수 × tick 수로 결정된다. 먼저 이걸 못 박아야 한다.

| 파라미터 | Phase 1 | Phase 2 | Phase 3 (목표) |
|---------|---------|---------|---------------|
| 에이전트 수 | 50 | 500 | 5,000+ |
| tick 해상도 | 1시간 | 1시간 | 1시간 |
| 시뮬 기간 | 2일 (48 tick) | 1주 (168 tick) | 2주 (336 tick) |
| 총 결정 루프 | 2,400 | 84,000 | 1,680,000+ |
| 행동 종류 | 3개 | 7개 | 11개 전체 |
| 타겟 실행 시간 | < 5초 | < 30초 | < 3분 |

Phase 1에서 50명 × 48tick = 2,400번 결정이면 Python으로도 충분하다. Phase 3에서 168만 루프가 되면 numpy vectorization이나 Rust 바인딩을 검토해야 한다. 하지만 Phase 1에서 이걸 걱정할 필요 없다.

---

## 2. Phase 1 — 최소 동작 루프 (MVP Loop)

### 2.1 목표

> 에이전트 50명이 2일간 CREATE_SPOT / JOIN_SPOT / NO_ACTION 세 가지 행동만 하고, event_log가 쌓이는 것을 확인한다.

이것만 되면 시뮬레이터의 뼈대가 증명된 것이다.

### 2.2 구현 순서

```
Step 1: 데이터 모델 정의
Step 2: Agent 초기화
Step 3: Tick 루프 구현
Step 4: 행동 결정 함수 (3개만)
Step 5: Event Log Writer
Step 6: 검증 & 로그 출력
```

### 2.3 데이터 모델

#### AgentState

```python
@dataclass
class AgentState:
    agent_id: str
    persona_type: str           # night_social, weekend_explorer, etc.
    home_region_id: str
    active_regions: list[str]
    interest_categories: list[str]

    # 행동 성향 (초기화 시 고정, persona에서 파생)
    host_score: float           # 0~1, 스팟 만들 성향
    join_score: float           # 0~1, 참여 성향

    # 동적 상태 (tick마다 변함)
    fatigue: float              # 0~1
    social_need: float          # 0~1
    current_state: str          # idle | hosting | joined | checked_in

    # 시간 선호
    schedule_weights: dict      # {"weekday_morning": 0.1, "weekday_night": 0.9, ...}

    # 추적
    last_action_tick: int
    hosted_spots: list[str]
    joined_spots: list[str]
```

#### Spot

```python
@dataclass
class Spot:
    spot_id: str
    host_agent_id: str
    region_id: str
    category: str
    capacity: int
    min_participants: int
    scheduled_tick: int         # 실행 예정 tick
    status: str                 # OPEN | MATCHED | CANCELED
    participants: list[str]
    created_at_tick: int
```

#### EventLog

```python
@dataclass
class EventLog:
    event_id: str
    tick: int
    event_type: str             # CREATE_SPOT | JOIN_SPOT | NO_ACTION
    agent_id: str
    spot_id: str | None
    region_id: str
    payload: dict
```

### 2.4 Fatigue / Social Need 감쇠 함수

이 두 값이 행동의 핵심 드라이버이므로 Phase 1부터 정확히 정의한다.

```python
# 매 tick 자연 변화
def decay_fatigue(agent: AgentState):
    """활동 안 하면 피로 회복"""
    agent.fatigue = max(0, agent.fatigue * 0.92 - 0.02)

def grow_social_need(agent: AgentState):
    """시간이 지나면 사회적 욕구 증가"""
    agent.social_need = min(1.0, agent.social_need + 0.03)

# 행동 후 즉시 변화
def after_create_spot(agent: AgentState):
    agent.fatigue += 0.25
    agent.social_need -= 0.15

def after_join_spot(agent: AgentState):
    agent.fatigue += 0.15
    agent.social_need -= 0.30

def after_complete_spot(agent: AgentState):
    agent.fatigue += 0.20
    agent.social_need -= 0.40  # 큰 충족
```

감쇠 파라미터 (`0.92`, `0.03` 등)는 Phase 1 로그를 보고 튜닝한다. 처음부터 완벽할 필요 없다.

### 2.5 시간대 매핑

tick을 실제 시간대로 변환해서 행동 확률에 반영한다.

```python
TIME_SLOTS = {
    (0, 6): "dawn",          # 새벽 — 거의 비활성
    (7, 9): "morning",       # 아침
    (10, 11): "late_morning",
    (12, 13): "lunch",
    (14, 17): "afternoon",
    (18, 20): "evening",     # 저녁 — 피크
    (21, 23): "night"        # 밤
}

def get_time_slot(tick: int) -> str:
    hour = tick % 24
    for (start, end), slot in TIME_SLOTS.items():
        if start <= hour <= end:
            return slot
    return "dawn"

def get_day_type(tick: int) -> str:
    day = (tick // 24) % 7
    return "weekend" if day >= 5 else "weekday"
```

에이전트의 `schedule_weights`는 `"weekday_evening": 0.9` 같은 조합 키를 사용한다.

### 2.6 행동 결정 함수 (Phase 1: 3개만)

```python
def decide_action(agent: AgentState, tick: int, open_spots: list[Spot]) -> str:

    time_key = f"{get_day_type(tick)}_{get_time_slot(tick)}"
    time_weight = agent.schedule_weights.get(time_key, 0.1)

    # 시간대 활성 확률이 너무 낮으면 아무것도 안 함
    if random() > time_weight:
        return "NO_ACTION"

    # ---- CREATE_SPOT 확률 ----
    p_create = (
        0.35 * agent.host_score
      + 0.20 * region_create_affinity(agent)   # 기존 모델 연결점
      + 0.25 * agent.social_need
      - 0.15 * agent.fatigue
      - 0.10 * recent_host_penalty(agent, tick)
    )
    p_create = clamp(p_create, 0, 1)

    # ---- JOIN_SPOT 확률 ----
    matchable = find_matchable_spots(agent, open_spots)
    p_join = 0
    if matchable:
        best = matchable[0]
        p_join = (
            0.30 * agent.join_score
          + 0.25 * category_match(agent, best)
          + 0.20 * agent.social_need
          - 0.15 * agent.fatigue
          - 0.10 * budget_penalty(agent, best)
        )
        p_join = clamp(p_join, 0, 1)

    # ---- 결정 ----
    roll = random()
    if roll < p_create and agent.current_state == "idle":
        return "CREATE_SPOT"
    elif roll < p_create + p_join and matchable:
        return "JOIN_SPOT"
    else:
        return "NO_ACTION"
```

### 2.7 Tick 루프 (메인 런타임)

```python
def run_simulation(agents: list[AgentState], config: SimConfig):
    event_log = []
    spots = []

    for tick in range(config.total_ticks):

        # 1. 자연 감쇠
        for agent in agents:
            decay_fatigue(agent)
            grow_social_need(agent)

        # 2. 활성 에이전트 선택 (Phase 1은 전수, Phase 3은 샘플링)
        active_agents = select_active_agents(agents, tick)

        # 3. 행동 결정 & 실행
        open_spots = [s for s in spots if s.status == "OPEN"]

        for agent in shuffle(active_agents):
            action = decide_action(agent, tick, open_spots)

            if action == "CREATE_SPOT":
                spot = execute_create_spot(agent, tick)
                spots.append(spot)
                open_spots.append(spot)
                event_log.append(make_event(tick, "CREATE_SPOT", agent, spot))
                after_create_spot(agent)

            elif action == "JOIN_SPOT":
                target = pick_best_spot(agent, open_spots)
                if target and len(target.participants) < target.capacity:
                    target.participants.append(agent.agent_id)
                    agent.joined_spots.append(target.spot_id)
                    event_log.append(make_event(tick, "JOIN_SPOT", agent, target))
                    after_join_spot(agent)

                    # 정원 도달 시 자동 매칭
                    if len(target.participants) >= target.min_participants:
                        target.status = "MATCHED"
                        event_log.append(make_event(tick, "SPOT_MATCHED", None, target))

    return event_log, spots
```

### 2.8 Phase 1 검증 기준

Phase 1이 "성공"하려면 아래를 만족해야 한다.

- [ ] 48 tick 동안 event_log에 최소 30개 이상 이벤트 발생
- [ ] CREATE_SPOT 이벤트가 5개 이상
- [ ] JOIN_SPOT 이벤트가 10개 이상
- [ ] SPOT_MATCHED 상태 스팟이 최소 2개 이상
- [ ] dawn 시간대(0~6시)에는 이벤트가 거의 없음 (시간 필터 작동 확인)
- [ ] 에이전트별 fatigue가 시간에 따라 오르내리는 패턴 확인
- [ ] host_score 높은 에이전트가 CREATE_SPOT을 더 많이 함 (상관관계 확인)

이 기준을 통과하면 Phase 2로 넘어간다.

---

## 3. Phase 2 — Lifecycle + 상호작용

### 3.1 목표

> 스팟이 OPEN → MATCHED → CONFIRMED → IN_PROGRESS → COMPLETED 까지 생명주기를 완주하고, 에이전트 간 상호작용이 행동에 영향을 미친다.

### 3.2 추가 행동 (7개로 확장)

기존: `CREATE_SPOT`, `JOIN_SPOT`, `NO_ACTION`

추가:
- `CANCEL_JOIN` — 참여 취소
- `CHECK_IN` — 스팟 시작 시 체크인
- `NO_SHOW` — 체크인 안 함
- `COMPLETE_SPOT` — 스팟 완료 처리

### 3.3 스팟 Lifecycle 상태 머신

```
OPEN ──────────────────┐
  │                    │
  ▼                    ▼
MATCHED           CANCELED (참여자 부족 timeout)
  │
  ▼
CONFIRMED (scheduled_tick - 2시간)
  │
  ├──→ CANCELED (전원 취소)
  │
  ▼
IN_PROGRESS (scheduled_tick 도달)
  │
  ├──→ DISPUTED (50% 이상 NO_SHOW)
  │
  ▼
COMPLETED
```

### 3.4 Lifecycle Processor (tick 루프에 추가)

```python
def process_lifecycle(spots: list[Spot], tick: int, event_log: list):

    for spot in spots:

        # OPEN → CANCELED (48시간 경과, 미매칭)
        if spot.status == "OPEN" and tick - spot.created_at_tick > 48:
            spot.status = "CANCELED"
            event_log.append(make_event(tick, "SPOT_TIMEOUT", None, spot))

        # MATCHED → CONFIRMED (시작 2시간 전)
        if spot.status == "MATCHED" and spot.scheduled_tick - tick <= 2:
            spot.status = "CONFIRMED"
            event_log.append(make_event(tick, "SPOT_CONFIRMED", None, spot))

        # CONFIRMED → IN_PROGRESS (시작 시간 도달)
        if spot.status == "CONFIRMED" and tick >= spot.scheduled_tick:
            spot.status = "IN_PROGRESS"
            event_log.append(make_event(tick, "SPOT_STARTED", None, spot))

        # IN_PROGRESS → COMPLETED (1~3시간 후)
        if spot.status == "IN_PROGRESS" and tick >= spot.scheduled_tick + spot.duration:
            checked_in = count_checked_in(spot)
            noshow = len(spot.participants) - checked_in

            if noshow / len(spot.participants) > 0.5:
                spot.status = "DISPUTED"
                event_log.append(make_event(tick, "SPOT_DISPUTED", None, spot))
            else:
                spot.status = "COMPLETED"
                event_log.append(make_event(tick, "SPOT_COMPLETED", None, spot))
```

### 3.5 에이전트 상호작용 모델

Phase 2의 핵심 추가사항. p_join에 동적 사회 변수를 반영한다.

```python
def calc_social_join_modifier(agent: AgentState, spot: Spot, all_agents: dict) -> float:
    """스팟의 현재 참여자 구성이 참여 결정에 미치는 영향"""

    if not spot.participants:
        return 0.0  # 아무도 없으면 보너스/패널티 없음

    # 1. 인원 충족도 — 거의 찼으면 FOMO 효과
    fill_rate = len(spot.participants) / spot.capacity
    fomo_bonus = 0.15 if fill_rate >= 0.7 else 0.0

    # 2. 호스트 신뢰도
    host = all_agents[spot.host_agent_id]
    host_trust = host.trust_score  # 0~1, 호스팅 이력 기반
    trust_modifier = 0.10 * (host_trust - 0.5)  # 0.5 기준으로 +/-

    # 3. 기존 참여자와의 카테고리 유사도 (유유상종)
    shared_interests = avg_interest_overlap(agent, spot.participants, all_agents)
    affinity_bonus = 0.10 * shared_interests

    return fomo_bonus + trust_modifier + affinity_bonus
```

p_join 공식에 이 값을 더한다:

```python
p_join = (
    0.25 * agent.join_score
  + 0.20 * category_match(agent, spot)
  + 0.15 * agent.social_need
  + 0.15 * social_join_modifier       # ← 추가
  + 0.10 * region_affinity
  - 0.10 * agent.fatigue
  - 0.05 * budget_penalty(agent, spot)
)
```

### 3.6 행동 시차 모델링

스팟 생성과 참여의 시간 간격을 현실적으로 만든다.

```python
# 스팟 생성 시, scheduled_tick은 "지금부터 12~72시간 후"
def pick_scheduled_tick(agent: AgentState, current_tick: int) -> int:
    # 페르소나별 리드타임 분포
    if agent.persona_type in ["spontaneous", "night_social"]:
        lead_hours = random_int(6, 24)     # 당일~내일
    elif agent.persona_type in ["planner", "weekend_explorer"]:
        lead_hours = random_int(24, 72)    # 1~3일 후
    else:
        lead_hours = random_int(12, 48)    # 기본

    candidate = current_tick + lead_hours

    # 선호 시간대에 맞춰 보정
    candidate = snap_to_preferred_time(agent, candidate)
    return candidate
```

이렇게 하면 "스팟 만들자마자 즉시 매칭"이 사라진다. 생성과 참여 사이에 자연스러운 시차가 생긴다.

### 3.7 Phase 2 검증 기준

- [ ] 스팟이 OPEN → COMPLETED까지 전체 lifecycle을 완주하는 케이스 존재
- [ ] CANCELED (타임아웃) 스팟이 전체의 15~30% 범위 (너무 적거나 많으면 파라미터 조정)
- [ ] FOMO 효과: 참여자 3명인 스팟(4인 정원)의 참여 확률이 1명인 스팟보다 높음
- [ ] 호스트 신뢰도 높은 에이전트의 스팟이 매칭 성공률이 더 높음
- [ ] 생성-매칭 시차 평균이 12시간 이상
- [ ] NO_SHOW 비율이 전체 CHECK_IN의 5~15%
- [ ] DISPUTED 스팟이 소수 존재 (0은 비현실적, 30% 이상도 비현실적)

---

## 4. Phase 3 — 정산 + 리뷰 + 신뢰 반영

### 4.1 목표

> COMPLETED 이후 리뷰, 만족도 계산, 정산까지 완료. 신뢰도가 다음 시뮬레이션 사이클에 피드백된다.

### 4.2 추가 행동 (11개 전체)

추가:
- `WRITE_REVIEW`
- `SETTLE`
- `VIEW_FEED`
- `SAVE_SPOT`

### 4.3 Settlement Processor

```python
def process_settlement(spot: Spot, agents: dict, tick: int) -> SettlementResult:

    participants = [agents[pid] for pid in spot.participants]
    checked_in_agents = [a for a in participants if a.checked_in_for(spot.spot_id)]

    # 1. 만족도 계산
    satisfaction_scores = []
    for agent in checked_in_agents:
        sat = calculate_satisfaction(agent, spot)
        satisfaction_scores.append(sat)

    avg_satisfaction = mean(satisfaction_scores) if satisfaction_scores else 0

    # 2. 리뷰 생성 확률
    for agent in checked_in_agents:
        p_review = 0.3 + 0.4 * abs(satisfaction_scores[agent] - 0.5)
        # 만족 or 불만이 클수록 리뷰 작성 확률 높음
        if random() < p_review:
            review = generate_review(agent, spot, satisfaction_scores[agent])
            event_log.append(make_review_event(tick, agent, spot, review))

    # 3. 호스트 신뢰도 반영
    host = agents[spot.host_agent_id]
    if avg_satisfaction >= 0.7:
        host.trust_score = min(1.0, host.trust_score + 0.05)
    elif avg_satisfaction < 0.4:
        host.trust_score = max(0.0, host.trust_score - 0.08)

    # 4. 참여자 신뢰도 반영 (노쇼 패널티)
    for agent in participants:
        if not agent.checked_in_for(spot.spot_id):
            agent.trust_score = max(0.0, agent.trust_score - 0.15)

    # 5. 정산
    return SettlementResult(
        spot_id=spot.spot_id,
        completed_count=len(checked_in_agents),
        noshow_count=len(participants) - len(checked_in_agents),
        avg_satisfaction=avg_satisfaction,
        host_trust_delta=host.trust_score - host.prev_trust,
        status="SETTLED"
    )
```

### 4.4 만족도 함수

```python
def calculate_satisfaction(agent: AgentState, spot: Spot) -> float:
    """0~1 사이 만족도"""

    base = 0.5

    # 카테고리 매치
    if spot.category in agent.interest_categories:
        base += 0.15

    # 인원 적정성 (너무 적으면 아쉽, 너무 많으면 부담)
    ideal_ratio = len(spot.checked_in) / spot.capacity
    if 0.6 <= ideal_ratio <= 0.9:
        base += 0.10
    elif ideal_ratio < 0.4:
        base -= 0.10

    # 노쇼가 있으면 전반적 불만
    noshow_ratio = spot.noshow_count / len(spot.participants)
    base -= 0.15 * noshow_ratio

    # 호스트와의 신뢰 fit
    host_trust_gap = abs(agent.trust_threshold - agents[spot.host_agent_id].trust_score)
    base -= 0.10 * host_trust_gap

    # 랜덤 노이즈 (현실 불확실성)
    noise = uniform(-0.08, 0.08)

    return clamp(base + noise, 0, 1)
```

### 4.5 분쟁 해소 규칙

DISPUTED 상태에서 영원히 멈추지 않도록 타임아웃 규칙을 둔다.

```python
def resolve_disputes(spots: list[Spot], tick: int, event_log: list):
    for spot in spots:
        if spot.status != "DISPUTED":
            continue

        dispute_age = tick - spot.disputed_at_tick

        if dispute_age > 24:  # 24시간(tick) 경과
            # 자동 정산: 호스트에게 불리하게
            spot.status = "FORCE_SETTLED"
            host = agents[spot.host_agent_id]
            host.trust_score = max(0, host.trust_score - 0.12)
            event_log.append(make_event(tick, "FORCE_SETTLED", None, spot,
                payload={"reason": "dispute_timeout"}))

        elif dispute_age > 6:  # 6시간 경과
            # 참여자 과반 만족이면 정상 정산
            if spot.avg_satisfaction >= 0.5:
                spot.status = "SETTLED"
                event_log.append(make_event(tick, "DISPUTE_RESOLVED", None, spot))
```

### 4.6 Phase 3 검증 기준

- [ ] COMPLETED → SETTLED 전환율 80% 이상
- [ ] DISPUTED → FORCE_SETTLED 비율 5% 미만
- [ ] 리뷰 작성 비율 30~50%
- [ ] 호스트 trust_score 상위 20%의 매칭 성공률이 하위 20%보다 2배 이상
- [ ] 노쇼 상습자(trust < 0.3)의 JOIN_SPOT 성공률이 시간이 지날수록 감소
- [ ] 전체 event_log에서 스팟 단위 타임라인 추출이 가능

---

## 5. 기존 모델 연결 지점

기존 페르소나-지역 선호 데이터가 시뮬레이터에 들어가는 정확한 위치:

| 기존 데이터 | 사용 위치 | 역할 |
|------------|----------|------|
| persona-region affinity | `region_create_affinity()` | 어디서 스팟을 만들지 |
| 지역별 활동 적합도 | `execute_create_spot()` | 어떤 카테고리 스팟을 만들지 |
| 페르소나 성향값 | `host_score`, `join_score` 초기화 | 생성/참여 기본 확률 |
| 카테고리 선호 | `category_match()` | 참여 결정 시 매칭 점수 |
| 시간대 선호 | `schedule_weights` 초기화 | 언제 활동할지 |
| budget_level | `budget_penalty()` | 비용 부담 필터 |

연결 방식:

```python
def init_agent_from_persona(persona: Persona, region_model: RegionModel) -> AgentState:
    return AgentState(
        agent_id=generate_id(),
        persona_type=persona.type,
        home_region_id=persona.home_region,
        active_regions=region_model.top_regions_for(persona, k=3),
        interest_categories=persona.preferred_categories,
        host_score=persona.host_tendency,
        join_score=persona.join_tendency,
        fatigue=uniform(0.05, 0.25),
        social_need=uniform(0.3, 0.7),
        current_state="idle",
        schedule_weights=persona.time_preferences,
        trust_score=0.5,   # 초기값
        last_action_tick=-1,
        hosted_spots=[],
        joined_spots=[]
    )
```

---

## 6. 출력물 정의

### 6.1 Raw Event Log (모든 Phase)

```json
{"tick": 14, "event": "CREATE_SPOT", "agent": "A_023", "spot": "S_001", "region": "emd_yeonmu"}
{"tick": 18, "event": "JOIN_SPOT", "agent": "A_047", "spot": "S_001", "region": "emd_yeonmu"}
{"tick": 19, "event": "JOIN_SPOT", "agent": "A_012", "spot": "S_001", "region": "emd_yeonmu"}
{"tick": 19, "event": "SPOT_MATCHED", "spot": "S_001"}
```

### 6.2 Spot Timeline (Phase 2+)

```
Spot S_001 [food/casual_meetup] @ emd_yeonmu
├─ tick 14: A_023 created (capacity: 4, min: 2)
├─ tick 18: A_047 joined
├─ tick 19: A_012 joined → MATCHED
├─ tick 26: CONFIRMED
├─ tick 28: STARTED
│   ├─ A_023 checked_in
│   ├─ A_047 checked_in
│   └─ A_012 no_show
├─ tick 30: COMPLETED
├─ tick 31: A_023 wrote review (sat: 0.72)
├─ tick 31: A_047 wrote review (sat: 0.65)
└─ tick 32: SETTLED (avg_sat: 0.68, host_trust: +0.03)
```

### 6.3 Aggregated Metrics (Phase 3)

```
=== 2주 시뮬레이션 결과 ===
총 스팟 생성: 312
매칭 성공: 198 (63.5%)
완료: 171 (86.4% of matched)
정산 완료: 164 (95.9% of completed)
평균 만족도: 0.68

지역별 TOP 3:
  emd_yeonmu:   82 spots (매칭률 71%)
  emd_jangan:   54 spots (매칭률 58%)
  emd_sinchon:  43 spots (매칭률 66%)

카테고리별:
  food:         38% (노쇼율 8%)
  cafe:         22% (노쇼율 6%)
  exercise:     15% (노쇼율 12%)

페르소나별 참여율:
  night_social:     평균 4.2회/2주
  weekend_explorer: 평균 2.8회/2주
  planner:          평균 1.9회/2주
```

---

## 7. 프로젝트 구조

```
spot-simulator/
├── config/
│   ├── simulation_config.yaml    # 스케일, 파라미터
│   └── persona_templates.yaml    # 페르소나 유형 정의
├── models/
│   ├── agent.py                  # AgentState
│   ├── spot.py                   # Spot, SpotStatus
│   └── event.py                  # EventLog
├── engine/
│   ├── runner.py                 # 메인 tick 루프
│   ├── decision.py               # 행동 결정 함수
│   ├── lifecycle.py              # 스팟 상태 전이
│   └── settlement.py             # 정산/리뷰/신뢰
├── data/
│   ├── region_features.json      # 기존 지역 데이터
│   └── persona_region_affinity.json
├── output/
│   ├── event_log.jsonl
│   ├── spot_timelines.json
│   └── metrics_report.json
├── analysis/
│   ├── validate.py               # Phase별 검증 스크립트
│   └── visualize.py              # 로그 시각화
├── tests/
│   ├── test_decision.py
│   ├── test_lifecycle.py
│   └── test_settlement.py
└── main.py
```

---

## 8. 구현 타임라인

| 단계 | 기간 | 산출물 |
|------|------|--------|
| Phase 1 Step 1-3 | 2일 | 데이터 모델 + Agent 초기화 + Tick 루프 뼈대 |
| Phase 1 Step 4-6 | 2일 | 행동 결정 + 로그 + 검증 통과 |
| 파라미터 튜닝 | 1일 | 감쇠 함수, 확률 가중치 조정 |
| Phase 2 | 4일 | Lifecycle + 상호작용 + 시차 모델 |
| Phase 2 검증 + 튜닝 | 2일 | 로그 분석, 비현실적 패턴 수정 |
| Phase 3 | 3일 | 정산 + 리뷰 + 신뢰 피드백 |
| Phase 3 검증 + 최종 조정 | 2일 | 전체 파이프라인 검증, metrics 출력 |
| **합계** | **~16일** | |

---

## 9. 리스크와 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 행동 분포가 비현실적 (전부 CREATE만 함) | 로그 무의미 | Phase 1 검증에서 조기 발견. 확률 가중치 재조정 |
| 스팟이 전부 CANCELED됨 | lifecycle 무의미 | min_participants를 낮추거나 에이전트 밀도를 높임 |
| Phase 3 스케일에서 성능 저하 | 실행 불가 | 전수 순회 → 활성 에이전트 샘플링으로 전환 |
| 감쇠 파라미터가 수렴/발산 | fatigue가 0 고정 or 1 고정 | 매 Phase 끝에 agent state 분포 시각화로 감지 |
| 기존 모델과 연결 시 데이터 포맷 불일치 | 통합 지연 | Phase 1에서 하드코딩, Phase 2에서 실제 연결 |

---

## 10. Phase 1 → 바이럴 시뮬레이션 페이지 연결

Phase 1의 event_log는 그대로 바이럴 마케팅 시뮬레이션 페이지(`/simulation`)의 데이터 소스가 된다.

```
Phase 1 event_log.jsonl
    ↓
프리빌트 JSON 이벤트 30~50개 추출
    ↓
카카오맵 위에서 자동 재생
    (SPOT_CREATED → MATCH → REVIEW → SPOT_CLOSED)
    ↓
다크테마 풀스크린맵 + 이벤트타임라인 + CTA
```

즉 시뮬레이터가 돌면, 바이럴 페이지에 들어갈 "실감나는 데모 데이터"가 자동으로 생성된다. 하드코딩 데이터를 만들 필요가 없어진다.
