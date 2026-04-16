# synthetic-content-pipeline — 산출물 & 인터페이스 레퍼런스

> 작성일: 2026-04-16 · 대상 코드: `synthetic-content-pipeline/src/pipeline/`
> simulator처럼 Web API는 없고, 외부 트리거 표면은 **click CLI 10개 서브커맨드**다. 이 문서는 세 축으로 정리한다.
> 1. **CLI 진입점** (`python -m pipeline.cli <command>`) — 10개 서브커맨드
> 2. **DB 테이블** — 6 테이블, publisher가 쓰는 최종 산출물
> 3. **공용 pydantic / dataclass** (`ContentSpec`, `Candidate`, `ValidationResult`, `SpotProcessResult`)

---

## 목차

1. [개요](#1-개요)
2. [파이프라인 단계 흐름](#2-파이프라인-단계-흐름)
3. [CLI 서브커맨드 10종](#3-cli-서브커맨드-10종)
4. [DB 스키마 6 테이블](#4-db-스키마-6-테이블)
5. [핵심 dataclass / pydantic 카탈로그](#5-핵심-dataclass--pydantic-카탈로그)
6. [Validator 계층과 스코어링](#6-validator-계층과-스코어링)
7. [골든 샘플 & 리포트 산출물](#7-골든-샘플--리포트-산출물)
8. [부록: DB 직접 질의 템플릿](#8-부록)

---

## 1. 개요

`synthetic-content-pipeline`은 `spot-simulator`가 뱉은 `event_log.jsonl`을 **5종 콘텐츠**(feed / detail / plan / messages / review)로 렌더하고, **6 Layer 검증**을 통과한 것만 DB에 publish하는 배치 파이프라인이다.

주요 특징:
- **LLM 호출은 codex-bridge 경유**: OpenAI/Anthropic API가 아니라 사용자의 Codex 구독(`codex exec` CLI)을 subprocess로 때림. 외부에서 보면 `pipeline.llm.codex_client.call_codex`가 유일 관문.
- **생성 → 검증 → 재시도 루프**: generator가 후보 2개를 내고(`candidate × 2`), 6 Layer validator가 채점. quality_score ≥ 0.80이면 approved, 0.65~0.79는 conditional, < 0.65면 rejected 후 재생성(최대 2회).
- **DB**: Postgres/SQLite 모두 지원. 6 테이블 전부 `dataset_version + spot_id` 인덱스. JSONB는 SQLite 호환 위해 `sqlalchemy.JSON`으로 매핑.
- **content_version_policy**는 상태 머신: `draft → active → deprecated → archived`. publisher가 `--dataset-version` 미지정 시 `active` 버전을 자동 선택.

---

## 2. 파이프라인 단계 흐름

plan §6 루프. 스팟 1개당 5종 콘텐츠를 한꺼번에 만든다.

```
event_log.jsonl (spot-simulator)
        ↓
  [Job 1] build-content-spec                          ← spec/builder.py
        ↓
  ContentSpec (pydantic)
        ↓
  ┌─────────────────────────────────────────────┐
  │ generate-feed                                │  → Candidate × 2
  │ generate-detail   (+ plan embed)             │  → Candidate × 2
  │ generate-messages (4 snippet one-shot)       │  → Candidate × 2
  │ generate-reviews                             │  → Candidate × 2
  └─────────────────────────────────────────────┘
        ↓                      ← Layer 1 schema        (validators/schema.py)
        ↓                      ← Layer 2 rule          (validators/rules.py)
  validate-individual
        ↓                      ← Layer 3 cross_ref     (validators/cross_reference.py)
  validate-cross-reference
        ↓                      ← Layer 5 diversity     (validators/diversity.py)
                                 (배치 단위, n-gram/TF-IDF)
        ↓                      ← Layer 4 critic        (validators/critic.py)
                                 (샘플링만, codex-bridge)
  evaluate-critic
        ↓                      ← Layer 6 scoring       (validators/scoring.py)
  score-and-approve            (classification: approved/conditional/rejected)
        ↓
  publish                      → synthetic_feed_content
                               → synthetic_spot_detail (plan_json embed)
                               → synthetic_spot_messages (4 row)
                               → synthetic_review
                               → content_validation_log (validator가 직접)
                               → content_version_policy (active 자동 선택)
```

각 단계는 `python -m pipeline.cli <command>`로 개별 실행 가능하고, `loop.generate_validate_retry`가 안에서 전체를 조립해 돌린다(`process_spot_full`). 즉 production 경로는 "loop가 생성→검증→재시도를 한 번에 수행 → publisher가 DB에 insert" 두 단계다. 개별 CLI 커맨드는 디버깅/재처리 시 수동 포트.

---

## 3. CLI 서브커맨드 10종

진입점: `python -m pipeline.cli <command> --help`. 모두 `click` 기반이라 `--help`가 권위 있는 소스.

| # | 명령 | 담당 Job module | 용도 |
|---|------|----------------|------|
| 1 | `build-content-spec`   | `jobs/build_content_spec.py`   | event_log → ContentSpec JSON |
| 2 | `generate-feed`        | `jobs/generate_feed.py`        | feed preview 카드 × 2 |
| 3 | `generate-detail`      | `jobs/generate_detail.py`      | 상세 페이지 × 2 (plan embed) |
| 4 | `generate-messages`    | `jobs/generate_messages.py`    | 4종 snippet 통합 생성 × 2 |
| 5 | `generate-reviews`     | `jobs/generate_reviews.py`     | 리뷰 × 2 |
| 6 | `validate-individual`  | `jobs/validate_individual.py`  | Layer 1 schema + Layer 2 rule |
| 7 | `validate-cross-reference` | `jobs/validate_cross_reference.py` | Layer 3 (5종 cross-consistency) |
| 8 | `evaluate-critic`      | `jobs/evaluate_critic.py`      | Layer 4 LLM critic (샘플링) |
| 9 | `score-and-approve`    | `jobs/score_and_approve.py`    | Layer 6 quality_score + classification |
| 10 | `publish`             | `jobs/publish.py`              | approved/conditional → synthetic_* 테이블 |

### 3.1 `build-content-spec`

| 옵션 | 기본 | 필수 | 설명 |
|------|------|:---:|------|
| `--event-log` | `../spot-simulator/output/event_log.jsonl` | | JSONL 경로 (cwd 기준) |
| `--spot-id` | | ✓ | 빌드할 spot id (예: `S_0001`) |
| `--region-features` | spot-simulator 기본 경로 | | `region_features.json` override |
| `--skills-catalog` | | | peer mode 전용 — fee_breakdown 추정에 사용 |
| `--mode` | `peer` | | `peer` / `legacy` (legacy는 `event_log_legacy_v1.jsonl` 읽을 때) |

출력: stdout으로 ContentSpec JSON을 떨어뜨린다. 파이프라인으로 `> spec.json` 리다이렉트해 파일로 받는 게 관례.

### 3.2 `generate-feed` / `generate-detail` / `generate-messages` / `generate-reviews`

모두 동일한 시그니처.

| 옵션 | 필수 | 설명 |
|------|:---:|------|
| `--spot-id` | ✓ | 대상 spot id |
| `--dataset-version` | ✓ | publish 대상 버전 — critic/logging에 태깅 |

내부적으로 `pipeline.generators.<type>.<Generator>` 를 호출해 `candidate × 2`를 만든다. 결과는 `_workspace/`에 intermediate JSON으로 떨어진다(다음 단계가 읽음).

### 3.3 `validate-individual`

| 옵션 | 설명 |
|------|------|
| `--content-type` | `feed` / `detail` / `plan` / `messages` / `review` |
| `--payload-json` | 검증할 candidate payload (파일 or `-`) |
| `--spec-json` | 원본 ContentSpec JSON 경로 |
| `--db-url` | optional — 로그를 `content_validation_log`에 기록할 경우 |

Layer 1 schema + Layer 2 rule을 순차 실행. 결과는 `ValidationResult.to_dict()`를 stdout으로 출력, 선택적으로 DB에 로깅.

### 3.4 `validate-cross-reference`

한 스팟의 **5종 콘텐츠가 서로 모순이 없는가**를 검증. 예: feed의 `price_label`과 detail의 `cost_breakdown_json` 합계가 일치하는지, review의 `rating`이 activity_result의 sentiment와 정합인지.

| 옵션 | 설명 |
|------|------|
| `--spot-id` | |
| `--dataset-version` | |
| `--payloads-json` | 5 종 type별 선택된 candidate payload를 담은 dict |
| `--spec-json` | ContentSpec |
| `--db-url` | optional |

### 3.5 `evaluate-critic`

Layer 4. LLM(codex-bridge) 기반 비평. **샘플링** — `should_sample_critic` 정책에 따라 일부 candidate만 돈다(비용/속도 이유).

주요 옵션: `--spot-id`, `--content-type`, `--payload-json`, `--spec-json`, `--sampling-policy` (`aggressive`/`default`/`off`).

출력: `CriticResult` JSON — `peer_tone_score`, `consistency_flag`, `reasoning` 포함.

### 3.6 `score-and-approve`

Layer 6. `compute_quality_score(layer_results, critic_result)` → `classify(score) ∈ {approved, conditional, rejected}`. JSONL로 여러 candidate 배치 처리 가능.

| 옵션 | 설명 |
|------|------|
| `--layer-results-jsonl` | individual/cross/critic layer 결과를 행마다 쌓아둔 JSONL |
| `--critic-results-jsonl` | (선택) 별도 critic 결과 파일 |
| `--output-jsonl` | 스코어링 결과 기록 |

### 3.7 `publish`

최종 publish. rejected는 건너뛰고 approved/conditional만 DB에 insert.

| 옵션 | 필수 | 설명 |
|------|:---:|------|
| `--spot-id` | ✓ | |
| `--spot-result-json` | | `SpotProcessResult` serialized (loop 결과 그대로) |
| `--spec-json` | | `--spot-result-json` 없을 때, spec에서 역합성 |
| `--dataset-version` | | 미지정 시 `content_version_policy.status="active"` 행 자동 선택 |
| `--dry-run` | | add/flush까지만, commit 생략 |
| `--db-url` | | SQLAlchemy URL 지정 |

결과: `PublishResult(published_rows, skipped_rows, errors)`.

**불변식**:
- rejected content는 publish 안 함 (skipped 카운트만 증가).
- plan은 별도 테이블이 없고 `synthetic_spot_detail.plan_json`에 embed.
- messages는 4 snippet → 4 row (message_type 컬럼에 키 이름).
- `content_validation_log`는 validator가 직접 기록 — publisher는 손대지 않는다.

---

## 4. DB 스키마 6 테이블

source: `src/pipeline/db/models.py`. 모든 테이블 `dataset_version + spot_id` 복합 인덱스 + spot_id 단독 인덱스.

### 4.1 `synthetic_feed_content` — feed preview 카드

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | str(36) PK | UUID4 |
| `dataset_version` | str(20) NN | |
| `spot_id` | str(50) NN | |
| `title` | str(100) NN | 피드 타이틀 |
| `summary` | Text NN | 짧은 설명 |
| `cover_tags_json` | JSON | `["태그1", ...]` |
| `supporter_label` | str(50) | 호스트 표기 |
| `price_label` | str(50) | "1.2만원/인" 등 |
| `region_label` | str(50) | |
| `time_label` | str(50) | |
| `status` | str(20) | |
| `latitude` | Numeric(9,6) | 피드 맵 핀 |
| `longitude` | Numeric(9,6) | |
| `quality_score` | Numeric(4,3) | Layer 6 최종 점수 |
| `validation_status` | str(20) | `approved` / `conditional` |
| `created_at` | DateTime NN | |

### 4.2 `synthetic_spot_detail` — 상세 페이지 (+ plan embed)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | str(36) PK | |
| `dataset_version` | str(20) NN | |
| `spot_id` | str(50) NN | |
| `title` | str(100) NN | |
| `description` | Text NN | 긴 설명 |
| `plan_json` | JSON | plan outline(generators/plan.py 결과) embed |
| `materials_json` | JSON | 준비물 리스트 |
| `target_audience` | str(100) | |
| `cost_breakdown_json` | JSON | `{peer_labor, material, venue, equipment, total}` |
| `host_intro` | Text | |
| `policy_notes` | Text | |
| `latitude` / `longitude` | Numeric(9,6) | |
| `quality_score` | Numeric(4,3) | |
| `validation_status` | str(20) | |
| `created_at` | DateTime NN | |

### 4.3 `synthetic_spot_messages` — 커뮤니케이션 snippet 4종

스팟 하나당 **4 row**. `message_type`에 다음 키 중 하나가 들어감:

| message_type | 시점 |
|--------------|------|
| `recruiting_intro` | 모집글 게시 |
| `join_approval` | 참여 신청 승인 |
| `day_of_notice` | 당일 안내 |
| `post_thanks` | 종료 후 감사 |

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | str(36) PK | |
| `dataset_version` / `spot_id` | NN | |
| `message_type` | str(30) NN | 위 4종 |
| `speaker_type` | str(20) NN | `host` / `learner` |
| `speaker_id` | str(50) | agent id |
| `content` | Text NN | 메시지 본문 |
| `created_at_simulated` | DateTime | 시뮬레이션 상의 발화 시각 |
| `quality_score` | Numeric(4,3) | |
| `validation_status` | str(20) | |

### 4.4 `synthetic_review` — 리뷰

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | str(36) PK | |
| `dataset_version` / `spot_id` | NN | |
| `reviewer_agent_id` | str(50) | WRITE_REVIEW 이벤트의 agent |
| `rating` | SmallInt | **1~5** (CheckConstraint) |
| `review_text` | Text | |
| `tags_json` | JSON | `["재방문", "가성비"]` 등 |
| `sentiment_score` | Numeric(4,3) | 0~1 |
| `quality_score` | Numeric(4,3) | |
| `validation_status` | str(20) | |
| `created_at` | DateTime NN | |

**제약**: `CHECK (rating IS NULL OR rating BETWEEN 1 AND 5)`.

### 4.5 `content_validation_log` — 검증 이력

모든 Layer 결과(`individual`/`cross`/`critic`/`diversity`)가 한 테이블에 들어감.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | str(36) PK | |
| `content_type` | str(30) NN | `feed` / `detail` / `plan` / `messages` / `review` |
| `content_id` | str(36) NN | 대상 synthetic_* row id (publish 전이면 candidate id) |
| `validator_type` | str(30) NN | `schema` / `rule` / `cross_ref` / `critic` / `diversity` |
| `score` | Numeric(4,3) | layer별 점수 (없을 수 있음) |
| `status` | str(20) | `ok` / `warn` / `reject` |
| `reason_json` | JSON | `ValidationResult.to_dict()` 저장 |
| `created_at` | DateTime NN | |

**인덱스**: `(content_type, content_id)`, `(validator_type)`.

### 4.6 `content_version_policy` — 버전 상태 머신

plan §9 전환 트리거. 한 `dataset_version`당 최대 1 row.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | str(36) PK | |
| `dataset_version` | str(20) NN | |
| `status` | str(20) NN | `draft` / `active` / `deprecated` / `archived` |
| `activation_date` | DateTime | `draft → active` 시각 |
| `deprecation_date` | DateTime | `active → deprecated` 시각 |
| `replacement_version` | str(20) | deprecate 시 후속 버전 가리킴 |
| `transition_strategy` | str(20) | `immediate` / `gradual` 등 |
| `real_content_threshold` | Integer | real content row 수가 이 값 넘으면 자동 deprecate |
| `created_at` | DateTime NN | |

**인덱스**: `(dataset_version)`, `(status)`.

**전환 규칙(plan §9)**:
- publisher가 `--dataset-version` 없이 호출되면 `status="active"` row를 찾아 자동 선택.
- `real_content_threshold`를 초과해 실 서비스 콘텐츠가 쌓이면 버전 자동 deprecated 전환.

---

## 5. 핵심 dataclass / pydantic 카탈로그

### 5.1 `pipeline.spec.models.ContentSpec`

pydantic `BaseModel`. event_log → spec 빌더가 만들고, 모든 generator의 입력이 된다.

#### Phase 1 필드 (legacy/peer 모두 공통)

| 필드 | 타입 | 설명 |
|------|------|------|
| `spot_id` | str | |
| `region` | str | region 코드 |
| `category` | str | 카테고리 |
| `spot_type` | str | 기본 `casual_meetup` |
| `host_persona` | `HostPersona` | `{type, tone, communication_style}` |
| `participants` | `Participants` | `{expected_count, persona_mix[]}` |
| `schedule` | `Schedule` | `{date, start_time, duration_minutes}` |
| `budget` | `Budget` | `{price_band:1~5, expected_cost_per_person}` |
| `activity_constraints` | `ActivityConstraints` | `{indoor, beginner_friendly, supporter_required}` |
| `plan_outline` | list[str] | plan generator의 입력이 되는 bullet list |
| `activity_result` | `ActivityResult` \| None | settle 이후 review/message generator가 참조 |

`ActivityResult`:
```python
{ actual_participants, no_show_count, duration_actual_minutes,
  issues: list[str], overall_sentiment: "positive"|"neutral"|"negative" }
```

#### Phase Peer-D 확장 필드 (전부 Optional / default)

| 필드 | 타입 | 설명 |
|------|------|------|
| `skill_topic` | str \| None | `SkillTopic` value (한국어) |
| `host_skill_level` | int \| None 0~5 | |
| `teach_mode` | str \| None | `1:1` / `small_group` / `workshop` |
| `venue_type` | str \| None | `cafe` / `home` / `studio` / `park` / `gym` / `online` |
| `fee_breakdown` | `FeeBreakdownSpec` \| None | `{peer_labor_fee, material_cost, venue_rental, equipment_rental}` + `.total` / `.passthrough_total` 프로퍼티 |
| `origination_mode` | str | `offer` / `request_matched` (기본 `offer`) |
| `originating_voice` | str | `host` / `learner` — `origination_mode`에서 파생 |
| `originating_request_summary` | str \| None | 학생 원 요청 한 줄 요약 |
| `responded_at_tick` | int \| None | |
| `is_request_matched` | bool | `origination_mode == "request_matched"` 편의 플래그 |
| `had_renegotiation` | bool | counter-offer 이력 여부 |
| `renegotiation_history` | list[dict] | |
| `original_target_partner_count` | int \| None | |
| `final_partner_count` | int \| None | |
| `bonded_partner_count` | int | settlement 후 regular 이상 관계 수 |
| `bond_updates_at_settlement` | list[dict] | `[{partner_id, from, to}]` |
| `friend_upgrades` | list[dict] | `[{partner_id, skill, sessions}]` |
| `referrals_triggered` | list[dict] | `[{from, to, host, reason}]` |
| `host_reputation_before/after` | float \| None | |
| `host_earn_from_this_spot` | int \| None | POCKET_MONEY_EARNED 금액 |
| `latitude` / `longitude` | float \| None | region center + seed jitter (±0.003°) |
| `peer_tone_required` | bool | LLM 프롬프트에서 또래 톤 강제 (기본 True) |

### 5.2 `pipeline.generators.base.Candidate`

```python
@dataclass
class Candidate:
    content_type: str          # "feed"/"detail"/"plan"/"messages"/"review"
    variant: str               # "primary" / "alternative"
    payload: Dict[str, Any]    # type별 LLM 출력 JSON
    length_bucket: str         # "short"/"medium"/"long" — deterministic seed
    # (추가 메타 필드 있음, base.py 참조)
```

generator는 항상 `candidate × 2`(`primary` + `alternative`)를 반환한다. `length_bucket`과 variant는 spot_id 시드로 결정론적으로 고정된다.

`spec_to_variables` 반환 dict는 `COMMON_VARIABLE_KEYS` frozenset의 **superset**이어야 한다. 현재 키는 Phase 1 16개 + Phase Peer-E 21개. 이름 불일치는 pipeline-qa의 boundary audit에서 바로 걸린다.

### 5.3 `pipeline.validators.types.ValidationResult` + `Rejection`

```python
@dataclass
class Rejection:
    layer: Literal["schema","rule","cross_ref","critic","diversity"]
    rejected_field: str         # "title", "tags[0]", "__schema__" 등
    reason: str                 # machine code, ex: "category_mismatch"
    detail: str                 # 사람이 읽는 설명
    instruction: str            # generator에게 주는 한국어 재생성 지시문
    severity: Literal["reject","warn"] = "reject"

@dataclass
class ValidationResult:
    ok: bool                    # severity=reject 하나도 없으면 True
    layer: str
    rejections: list[Rejection]
    meta: dict                  # 디버깅 / 튜닝 부가정보
    # 프로퍼티
    hard_rejections -> list    # severity=reject 필터
    warnings        -> list    # severity=warn 필터
```

`instruction`은 반드시 한국어 재생성 지시문이어야 한다 — 사람과 LLM이 둘 다 따를 수 있어야 한다는 계약.

### 5.4 `pipeline.loop.generate_validate_retry.ContentProcessResult` / `SpotProcessResult`

루프가 반환하는 최상위 컨테이너. `to_dict()`로 JSON 직렬화 가능.

#### `ContentProcessResult`

| 필드 | 타입 | 설명 |
|------|------|------|
| `spot_id` | str | |
| `content_type` | str | |
| `selected_candidate` | `Candidate` \| None | 선택된 후보 (없으면 전부 rejected) |
| `quality_score` | float | Layer 6 점수 |
| `classification` | str | `approved` / `conditional` / `rejected` |
| `critic_used` | bool | Layer 4가 돌았는지 |
| `critic_sample_reason` | str | 샘플링 이유 (`sampled` / `skipped_policy` / ...) |
| `layer_results` | dict | 각 layer별 `ValidationResult.to_dict()` |
| `candidates_meta` | list[dict] | 2 candidate의 meta (length_bucket, variant 등) |

#### `SpotProcessResult`

| 필드 | 타입 | 설명 |
|------|------|------|
| `spot_id` | str | |
| `contents` | `dict[str, ContentProcessResult]` | 5종 type |
| `cross_ref_result` | `ValidationResult` \| None | |
| `llm_calls_total` | int | 해당 스팟에 소모된 LLM 호출 수 |
| `elapsed_seconds` | float | |
| `retry_count_total` | int | |
| `approved` | bool | 전체 승인 여부 |
| `content_spec` | `ContentSpec` \| None | publisher가 lat/lng 꺼낼 때 필요 |

`process_spot_full(spec, ...)`이 이 값을 반환. publisher는 바로 이걸 입력으로 받는다.

### 5.5 `pipeline.publish.publisher.PublishResult`

```python
@dataclass
class PublishResult:
    published_rows: int
    skipped_rows: int
    errors: list[str]
```

publisher가 한 스팟 처리 후 반환. `rejected` classification을 만나면 publish 안 하고 `skipped_rows++`.

---

## 6. Validator 계층과 스코어링

### 6.1 6 Layer 게이트

| Layer | 파일 | 입력 | 출력 | 재시도? |
|:----:|------|------|------|:------:|
| 1 | `validators/schema.py` | candidate payload | `ValidationResult` | ✓ generator 내부에서 |
| 2 | `validators/rules.py` + `<type>_rules.py` | payload + spec | `ValidationResult` | ✓ generator 내부에서 |
| 3 | `validators/cross_reference.py` | 5종 payload + spec | `ValidationResult` | 1회 loop 재생성 |
| 4 | `validators/critic.py` | 샘플된 candidate | `CriticResult` | — |
| 5 | `validators/diversity.py` | 배치 n-gram/TF-IDF | meta dict | — |
| 6 | `validators/scoring.py` | 위 결과 전부 | `quality_score, classification` | — |

Layer 1/2는 generator가 자체 재시도 루프(`generate_with_retry`) 안에서 쓴다 — loop가 받는 후보는 이미 통과한 상태. Layer 3 실패 시에만 loop가 해당 content type을 한 번 더 재생성한다.

### 6.2 scoring 공식 (plan §5 + peer pivot §5 Phase E)

**source of truth**: `pipeline.validators.scoring.SCORING_WEIGHTS`. 변경 시 plan 문서와 `config/weights/scoring_weights.json`도 동기화 필수.

```
quality_score =
    0.25 × naturalness
  + 0.20 × consistency
  + 0.15 × persona_fit
  + 0.10 × region_fit
  + 0.05 × business_rule_fit
  + 0.10 × diversity
  + 0.15 × peer_tone_fit       # Peer-E 신규
────────────────────────────
  = 1.00
```

**분류 임계** (상수로 export):

| 상수 | 값 | classification |
|------|---:|---------------|
| `APPROVED_THRESHOLD` | 0.80 | `approved` |
| `CONDITIONAL_THRESHOLD` | 0.65 | `conditional` (0.65~0.79) |
| (< 0.65) | — | `rejected` → 재생성 |

**peer_tone_fit 소스**:
- `CriticResult.peer_tone_score` 사용.
- critic이 돌지 않은 경우 deterministic default `0.85`.

### 6.3 `critic.CriticResult`

| 필드 | 타입 | 설명 |
|------|------|------|
| `content_type` | str | |
| `content_id` | str | |
| `naturalness` | float 0~1 | |
| `consistency_flag` | bool | 내부 모순 없음 |
| `persona_fit` | float 0~1 | |
| `peer_tone_score` | float 0~1 | Peer-E 핵심 |
| `reasoning` | str | 한국어 비평문 (debug용) |

**샘플링**: `should_sample_critic(spot_id, content_type, policy)`가 True일 때만 LLM 호출. 정책은 `load_critic_sampling_policy`가 yaml/json 설정 읽음.

### 6.4 `COMMON_VARIABLE_KEYS` (generator ↔ prompt 계약)

`generators/base.py`의 `spec_to_variables` 반환 dict 키와 codex-bridge `prompt_contract.md`의 변수 이름이 100% 일치해야 한다. Phase 1 16 키 + Phase Peer-E 21 키 = 37 키.

Phase 1 측: `spot_id`, `region_label`, `category`, `host_persona`, `participants_expected_count`, `schedule_date`, `schedule_time`, `schedule_day_type`, `schedule_time_slot`, ... (base.py 참조).

Peer-E 측: `skill_topic`, `host_skill_level`, `teach_mode`, `venue_type`, `fee_total`, `fee_peer_labor`, `fee_passthrough`, `origination_mode`, `originating_voice`, ... .

키를 바꾸면 pipeline-qa boundary audit이 즉시 걸린다 — 로컬에서 `pytest tests/test_variable_contract.py` 돌려볼 수 있음.

---

## 7. 골든 샘플 & 리포트 산출물

파이프라인이 생성하는 **DB가 아닌** 파일 산출물들.

### 7.1 `data/goldens/`

골든 input 샘플 (`golden_<category>_<region>_<time>.json`). 10~20개 수작업 ContentSpec으로, 정확히 예상되는 출력이 `_results/`에 동결되어 있다.

### 7.2 `data/goldens/_results/*.jsonl`

골든 검증 결과. 예: `phase1_e2e.jsonl` — 각 행에 spec 파일명, variant, schema/rule ok 여부, length_bucket.

```json
{"spec": "golden_food_yeonmu_evening.json", "variant": "primary",
 "schema_ok": true, "schema_rejections": [],
 "rule_ok": true, "rule_rejections": [], "length_bucket": "medium"}
```

회귀 탐지: e2e 출력이 동결본과 달라지면 QA 게이트가 fail.

### 7.3 `_workspace/scp_05_qa/`

pipeline-qa 작업 공간. batch stats, incremental boundary audit 결과 등.

예: `phase_mvp_batch_stats.json` — 배치 실행 통계 (approved/conditional/rejected 수, avg quality_score, LLM call 총수).

### 7.4 `_workspace/mvp_live_smoke.db`

SQLite 스모크 테스트 DB. Postgres 없이 전체 흐름을 end-to-end로 돌려볼 때 사용.

### 7.5 `scripts/batch_publish_mvp_500.py`

MVP 배치 publish 스크립트 — 500 스팟 한 번에. `process_spot_full` + `Publisher.publish_spot`를 반복 호출.

---

## 8. 부록 — DB 직접 질의 템플릿

CLI 외에 DB를 직접 볼 때. Postgres/SQLite 둘 다 돌아간다.

### 8.1 dataset_version별 5종 콘텐츠 수

```sql
SELECT 'feed'     AS type, COUNT(*) FROM synthetic_feed_content   WHERE dataset_version = :v
UNION ALL SELECT 'detail',   COUNT(*) FROM synthetic_spot_detail   WHERE dataset_version = :v
UNION ALL SELECT 'messages', COUNT(*) FROM synthetic_spot_messages WHERE dataset_version = :v
UNION ALL SELECT 'review',   COUNT(*) FROM synthetic_review        WHERE dataset_version = :v;
```

### 8.2 quality_score 분포

```sql
SELECT validation_status, COUNT(*) AS n, AVG(quality_score) AS avg_q
  FROM synthetic_feed_content
 WHERE dataset_version = :v
 GROUP BY validation_status;
```

### 8.3 rejected된 content의 사유

```sql
SELECT content_type, validator_type,
       reason_json->>'reason' AS reason,
       reason_json->>'detail' AS detail,
       COUNT(*) AS n
  FROM content_validation_log
 WHERE status = 'reject'
 GROUP BY content_type, validator_type,
          reason_json->>'reason', reason_json->>'detail'
 ORDER BY n DESC
 LIMIT 20;
```

(SQLite면 `json_extract(reason_json, '$.reason')`로 바꾸면 됨.)

### 8.4 messages 4종이 모두 있는지 검사

```sql
SELECT spot_id, COUNT(DISTINCT message_type) AS msg_types
  FROM synthetic_spot_messages
 WHERE dataset_version = :v
 GROUP BY spot_id
HAVING COUNT(DISTINCT message_type) < 4;
```

4보다 작은 spot이 나오면 publisher가 이상.

### 8.5 review 별점 분포

```sql
SELECT rating, COUNT(*) AS n
  FROM synthetic_review
 WHERE dataset_version = :v
 GROUP BY rating
 ORDER BY rating;
```

### 8.6 active 버전 찾기 (publisher 자동 선택 로직 재현)

```sql
SELECT dataset_version, activation_date, transition_strategy
  FROM content_version_policy
 WHERE status = 'active'
 ORDER BY activation_date DESC
 LIMIT 1;
```

### 8.7 한 스팟의 전체 산출물 dump

```sql
SELECT 'feed' AS t, title, summary, quality_score, validation_status
  FROM synthetic_feed_content WHERE spot_id = :spot_id AND dataset_version = :v
UNION ALL
SELECT 'detail', title, description, quality_score, validation_status
  FROM synthetic_spot_detail  WHERE spot_id = :spot_id AND dataset_version = :v
UNION ALL
SELECT 'message_' || message_type, speaker_type, content, quality_score, validation_status
  FROM synthetic_spot_messages WHERE spot_id = :spot_id AND dataset_version = :v
UNION ALL
SELECT 'review', reviewer_agent_id,
       COALESCE(review_text, ''), quality_score, validation_status
  FROM synthetic_review        WHERE spot_id = :spot_id AND dataset_version = :v;
```

---

## 부가 정보

- **LLM 호출 경계**: `pipeline/llm/codex_client.py`의 `call_codex`가 유일 관문. 새 generator를 만들 때도 이 함수만 쓰면 되고, 테스트에서는 monkey-patch로 가짜 응답을 주입한다.
- **append-only 원칙**: `ContentSpec`의 Phase 1 필드, DB 6 테이블 컬럼은 **절대 제거/리네임 금지**. 신규 필드는 전부 `Optional` / default로 추가한다.
- **spot-simulator와의 계약**: `build-content-spec`은 `../spot-simulator/output/event_log.jsonl`을 기본 경로로 본다. simulator가 EventLog payload key를 바꾸면 `spec/_peer.py` / `_legacy.py`가 깨지니 양쪽을 동시에 업데이트해야 한다.
- **상한 상수는 simulator 것을 공유**: `LABOR_CAP_PER_PARTNER`, `SOFT_CAP_PER_PARTNER`, `HARD_CAP_PER_PARTNER`는 `spot-simulator/models/skills.py`에서 import. feed validator가 reject 임계로 사용.
- **dataset_version 네이밍**: 20자 상한. 관례는 `v_YYYYMMDD_<tag>` — simulator/builder의 `v_YYYYMMDD_<hex6>`와 구분되므로 환경에 따라 별도 매핑이 필요할 수 있음.
