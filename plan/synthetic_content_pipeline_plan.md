# Multi-Run Simulation & Attractiveness Curation — 구현 플랜

> 시뮬레이션을 N회 반복 실행하여 대량 스팟 로그 풀을 만들고,  
> "유저가 혹할 만한" 매력적인 스팟만 선별하여 콘텐츠 파이프라인에 공급하는 시스템

---

## 0. 문서 위치 및 의존 관계

```
[spot-simulator]                    ← 시뮬레이션 엔진 (기존)
        ↓
[multi-run-attractiveness-curation] ← 이 문서
        ↓
[synthetic-content-pipeline]        ← 콘텐츠 렌더링/검증 (기존)
```

이 파이프라인은 시뮬레이터와 콘텐츠 파이프라인 **사이에** 위치한다.  
시뮬레이터의 출력(event_log)을 입력으로 받고, 선별된 스팟 로그를 콘텐츠 파이프라인에 넘긴다.

### 기존 플랜 변경 사항 요약

| 기존 문서                  | 변경 내용                                                              |
| -------------------------- | ---------------------------------------------------------------------- |
| spot-simulator             | `run_simulation()`을 variant 파라미터 받아 N회 실행하도록 확장         |
| synthetic-content-pipeline | Job 1 `build_content_spec`의 입력이 단일 로그 → curated 로그 풀로 변경 |

---

## 1. 핵심 원칙

### 원칙 1. 시뮬레이션은 무한 자원이다

시뮬레이션은 Python 코드 실행이므로 비용이 0에 가깝다.  
5회든 50회든 LLM 호출 없이 실행 가능하다.  
**비싼 자원(LLM)은 이미 좋은 재료에만 투입한다.**

### 원칙 2. "현실적"과 "매력적"은 다르다

시뮬레이터는 현실적인 분포(매칭률 60%, 노쇼 10%)를 만든다.  
하지만 초기 유저에게 보여줄 피드는 **매력적인 분포**가 필요하다.  
Multi-run으로 현실적인 로그를 대량 생산한 뒤, 매력적인 것만 골라쓴다.

### 원칙 3. 매력의 스펙트럼

전부 완벽한 스팟만 있으면 fake 느낌이 난다.  
"노쇼 1명 있었지만 나머지가 대만족", "첫 호스팅인데 성공" 같은  
**매력적인 불완전함**을 의도적으로 포함한다.

### 원칙 4. 선별이지 조작이 아니다

시뮬레이션 로그 자체를 수정하지 않는다.  
시뮬레이터가 만든 사실(fact)을 그대로 보존하되, **어떤 사실을 보여줄지만** 선택한다.  
이 원칙은 synthetic content pipeline의 "LLM은 문서 작성기" 원칙과 일치한다.

---

## 2. 전체 아키텍처

```
[Run Variant Config]
        ↓
[Simulation Engine × N runs]
    ├─ Run 1: baseline
    ├─ Run 2: high_host
    ├─ Run 3: high_engagement
    ├─ Run 4: regional_cluster
    └─ Run 5: weekend_peak
        ↓
[Raw Event Log Pool]
    (N runs × ~300 spots = ~1,500 spots)
        ↓
[Spot Lifecycle Assembler]
    raw event log → spot 단위 lifecycle 조립
        ↓
[Attractiveness Scorer]
    ├─ Signal Extractors (8개 매력 신호)
    ├─ Composite Score 계산
    └─ Diversity-Aware Selection
        ↓
[Curated Spot Pool]
    (상위 500개, 매력 유형별 quota 적용)
        ↓
[Feed Mix Composer]
    매력 유형별 비율 조정 → 최종 피드 구성
        ↓
[Content Spec Builder] → (기존 synthetic content pipeline)
```

---

## 3. Multi-Run 전략

### 3.1 Run Variant 설계

각 variant는 시뮬레이터의 **초기 조건만** 바꾼다.  
시뮬레이션 로직(행동 결정, lifecycle, 정산)은 동일하다.

```python
@dataclass
class RunVariant:
    variant_id: str
    variant_name: str
    description: str

    # 에이전트 구성
    agent_count: int
    host_score_distribution: dict       # {"mean": 0.4, "std": 0.15}
    join_score_distribution: dict
    social_need_init_range: tuple       # (min, max)

    # 페르소나 비율
    persona_mix: dict                   # {"casual_foodie": 0.3, "night_social": 0.25, ...}

    # 지역 집중도
    region_concentration: dict | None   # {"emd_yeonmu": 0.3} or None (균등 분배)

    # 시간대 가중치 오버라이드
    schedule_weight_override: dict | None

    # 시뮬레이션 기간
    sim_duration_ticks: int             # 기본 336 (2주)

    # 시드
    random_seed: int
```

### 3.2 기본 Variant Set (5개)

#### Variant 1: `baseline`

기본 파라미터. 시뮬레이터 설계 의도 그대로 실행.  
다른 variant의 비교 기준선 역할.

```python
BASELINE = RunVariant(
    variant_id="v1_baseline",
    variant_name="baseline",
    description="기본 파라미터, 시뮬레이터 설계값 그대로",
    agent_count=500,
    host_score_distribution={"mean": 0.4, "std": 0.15},
    join_score_distribution={"mean": 0.5, "std": 0.15},
    social_need_init_range=(0.3, 0.7),
    persona_mix={
        "casual_foodie": 0.25,
        "night_social": 0.20,
        "lesson_seeker": 0.20,
        "solo_healing": 0.15,
        "supporter_teacher": 0.20,
    },
    region_concentration=None,
    schedule_weight_override=None,
    sim_duration_ticks=336,
    random_seed=42,
)
```

**기대 산출:** 보통 수준의 매칭률, 자연스러운 분포.  
완주 스팟 중 일부가 매력적일 것.

#### Variant 2: `high_host`

호스팅 성향이 높은 에이전트 비율 상향.  
반복 호스트(recurring host) 패턴이 더 많이 출현한다.

```python
HIGH_HOST = RunVariant(
    variant_id="v2_high_host",
    variant_name="high_host",
    description="호스팅 성향 높은 에이전트 비율 증가 → 반복 개최 패턴 유도",
    agent_count=500,
    host_score_distribution={"mean": 0.6, "std": 0.12},
    join_score_distribution={"mean": 0.5, "std": 0.15},
    social_need_init_range=(0.3, 0.7),
    persona_mix={
        "casual_foodie": 0.15,
        "night_social": 0.15,
        "lesson_seeker": 0.15,
        "solo_healing": 0.10,
        "supporter_teacher": 0.45,   # 호스트형 비율 대폭 상향
    },
    region_concentration=None,
    schedule_weight_override=None,
    sim_duration_ticks=336,
    random_seed=123,
)
```

**기대 산출:** "3회차 모임", "단골 호스트" 같은 시리즈 패턴.  
호스트 신뢰도가 빠르게 올라가는 로그.

#### Variant 3: `high_engagement`

참여 욕구가 높은 에이전트들. 매칭이 빠르고 참여율이 높다.

```python
HIGH_ENGAGEMENT = RunVariant(
    variant_id="v3_high_engagement",
    variant_name="high_engagement",
    description="참여 욕구 높은 에이전트 → 빠른 마감, 높은 완주율",
    agent_count=500,
    host_score_distribution={"mean": 0.45, "std": 0.15},
    join_score_distribution={"mean": 0.7, "std": 0.10},
    social_need_init_range=(0.5, 0.9),
    persona_mix={
        "casual_foodie": 0.30,
        "night_social": 0.30,
        "lesson_seeker": 0.15,
        "solo_healing": 0.05,
        "supporter_teacher": 0.20,
    },
    region_concentration=None,
    schedule_weight_override=None,
    sim_duration_ticks=336,
    random_seed=456,
)
```

**기대 산출:** "올린 지 3시간 만에 마감", 노쇼율 낮음, 리뷰 작성률 높음.

#### Variant 4: `regional_cluster`

특정 인기 지역에 에이전트가 밀집. 지역 커뮤니티 느낌.

```python
REGIONAL_CLUSTER = RunVariant(
    variant_id="v4_regional_cluster",
    variant_name="regional_cluster",
    description="인기 지역 밀집 → 동네 커뮤니티 느낌, 재참여 패턴",
    agent_count=500,
    host_score_distribution={"mean": 0.4, "std": 0.15},
    join_score_distribution={"mean": 0.5, "std": 0.15},
    social_need_init_range=(0.3, 0.7),
    persona_mix={
        "casual_foodie": 0.25,
        "night_social": 0.20,
        "lesson_seeker": 0.20,
        "solo_healing": 0.15,
        "supporter_teacher": 0.20,
    },
    region_concentration={
        "emd_yeonmu": 0.25,
        "emd_yeongtong": 0.20,
        "emd_ingye": 0.20,
        # 나머지 35%는 기타 지역에 분산
    },
    schedule_weight_override=None,
    sim_duration_ticks=336,
    random_seed=789,
)
```

**기대 산출:** 같은 동네에서 여러 스팟이 열리고, 참여자가 겹치는 패턴.  
"연무동에서 이번 주에만 8개 모임이 열렸어요" 같은 지역 활기.

#### Variant 5: `weekend_peak`

금토 저녁에 활동이 몰리는 설정. 시간대별 집중 효과.

```python
WEEKEND_PEAK = RunVariant(
    variant_id="v5_weekend_peak",
    variant_name="weekend_peak",
    description="금토 저녁 집중 → 동시다발 스팟, FOMO 극대화",
    agent_count=500,
    host_score_distribution={"mean": 0.45, "std": 0.15},
    join_score_distribution={"mean": 0.55, "std": 0.15},
    social_need_init_range=(0.3, 0.7),
    persona_mix={
        "casual_foodie": 0.25,
        "night_social": 0.30,
        "lesson_seeker": 0.15,
        "solo_healing": 0.10,
        "supporter_teacher": 0.20,
    },
    region_concentration=None,
    schedule_weight_override={
        "weekend_evening": 0.95,
        "weekend_night": 0.85,
        "friday_evening": 0.90,
    },
    sim_duration_ticks=336,
    random_seed=1011,
)
```

**기대 산출:** 금요일~토요일 저녁에 동시에 10개+ 스팟이 열리는 로그.  
바이럴 시뮬레이션 페이지에서 "불금 8시, 수원에서 동시에 진행 중인 모임 12개"로 연출 가능.

### 3.3 Variant 확장 전략

MVP에서 5개로 시작하되, 이후 특수 목적 variant를 추가할 수 있다.

| 확장 Variant        | 목적                                                   | 시기             |
| ------------------- | ------------------------------------------------------ | ---------------- |
| `newcomer_friendly` | 첫 참여자가 많은 환경 → "초면환영" 태그 스팟 다수      | v1.1             |
| `seasonal_event`    | 계절 이벤트(벚꽃/크리스마스) 기간 집중                 | v1.2             |
| `category_deep`     | 특정 카테고리(예: lesson) 비중 극대화                  | 카테고리 런칭 시 |
| `stress_test`       | 5,000 에이전트, 극단 파라미터 → 시뮬레이터 안정성 검증 | Phase 3          |

---

## 4. Spot Lifecycle Assembler

raw event log를 스팟 단위 lifecycle 객체로 조립한다.  
Attractiveness Scorer의 입력 형태를 표준화하는 단계.

### 4.1 입력 / 출력

```
입력: event_log.jsonl (한 run의 전체 이벤트)
출력: List[SpotLifecycle] — 스팟별 전체 이력이 담긴 객체
```

### 4.2 SpotLifecycle 데이터 모델

```python
@dataclass
class SpotLifecycle:
    # 식별
    spot_id: str
    run_id: str
    variant_name: str

    # 기본 정보
    host_agent_id: str
    host_persona_type: str
    region_id: str
    category: str
    spot_type: str
    capacity: int
    min_participants: int

    # 타임라인
    created_at_tick: int
    matched_at_tick: int | None
    confirmed_at_tick: int | None
    started_at_tick: int | None
    completed_at_tick: int | None
    settled_at_tick: int | None
    canceled_at_tick: int | None

    # 참여
    participants: list[str]             # 참여 확정 에이전트 ID
    checked_in: list[str]               # 체크인한 에이전트 ID
    no_shows: list[str]                 # 노쇼 에이전트 ID
    cancel_joins: list[str]             # 참여 취소한 에이전트 ID

    # 결과
    final_status: str                   # COMPLETED / SETTLED / CANCELED / DISPUTED / FORCE_SETTLED
    avg_satisfaction: float | None
    satisfaction_scores: dict[str, float]  # {agent_id: score}

    # 리뷰
    reviews: list[ReviewRecord]
    review_count: int
    avg_review_rating: float | None

    # 호스트 맥락
    host_trust_score_before: float
    host_trust_score_after: float
    host_previous_spot_count: int       # 이 스팟 이전 호스팅 횟수
    host_previous_avg_satisfaction: float | None

    # 참여자 맥락
    repeat_participants: list[str]      # 이 호스트의 이전 스팟에도 참여한 에이전트
    participant_persona_mix: list[str]  # 참여자 페르소나 유형 목록

    # 시간 파생
    fill_duration_ticks: int | None     # 생성 → 매칭 소요 시간
    total_duration_ticks: int | None    # 생성 → 정산 전체 소요 시간

    # 이슈
    issues: list[str]                   # ["late_start_5min", "venue_change"]
    is_disputed: bool
```

```python
@dataclass
class ReviewRecord:
    reviewer_agent_id: str
    reviewer_persona_type: str
    rating: int                         # 1~5
    satisfaction_score: float            # 0~1
    is_repeat_participant: bool
```

### 4.3 조립 로직

```python
def assemble_spot_lifecycles(
    event_log: list[EventLog],
    agents: dict[str, AgentState],
    spots: list[Spot],
    run_id: str,
    variant_name: str,
) -> list[SpotLifecycle]:

    lifecycles = []

    for spot in spots:
        # 해당 스팟의 이벤트만 필터
        spot_events = [e for e in event_log if e.spot_id == spot.spot_id]
        spot_events.sort(key=lambda e: e.tick)

        # 타임라인 추출
        timeline = extract_timeline(spot_events)

        # 호스트 맥락
        host = agents[spot.host_agent_id]
        host_history = get_host_history(host, spots, before_tick=spot.created_at_tick)

        # 참여자 분석
        repeat_parts = find_repeat_participants(spot, host_history)

        # 리뷰 수집
        review_events = [e for e in spot_events if e.event_type == "WRITE_REVIEW"]
        reviews = [build_review_record(e, agents, host_history) for e in review_events]

        lifecycle = SpotLifecycle(
            spot_id=spot.spot_id,
            run_id=run_id,
            variant_name=variant_name,
            host_agent_id=spot.host_agent_id,
            host_persona_type=host.persona_type,
            region_id=spot.region_id,
            category=spot.category,
            spot_type=spot.spot_type,
            capacity=spot.capacity,
            min_participants=spot.min_participants,
            # ... timeline, participants, results, reviews, context ...
            fill_duration_ticks=timeline.get("matched", None) and (timeline["matched"] - timeline["created"]),
            host_previous_spot_count=len(host_history),
            host_previous_avg_satisfaction=safe_mean([s.avg_satisfaction for s in host_history]),
            repeat_participants=repeat_parts,
            reviews=reviews,
            review_count=len(reviews),
            avg_review_rating=safe_mean([r.rating for r in reviews]),
            # ...
        )
        lifecycles.append(lifecycle)

    return lifecycles
```

---

## 5. Attractiveness Scorer

### 5.1 설계 철학

매력도는 단일 점수가 아니라 **8개 매력 신호(signal)**의 조합이다.  
각 signal은 독립적으로 계산되고, 최종 composite score로 합산된다.  
신호별 가중치는 "유저가 피드에서 이걸 보면 어떤 감정을 느끼는가"를 기준으로 설정한다.

### 5.2 매력 신호 8종

```
Signal 1: recurring_host      — 반복 개최 (시리즈 모임)
Signal 2: quick_fill           — 빠른 마감
Signal 3: high_satisfaction    — 높은 만족도
Signal 4: rich_reviews         — 풍부한 후기
Signal 5: clean_completion     — 깔끔한 완주
Signal 6: repeat_participants  — 재참여자 존재
Signal 7: host_credibility     — 호스트 신뢰도
Signal 8: charming_imperfection — 매력적 불완전함
```

### 5.3 Signal 1: `recurring_host` — 반복 개최

같은 호스트가 동일/유사 카테고리에서 여러 번 스팟을 열고,  
후속 스팟일수록 만족도가 유지되거나 올라가는 패턴.

**유저 감정:** "벌써 3회차라고? 그만큼 검증됐다는 거잖아"

```python
def signal_recurring_host(lifecycle: SpotLifecycle, all_lifecycles: list[SpotLifecycle]) -> float:
    # 같은 run 내, 같은 호스트의 완주된 스팟 히스토리
    host_completed = [
        lc for lc in all_lifecycles
        if lc.host_agent_id == lifecycle.host_agent_id
        and lc.run_id == lifecycle.run_id
        and lc.final_status in ("COMPLETED", "SETTLED")
        and lc.created_at_tick <= lifecycle.created_at_tick
    ]
    host_completed.sort(key=lambda lc: lc.created_at_tick)

    episode_count = len(host_completed)

    if episode_count < 2:
        return 0.0

    # 회차 점수: 2회=0.4, 3회=0.6, 4회=0.8, 5회+=1.0
    episode_score = min(1.0, (episode_count - 1) * 0.2)

    # 만족도 추세: 상승 또는 유지면 보너스
    sats = [lc.avg_satisfaction for lc in host_completed if lc.avg_satisfaction is not None]
    if len(sats) >= 2:
        trend = sats[-1] - sats[0]
        if trend >= 0:
            trend_bonus = min(0.2, trend * 0.5)  # 최대 0.2 보너스
        else:
            trend_bonus = max(-0.15, trend * 0.3)  # 하락 시 감점
    else:
        trend_bonus = 0.0

    # 카테고리 일관성: 같은 카테고리 반복이면 "전문 호스트" 느낌
    same_category = [lc for lc in host_completed if lc.category == lifecycle.category]
    category_consistency_bonus = 0.1 if len(same_category) >= 2 else 0.0

    return clamp(episode_score + trend_bonus + category_consistency_bonus, 0, 1)
```

### 5.4 Signal 2: `quick_fill` — 빠른 마감

생성 후 짧은 시간 내에 정원이 차는 패턴.

**유저 감정:** "4시간 만에 마감됐대, 다음엔 빨리 신청해야겠다"

```python
def signal_quick_fill(lifecycle: SpotLifecycle) -> float:
    if lifecycle.fill_duration_ticks is None:
        return 0.0

    fill_hours = lifecycle.fill_duration_ticks  # tick = 1시간

    if fill_hours <= 3:
        return 1.0      # 3시간 이내: 극강 매력
    elif fill_hours <= 6:
        return 0.85
    elif fill_hours <= 12:
        return 0.65
    elif fill_hours <= 24:
        return 0.4
    elif fill_hours <= 36:
        return 0.2
    else:
        return 0.0
```

### 5.5 Signal 3: `high_satisfaction` — 높은 만족도

체크인한 참여자들의 평균 만족도.

**유저 감정:** "참여자 전원 만족이라니, 좋은 모임인가 보다"

```python
def signal_high_satisfaction(lifecycle: SpotLifecycle) -> float:
    if lifecycle.avg_satisfaction is None:
        return 0.0

    sat = lifecycle.avg_satisfaction

    # 만족도 자체 + 편차 고려 (전원 비슷하게 높으면 더 좋음)
    base = sat  # 0~1 그대로

    if lifecycle.satisfaction_scores:
        scores = list(lifecycle.satisfaction_scores.values())
        std = statistics.stdev(scores) if len(scores) > 1 else 0
        # 편차 작을수록 보너스 (전원 고르게 만족)
        consistency_bonus = max(0, 0.15 - std * 0.3)
    else:
        consistency_bonus = 0.0

    return clamp(base + consistency_bonus, 0, 1)
```

### 5.6 Signal 4: `rich_reviews` — 풍부한 후기

리뷰 수와 평점 분포.

**유저 감정:** "후기 3개나 달렸고 다 별 4~5개, 진짜 좋았나 보다"

```python
def signal_rich_reviews(lifecycle: SpotLifecycle) -> float:
    if lifecycle.review_count == 0:
        return 0.0

    checked_in_count = len(lifecycle.checked_in)
    if checked_in_count == 0:
        return 0.0

    # 리뷰 작성률
    review_rate = lifecycle.review_count / checked_in_count
    rate_score = min(1.0, review_rate * 1.5)  # 67% 작성률이면 만점

    # 평균 평점 (5점 만점 → 0~1 정규화)
    if lifecycle.avg_review_rating is not None:
        rating_score = (lifecycle.avg_review_rating - 1) / 4  # 1→0, 5→1
    else:
        rating_score = 0.0

    # 복수 리뷰 보너스
    multi_review_bonus = 0.0
    if lifecycle.review_count >= 2:
        multi_review_bonus = 0.1
    if lifecycle.review_count >= 3:
        multi_review_bonus = 0.2

    return clamp(
        0.35 * rate_score + 0.40 * rating_score + multi_review_bonus,
        0, 1
    )
```

### 5.7 Signal 5: `clean_completion` — 깔끔한 완주

노쇼 0, 이슈 0, 분쟁 0.

**유저 감정:** "문제 없이 잘 진행됐다니 안심이다"

```python
def signal_clean_completion(lifecycle: SpotLifecycle) -> float:
    if lifecycle.final_status not in ("COMPLETED", "SETTLED"):
        return 0.0

    deductions = 0.0

    # 노쇼
    noshow_ratio = len(lifecycle.no_shows) / max(1, len(lifecycle.participants))
    deductions += noshow_ratio * 0.5

    # 이슈
    deductions += len(lifecycle.issues) * 0.1

    # 분쟁
    if lifecycle.is_disputed:
        deductions += 0.4

    # 참여 취소
    cancel_ratio = len(lifecycle.cancel_joins) / max(1, len(lifecycle.participants))
    deductions += cancel_ratio * 0.2

    return clamp(1.0 - deductions, 0, 1)
```

### 5.8 Signal 6: `repeat_participants` — 재참여자 존재

이 호스트의 이전 스팟에 참여했던 사람이 다시 참여한 패턴.

**유저 감정:** "한 번 가본 사람이 또 갔네, 그만큼 좋았다는 거지"

```python
def signal_repeat_participants(lifecycle: SpotLifecycle) -> float:
    if lifecycle.host_previous_spot_count == 0:
        return 0.0  # 첫 호스팅이면 재참여 불가능

    repeat_count = len(lifecycle.repeat_participants)
    participant_count = len(lifecycle.checked_in)

    if participant_count == 0:
        return 0.0

    repeat_ratio = repeat_count / participant_count

    # 재참여자 1명이면 0.5, 2명이면 0.8, 50% 이상이면 1.0
    if repeat_ratio >= 0.5:
        return 1.0
    elif repeat_count >= 2:
        return 0.8
    elif repeat_count >= 1:
        return 0.5
    else:
        return 0.0
```

### 5.9 Signal 7: `host_credibility` — 호스트 신뢰도

호스트의 누적 신뢰도와 호스팅 이력.

**유저 감정:** "이 호스트 신뢰도 높네, 믿고 가도 되겠다"

```python
def signal_host_credibility(lifecycle: SpotLifecycle) -> float:
    trust = lifecycle.host_trust_score_after
    history_count = lifecycle.host_previous_spot_count

    # 신뢰도 자체 (0~1)
    trust_score = trust

    # 경험치 보너스
    if history_count >= 5:
        experience_bonus = 0.15
    elif history_count >= 3:
        experience_bonus = 0.10
    elif history_count >= 1:
        experience_bonus = 0.05
    else:
        experience_bonus = 0.0

    # 이전 평균 만족도
    if lifecycle.host_previous_avg_satisfaction is not None:
        prev_sat_bonus = lifecycle.host_previous_avg_satisfaction * 0.15
    else:
        prev_sat_bonus = 0.0

    return clamp(trust_score * 0.6 + experience_bonus + prev_sat_bonus, 0, 1)
```

### 5.10 Signal 8: `charming_imperfection` — 매력적 불완전함

완벽하지 않지만 오히려 공감을 유발하는 패턴.  
이 signal은 다른 signal과 다르게 **특정 패턴 매칭** 방식으로 동작한다.

**유저 감정:** "노쇼 있었는데도 나머지가 대만족이라니", "첫 호스팅인데 성공했다니 응원하고 싶다"

```python
CHARMING_PATTERNS = [
    {
        "name": "one_noshow_but_great",
        "description": "노쇼 1명 있었지만 나머지 만족도 높음",
        "score": 0.75,
        "condition": lambda lc: (
            len(lc.no_shows) == 1
            and lc.avg_satisfaction is not None
            and lc.avg_satisfaction >= 0.75
            and lc.final_status in ("COMPLETED", "SETTLED")
        ),
    },
    {
        "name": "first_time_host_success",
        "description": "첫 호스팅인데 성공적으로 완주",
        "score": 0.80,
        "condition": lambda lc: (
            lc.host_previous_spot_count == 0
            and lc.final_status in ("COMPLETED", "SETTLED")
            and lc.avg_satisfaction is not None
            and lc.avg_satisfaction >= 0.65
        ),
    },
    {
        "name": "almost_full_open",
        "description": "1자리만 남은 모집 중 스팟",
        "score": 0.85,
        "condition": lambda lc: (
            lc.final_status == "OPEN"
            and len(lc.participants) == lc.capacity - 1
        ),
    },
    {
        "name": "late_start_but_loved",
        "description": "늦게 시작했지만 결국 모두 만족",
        "score": 0.65,
        "condition": lambda lc: (
            "late_start" in str(lc.issues)
            and lc.avg_satisfaction is not None
            and lc.avg_satisfaction >= 0.70
        ),
    },
    {
        "name": "small_group_deep_connection",
        "description": "2~3명 소규모인데 만족도 극상",
        "score": 0.70,
        "condition": lambda lc: (
            len(lc.checked_in) in (2, 3)
            and lc.avg_satisfaction is not None
            and lc.avg_satisfaction >= 0.85
        ),
    },
    {
        "name": "disputed_but_resolved",
        "description": "분쟁 발생했지만 원만히 해결",
        "score": 0.50,
        "condition": lambda lc: (
            lc.is_disputed
            and lc.final_status == "SETTLED"
            and lc.avg_satisfaction is not None
            and lc.avg_satisfaction >= 0.55
        ),
    },
]

def signal_charming_imperfection(lifecycle: SpotLifecycle) -> float:
    matched = [p for p in CHARMING_PATTERNS if p["condition"](lifecycle)]

    if not matched:
        return 0.0

    # 여러 패턴 매칭 시 최고점 사용
    return max(p["score"] for p in matched)
```

### 5.11 Composite Attractiveness Score

```python
SIGNAL_WEIGHTS = {
    "recurring_host":        0.15,
    "quick_fill":            0.15,
    "high_satisfaction":     0.15,
    "rich_reviews":          0.15,
    "clean_completion":      0.10,
    "repeat_participants":   0.10,
    "host_credibility":      0.10,
    "charming_imperfection": 0.10,
}

def calculate_attractiveness(
    lifecycle: SpotLifecycle,
    all_lifecycles: list[SpotLifecycle],
) -> AttractivenessResult:

    signals = {
        "recurring_host":        signal_recurring_host(lifecycle, all_lifecycles),
        "quick_fill":            signal_quick_fill(lifecycle),
        "high_satisfaction":     signal_high_satisfaction(lifecycle),
        "rich_reviews":          signal_rich_reviews(lifecycle),
        "clean_completion":      signal_clean_completion(lifecycle),
        "repeat_participants":   signal_repeat_participants(lifecycle),
        "host_credibility":      signal_host_credibility(lifecycle),
        "charming_imperfection": signal_charming_imperfection(lifecycle),
    }

    composite = sum(
        SIGNAL_WEIGHTS[name] * score
        for name, score in signals.items()
    )

    # 주요 매력 유형 판정 (가장 높은 signal)
    primary_charm = max(signals, key=signals.get)

    # 매력 유형 분류
    charm_type = classify_charm_type(signals)

    return AttractivenessResult(
        spot_id=lifecycle.spot_id,
        run_id=lifecycle.run_id,
        signals=signals,
        composite_score=composite,
        primary_charm=primary_charm,
        charm_type=charm_type,
    )
```

### 5.12 매력 유형 분류 (`charm_type`)

피드 구성 시 다양성을 보장하기 위해 각 스팟을 유형으로 분류한다.

```python
def classify_charm_type(signals: dict[str, float]) -> str:
    """주요 매력 유형을 하나로 분류"""

    # 우선순위 기반 분류 (복수 signal이 높을 때 대표 유형 결정)
    if signals["recurring_host"] >= 0.6 and signals["repeat_participants"] >= 0.4:
        return "series_favorite"       # 시리즈 인기 모임

    if signals["quick_fill"] >= 0.7:
        return "hot_demand"            # 인기 폭발

    if signals["rich_reviews"] >= 0.7 and signals["high_satisfaction"] >= 0.7:
        return "highly_rated"          # 리뷰 명소

    if signals["charming_imperfection"] >= 0.5:
        return "underdog_charm"        # 불완전한 매력

    if signals["host_credibility"] >= 0.7:
        return "trusted_host"          # 믿을 만한 호스트

    if signals["clean_completion"] >= 0.8:
        return "safe_bet"              # 안심 모임

    return "general_good"              # 일반 우수
```

---

## 6. Diversity-Aware Selection

상위 N개를 단순히 점수순으로 자르면 비슷한 스팟만 선택된다.  
카테고리, 지역, 매력 유형의 다양성을 보장하면서 선별한다.

### 6.1 Selection Quota

```python
FEED_MIX_QUOTA = {
    # 매력 유형별 목표 비율 (500개 기준)
    "series_favorite":  0.15,   #  75개 — "3회차 모임" 류
    "hot_demand":       0.15,   #  75개 — "마감 임박" 류
    "highly_rated":     0.25,   # 125개 — "리뷰 좋은" 류
    "underdog_charm":   0.10,   #  50개 — "불완전한 매력" 류
    "trusted_host":     0.10,   #  50개 — "믿을 만한 호스트" 류
    "safe_bet":         0.15,   #  75개 — "안심 모임" 류
    "general_good":     0.10,   #  50개 — 기타 우수
}
```

### 6.2 Selection 알고리즘

```python
def select_curated_spots(
    all_results: list[AttractivenessResult],
    target_count: int = 500,
    quota: dict = FEED_MIX_QUOTA,
    min_score: float = 0.35,
) -> list[AttractivenessResult]:

    # 1. 최소 점수 미달 제거
    candidates = [r for r in all_results if r.composite_score >= min_score]

    # 2. charm_type별 풀 분류
    pools = defaultdict(list)
    for r in candidates:
        pools[r.charm_type].append(r)

    # 각 풀 내부를 점수순 정렬
    for charm_type in pools:
        pools[charm_type].sort(key=lambda r: r.composite_score, reverse=True)

    selected = []

    # 3. quota별 선택
    for charm_type, ratio in quota.items():
        target = int(target_count * ratio)
        pool = pools.get(charm_type, [])
        picked = pool[:target]
        selected.extend(picked)

    # 4. 부족분 보충 (quota 미달 유형이 있으면 나머지에서 채움)
    remaining_target = target_count - len(selected)
    if remaining_target > 0:
        selected_ids = {r.spot_id for r in selected}
        overflow = [
            r for r in candidates
            if r.spot_id not in selected_ids
        ]
        overflow.sort(key=lambda r: r.composite_score, reverse=True)
        selected.extend(overflow[:remaining_target])

    # 5. 지역/카테고리 다양성 검증
    selected = enforce_diversity_constraints(selected)

    return selected[:target_count]


def enforce_diversity_constraints(
    selected: list[AttractivenessResult],
) -> list[AttractivenessResult]:
    """특정 지역/카테고리가 과도하게 편중되지 않도록 조정"""

    MAX_REGION_RATIO = 0.15     # 단일 지역이 전체의 15% 초과 불가
    MAX_CATEGORY_RATIO = 0.30   # 단일 카테고리가 전체의 30% 초과 불가

    # 지역별 카운트 체크
    region_counts = Counter(r.region_id for r in selected)
    max_per_region = int(len(selected) * MAX_REGION_RATIO)

    # 초과 지역의 하위 점수 스팟을 제거
    final = []
    region_tracker = Counter()
    category_tracker = Counter()

    for r in sorted(selected, key=lambda x: x.composite_score, reverse=True):
        if region_tracker[r.region_id] >= max_per_region:
            continue
        max_per_category = int(len(selected) * MAX_CATEGORY_RATIO)
        if category_tracker[r.category] >= max_per_category:
            continue
        final.append(r)
        region_tracker[r.region_id] += 1
        category_tracker[r.category] += 1

    return final
```

---

## 7. 미완료 스팟 활용 (OPEN / MATCHED 상태)

완주된 스팟만 선별하면 피드가 "과거 기록"만 된다.  
실제 서비스 피드에는 **현재 모집 중인 스팟**이 핵심이다.

### 7.1 OPEN 스팟 합성 전략

시뮬레이션에서 COMPLETED된 스팟의 lifecycle을 기반으로,  
**"아직 모집 중인 시점"의 스냅샷**을 역으로 생성한다.

```python
def synthesize_open_snapshot(lifecycle: SpotLifecycle) -> OpenSpotSnapshot:
    """완주 스팟의 초기 상태를 모집 중 스팟처럼 재구성"""

    # 참여자 수를 capacity - 1 또는 capacity - 2로 설정 (마감 임박 연출)
    if lifecycle.capacity <= 3:
        shown_participants = lifecycle.capacity - 1
    else:
        # 70% 확률로 1자리 남음, 30% 확률로 2자리 남음
        shown_participants = lifecycle.capacity - (1 if random() < 0.7 else 2)

    return OpenSpotSnapshot(
        spot_id=lifecycle.spot_id,
        status="recruiting",
        host_agent_id=lifecycle.host_agent_id,
        region_id=lifecycle.region_id,
        category=lifecycle.category,
        capacity=lifecycle.capacity,
        current_participants=shown_participants,
        # 이 스팟이 완주 후 좋은 결과를 낸 사실은 콘텐츠에 반영하지 않음
        # (아직 모집 중인 것처럼 보여야 하므로)
        is_synthetic_open=True,
    )
```

### 7.2 상태별 피드 구성 비율

```
모집 중 (OPEN/recruiting)     40%   ← CTA 핵심. "지금 참여 가능"
진행 중 (IN_PROGRESS)         10%   ← "지금 이 순간 진행 중" 라이브 느낌
완료 + 리뷰 있음              35%   ← 사회적 증거
완료 + 리뷰 없음              15%   ← 기본 이력
```

---

## 8. DB 스키마

### 8.1 `simulation_run`

```sql
CREATE TABLE simulation_run (
    id              UUID PRIMARY KEY,
    run_id          VARCHAR(50) NOT NULL UNIQUE,
    variant_name    VARCHAR(50) NOT NULL,
    variant_config  JSONB NOT NULL,           -- RunVariant 전체 파라미터
    agent_count     INT NOT NULL,
    sim_duration_ticks INT NOT NULL,
    random_seed     INT,
    -- 결과 요약
    total_spots_created INT,
    total_spots_completed INT,
    total_spots_canceled INT,
    avg_matching_rate    DOUBLE PRECISION,
    avg_satisfaction     DOUBLE PRECISION,
    avg_noshow_rate      DOUBLE PRECISION,
    -- 메타
    execution_time_ms   BIGINT,
    status              VARCHAR(20) DEFAULT 'running',  -- running / completed / failed
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);
```

### 8.2 `spot_lifecycle`

```sql
CREATE TABLE spot_lifecycle (
    id                      UUID PRIMARY KEY,
    spot_id                 VARCHAR(50) NOT NULL,
    run_id                  VARCHAR(50) NOT NULL REFERENCES simulation_run(run_id),
    -- 기본 정보
    host_agent_id           VARCHAR(50) NOT NULL,
    host_persona_type       VARCHAR(50),
    region_id               VARCHAR(50) NOT NULL,
    category                VARCHAR(30) NOT NULL,
    spot_type               VARCHAR(50),
    capacity                INT,
    min_participants        INT,
    -- 타임라인
    created_at_tick         INT NOT NULL,
    matched_at_tick         INT,
    completed_at_tick       INT,
    settled_at_tick         INT,
    -- 참여
    participant_count       INT DEFAULT 0,
    checked_in_count        INT DEFAULT 0,
    noshow_count            INT DEFAULT 0,
    cancel_join_count       INT DEFAULT 0,
    repeat_participant_count INT DEFAULT 0,
    -- 결과
    final_status            VARCHAR(30) NOT NULL,
    avg_satisfaction        DOUBLE PRECISION,
    review_count            INT DEFAULT 0,
    avg_review_rating       DOUBLE PRECISION,
    -- 호스트 맥락
    host_trust_before       DOUBLE PRECISION,
    host_trust_after        DOUBLE PRECISION,
    host_prev_spot_count    INT DEFAULT 0,
    host_prev_avg_satisfaction DOUBLE PRECISION,
    -- 시간 파생
    fill_duration_ticks     INT,
    -- 이슈
    issues_json             JSONB,
    is_disputed             BOOLEAN DEFAULT FALSE,
    -- 전체 데이터 (디버깅/상세 분석용)
    full_lifecycle_json     JSONB,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(spot_id, run_id)
);

CREATE INDEX idx_lifecycle_run ON spot_lifecycle(run_id);
CREATE INDEX idx_lifecycle_status ON spot_lifecycle(final_status);
CREATE INDEX idx_lifecycle_host ON spot_lifecycle(host_agent_id, run_id);
CREATE INDEX idx_lifecycle_region ON spot_lifecycle(region_id);
CREATE INDEX idx_lifecycle_category ON spot_lifecycle(category);
```

### 8.3 `spot_attractiveness`

```sql
CREATE TABLE spot_attractiveness (
    id                      UUID PRIMARY KEY,
    spot_id                 VARCHAR(50) NOT NULL,
    run_id                  VARCHAR(50) NOT NULL,
    -- 개별 signal 점수
    sig_recurring_host      DOUBLE PRECISION DEFAULT 0,
    sig_quick_fill          DOUBLE PRECISION DEFAULT 0,
    sig_high_satisfaction   DOUBLE PRECISION DEFAULT 0,
    sig_rich_reviews        DOUBLE PRECISION DEFAULT 0,
    sig_clean_completion    DOUBLE PRECISION DEFAULT 0,
    sig_repeat_participants DOUBLE PRECISION DEFAULT 0,
    sig_host_credibility    DOUBLE PRECISION DEFAULT 0,
    sig_charming_imperfection DOUBLE PRECISION DEFAULT 0,
    -- 종합
    composite_score         DOUBLE PRECISION NOT NULL,
    primary_charm           VARCHAR(50),
    charm_type              VARCHAR(50) NOT NULL,
    -- 선별
    is_selected             BOOLEAN DEFAULT FALSE,
    selection_rank          INT,
    feed_status             VARCHAR(20),            -- recruiting / completed / in_progress
    -- 메타
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(spot_id, run_id),
    FOREIGN KEY (spot_id, run_id) REFERENCES spot_lifecycle(spot_id, run_id)
);

CREATE INDEX idx_attract_composite ON spot_attractiveness(composite_score DESC);
CREATE INDEX idx_attract_selected ON spot_attractiveness(is_selected) WHERE is_selected = TRUE;
CREATE INDEX idx_attract_charm ON spot_attractiveness(charm_type);
```

### 8.4 `curation_batch`

선별 배치 기록. content pipeline에 넘기는 단위.

```sql
CREATE TABLE curation_batch (
    id                  UUID PRIMARY KEY,
    batch_id            VARCHAR(50) NOT NULL UNIQUE,
    -- 입력
    source_run_ids      JSONB NOT NULL,               -- 사용한 run_id 목록
    total_candidates    INT NOT NULL,
    -- 선별 결과
    selected_count      INT NOT NULL,
    min_composite_score DOUBLE PRECISION,
    avg_composite_score DOUBLE PRECISION,
    -- 다양성 지표
    charm_type_distribution JSONB,                    -- {"series_favorite": 75, ...}
    region_distribution     JSONB,
    category_distribution   JSONB,
    -- 메타
    selection_config    JSONB,                        -- quota, min_score 등 파라미터
    status              VARCHAR(20) DEFAULT 'created', -- created / published / archived
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 9. 실행 Job 구성

기존 synthetic content pipeline의 Job 번호 앞에 위치한다.

### Job 0-1. `run_multi_simulation`

```
입력:  RUN_VARIANTS 설정 (variant 목록 + 공통 config)
처리:  각 variant로 시뮬레이터 실행
출력:  run별 event_log.jsonl + simulation_run 레코드

병렬 실행 가능:
  - 각 run은 독립적이므로 multiprocessing 또는 sequential 모두 가능
  - Phase 1 (50 agents × 48 ticks): sequential로 충분
  - Phase 3 (5,000 agents × 336 ticks): 병렬 권장
```

### Job 0-2. `assemble_lifecycles`

```
입력:  각 run의 event_log + agent 상태 + spot 상태
처리:  raw events → SpotLifecycle 객체 조립
출력:  spot_lifecycle 테이블 적재

주의:
  - 한 run 내에서 동일 host의 스팟 히스토리를 시간순으로 정렬해야 함
  - repeat_participant 판정을 위해 에이전트-스팟 참여 이력 cross-reference 필요
```

### Job 0-3. `score_attractiveness`

```
입력:  spot_lifecycle 전체 (all runs)
처리:  8개 signal 계산 → composite score → charm_type 분류
출력:  spot_attractiveness 테이블 적재

의존:
  - Signal 1 (recurring_host): 같은 run 내 동일 호스트의 다른 lifecycle 필요
  - Signal 6 (repeat_participants): 같은 run 내 cross-reference 필요
  - 나머지 signal: 개별 lifecycle 내부 데이터만으로 계산 가능
```

### Job 0-4. `curate_and_select`

```
입력:  spot_attractiveness 전체
처리:  quota 기반 diversity-aware selection
출력:  is_selected=TRUE 마킹 + curation_batch 레코드 생성

후처리:
  - 선별된 스팟 중 일부를 OPEN 스냅샷으로 변환 (섹션 7)
  - feed_status 할당 (recruiting / completed / in_progress)
```

### Job 0-5. `export_curated_specs`

```
입력:  선별된 spot_lifecycle + spot_attractiveness
처리:  content_spec 형태로 변환 (기존 파이프라인 입력 포맷)
출력:  content_spec.json 배열 → 기존 Job 1 (build_content_spec)에 공급

변환 시 추가 정보:
  - charm_type → 콘텐츠 생성 힌트로 전달
    예: "hot_demand" → "마감 임박" 느낌의 제목/태그 유도
    예: "series_favorite" → "N회차" 표현 포함 유도
  - feed_status → 콘텐츠 상태 반영
```

---

## 10. Content Spec 확장

기존 content_spec에 매력도 정보를 추가하여 LLM이 더 매력적인 콘텐츠를 생성하도록 유도한다.

### 10.1 추가 필드

```json
{
  "spot_id": "S_22019",
  "region": "수원시 연무동",
  "category": "food",

  "... 기존 필드 ...",

  "attractiveness_context": {
    "charm_type": "series_favorite",
    "primary_charm": "recurring_host",
    "composite_score": 0.82,

    "narrative_hints": [
      "이 호스트는 같은 카테고리로 3번째 모임을 개최합니다",
      "이전 2회 모임의 평균 만족도는 0.78이었습니다",
      "재참여자가 1명 있습니다"
    ],

    "feed_status": "recruiting",
    "feed_display_hints": {
      "show_episode_badge": true,
      "episode_number": 3,
      "show_quick_fill_badge": false,
      "show_review_highlight": true,
      "review_highlight_count": 2,
      "show_remaining_seats": true,
      "remaining_seats": 1
    }
  }
}
```

### 10.2 Charm Type → 콘텐츠 생성 지침 매핑

```python
CHARM_TO_CONTENT_DIRECTIVE = {
    "series_favorite": {
        "title_hint": "회차 번호 또는 '다시 돌아온' 표현 포함 권장",
        "summary_hint": "이전 회차 참여자 반응 언급 가능",
        "tag_suggestions": ["인기모임", "N회차", "단골호스트"],
        "review_emphasis": "재참여 의향, 이전 회차와 비교",
    },
    "hot_demand": {
        "title_hint": "구체적 모집 인원 명시 권장",
        "summary_hint": "간결하고 임팩트 있게, 장황한 설명 지양",
        "tag_suggestions": ["빠른마감", "소수정예"],
        "review_emphasis": None,
    },
    "highly_rated": {
        "title_hint": "활동 내용 중심, 과장 표현 지양",
        "summary_hint": "실제 참여자 반응을 간접 언급 가능",
        "tag_suggestions": ["만족도높음", "추천"],
        "review_emphasis": "구체적 만족 포인트 중심",
    },
    "underdog_charm": {
        "title_hint": "진솔한 톤, 완벽하지 않아도 괜찮다는 느낌",
        "summary_hint": "솔직한 상황 설명 포함 (노쇼가 있었지만...)",
        "tag_suggestions": ["솔직후기", "리얼"],
        "review_emphasis": "불완전한 상황에서도 좋았던 점",
    },
    "trusted_host": {
        "title_hint": "호스트 경험/전문성 암시",
        "summary_hint": "호스트 소개 비중 높게",
        "tag_suggestions": ["경험많은호스트", "안심"],
        "review_emphasis": "호스트에 대한 평가 중심",
    },
    "safe_bet": {
        "title_hint": "안정감 있는 표현, 구체적 정보 위주",
        "summary_hint": "장소, 시간, 비용 등 팩트 중심",
        "tag_suggestions": ["초면환영", "편안한분위기"],
        "review_emphasis": "전반적 분위기, 진행 매끄러움",
    },
    "general_good": {
        "title_hint": "자유",
        "summary_hint": "자유",
        "tag_suggestions": [],
        "review_emphasis": "자유",
    },
}
```

---

## 11. 품질 안전장치

### 11.1 Multi-Run 결과 검증

각 run 완료 후 기본 sanity check를 수행한다.

```python
def validate_run_result(run: SimulationRun, event_log: list) -> RunValidation:
    checks = []

    # 최소 이벤트 수
    checks.append(("min_events", len(event_log) >= 100))

    # 스팟 생성 수
    created = [e for e in event_log if e.event_type == "CREATE_SPOT"]
    checks.append(("min_spots_created", len(created) >= 20))

    # 완료 스팟 존재
    completed = [e for e in event_log if e.event_type == "SPOT_COMPLETED"]
    checks.append(("has_completed_spots", len(completed) >= 5))

    # 매칭률 범위 (너무 낮거나 높으면 파라미터 이상)
    matching_rate = len(completed) / max(1, len(created))
    checks.append(("matching_rate_range", 0.20 <= matching_rate <= 0.95))

    # 에이전트 활동 분포 (1명이 90% 이벤트를 차지하면 이상)
    agent_events = Counter(e.agent_id for e in event_log if e.agent_id)
    top_agent_ratio = agent_events.most_common(1)[0][1] / len(event_log)
    checks.append(("agent_distribution", top_agent_ratio < 0.15))

    all_passed = all(passed for _, passed in checks)
    return RunValidation(checks=checks, passed=all_passed)
```

### 11.2 Attractiveness Score 분포 검증

스코어링 후 분포가 건강한지 확인한다.

```python
def validate_attractiveness_distribution(results: list[AttractivenessResult]) -> bool:
    scores = [r.composite_score for r in results]

    # 평균이 너무 높으면 변별력 없음 (모든 스팟이 매력적 = 아무것도 매력적이지 않음)
    assert mean(scores) < 0.7, "평균 매력도가 너무 높음 — signal 계산 검토 필요"

    # 표준편차가 너무 낮으면 변별력 없음
    assert stdev(scores) > 0.10, "매력도 분포가 너무 좁음 — signal 가중치 검토 필요"

    # charm_type 분포 체크 (한 유형이 80% 이상 차지하면 이상)
    type_counts = Counter(r.charm_type for r in results)
    max_type_ratio = type_counts.most_common(1)[0][1] / len(results)
    assert max_type_ratio < 0.5, f"charm_type 편중: {type_counts.most_common(1)}"

    return True
```

### 11.3 Cross-Run 중복 제거

다른 run에서 거의 동일한 스팟이 선별될 수 있다.  
(같은 지역 + 같은 카테고리 + 비슷한 시간대 + 비슷한 결과)

```python
def deduplicate_across_runs(selected: list[AttractivenessResult]) -> list[AttractivenessResult]:
    """다른 run에서 온 유사 스팟 제거"""

    seen_signatures = set()
    deduped = []

    for r in sorted(selected, key=lambda x: x.composite_score, reverse=True):
        # 유사성 시그니처: 지역 + 카테고리 + 호스트 페르소나 + 참여자 수 + charm_type
        sig = (
            r.region_id,
            r.category,
            r.host_persona_type,
            r.participant_count,
            r.charm_type,
        )

        if sig in seen_signatures:
            continue  # 유사 스팟 이미 선별됨 → 스킵

        seen_signatures.add(sig)
        deduped.append(r)

    return deduped
```

---

## 12. 비용 분석

### 12.1 시뮬레이션 비용

```
5 runs × (500 agents × 336 ticks) = 840,000 결정 루프
Python 순수 계산: < 30초 (Phase 2 기준)
LLM 호출: 0회
클라우드 비용: 사실상 0 (단일 서버에서 실행)
```

### 12.2 LLM 비용 변화 (기존 대비)

```
기존 (1 run → 500 스팟 직접 파이프라인):
  생성: 500 × 5종 × 2후보 = 5,000회
  Critic: 75 × 5 = 375회
  재생성 (30% reject): ~1,500회
  총: ~6,875회

개선 (5 runs → 선별 500 스팟 → 파이프라인):
  시뮬레이션: 0회 (코드)
  스코어링: 0회 (코드)
  생성: 500 × 5종 × 2후보 = 5,000회
  Critic: 75 × 5 = 375회
  재생성 (15% reject, 좋은 재료 효과): ~750회
  총: ~6,125회

차이: 약 750회 절감 (reject율 30% → 15% 예상)
```

핵심: **LLM 비용은 거의 동일하지만, 콘텐츠 품질이 크게 향상된다.**  
더 매력적인 시뮬레이션 로그를 input으로 주니까 LLM이 더 자연스럽고 설득력 있는 콘텐츠를 생성할 수 있다.

---

## 13. 바이럴 시뮬레이션 페이지 연결

기존 플랜의 `/simulation` 바이럴 페이지에 curated 데이터를 공급한다.

### 13.1 데이터 흐름

```
[Curated Spot Pool (500개)]
        ↓
    필터: charm_type별 대표 스팟 30~50개 추출
        ↓
    변환: 카카오맵 재생용 이벤트 시퀀스
        ↓
    /simulation 페이지 프리빌트 JSON
```

### 13.2 바이럴 페이지용 스팟 선별 기준

```python
VIRAL_PAGE_SELECTION = {
    "hot_demand":       5,    # 빠른 마감 → 마커 빠르게 등장
    "series_favorite":  5,    # 시리즈 → 같은 위치에 반복 마커
    "highly_rated":     8,    # 리뷰 좋은 → 별점 팝업 연출
    "safe_bet":         7,    # 깔끔 완주 → 기본 성사 애니메이션
    "underdog_charm":   3,    # 불완전 → 노쇼 마커 → 그래도 성공 연출
    "trusted_host":     5,    # 호스트 → 프로필 팝업 연출
    "simultaneous":     7,    # weekend_peak run에서 동시 진행 스팟 묶음
}
```

### 13.3 이벤트 시퀀스 변환

```python
def to_viral_event_sequence(lifecycle: SpotLifecycle) -> list[dict]:
    """SpotLifecycle → 바이럴 페이지 재생용 이벤트 목록"""
    events = []

    events.append({
        "type": "SPOT_CREATED",
        "tick": lifecycle.created_at_tick,
        "position": get_region_center(lifecycle.region_id),
        "data": {
            "title": f"{lifecycle.category} 모임",
            "capacity": lifecycle.capacity,
            "host_persona": lifecycle.host_persona_type,
        }
    })

    # 참여자 합류 이벤트
    for i, participant in enumerate(lifecycle.participants):
        join_tick = lifecycle.created_at_tick + (i + 1) * 2  # 2tick 간격
        events.append({
            "type": "JOIN",
            "tick": join_tick,
            "data": {"participant_index": i + 1}
        })

    if lifecycle.matched_at_tick:
        events.append({
            "type": "MATCHED",
            "tick": lifecycle.matched_at_tick,
        })

    if lifecycle.completed_at_tick:
        events.append({
            "type": "COMPLETED",
            "tick": lifecycle.completed_at_tick,
        })

    if lifecycle.reviews:
        events.append({
            "type": "REVIEW",
            "tick": (lifecycle.completed_at_tick or 0) + 1,
            "data": {
                "avg_rating": lifecycle.avg_review_rating,
                "review_count": lifecycle.review_count,
            }
        })

    return events
```

---

## 14. 프로젝트 구조 (추가분)

기존 `spot-simulator/` 프로젝트에 추가되는 디렉토리/파일:

```
spot-simulator/
├── ... (기존 구조 유지)
│
├── multi_run/
│   ├── variants.py                   # RunVariant 정의 + 기본 5개 variant
│   ├── runner.py                     # multi-run 실행 오케스트레이터
│   └── config.py                     # 공통 설정 (target_count, quotas 등)
│
├── curation/
│   ├── assembler.py                  # Spot Lifecycle Assembler
│   ├── signals.py                    # 8개 매력 signal 함수
│   ├── scorer.py                     # Composite score + charm_type 분류
│   ├── selector.py                   # Diversity-aware selection
│   ├── deduplicator.py               # Cross-run 중복 제거
│   ├── open_snapshot.py              # OPEN 스팟 스냅샷 합성
│   ├── export.py                     # content_spec 변환 + 출력
│   └── validators.py                 # 분포 검증, sanity check
│
├── models/
│   ├── ... (기존)
│   ├── lifecycle.py                  # SpotLifecycle 데이터 모델
│   ├── attractiveness.py             # AttractivenessResult 데이터 모델
│   └── curation_batch.py             # CurationBatch 데이터 모델
│
├── output/
│   ├── ... (기존)
│   ├── curated_spots.json            # 선별 결과
│   ├── content_specs/                # content pipeline 입력용
│   │   └── batch_{id}.json
│   └── viral_events/                 # 바이럴 페이지용
│       └── viral_sequence.json
│
└── analysis/
    ├── ... (기존)
    ├── attractiveness_report.py       # 매력도 분포 리포트
    └── variant_comparison.py          # run 간 비교 분석
```

---

## 15. 구현 타임라인

기존 시뮬레이터 Phase 2 완료 이후 시작을 전제로 한다.  
(Lifecycle + 상호작용 + 정산/리뷰가 동작해야 매력도 스코어링이 의미 있음)

### Phase A: Multi-Run 기반 (3일)

```
Day 1:
  - RunVariant 데이터 모델 정의
  - 기본 5개 variant 설정값 확정
  - multi_run/runner.py 구현 (sequential 실행)
  - simulation_run 테이블 생성

Day 2:
  - SpotLifecycle 데이터 모델 정의
  - assembler.py 구현 (event_log → lifecycle 조립)
  - spot_lifecycle 테이블 생성
  - 5 runs 실행 → lifecycle 적재 테스트

Day 3:
  - run 결과 검증 (validate_run_result)
  - variant 간 결과 비교 분석
  - 파라미터 초기 튜닝
```

### Phase B: Attractiveness Scoring (3일)

```
Day 4:
  - Signal 1~4 구현 (recurring_host, quick_fill, high_satisfaction, rich_reviews)
  - 개별 signal 단위 테스트

Day 5:
  - Signal 5~8 구현 (clean_completion, repeat_participants, host_credibility, charming_imperfection)
  - CHARMING_PATTERNS 정의 및 테스트

Day 6:
  - Composite scorer 구현
  - charm_type 분류 로직
  - spot_attractiveness 테이블 생성
  - 전체 스코어링 실행 → 분포 검증
```

### Phase C: Selection + Export (2일)

```
Day 7:
  - Diversity-aware selection 구현
  - Cross-run 중복 제거 구현
  - curation_batch 테이블 생성
  - 500개 선별 실행 → quota 대비 실제 분포 확인

Day 8:
  - OPEN 스냅샷 합성 로직
  - content_spec 변환 + export
  - 기존 content pipeline Job 1과 연결 테스트
  - 바이럴 이벤트 시퀀스 변환
```

### Phase D: 튜닝 + 운영화 (2일)

```
Day 9:
  - Signal 가중치 튜닝 (실제 분포 보고 조정)
  - Variant 파라미터 튜닝
  - Quota 비율 조정
  - 매력도 리포트 자동화

Day 10:
  - End-to-end 파이프라인 실행 (multi-run → curation → content pipeline)
  - 생성된 콘텐츠 샘플 수동 검토
  - 문서 정리, 운영 가이드 작성
```

**합계: ~10일** (시뮬레이터 Phase 2 완료 전제)

---

## 16. 성공 지표

| 지표                                       | 목표              | 측정 방법                                     |
| ------------------------------------------ | ----------------- | --------------------------------------------- |
| Curated 스팟 평균 매력도                   | ≥ 0.60            | `AVG(composite_score) WHERE is_selected=TRUE` |
| 전체 풀 대비 선별 비율                     | 25~40%            | `selected_count / total_candidates`           |
| charm_type 다양성 (최대 유형 비율)         | ≤ 35%             | `MAX(type_count) / total_selected`            |
| 지역 편중도 (최대 지역 비율)               | ≤ 15%             | `MAX(region_count) / total_selected`          |
| 카테고리 편중도 (최대 카테고리 비율)       | ≤ 30%             | `MAX(category_count) / total_selected`        |
| Content pipeline reject율 (기존 대비)      | ≤ 20% (기존 ~30%) | `rejected / total_generated`                  |
| recurring_host signal > 0.5인 스팟 비율    | ≥ 10%             | curated pool 내                               |
| repeat_participants signal > 0인 스팟 비율 | ≥ 8%              | curated pool 내                               |
| Multi-run 총 실행 시간                     | ≤ 3분 (5 runs)    | wall clock                                    |

---

## 17. 리스크와 대응

| 리스크                                       | 영향                           | 대응                                             |
| -------------------------------------------- | ------------------------------ | ------------------------------------------------ |
| 모든 variant에서 비슷한 결과                 | 선별 풀 다양성 부족            | variant 간 파라미터 차이 확대, seed 변경         |
| Attractiveness Score가 대부분 0.3~0.4에 몰림 | 변별력 없음                    | signal 가중치 재조정, threshold 조정             |
| recurring_host signal이 거의 0               | 시리즈 패턴 부족               | high_host variant의 host_score_mean 추가 상향    |
| 특정 지역만 매력적 스팟 집중                 | 피드 지역 편중                 | enforce_diversity_constraints 강화               |
| OPEN 스냅샷이 부자연스러움                   | 모집 중 피드 품질 저하         | 합성 로직 정교화, content pipeline에서 추가 검증 |
| Content pipeline이 charm_type 힌트를 무시    | 매력 유형별 콘텐츠 차별화 실패 | LLM 프롬프트에 charm directive 강화              |

---

## 18. 향후 확장

### v1.1 — Feedback Loop

실서비스에서 유저 반응(클릭률, 참여 전환율)을 수집하고,  
Attractiveness Score의 signal 가중치를 역으로 학습한다.

```
유저 클릭률 높은 스팟의 signal 분포 분석
  → "quick_fill + rich_reviews 조합이 CTR 높다"
  → signal 가중치 자동 조정
```

### v1.2 — Dynamic Variant Generation

이전 curation 결과에서 부족한 charm_type을 분석하고,  
해당 유형을 더 많이 생산하는 variant를 자동 생성한다.

```
"series_favorite가 quota 대비 60%만 채워짐"
  → host_score 더 높은 variant 자동 추가
  → 다음 배치에서 보충
```

### v1.3 — Real-Time Curation

시뮬레이션을 주기적으로 실행하고,  
실데이터 유입에 따라 synthetic 비율을 자동 조절한다.  
(synthetic content pipeline의 Phase 2~3 전환 전략과 연동)
