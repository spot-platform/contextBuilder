# Column Contract — 6 테이블 × 컬럼 × 책임자 매핑

> 누가 INSERT/UPDATE 하고, 누가 SELECT 하는지의 **소유권 계약**.
> 본 문서는 `pipeline-infra-architect`가 정의하고, 다른 4명은 자신이 owner인 컬럼만 채운다.

범례:
- **owner**: 값을 채우는 책임자 (인프라 / 생성기 / 검증기 / publisher 중 하나)
- **reader**: 값을 읽는 모든 컴포넌트
- 모든 row의 `id`, `created_at`, `dataset_version`, `spot_id` 는 **인프라가 default 또는 caller insert 시점에 강제** 한다.

---

## 1. `synthetic_feed_content`

| column | type | owner | readers | 비고 |
|---|---|---|---|---|
| id | UUID | infra (default) | all | `_uuid_str()` |
| dataset_version | str(20) | caller (job dispatcher) | all | 파티셔닝 키 |
| spot_id | str(50) | caller | all | builder 출력과 1:1 |
| title | str(100) | content-generator-engineer | validator, publisher | feed 카드 제목 12~40자 |
| summary | text | content-generator-engineer | validator, publisher | 1~2문장 |
| cover_tags_json | JSON | content-generator-engineer | validator (n-gram), publisher | 태그 배열 |
| supporter_label | str(50) | content-generator-engineer | validator, publisher | 호스트 카테고리 라벨 |
| price_label | str(50) | content-generator-engineer | validator (rule: 금액 일관성) | "1인 1.5~2만원" 형태 |
| region_label | str(50) | content-generator-engineer | validator (rule: 지역 일관성) | ContentSpec.region 그대로 권장 |
| time_label | str(50) | content-generator-engineer | validator | "4/18(금) 19:00" |
| status | str(20) | content-generator-engineer | publisher | recruiting / closed / in_progress |
| **quality_score** | dec(4,3) | **validator-engineer** | publisher | Layer 6 산출 |
| **validation_status** | str(20) | **validator-engineer** | publisher | pending/passed/failed/approved |
| created_at | datetime | infra (default) | — | utcnow |

---

## 2. `synthetic_spot_detail`

| column | owner | readers | 비고 |
|---|---|---|---|
| id, dataset_version, spot_id, created_at | infra/caller | all | 동일 |
| title | content-generator-engineer | validator, publisher | detail 페이지 제목 |
| description | content-generator-engineer | validator (Layer 2 rule, Layer 3 cross), publisher | 본문 (분포: 30/50/20%) |
| plan_json | content-generator-engineer | validator (cross-ref to message timing) | 최소 3 step |
| materials_json | content-generator-engineer | validator (rule: 카테고리 일관성) | 준비물 (없음 40%) |
| target_audience | content-generator-engineer | validator | beginner_friendly 일관성 |
| cost_breakdown_json | content-generator-engineer | validator (rule: 금액 현실성) | spec.budget 정합 |
| host_intro | content-generator-engineer | validator (rule: supporter_required) | supporter_required 시 필수 |
| policy_notes | content-generator-engineer | publisher | 환불/규칙 |
| **quality_score, validation_status** | **validator-engineer** | publisher | — |

---

## 3. `synthetic_spot_messages`

| column | owner | readers | 비고 |
|---|---|---|---|
| id, dataset_version, spot_id | infra/caller | all | — |
| message_type | content-generator-engineer | validator (cross: feed.status) | recruit/approve/dayof/thanks 4종 |
| speaker_type | content-generator-engineer | validator | host / participant / system |
| speaker_id | content-generator-engineer | publisher | event_log agent_id |
| content | content-generator-engineer | validator (Layer 1~2), publisher | 자연어 본문 |
| created_at_simulated | content-generator-engineer | publisher | tick → datetime 변환 |
| **quality_score, validation_status** | **validator-engineer** | publisher | — |

---

## 4. `synthetic_review`

| column | owner | readers | 비고 |
|---|---|---|---|
| id, dataset_version, spot_id, created_at | infra/caller | all | — |
| reviewer_agent_id | content-generator-engineer | publisher | event_log WRITE_REVIEW agent_id |
| rating | content-generator-engineer | validator (분포 체크), publisher | 1~5 (CHECK 제약) |
| review_text | content-generator-engineer | validator (Layer 5 diversity), publisher | — |
| tags_json | content-generator-engineer | validator | 만족도 태그 배열 |
| sentiment_score | content-generator-engineer | validator (cross-ref to activity_result) | -1 ~ 1 |
| **quality_score, validation_status** | **validator-engineer** | publisher | — |

---

## 5. `content_validation_log`

| column | owner | readers | 비고 |
|---|---|---|---|
| id, created_at | infra (default) | all | — |
| content_type | validator-engineer | analyst | feed / detail / message / review / spot |
| content_id | validator-engineer | analyst | 위 4개 테이블의 id |
| validator_type | validator-engineer | analyst | schema / rule / cross_ref / critic / diversity |
| score | validator-engineer | analyst, score_and_approve | 0.000 ~ 1.000 |
| status | validator-engineer | score_and_approve | passed / failed / warning |
| reason_json | validator-engineer | analyst | 실패 사유 / rejected_field 등 |

---

## 6. `content_version_policy`

| column | owner | readers | 비고 |
|---|---|---|---|
| id, created_at | infra (default) | — | — |
| dataset_version | pipeline-infra-architect | publisher | unique 식별 |
| status | pipeline-infra-architect | publisher | draft → active → deprecated → archived |
| activation_date / deprecation_date | pipeline-infra-architect | publisher | atomic switch 시점 |
| replacement_version | pipeline-infra-architect | publisher | v1 → v2 같은 link |
| transition_strategy | pipeline-infra-architect | publisher | immediate / gradual / ab_test |
| real_content_threshold | pipeline-infra-architect | publisher (§9 트리거) | 10/30/50 |

---

## 까다로운 컬럼 TOP 3

다른 에이전트가 가장 헷갈릴 컬럼들. 인프라가 명시적으로 contract 잡아둔 부분.

### 1. `synthetic_feed_content.cover_tags_json`
- **누가 채우나**: content-generator-engineer (LLM 호출 결과)
- **누가 읽나**: validator-engineer (Layer 5 n-gram 다양성), publisher (UI 태그)
- **함정**: ContentSpec.region/category 단어를 그대로 박으면 diversity layer에서 패널티. tag 자체는 LLM이 만들지만, "min 1개 ContentSpec.region 단어 포함" 같은 rule은 validator 영역 (인프라는 컬럼 자리만 보장).

### 2. `synthetic_spot_messages.created_at_simulated`
- **누가 채우나**: content-generator-engineer
- **함정**: real timestamp가 아니라 **시뮬레이션 tick 기반 가상 시각**. tick → datetime 변환식이 builder의 `_tick_to_schedule` 과 **동일해야** cross-reference validator의 시간 일관성 검사가 통과한다. content-generator-engineer가 이 함수를 직접 import 해서 쓰는 게 안전.

### 3. `ContentSpec.participants.persona_mix` → `synthetic_spot_detail.target_audience` 흐름
- **데이터 출처 부재**: 시뮬레이터 event_log에는 agent의 persona가 직접 기록되지 않음 (agent_id만 있고, persona는 별도 매핑). 현재 builder는 빈 리스트로 둠.
- **owner 분담**:
  - 인프라가 builder.py에 빈 리스트만 만들어 두고 TODO 마킹.
  - content-generator-engineer는 빈 persona_mix를 받았을 때 region density 기반으로 LLM 프롬프트에서 "다양한 취향 환영" 같은 generic 표현으로 폴백.
  - 추후 spot-simulator가 agent_persona_mapping.json 을 export하면, builder가 채워주면서 generator 폴백 코드는 그대로 둠 (점진적 강화).
