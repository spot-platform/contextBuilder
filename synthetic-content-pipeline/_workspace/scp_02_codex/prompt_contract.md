# prompt_contract.md — 프롬프트 템플릿 규약 + 공용 변수 표준

> **이 문서가 정의하는 변수 이름은 모든 생성기/검증기 템플릿이 공유하는 표준이다.**
> content-generator-engineer 가 `ContentSpec` → 변수 dict 로 변환할 때 반드시 이 이름으로 매핑해야 한다.
> 경계면 버그의 1순위는 변수 이름 불일치다.

---

## 1. 파일 경로 규칙

```
config/prompts/
    feed/
        v1.j2
        v2.j2
    detail/
        v1.j2
    plan/
        v1.j2
    messages/
        v1.j2
    review/
        v1.j2
    critic/
        v1.j2
```

- `template_id` 는 `"<dir>:v<n>"` 형식. 예: `feed:v1`, `review:v2`
- `prompt_loader.parse_template_id("feed:v1") → ("feed", 1)`
- `prompt_loader.get_latest_version("feed") → 1` (가장 큰 n)
- 신버전 추가 시: 같은 디렉토리에 `v{n+1}.j2` 추가만 하면 됨. 캐시 키에 version 이 포함되므로 자연 무효화

### Jinja2 환경

| 옵션 | 값 |
|---|---|
| `autoescape` | `False` |
| `keep_trailing_newline` | `True` |
| `undefined` | `StrictUndefined` (누락 변수 즉시 `UndefinedError`) |
| `trim_blocks` / `lstrip_blocks` | `False` |

→ **변수 누락은 런타임 에러로 즉시 노출된다.** 옵셔널 변수는 템플릿에서 반드시 `{% if var is defined and var %}` 로 감싸지 말고, **항상 dict 에 키를 채우고** 빈 값(`""`, `[]`, `null`)을 보내라.

---

## 2. 공용 변수 표준 (모든 생성기 템플릿 공통)

`call_codex(template_id, variables, ...)` 의 `variables` 딕셔너리는 다음 키를 포함해야 한다.
content-generator-engineer 는 `ContentSpec` (pydantic) → 이 딕셔너리로 변환하는 어댑터 함수를 작성해야 한다.

### 2-1. 식별/지역/카테고리

| 변수 | 타입 | 예시 | 출처 (`ContentSpec` 필드) |
|---|---|---|---|
| `spot_id` | `str` | `"S_001"` | `spec.spot_id` |
| `region_label` | `str` | `"수원시 연무동"` | `spec.region` |
| `category` | `str` | `"food"` | `spec.category` |

### 2-2. 호스트 페르소나 (객체)

| 변수 | 타입 | 예시 |
|---|---|---|
| `host_persona` | `dict` | `{"type": "supporter_teacher", "tone": "친절", "communication_style": "가벼움"}` |

내부 키:
- `type` (str) — `spec.host_persona.type`
- `tone` (str) — `spec.host_persona.tone`
- `communication_style` (str) — `spec.host_persona.communication_style`

### 2-3. 참가자

| 변수 | 타입 | 예시 | 출처 |
|---|---|---|---|
| `participants_expected_count` | `int` | `4` | `spec.participants.expected_count` |

### 2-4. 일정

| 변수 | 타입 | 예시 | 출처 |
|---|---|---|---|
| `schedule_date` | `str` (`YYYY-MM-DD`) | `"2026-04-18"` | `spec.schedule.date` |
| `schedule_time` | `str` (`HH:MM`) | `"19:00"` | `spec.schedule.start_time` |
| `schedule_day_type` | `str` enum | `"weekday"` \| `"weekend"` | derived |
| `schedule_time_slot` | `str` enum | `"morning"` \| `"afternoon"` \| `"evening"` \| `"night"` | derived |

### 2-5. 예산

| 변수 | 타입 | 예시 | 출처 |
|---|---|---|---|
| `budget_price_band` | `int` (1~5) | `1` | `spec.budget.price_band` |
| `budget_cost_per_person` | `int` (원) | `18000` | `spec.budget.expected_cost_per_person` |

### 2-6. 활동 제약 (객체)

| 변수 | 타입 | 예시 |
|---|---|---|
| `activity_constraints` | `dict` | `{"indoor": true, "beginner_friendly": true, "supporter_required": true}` |

### 2-7. 진행 개요

| 변수 | 타입 | 예시 |
|---|---|---|
| `plan_outline` | `list[str]` | `["인사", "식사", "마무리"]` |

### 2-8. 활동 결과 (settle 이후, 리뷰/메시지 전용)

| 변수 | 타입 | 비고 |
|---|---|---|
| `activity_result` | `dict \| None` | feed/detail 생성 시점에는 없을 수 있음. 그래도 키는 dict 에 포함하고 `None` 또는 `{}` 으로 |

내부 키:
- `actual_participants` (int)
- `no_show_count` (int)
- `duration_actual_minutes` (int)
- `issues` (list[str])
- `overall_sentiment` (`"positive" \| "neutral" \| "negative"`)

### 2-9. 다양성/샘플링 힌트

| 변수 | 타입 | 예시 | 의미 |
|---|---|---|---|
| `desired_length_bucket` | `str` enum | `"short" \| "medium" \| "long"` | Plan §7-2 길이 분포에서 샘플링한 버킷 |
| `sample_variant` | `str` enum | `"primary" \| "alternative"` | 한 spot 당 후보 2개 생성 시 구분자 |

---

## 3. ContentSpec → variables 매핑 (참조 구현 예시)

content-generator-engineer 가 작성할 어댑터의 형태:

```python
def spec_to_variables(spec: ContentSpec, *, sample_variant: str, length_bucket: str) -> dict:
    return {
        "spot_id": spec.spot_id,
        "region_label": spec.region,
        "category": spec.category,
        "host_persona": {
            "type": spec.host_persona.type,
            "tone": spec.host_persona.tone,
            "communication_style": spec.host_persona.communication_style,
        },
        "participants_expected_count": spec.participants.expected_count,
        "schedule_date": spec.schedule.date,
        "schedule_time": spec.schedule.start_time,
        "schedule_day_type": _derive_day_type(spec.schedule.date),
        "schedule_time_slot": _derive_time_slot(spec.schedule.start_time),
        "budget_price_band": spec.budget.price_band,
        "budget_cost_per_person": spec.budget.expected_cost_per_person,
        "activity_constraints": spec.activity_constraints.model_dump(),
        "plan_outline": list(spec.plan_outline),
        "activity_result": (
            spec.activity_result.model_dump() if spec.activity_result else None
        ),
        "desired_length_bucket": length_bucket,
        "sample_variant": sample_variant,
    }
```

`_derive_day_type` / `_derive_time_slot` 도 content-generator-engineer 가 소유한다. 브리지는 이름만 고정한다.

---

## 4. rejection_feedback 블록 포맷

브리지가 `previous_rejections` 를 자동으로 컨텍스트에 주입한다 (없으면 빈 리스트). 템플릿 작성자는 다음 블록을 표준으로 사용한다.

```jinja
{% if previous_rejections %}
---
이전 시도가 검증을 통과하지 못했습니다. 다음 거절 사유를 모두 반영해 다시 작성해주세요:

{% for r in previous_rejections %}
- 필드: {{ r.rejected_field }}
  사유: {{ r.reason }}
  상세: {{ r.detail }}
  지시: {{ r.instruction }}
{% endfor %}
---
{% endif %}
```

각 rejection 항목은 다음 4개 키를 가진다:

| 키 | 타입 | 설명 |
|---|---|---|
| `rejected_field` | `str` | 콘텐츠의 어떤 필드가 문제인지. 메타 사유는 `__schema__`, `__cross__` 등 |
| `reason` | `str` | 짧은 키워드 (`category_mismatch`, `length_too_short`, `json_parse`, ...) |
| `detail` | `str` | 사람이 읽을 수 있는 상세 설명 |
| `instruction` | `str` | LLM 에게 줄 행동 지시 |

---

## 5. 스키마 파일 매핑

| `template_id` | schema 파일 | 소유 |
|---|---|---|
| `feed:v1` | `src/pipeline/llm/schemas/feed.json` | bridge (이번 phase 1) |
| `detail:v1` | `src/pipeline/llm/schemas/detail.json` | bridge (phase 2 예정) |
| `plan:v1` | `src/pipeline/llm/schemas/plan.json` | bridge (phase 2 예정) |
| `messages:v1` | `src/pipeline/llm/schemas/messages.json` | bridge (phase 2 예정) |
| `review:v1` | `src/pipeline/llm/schemas/review.json` | bridge (phase 2 예정) |
| `critic:v1` | `src/pipeline/llm/schemas/critic.json` | bridge (phase 2 예정) |

스키마 파일 경로는 호출자가 명시적으로 전달한다 (브리지가 자동 매핑하지 않음 — 결합도 최소화).

---

## 6. 테스트 픽스처 위치 (stub 모드)

```
tests/fixtures/codex_stub/
    feed/
        v1/
            default.json            # fallback (이번 phase 1 에 포함)
            <key[:8]>.json          # 특정 variables hash 매칭 시 우선 (pipeline-qa 큐레이션)
    detail/
        v1/
            default.json
    ...
```

- `key = sha256("feed:v1|v1|" + canonical_json(variables))` 의 첫 8자
- 매칭 파일이 없으면 `default.json` fallback (warning 로그)
- pipeline-qa 가 골든 케이스마다 정확한 픽스처를 추가해 결정적 CI 보장

---

## 7. 금지 사항 재확인

1. 프롬프트 본문을 Python 코드에 하드코딩 금지 (반드시 `config/prompts/` 안에)
2. 이 문서에 정의되지 않은 변수 키를 임의로 추가하지 말 것 (추가 필요 시 본 문서를 먼저 갱신)
3. 호출자가 직접 `subprocess.run(["codex", ...])` 호출 금지 — 반드시 `call_codex` / `generate_with_retry` 경유

---

## 8. Phase 2 템플릿 목록 및 추가 변수

Phase 2 에서 brige 는 **4개 템플릿을 추가 등록**한다. 프롬프트 본문은 content-generator-engineer 가 작성하지만, 변수 이름과 스키마 경로는 브리지 소유다.

| `template_id` | schema 파일 | §2 공용 16 변수 외 **추가 변수** | 비고 |
|---|---|---|---|
| `detail:v1` | `src/pipeline/llm/schemas/detail.json` | 없음 | `activity_result` 가 None 이면 그대로 None 전달 (detail 은 모집 시점에도 생성 가능) |
| `plan:v1` | `src/pipeline/llm/schemas/plan.json` | 없음 | `plan_outline` 을 반드시 채워 전달. 빈 리스트 금지 |
| `messages:v1` | `src/pipeline/llm/schemas/messages.json` | **`host_trust_level`** (`str` enum `"trusted" \| "neutral"`) | `ContentSpec` 에 없는 파생 변수. 규칙: `activity_constraints.supporter_required == true` 이면 `"trusted"`, 아니면 `"neutral"`. 어댑터 `spec_to_variables` 가 `template_id.startswith("messages:")` 일 때 주입 |
| `review:v1` | `src/pipeline/llm/schemas/review.json` | **`review_variant_rating`** (`int`, 1~5) | `ContentSpec` 에 없는 QA 주입 변수. pipeline-qa 가 Plan §7-3 분포 (5점 55%/4점 30%/3점 10%/2점 3%/1점 2%) 로 샘플링해 주입. activity_result.overall_sentiment 와 일치해야 한다 (negative 면 1~2, neutral 이면 3~4, positive 면 4~5 범위 내) |

### 8-1. 파생 변수 규칙 요약

```python
# messages:v1 전용
def _derive_host_trust_level(spec: ContentSpec) -> str:
    return "trusted" if spec.activity_constraints.supporter_required else "neutral"

# review:v1 전용 — pipeline-qa 가 분포 샘플링해서 주입
# content-generator-engineer 는 단순히 변수 dict 에 키를 추가만 하면 된다
```

### 8-2. 어댑터 확장 (참고)

```python
def spec_to_variables(
    spec: ContentSpec,
    *,
    template_id: str,  # ← Phase 2 부터 필요
    sample_variant: str,
    length_bucket: str,
    review_variant_rating: int | None = None,  # review:v1 전용
) -> dict:
    base = { ...§3 그대로... }
    if template_id.startswith("messages:"):
        base["host_trust_level"] = (
            "trusted" if spec.activity_constraints.supporter_required else "neutral"
        )
    if template_id.startswith("review:"):
        assert review_variant_rating is not None, "review 생성 시 rating 분포 주입 필수"
        base["review_variant_rating"] = review_variant_rating
    return base
```

> 브리지는 여전히 변수 dict 에 대해 **완전성 체크를 하지 않는다** (StrictUndefined 가 렌더 타임에 잡는다). Phase 2 에서 추가되는 변수도 마찬가지로 템플릿에서 참조되지 않으면 조용히 무시된다 — **템플릿 작성 시 반드시 참조할 것**.

---

## 9. Phase 2 스킴 길이 제약 요약

Plan §5 Layer 1 과 정확히 맞춤. Layer 1 validator 는 이 스키마를 사용해 1차 reject 를 내리고, rejection_feedback 루프에서 `__schema__` 메타 사유로 전달된다.

### detail:v1

| 필드 | 제약 |
|---|---|
| title | 12~60 chars |
| description | 80~800 chars (3~6문장 권장, 프롬프트에서 유도) |
| activity_purpose | 20~200 chars |
| progress_style | 20~300 chars |
| materials | array[str], 0~10, 각 1~40 chars. §7-2 분포: 40% 없음 / 35% 1~2개 / 25% 3개+ |
| target_audience | 10~120 chars |
| cost_breakdown | array[{item,amount}], 1~6. amount: int 0~500000 |
| host_intro | 30~300 chars |
| policy_notes | optional, 0~200 chars |

### plan:v1

| 필드 | 제약 |
|---|---|
| steps | array, 3~7 items. 각 `{time, activity}`. time 은 `HH:MM` 또는 `+N분` 패턴 |
| steps[].activity | 4~40 chars |
| total_duration_minutes | int, 60~360 |

### messages:v1

| 필드 | 제약 |
|---|---|
| recruiting_intro | 40~200 chars |
| join_approval | 20~150 chars |
| day_of_notice | 30~200 chars |
| post_thanks | 20~150 chars |

> 4개 snippet 을 **한 번의 LLM 호출** 로 받는다. 호스트 톤·지역·시간 consistency 는 프롬프트 몫.

### review:v1

| 필드 | 제약 |
|---|---|
| rating | int 1~5 |
| review_text | 15~400 chars |
| satisfaction_tags | array[str], 1~5, 각 1~20 chars |
| recommend | bool |
| will_rejoin | bool |
| sentiment | enum `"positive" \| "neutral" \| "negative"` |

---

## 10. Phase 2 stub 픽스처

```
tests/fixtures/codex_stub/
    feed/v1/default.json        (Phase 1)
    detail/v1/default.json      (Phase 2)
    plan/v1/default.json        (Phase 2)
    messages/v1/default.json    (Phase 2)
    review/v1/default.json      (Phase 2)
```

- 5개 default fixture 는 **동일 spot context (연무동 저녁 식사 4명)** 로 작성되어 있다.
  Layer 3 cross-reference validator 테스트가 "같은 spot → 일관된 feed/detail/plan/messages/review" 를 검증할 수 있다.
- spec 별 variant fixture 가 필요한 경우 파일명은 `{key[:8]}.json` 형식.
  `key = sha256("<template_id>|v<n>|<canonical_json(variables)>").hexdigest()`
  (cache.make_key 와 동일 알고리즘). pipeline-qa 가 실제 variables dict 로부터 hash 를 계산해 파일명을 확정한다.
- Phase 1 gate G3 (stub 편향) 의 부분 대응으로, pipeline-qa 가 Phase 2 gate 에서 카페/운동 등 다른 카테고리 variant 2~3 개를 각 템플릿에 추가할 예정.

---

## 11. Phase 3: critic template

Phase 3 에서 브리지는 **critic 템플릿 1개**를 추가 등록한다. critic 은 §5 Layer 4 에 정의된 **샘플링 기반 LLM 평가기**로, 다른 5 개 생성기와 구조적으로 다르다.

### 11-1. 핵심 차이점

| 구분 | 생성기 (feed/detail/plan/messages/review) | critic |
|---|---|---|
| 입력 | `ContentSpec` → §2 공용 16 변수 | **이미 생성된 payload 한 건 + spec 요약** |
| 출력 스키마 | content type 별 개별 스키마 | **critic.json (점수 5개 + reject + reasons)** |
| 호출 빈도 | 스팟당 5 × 2 = 10 회 | **샘플링 10~20%** (§10 전략 1) |
| 재시도 루프 | rejection_feedback 재시도 대상 | **재시도 대상 아님** (평가 결과 그대로 사용) |
| 사용 모델 | `SCP_CODEX_MODEL` (기본 `gpt-5-codex`) | `SCP_CODEX_MODEL_CRITIC` (§10 전략 4, MVP 는 동일 모델) |

즉 critic 호출은 `call_codex(template_id="critic:v1", variables={...payload 기반...}, output_schema=critic.json)` 형태지만 **§2 공용 변수 16 개를 사용하지 않는다**. validator-engineer 가 Layer 4 evaluator 에서 직접 아래 변수 dict 를 조립한다.

### 11-2. 입력 변수 (critic 전용)

| 변수 | 타입 | 예시 | 설명 |
|---|---|---|---|
| `content_type` | `str` enum | `"feed"` | 평가 대상 content type. `"feed" \| "detail" \| "plan" \| "messages" \| "review"` 중 하나 |
| `content_payload` | `dict` | `{"title": "...", "summary": "...", ...}` | Layer 1~3 을 통과한 생성 결과 JSON **그대로**. 키/값 변형 없이 주입 |
| `content_spec_summary` | `dict` | 아래 표 | spec 전체가 아닌 평가에 필요한 **요약 5 필드** |
| `eval_focus` | `list[str]` | `["naturalness", "regional_fit"]` | 이번 호출에서 critic 이 특히 주목해야 할 항목. 0~5 개. 비어 있으면 전 항목 균등 평가 |
| `sample_reason` | `str` enum | `"random_10pct"` | 이 호출이 샘플링된 이유. 로그/분석 전용. `"random_10pct" \| "boundary_score" \| "new_category_region"` |

#### `content_spec_summary` 내부 키

| 키 | 타입 | 출처 |
|---|---|---|
| `region` | `str` | `spec.region` |
| `category` | `str` | `spec.category` |
| `host_persona_type` | `str` | `spec.host_persona.type` |
| `participants_expected_count` | `int` | `spec.participants.expected_count` |
| `budget_cost_per_person` | `int` | `spec.budget.expected_cost_per_person` |

> 원칙: critic 은 "생성물 vs spec 요약" 이라는 얇은 컨텍스트만 본다. 전체 spec 을 주면 평가 범위가 흐려지고 토큰이 늘어난다.

### 11-3. 출력 스키마

- 파일: `src/pipeline/llm/schemas/critic.json`
- 구조: §5 Layer 4 그대로
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
- 각 score 는 `number [0, 1]`
- `reject` 는 boolean. `reject=true` 일 때 `reasons` 는 최소 1 개 이상이 되도록 프롬프트에서 유도 (스키마 자체는 `minItems: 0` 이다 — 통과 케이스에서 빈 배열 허용이 우선)
- `reasons.items` 는 `string [1, 200]`, 최대 5 개
- **strict mode**: `additionalProperties: false`, `required` 는 7 개 전부 (`naturalness_score`, `consistency_score`, `regional_fit_score`, `persona_fit_score`, `safety_score`, `reject`, `reasons`). Phase 2 detail.json 의 `policy_notes` 교훈 반영

### 11-4. 호출 예시 (validator-engineer 용)

```python
from pathlib import Path
from pipeline.llm.codex_client import call_codex

critic_variables = {
    "content_type": "feed",
    "content_payload": feed_payload,  # Layer 1~3 통과한 JSON dict
    "content_spec_summary": {
        "region": spec.region,
        "category": spec.category,
        "host_persona_type": spec.host_persona.type,
        "participants_expected_count": spec.participants.expected_count,
        "budget_cost_per_person": spec.budget.expected_cost_per_person,
    },
    "eval_focus": ["naturalness", "regional_fit"],
    "sample_reason": "random_10pct",
}

result = call_codex(
    template_id="critic:v1",
    variables=critic_variables,
    output_schema=Path("src/pipeline/llm/schemas/critic.json"),
    # MVP 는 기본 모델. 경량 모델 실험 시 SCP_CODEX_MODEL_CRITIC 환경변수 사용
)
# result: {"naturalness_score": ..., "reject": False, "reasons": [], ...}
```

### 11-5. 모델 선택 환경변수

| 환경변수 | 기본값 | 용도 |
|---|---|---|
| `SCP_CODEX_MODEL` | `gpt-5-codex` | 생성기 5 종 기본 모델 |
| `SCP_CODEX_MODEL_CRITIC` | `gpt-5-codex` | critic 전용 모델. §10 전략 4 에 따라 차후 경량 모델로 교체 가능. MVP 는 동일 모델 사용 |

> 브리지 `call_codex` 는 기존 `model` 인자를 그대로 유지한다. validator-engineer 가 Layer 4 evaluator 에서 `os.environ.get("SCP_CODEX_MODEL_CRITIC", "gpt-5-codex")` 를 읽어 `call_codex(..., model=...)` 로 전달한다. 브리지는 critic 템플릿을 특별 취급하지 않는다.

### 11-6. stub 픽스처

```
tests/fixtures/codex_stub/
    critic/
        v1/
            default.json                 # reject=false 통과 케이스
            critic_reject_sample.json    # reject=true 거절 케이스
```

- `default.json`: 모든 score 0.85~0.95, `reject=false`, `reasons=[]`
- `critic_reject_sample.json`: `naturalness_score=0.55`, `consistency_score=0.70`, `regional_fit_score=0.90`, `persona_fit_score=0.50`, `safety_score=0.98`, `reject=true`, `reasons=["톤이 지나치게 기계적", "persona_tone 반영 부족"]`
- pipeline-qa 는 Phase 3 테스트에서 두 케이스 모두를 사용해 Layer 4 evaluator 의 통과/거절 분기를 검증한다
- 파일명 해시 매칭 규칙은 §6 과 동일 (`<key[:8]>.json` → `default.json` fallback). `critic_reject_sample.json` 은 variant 파일이 아닌 **이름 기반 명시적 선택** 을 위한 것으로, pipeline-qa 가 테스트에서 이 파일을 직접 지정해 로드한다.

### 11-7. critic 이 건드리지 않는 것

- 생성기 템플릿 5 종 (feed/detail/plan/messages/review) 의 변수 규약 (§2, §8) — 불변
- rejection_feedback 루프 (§4) — critic 은 이 루프에 참여하지 않음
- `spec_to_variables` 어댑터 — critic 은 별도 변수 dict 를 사용하므로 어댑터를 거치지 않음

> 결론: critic 은 같은 `call_codex` API 를 재사용하지만 **변수 dict 의 내용물이 완전히 다른 계약** 이다. Layer 4 evaluator 를 작성하는 validator-engineer 가 본 §11 을 단일 소스로 삼는다.

