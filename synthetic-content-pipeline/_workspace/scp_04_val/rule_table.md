# Layer 2 Rule Table — feed (Phase 1)

> 출처: `synthetic_content_pipeline_plan.md` §5 Layer 2 표
> 소유: `validator-engineer`
> 모듈: `src/pipeline/validators/rules.py`

본 문서는 플랜 §5 Layer 2 표의 8개 행을 코드 함수와 1:1 매핑한다.
`pipeline-qa` 가 goldens (positive/negative)를 만들 때 reject 조건을 검색
가능한 형태로 유지하기 위해 작성됐다.

## 1. 매핑 표

| # | Plan §5 Layer 2 행 | 함수명 | 파라미터 출처 | reject 조건 |
|---|---|---|---|---|
| 1 | 지역 일관성 | `rule_region_consistency` | `spec.region` (builder), payload(title/summary/region_label/tags) | rapidfuzz `partial_ratio(spec.region, payload_blob) < 75` AND 핵심 토큰 동일 |
| 2 | 카테고리 일관성 | `rule_category_consistency` | `spec.category`, `feed_rules.yaml::categories.<cat>.deny_keywords` | payload 본문에 deny 키워드 1개라도 substring 매치 |
| 3 | 금액 일관성 | `rule_price_consistency` | `spec.budget.expected_cost_per_person`, `shared_rules.yaml::price_tolerance_low/high` | `parse(price_label)` 추출액이 `[expected*0.5, expected*2.5]` 밖 |
| 4 | 시간 일관성 | `rule_time_consistency` | `spec.schedule.start_time` → slot, `NIGHT/MORNING_FORBIDDEN_KEYWORDS` (코드 상수) | slot=night → 본문에 ("아침","오전","햇살","이른 오후","브런치") |
| 5 | 대상 일관성 | `rule_target_consistency` | `spec.activity_constraints.beginner_friendly`, `ADVANCED_ONLY_KEYWORDS` (코드 상수) | beginner_friendly=True 인데 본문에 ("숙련자","경험자 위주","고급반","프로 전용",...) |
| 6 | 호스트 정보 | `rule_host_consistency` | `spec.activity_constraints.supporter_required`, `payload.supporter_label`, `EMPTY_SUPPORTER_VALUES` | supporter_required=True 인데 supporter_label ∈ {"", "none", "null", "없음", ...} |
| 7 | 현실성 (1인 23만원) | `rule_realism_budget` | `spec.participants.expected_count`, `shared_rules.yaml::small_group_threshold/max_per_person_small_group` | expected_count ≤ 6 AND `parse(price_label)` 추출액 > 50000 |
| 8 | 현실성 (8시간 일정) | `rule_realism_duration` | `spec.schedule.duration_minutes`, `feed_rules.yaml::forbidden_long_duration_phrases` | duration_minutes ≤ 240 AND 본문에 ("8시간","하루 종일","하루종일","무박","밤샘",...) |

> 정확한 deny 키워드 / 임계값 조정은 `feed_rules.yaml`, `shared_rules.yaml` 한 곳에서.

## 2. 경계값 테스트 케이스 (pipeline-qa goldens 시드)

각 케이스는 `validate_feed_rules` 호출 시 표시된 reason 코드가 반드시 떠야 한다.

### 2-1. region 경계 (rule 1)

- **case_region_pass**: `spec.region="수원시 연무동"`, `payload.title="연무동 저녁 한 끼 같이할 4명"`
  - 기대: `ok=True`, similarity ≥ 75
- **case_region_fail**: `spec.region="수원시 연무동"`, payload 본문 어디에도 "연무동" 미포함, region_label="강남구"
  - 기대: `reason="region_mismatch"`

### 2-2. category 경계 (rule 2)

- **case_category_pass_food**: `spec.category="food"`, summary="가볍게 식사하면서..."
  - 기대: `ok=True`
- **case_category_fail_food_drawing**: `spec.category="food"`, summary="드로잉 클래스에서 자유롭게 그림을 그려요"
  - 기대: `reason="category_mismatch"`, detail에 "드로잉"
- **case_category_fail_exercise_wine**: `spec.category="exercise"`, tags=["저녁","와인"]
  - 기대: `reason="category_mismatch"`, detail에 "와인"

### 2-3. price 경계 (rule 3)

- **case_price_low_edge**: `spec.budget.expected_cost_per_person=18000`, payload.price_label="1인 9,000원"
  - low=9000 → `ok=True` (경계 내)
- **case_price_below**: `expected=18000`, price_label="1인 5,000원"
  - 기대: `reason="price_out_of_range"`
- **case_price_high_edge**: `expected=18000`, price_label="1인 45,000원"
  - high=45000 → `ok=True`
- **case_price_above**: `expected=18000`, price_label="참가비 120,000원"
  - 기대: `reason="price_out_of_range"`
- **case_price_unparseable**: price_label="가성비 좋아요" (숫자 없음)
  - 기대: `reason="price_unparseable"`, severity=warn (ok=True)

### 2-4. time 경계 (rule 4)

- **case_time_pass_night**: start_time="19:00" (night), summary에 "저녁/밤" 단어
  - 기대: `ok=True`
- **case_time_fail_night_morning**: start_time="22:00", summary="햇살 좋은 오후 산책"
  - 기대: `reason="time_mismatch_night"`, "햇살" 검출

### 2-5. target 경계 (rule 5)

- **case_target_pass**: beginner_friendly=True, summary="초면 환영해요"
  - 기대: `ok=True`
- **case_target_fail**: beginner_friendly=True, summary="숙련자 위주 모임"
  - 기대: `reason="target_mismatch_beginner"`

### 2-6. host 경계 (rule 6)

- **case_host_pass**: supporter_required=True, supporter_label="supporter_teacher"
  - 기대: `ok=True`
- **case_host_fail_empty**: supporter_required=True, supporter_label=""
  - 기대: `reason="host_label_empty"`
- **case_host_fail_none_word**: supporter_label="없음"
  - 기대: `reason="host_label_empty"`

### 2-7. realism budget 경계 (rule 7)

- **case_realism_pass_small**: expected_count=4, price_label="1인 4만원"
  - 40000 ≤ 50000 → `ok=True`
- **case_realism_fail_small**: expected_count=4, price_label="1인 230,000원"
  - 기대: `reason="realism_budget_too_high"`
- **case_realism_skip_large**: expected_count=10, price_label="1인 80,000원"
  - large group → 이 rule 자체가 skip (`ok=True` from this rule)

### 2-8. realism duration 경계 (rule 8)

- **case_duration_pass**: duration_minutes=120, summary에 "약 2시간"
  - `ok=True`
- **case_duration_fail**: duration_minutes=120, summary="하루 종일 함께해요"
  - 기대: `reason="realism_duration_too_long"`
- **case_duration_skip_long**: duration_minutes=480 (8h), summary="하루 종일"
  - duration > 240 → rule skip (`ok=True`)

## 3. 호출 순서

`validate_feed_rules` 는 위 8개 함수를 **순서대로** 모두 실행한다. early-exit 없음 — generator-engineer 가 한 번에 모든 위반을 보고 재생성 프롬프트를 구성할 수 있어야 하기 때문.

## 4. severity 정책

| severity | 의미 | 사용처 |
|---|---|---|
| `reject` | 즉시 재생성 | rule 1~8 거의 전부 |
| `warn` | 점수 감점만 | `rule_price_consistency` 의 `price_unparseable` (숫자 없는 텍스트 라벨 허용) |

## 5. Phase 2/3 확장 포인트

- detail / message / review content type 추가 시 `validate_<type>_rules` 새 함수와 `_<TYPE>_RULE_FUNCTIONS` 튜플을 만들 것.
- rule 함수 시그니처는 `(payload, spec, rules) -> list[Rejection]` 동일 유지.
- `feed_rules.yaml` 의 `categories` 노드를 다른 type에서도 재사용 가능.

---

## 6. Phase 2 — Detail / Plan / Messages / Review Rule Tables

> Phase 2 에서 4종 content type 개별 Layer 2 rule 이 추가됐다. 모든 rule 은
> `(payload, spec, rules) -> list[Rejection]` 시그니처를 공유한다.

### 6-1. SpotDetail (`detail_rules.py`, `detail_rules.yaml`)

| # | 함수명 | 파라미터 출처 | reject 조건 |
|---|---|---|---|
| 1 | `rule_description_sentence_count` | `detail_rules.yaml::description_min/max_sentences` | description 문장 수 < 3 OR > 6 |
| 2 | `rule_category_consistency_detail` | `feed_rules.yaml::categories.<cat>.deny_keywords` (재사용) | detail 본문 blob 에 deny 키워드 substring 매치 |
| 3 | `rule_cost_breakdown_total` | `detail_rules.yaml::cost_total_tolerance_low/high`, `spec.budget.expected_cost_per_person` | sum(cost_breakdown.amount) ∉ [expected×0.7, expected×1.5] |
| 4 | `rule_host_intro_length` | `detail_rules.yaml::host_intro_min_length_when_supporter`, `spec.activity_constraints.supporter_required` | supporter_required=True AND len(host_intro) < 60 |
| 5 | `rule_policy_notes_safe` | `detail_rules.yaml::policy_forbidden_terms` | policy_notes 에 "환불 불가", "계약", "법적 대응" 등 포함 |

### 6-2. SpotPlan (`plan_rules.py`, `plan_rules.yaml`)

| # | 함수명 | 파라미터 출처 | reject 조건 |
|---|---|---|---|
| 1 | `rule_total_duration_match` | `plan_rules.yaml::duration_tolerance_minutes`, `spec.schedule.duration_minutes` | \|total_duration_minutes − spec.duration\| > 5 |
| 2 | `rule_step_count_range` | `plan_rules.yaml::step_min/max_count` | len(steps) ∉ [3,7] |
| 3 | `rule_step_time_monotonic` | (하드) `HH:MM` / `+N분` 파서 | steps 시간이 오름차순 아님 (또는 파싱 실패) |
| 4 | `rule_first_step_is_intro` (**warn**) | `plan_rules.yaml::intro_step_keywords` | steps[0].activity 에 "인사/도착/집결/소개" 키워드 0개 |

### 6-3. Messages (`messages_rules.py`, `messages_rules.yaml`)

| # | 함수명 | 파라미터 출처 | reject 조건 |
|---|---|---|---|
| 1 | `rule_snippets_all_present` | — (하드) | 4 snippet 중 하나라도 null/빈 문자열 |
| 2 | `rule_host_tone_consistency` (**warn**) | (하드) `_HOST_TONE_HINTS` | 4 snippet 전체에 "저/제가/저희/호스트/supporter/드릴" 0건 |
| 3 | `rule_recruit_status_match` | `messages_rules.yaml::recruit_intent_keywords`, `spec.activity_result` None 여부 | recruiting 상태인데 recruiting_intro 에 모집 어휘 0건 |
| 4 | `rule_forbidden_phrases` | `messages_rules.yaml::forbidden_phrases` | 4 snippet 중 하나라도 "환불/위약금/법적 대응" 포함 |
| 5 | `rule_day_of_notice_has_time` | (하드) `\d+:\d{2}` / `N시` 정규식 | day_of_notice 에 시간 표현 없음 |

### 6-4. Review (`review_rules.py`, `review_rules.yaml`)

| # | 함수명 | 파라미터 출처 | reject 조건 |
|---|---|---|---|
| 1 | `rule_rating_sentiment_match` | `review_rules.yaml::rating_sentiment_map` | rating≥4 ↛ positive / rating==3 ↛ neutral / rating≤2 ↛ negative |
| 2 | `rule_noshow_mention_consistency` | `review_rules.yaml::forbidden_unanimous_terms`, `spec.activity_result.no_show_count` | no_show_count>0 AND review_text 에 "전원/모두/빠짐없이" 포함 |
| 3 | `rule_will_rejoin_vs_rating` (**warn**) | — | rating==1 AND will_rejoin==True |
| 4 | `rule_review_length_bucket_match` | `review_rules.yaml::length_bucket_sentences`, `payload.meta.review_length_bucket` | 문장 수 ∉ 버킷 범위 (meta 누락 시 skip) |
| 5 | `rule_satisfaction_tags_range` | `review_rules.yaml::tag_*` | 태그 개수 ∉ [1,5] OR 태그 길이 ∉ [2,12] |

### 6-5. Rule 함수 개수 표

| content_type | hard reject | warn | 총 |
|---|---|---|---|
| feed     | 7 | 1 | 8 |
| detail   | 5 | 0 | 5 |
| plan     | 3 | 1 | 4 |
| messages | 4 | 1 | 5 |
| review   | 4 | 1 | 5 |

feed 의 `price_unparseable` 는 warn — rules.py 에서 1건.

