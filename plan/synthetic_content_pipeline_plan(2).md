# Spot Synthetic Content Pipeline — 구현 플랜

> 시뮬레이션 로그를 구조화된 실제 서비스 콘텐츠로 렌더링하고,  
> 자동 검수해서 통과한 것만 배포하는 synthetic content pipeline

---

## 1. 핵심 원칙

### 원칙 1. LLM은 "문서 작성기"

- 시뮬레이터가 사실(fact)을 결정함
- LLM은 구조화 데이터를 자연어로 렌더링만 함
- LLM에게 자유 생성 권한 없음

### 원칙 2. 생성과 검증의 분리

```
생성기 → 검증기 → 재생성기
```

- 한 번 생성 후 바로 쓰지 않음
- 반드시 3단계를 거침

### 원칙 3. 텍스트보다 먼저 JSON

```
Simulation Output → Structured Content JSON → Natural Language Rendering
```

### 원칙 4. 스팟 단위 일관성 (피드백 반영)

- 개별 콘텐츠 검증만으로 부족
- feed / detail / review / message가 하나의 스팟 안에서 서로 모순되지 않아야 함
- **개별 검증 → 스팟 단위 cross-validation → 최종 승인** 3단 구조

### 원칙 5. Critic 비용 관리 (피드백 반영)

- 전수 critic 평가는 비용상 불가
- deterministic rule로 1차 필터링 → 통과한 것만 critic 투입
- MVP에서는 전체의 10~20%만 critic 샘플링

---

## 2. 전체 아키텍처

```
[Simulation Engine]
        ↓
[Event Log / Spot State / Review State]
        ↓
[Content Spec Builder]
        ↓
[LLM Generation Service]
    ├─ Feed Generator
    ├─ Spot Detail Generator
    ├─ Review Generator
    └─ Message Generator
        ↓
[Validation Pipeline]
    ├─ Layer 1: Schema Validation (코드)
    ├─ Layer 2: Rule Validation (deterministic)
    ├─ Layer 3: Cross-Reference Validation (스팟 단위)  ← NEW
    ├─ Layer 4: LLM Critic Validation (샘플링)
    ├─ Layer 5: Diversity Check (n-gram / TF-IDF)  ← NEW
    └─ Layer 6: Scoring / Ranking
        ↓
[Approved Synthetic Content DB]
        ↓
[Version Transition Manager]  ← NEW
        ↓
[Real Service Read Model]
```

---

## 3. 생성 대상 콘텐츠 (5종)

### A. Feed Preview

리스트에 보이는 요약 카드

| 필드            | 설명                 | 예시                                                         |
| --------------- | -------------------- | ------------------------------------------------------------ |
| title           | 제목 (12~40자)       | 연무동 저녁 한 끼 같이할 4명 모집                            |
| summary         | 한 줄 소개 (1~2문장) | 가볍게 식사하면서 취향 이야기 나누는 소규모 저녁 모임이에요. |
| tags            | 태그 배열            | ["저녁모임", "소규모", "연무동", "초면환영"]                 |
| price_label     | 금액 라벨            | 1인 예상 1.5~2만원                                           |
| region_label    | 지역 라벨            | 수원시 연무동                                                |
| time_label      | 일정 라벨            | 4/18(금) 19:00                                               |
| status          | 모집 상태            | recruiting / closed / in_progress                            |
| supporter_label | 서포터 유형          | supporter_teacher                                            |

### B. Spot Detail

상세 페이지용 콘텐츠

- 제목, 소개글, 활동 목적
- 진행 방식, 준비물, 대상
- 시간표, 비용 포함 내역
- 서포터/호스트 정보 요약

### C. Spot Plan / Itinerary

실제 활동 타임라인

```
19:00 만나기
19:10 아이스브레이킹
19:30 활동 시작
20:30 마무리
20:40 후기 공유
```

### D. Communication Snippets

커뮤니케이션 흔적 4종

- 모집 소개 문장
- 참여 승인 메시지
- 당일 안내 메시지
- 종료 후 감사 메시지

### E. Review

활동 종료 후 리뷰

- 별점 (분포: 5점 55% / 4점 30% / 3점 10% / 1~2점 5%)
- 만족도 태그, 자유 리뷰, 추천 여부, 재참여 의향

---

## 4. LLM 입력 스키마 (Content Spec)

```json
{
    "spot_id": "S_22019",
    "region": "수원시 연무동",
    "category": "food",
    "spot_type": "casual_meetup",
    "host_persona": {
        "type": "supporter_teacher",
        "tone": "친절하고 실용적",
        "communication_style": "가볍고 직접적"
    },
    "participants": {
        "expected_count": 4,
        "persona_mix": ["night_social", "casual_foodie"]
    },
    "schedule": {
        "date": "2026-04-18",
        "start_time": "19:00",
        "duration_minutes": 120
    },
    "budget": {
        "price_band": 1,
        "expected_cost_per_person": 18000
    },
    "activity_constraints": {
        "indoor": true,
        "beginner_friendly": true,
        "supporter_required": true
    },
    "plan_outline": ["가볍게 인사", "식사와 대화", "다음 모임 취향 공유"],
    "activity_result": {
        "actual_participants": 3,
        "no_show_count": 1,
        "duration_actual_minutes": 110,
        "issues": ["late_start_5min"],
        "overall_sentiment": "positive"
    }
}
```

---

## 5. 검증 파이프라인 (6 Layer)

### Layer 1. Schema Validation (코드)

자동화된 구조 체크

| 규칙        | 조건              |
| ----------- | ----------------- |
| 제목 길이   | 12~40자           |
| summary     | 1~2문장           |
| price_label | 필수 존재         |
| plan steps  | 최소 3개          |
| review 별점 | 1~5 범위          |
| 필수 필드   | null / empty 불가 |

### Layer 2. Rule Validation (deterministic)

비즈니스 규칙 검증

| 규칙            | reject 조건                                 |
| --------------- | ------------------------------------------- |
| 지역 일관성     | 입력 region과 콘텐츠 지역명 불일치          |
| 카테고리 일관성 | food인데 "운동복, 실내용화" 준비물          |
| 금액 일관성     | 입력 18,000원인데 "참가비 120,000원"        |
| 시간 일관성     | 심야 모임인데 "햇살 좋은 오후 산책"         |
| 대상 일관성     | beginner_friendly인데 숙련자 전용 톤        |
| 호스트 정보     | supporter_required인데 호스트 소개 비어있음 |
| 현실성          | 소규모 밥모임에 1인 23만원                  |
| 현실성          | 초면 소셜 모임에 8시간 일정                 |

### Layer 3. Cross-Reference Validation (스팟 단위) — NEW

동일 스팟의 콘텐츠 간 모순 검증

| 검증 대상                | 체크 내용                                       |
| ------------------------ | ----------------------------------------------- |
| feed ↔ detail            | 인원수, 금액, 카테고리 일치 여부                |
| detail ↔ plan            | 활동 내용과 타임라인 정합성                     |
| detail ↔ review          | 활동 종류 일치 (드로잉 클래스 ↔ 맛집 투어 모순) |
| feed ↔ message           | 모집 상태와 메시지 타입 정합성                  |
| review ↔ activity_result | 노쇼 있었는데 리뷰에 "전원 참여" 모순           |

```
검증 흐름:
  개별 콘텐츠 생성 완료
      ↓
  개별 schema + rule 검증
      ↓
  스팟 단위 cross-reference 검증
      ↓
  통과 시 → critic / diversity 단계로
  실패 시 → 모순 필드 식별 → 해당 콘텐츠만 재생성
```

### Layer 4. LLM Critic Validation (샘플링)

비용 관리 전략 적용

```
전체 스팟 중 10~20%만 critic 평가
나머지는 Layer 1~3 통과 시 자동 승인 (quality_score 기본값 부여)

critic 대상 선정 기준:
  - 새로운 카테고리/지역 조합 (학습 데이터 없는 영역)
  - Layer 1~3에서 경계값 근처인 콘텐츠
  - 랜덤 샘플 (전체 분포 모니터링용)
```

평가 항목:

```json
{
    "naturalness_score": 0.84,
    "consistency_score": 0.91,
    "regional_fit_score": 0.76,
    "persona_fit_score": 0.88,
    "safety_score": 0.98,
    "reject": false,
    "reasons": []
}
```

### Layer 5. Diversity Check — NEW

반복 패턴 탐지

| 방법             | 설명                                                          |
| ---------------- | ------------------------------------------------------------- |
| n-gram 중복률    | 동일 카테고리 내 3-gram 이상 반복률 15% 초과 시 감점          |
| TF-IDF 유사도    | 기존 승인 콘텐츠 대비 cosine similarity 0.85 초과 시 감점     |
| 템플릿 패턴 감지 | "가볍게 OO하면서 OO 나누는" 같은 패턴 3회 이상 반복 시 reject |
| 배치 단위 검증   | 동일 배치 내 feed title 유사도 체크                           |

### Layer 6. Scoring / Ranking

```
quality_score =
    0.25 × naturalness
  + 0.20 × consistency
  + 0.20 × persona_fit
  + 0.15 × region_fit
  + 0.10 × business_rule_fit
  + 0.10 × diversity_score      ← 기존 0.05에서 상향
```

승인 기준:

| 점수        | 판정                           |
| ----------- | ------------------------------ |
| ≥ 0.80      | 승인                           |
| 0.65 ~ 0.79 | 조건부 승인 (critic 리뷰 필요) |
| < 0.65      | reject → 재생성                |

---

## 6. 생성-검증-재시도 루프

```
Generate candidate × 2 (MVP)
        ↓
    Schema validate
        ↓
    Rule validate
        ↓
    Cross-reference validate (스팟 단위)
        ↓
    Diversity check (배치 단위)
        ↓
    Critic evaluate (샘플링)
        ↓
    Score & rank
        ↓
    if best_score < 0.65:
        재생성 (rejection feedback 포함, 최대 2회)
    elif best_score < 0.80:
        critic 리뷰 후 판정
    else:
        승인
```

재생성 시 rejection feedback 예시:

```json
{
    "rejected_field": "summary",
    "reason": "category_mismatch",
    "detail": "food 카테고리인데 '드로잉 체험'이 포함됨",
    "instruction": "식사/카페/대화 중심 표현으로 재생성"
}
```

---

## 7. 자연스러움 전략

### 7-1. 문체 다양성

| 페르소나   | 말투 특성         | 예시                                                  |
| ---------- | ----------------- | ----------------------------------------------------- |
| strategist | 구조적, 정보 명확 | "장소는 OO, 시간은 OO입니다. 준비물 확인 부탁드려요." |
| gadfly     | 가볍고 장난기     | "ㅎㅎ 다들 빈속으로 오세요~ 맛집 갑니다"              |
| stoic      | 짧고 담백         | "연무동 저녁 식사. 4명. 가볍게."                      |
| optimist   | 밝고 긍정적       | "같이 맛있는 거 먹으면서 좋은 시간 보내요!"           |

### 7-2. 콘텐츠 불완전성 분포

모든 스팟이 동일한 완성도를 가지면 안 됨

```
소개 길이 분포:
  짧은 (1~2문장)   30%
  보통 (3~4문장)   50%
  상세 (5문장+)    20%

준비물 기재율:
  없음              40%
  간단 (1~2개)      35%
  자세 (3개+)       25%

리뷰 길이 분포:
  한 줄 (1문장)     25%
  보통 (2~3문장)    50%
  상세 (4문장+)     25%
```

### 7-3. 리뷰 분포

```
5점: 55%
4점: 30%
3점: 10%
2점: 3%
1점: 2%
```

부정 리뷰 패턴 포함:

- 노쇼로 인원 부족
- 진행 시간 지연
- 장소 접근성 불편
- 예상과 다른 활동 내용

---

## 8. DB 스키마

### synthetic_feed_content

```sql
CREATE TABLE synthetic_feed_content (
    id              UUID PRIMARY KEY,
    dataset_version VARCHAR(20) NOT NULL,
    spot_id         VARCHAR(50) NOT NULL,
    title           VARCHAR(100) NOT NULL,
    summary         TEXT NOT NULL,
    cover_tags_json JSONB,
    supporter_label VARCHAR(50),
    price_label     VARCHAR(50),
    region_label    VARCHAR(50),
    time_label      VARCHAR(50),
    status          VARCHAR(20),
    quality_score   DECIMAL(4,3),
    validation_status VARCHAR(20),
    created_at      TIMESTAMP DEFAULT NOW()
);
```

### synthetic_spot_detail

```sql
CREATE TABLE synthetic_spot_detail (
    id                UUID PRIMARY KEY,
    dataset_version   VARCHAR(20) NOT NULL,
    spot_id           VARCHAR(50) NOT NULL,
    title             VARCHAR(100) NOT NULL,
    description       TEXT NOT NULL,
    plan_json         JSONB,
    materials_json    JSONB,
    target_audience   VARCHAR(100),
    cost_breakdown_json JSONB,
    host_intro        TEXT,
    policy_notes      TEXT,
    quality_score     DECIMAL(4,3),
    validation_status VARCHAR(20),
    created_at        TIMESTAMP DEFAULT NOW()
);
```

### synthetic_spot_messages

```sql
CREATE TABLE synthetic_spot_messages (
    id                  UUID PRIMARY KEY,
    dataset_version     VARCHAR(20) NOT NULL,
    spot_id             VARCHAR(50) NOT NULL,
    message_type        VARCHAR(30) NOT NULL,
    speaker_type        VARCHAR(20) NOT NULL,
    speaker_id          VARCHAR(50),
    content             TEXT NOT NULL,
    created_at_simulated TIMESTAMP,
    quality_score       DECIMAL(4,3),
    validation_status   VARCHAR(20)
);
```

### synthetic_review

```sql
CREATE TABLE synthetic_review (
    id                UUID PRIMARY KEY,
    dataset_version   VARCHAR(20) NOT NULL,
    spot_id           VARCHAR(50) NOT NULL,
    reviewer_agent_id VARCHAR(50),
    rating            SMALLINT CHECK (rating BETWEEN 1 AND 5),
    review_text       TEXT,
    tags_json         JSONB,
    sentiment_score   DECIMAL(4,3),
    quality_score     DECIMAL(4,3),
    validation_status VARCHAR(20),
    created_at        TIMESTAMP DEFAULT NOW()
);
```

### content_validation_log

```sql
CREATE TABLE content_validation_log (
    id              UUID PRIMARY KEY,
    content_type    VARCHAR(30) NOT NULL,
    content_id      UUID NOT NULL,
    validator_type  VARCHAR(30) NOT NULL,
    score           DECIMAL(4,3),
    status          VARCHAR(20),
    reason_json     JSONB,
    created_at      TIMESTAMP DEFAULT NOW()
);
```

### content_version_policy — NEW

```sql
CREATE TABLE content_version_policy (
    id                UUID PRIMARY KEY,
    dataset_version   VARCHAR(20) NOT NULL,
    status            VARCHAR(20) NOT NULL,  -- draft / active / deprecated / archived
    activation_date   TIMESTAMP,
    deprecation_date  TIMESTAMP,
    replacement_version VARCHAR(20),
    transition_strategy VARCHAR(20),  -- immediate / gradual / ab_test
    real_content_threshold INTEGER,   -- real 콘텐츠 N개 도달 시 synthetic 제거
    created_at        TIMESTAMP DEFAULT NOW()
);
```

---

## 9. Synthetic → Real 전환 전략 — NEW

### 9-1. 전환 정책

```
Phase 1: Pure Synthetic (서비스 초기)
  - 100% synthetic content
  - synthetic 라벨 숨김 (사용자에게 노출 안 함)

Phase 2: Mixed (실사용자 유입 시작)
  - real + synthetic 혼합
  - 카테고리/지역별로 real이 N개 이상이면 해당 영역 synthetic 비중 축소
  - 피드 정렬에서 real 콘텐츠 우선 노출

Phase 3: Real Dominant (충분한 실데이터 확보)
  - synthetic은 트래픽 적은 카테고리/지역에만 유지
  - 나머지 영역에서 synthetic 제거

Phase 4: Sunset
  - 전체 synthetic 콘텐츠 archived 처리
  - 검증 파이프라인은 유지 (신규 카테고리 런칭 시 재활용)
```

### 9-2. 자동 전환 트리거

```
지역/카테고리 조합별:
  if real_spot_count >= 10:
      synthetic 비중 50%로 축소
  if real_spot_count >= 30:
      synthetic 비중 20%로 축소
  if real_spot_count >= 50:
      synthetic 제거 (archived)
```

### 9-3. 버전 간 전환

```
v1 active 상태에서 v2 배포 시:
  1. v2를 draft로 생성
  2. v2 콘텐츠 전체 검증 완료
  3. v1 → deprecated, v2 → active (atomic switch)
  4. 30일 후 v1 → archived
```

---

## 10. Critic 비용 관리 — NEW

### 10-1. 호출 횟수 추정

| 항목                | MVP 기준              |
| ------------------- | --------------------- |
| 스팟 수             | 500개                 |
| 콘텐츠 타입         | 5종                   |
| 후보 수             | 2개/타입              |
| 생성 호출           | 500 × 5 × 2 = 5,000회 |
| Critic 대상 (15%)   | 75 스팟 × 5 = 375회   |
| 재생성 (10% reject) | ~500회                |
| **총 LLM 호출**     | **~5,875회**          |

### 10-2. 비용 최적화

```
전략 1: Critic 샘플링
  - 전수가 아닌 10~20% 샘플만 critic
  - deterministic rule 통과율 높으면 critic 비율 점진적 축소

전략 2: 배치 생성
  - 동일 카테고리/지역 스팟을 묶어서 배치 처리
  - system prompt 재사용으로 토큰 절약

전략 3: Critic 캐싱
  - 유사 패턴의 critic 결과 재활용
  - "food + 수원 + casual_meetup" 조합이 이미 통과했으면
    동일 조합의 다음 스팟은 critic 확률 50%로 축소

전략 4: 경량 critic 모델
  - 전체 평가는 고성능 모델 (claude-sonnet)
  - 단순 자연스러움 체크는 경량 모델 (haiku) 사용
```

---

## 11. 실행 Job 구성

### Job 1. build_content_spec

```
입력: 시뮬레이션 로그 (spot lifecycle, 참여자, 지역, 비용, 결과)
출력: content_spec.json
```

### Job 2. generate_feed_content

```
입력: content_spec
출력: feed preview × 2 후보
```

### Job 3. generate_spot_detail

```
입력: content_spec
출력: spot detail + plan × 2 후보
```

### Job 4. generate_messages

```
입력: content_spec + spot lifecycle
출력: 커뮤니케이션 snippet 4종 × 2 후보
```

### Job 5. generate_reviews

```
입력: content_spec + activity_result
출력: review × 2 후보
```

### Job 6. validate_individual

```
입력: 생성된 콘텐츠 개별
처리: schema → rule → diversity
출력: pass / fail + 사유
```

### Job 7. validate_cross_reference — NEW

```
입력: 동일 스팟의 전체 콘텐츠
처리: 필드 간 정합성 체크
출력: pass / fail + 모순 필드 목록
```

### Job 8. evaluate_critic

```
입력: 샘플링된 콘텐츠
처리: LLM critic 평가
출력: 점수 + 판정
```

### Job 9. score_and_approve

```
입력: 검증 완료 콘텐츠
처리: quality_score 산정 → 최고점 선택
출력: approved content
```

### Job 10. publish_synthetic_content

```
입력: approved content
출력: 실서비스 read model 반영
```

---

## 12. 구현 순서 (수정됨)

### Phase 1: 기반 + 피드 생성기 + 검증기 동시 (2주)

```
Week 1:
  - content_spec_builder 구현
  - LLM 입력 스키마 확정
  - DB 테이블 생성

Week 2:
  - feed_generator 구현
  - schema_validator + rule_validator 동시 구현
  - feed 생성 → 즉시 검증 → 프롬프트 튜닝 루프 시작
```

> **핵심**: generator와 validator를 동시에 만들어서  
> 생성 결과의 품질을 즉시 확인하고 프롬프트를 조기 튜닝

### Phase 2: 나머지 생성기 + Cross-Reference (2주)

```
Week 3:
  - spot_detail_generator 구현
  - review_generator 구현
  - message_generator 구현
  - 각 생성기별 rule_validator 확장

Week 4:
  - cross_reference_validator 구현 (스팟 단위)
  - 스팟 단위 통합 검증 테스트
```

### Phase 3: Critic + Diversity + 루프 (1주)

```
Week 5:
  - LLM critic evaluator 구현 (샘플링 방식)
  - diversity checker 구현 (n-gram + TF-IDF)
  - quality_score 산정 로직
  - generate → validate → retry 전체 루프 연결
```

### Phase 4: 배포 + 전환 정책 (1주)

```
Week 6:
  - approved dataset → 실서비스 publish
  - content_version_policy 테이블 + 전환 로직
  - synthetic → real 자동 전환 트리거
  - 모니터링 대시보드 (생성량, 승인률, 평균 quality_score)
```

---

## 13. MVP 범위 (최소)

### 생성

- Feed title + summary
- Spot detail description
- Spot plan 3~5 steps
- Review 1개
- Host intro 1개

### 검증

- Schema check
- Rule check
- Cross-reference check (스팟 단위)
- Critic score (15% 샘플링)
- Diversity check (배치 내 n-gram)

### 전략

- 후보 2개 생성
- 최고점 선택
- 1회 재시도 허용
- 500 스팟 목표

---

## 14. 성공 지표

| 지표                            | 목표   |
| ------------------------------- | ------ |
| 1차 승인률 (재시도 없이 통과)   | ≥ 70%  |
| 최종 승인률 (재시도 포함)       | ≥ 95%  |
| 평균 quality_score              | ≥ 0.80 |
| 배치 내 diversity (평균 유사도) | ≤ 0.60 |
| 스팟당 총 LLM 호출              | ≤ 15회 |
| 스팟당 생성 소요 시간           | ≤ 30초 |
| Critic 비용 비율 (전체 대비)    | ≤ 20%  |
