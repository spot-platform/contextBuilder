# scp_02_codex Phase 2 Delta — schema & stub fixtures 추가

> 완료 마크: `scp_02_codex_phase2_complete`
> 범위: **Python 코드 변경 없음.** JSON schema 4종 + stub fixture 4종 + 문서만 추가.
> Phase 1 artifacts (codex_client / prompt_loader / retry / cache / health) 는 **건드리지 않았다.**

---

## 1. 추가된 파일

### 1-1. JSON Schema (draft-07)

```
src/pipeline/llm/schemas/detail.json      (신규)
src/pipeline/llm/schemas/plan.json        (신규)
src/pipeline/llm/schemas/messages.json    (신규)
src/pipeline/llm/schemas/review.json      (신규)
```

- 모두 `$schema: http://json-schema.org/draft-07/schema#`, `additionalProperties: false`
- feed.json 과 동일한 style (required / properties / 길이 제약만 사용, 복잡한 $ref 없음)
- `Draft7Validator.check_schema` self-validation 통과 확인 완료

### 1-2. Stub fixture (default.json)

```
tests/fixtures/codex_stub/detail/v1/default.json
tests/fixtures/codex_stub/plan/v1/default.json
tests/fixtures/codex_stub/messages/v1/default.json
tests/fixtures/codex_stub/review/v1/default.json
```

- **5개 default fixture (feed 포함) 가 모두 같은 spot context** ("연무동 저녁 식사 4명, 4/18 19:00, supporter_teacher, 1인 1.5~2만원") 로 작성됨
- Layer 3 cross-reference validator 가 "같은 spot 의 feed/detail/plan/messages/review 가 일관된가" 를 테스트할 수 있음
- 각 fixture 는 자신의 schema 를 통과 (Draft7Validator.iter_errors 로 확인)

### 1-3. 문서

```
_workspace/scp_02_codex/prompt_contract.md   (§8~§10 추가)
_workspace/scp_02_codex/phase2_delta.md      (이 문서)
```

---

## 2. Schema diff summary (feed 대비)

`feed.json` 에는 없고 Phase 2 스키마 중 하나 이상에만 있는 필드/제약:

| 필드 | 어느 스키마 | 타입 | 비고 |
|---|---|---|---|
| `description` | detail | str 80~800 | feed.summary (1~400) 보다 길고 빡셈 |
| `activity_purpose` | detail | str 20~200 | feed 에 없음 |
| `progress_style` | detail | str 20~300 | |
| `materials` | detail | array[str] 0~10 | Plan §7-2 분포 샘플링 필요 |
| `target_audience` | detail | str 10~120 | |
| `cost_breakdown` | detail | array[{item,amount}] 1~6 | **nested object array** (feed 에는 없음) |
| `host_intro` | detail | str 30~300 | |
| `policy_notes` | detail | str 0~200 optional | 유일한 optional 필드 |
| `steps` | plan | array[{time,activity}] 3~7 | time pattern `^(HH:MM\|\+N분)$` |
| `total_duration_minutes` | plan | int 60~360 | |
| `recruiting_intro` | messages | str 40~200 | 4 snippet 중 최장 lower bound |
| `join_approval` | messages | str 20~150 | |
| `day_of_notice` | messages | str 30~200 | |
| `post_thanks` | messages | str 20~150 | |
| `rating` | review | int 1~5 | |
| `review_text` | review | str 15~400 | |
| `satisfaction_tags` | review | array[str] 1~5 | |
| `recommend` | review | bool | **bool 은 feed 에 없음** |
| `will_rejoin` | review | bool | |
| `sentiment` | review | enum positive/neutral/negative | feed.status 와 enum 패턴 공유 |

**feed 와 공통 패턴**: `additionalProperties: false`, 필수 필드 나열, 길이 제약 위주.
**new patterns (validator-engineer 주의)**:
1. `plan.steps[].time` 은 regex pattern 이 걸려 있음 — regex validator 지원 필요 (`jsonschema` 는 기본 지원)
2. `detail.cost_breakdown` 과 `plan.steps` 는 nested array[object]. Layer 1 에서 message path 가 깊어짐 → rejection instruction 에서 이 path 를 그대로 노출할지 정책 필요
3. `review.recommend / will_rejoin` 은 bool 이라 길이 제약이 없음. Layer 2 rule validator 에서 "rating 낮은데 recommend=true" 같은 교차 규칙을 책임

---

## 3. 다른 에이전트가 새 schema 를 언제/어떻게 import 해야 하나

### 3-1. content-generator-engineer

- `pipeline.llm.codex_client.call_codex` 호출 시 `schema_path` 로 해당 파일 경로를 전달:
  ```python
  from pathlib import Path
  SCHEMA_ROOT = Path(__file__).resolve().parents[...] / "src/pipeline/llm/schemas"

  call_codex("detail:v1", variables, SCHEMA_ROOT / "detail.json")
  call_codex("plan:v1",    variables, SCHEMA_ROOT / "plan.json")
  call_codex("messages:v1",variables, SCHEMA_ROOT / "messages.json")
  call_codex("review:v1",  variables, SCHEMA_ROOT / "review.json")
  ```
- `spec_to_variables(..., template_id=...)` 분기 추가 필요 (prompt_contract.md §8-2 참조):
  - `messages:v1` → `host_trust_level` 주입
  - `review:v1` → `review_variant_rating` (pipeline-qa 에서 받아서 전달)
- `config/prompts/{detail,plan,messages,review}/v1.j2` 프롬프트 본문 작성은 **content-generator-engineer 책임** (브리지는 터치하지 않음)

### 3-2. validator-engineer

- **Layer 1 schema validator** 는 `src/pipeline/llm/schemas/*.json` 을 읽어 `Draft7Validator` 로 검증만 하면 됨 (Python 코드 변경 없음)
- Layer 1 에서 reject 시 `rejected_field` 는 jsonschema error 의 `.absolute_path` 를 "." 로 join 한 값을 그대로 사용하길 권장 (예: `cost_breakdown.0.amount`, `steps.2.time`)
- **Layer 2 rule validator** 가 책임져야 할 추가 규칙 (schema 로는 못 잡음):
  - `review.rating ≤ 2` 이면 `sentiment == "negative"`, `recommend == false`
  - `detail.cost_breakdown[].amount` 합이 `budget_cost_per_person` 의 ±30% 이내
  - `plan.steps` 의 시간 간격이 `total_duration_minutes` 와 모순 없는지
  - `messages.*` 에 `region_label`, `schedule_date/time`, `host_persona.tone` 이 반영되었는지
- **Layer 3 cross-reference validator** 는 5개 default fixture 가 모두 같은 spot context 이므로 "통과하는 golden case" 로 사용 가능

### 3-3. pipeline-qa

- stub 모드 CI 를 Phase 2 생성기 테스트로 확장할 때 **추가 코드 불필요**.
  `SCP_LLM_MODE=stub` 상태에서 `call_codex("{detail,plan,messages,review}:v1", ...)` 이 각각 default.json 을 반환하는 것을 이미 확인했음:

  ```
  stub fallback: no fixture for key 7e58d609, using default.json (template=detail:v1)
  stub fallback: no fixture for key 37fb5be1, using default.json (template=plan:v1)
  stub fallback: no fixture for key 4e20cec3, using default.json (template=messages:v1)
  stub fallback: no fixture for key 6f8366a0, using default.json (template=review:v1)
  ```

- Phase 1 gate G3 (stub 편향) 의 잔여 작업:
  - pipeline-qa 가 카페/운동/드로잉 등 **카테고리 variant 2~3 개** 의 fixture 를 각 템플릿에 추가.
  - 파일명은 `tests/fixtures/codex_stub/<template>/v1/<key[:8]>.json`
  - `key = cache.make_key(template_id, version, variables)` 와 동일한 함수를 그대로 사용 (이미 codex_client 가 stub lookup 에 쓰는 함수이므로 pipeline-qa 는 소스 재사용):
    ```python
    from pipeline.llm import cache as cache_mod
    key = cache_mod.make_key("detail:v1", 1, variables)
    fname = key[:8] + ".json"
    ```
  - variant fixture 추가 시 `review_variant_rating` 분포가 Plan §7-3 (5점 55% / 4점 30% / 3점 10% / 2점 3% / 1점 2%) 와 맞는지 qa 가 직접 확인

- **review 분포 주입 테스트**: pipeline-qa 는 `review_variant_rating` 변수를 바꿔가며 여러 variant fixture 를 생성해, review_generator 가 rating 을 컨트롤 가능한지 (그리고 Layer 2 가 rating↔sentiment 모순을 잡는지) CI 에서 확인할 수 있다.

### 3-4. pipeline-infra-architect

- 디렉토리 구조 변경 없음. Phase 1 에서 확정한 `src/pipeline/llm/schemas/` 와 `tests/fixtures/codex_stub/` 트리에 파일만 추가됨.

---

## 4. 검증 로그 (2026-04-14)

```
$ PYTHONPATH=src python3 -c "...Draft7Validator.check_schema..."
detail schema ok
plan schema ok
messages schema ok
review schema ok

$ PYTHONPATH=src python3 -c "...fixture iter_errors..."
detail fixture ok
plan fixture ok
messages fixture ok
review fixture ok

$ SCP_LLM_MODE=stub PYTHONPATH=src python3 -c "...call_codex smoke..."
stub fallback: no fixture for key 7e58d609, using default.json (template=detail:v1)
detail ['title', 'description', 'activity_purpose']
stub fallback: no fixture for key 37fb5be1, using default.json (template=plan:v1)
plan ['steps', 'total_duration_minutes']
stub fallback: no fixture for key 4e20cec3, using default.json (template=messages:v1)
messages ['recruiting_intro', 'join_approval', 'day_of_notice']
stub fallback: no fixture for key 6f8366a0, using default.json (template=review:v1)
review ['rating', 'review_text', 'satisfaction_tags']
```

3개 검증 모두 통과. Phase 2 브리지 작업 완료.

---

## 5. 안 한 것 (경계선 재확인)

- 프롬프트 본문 `.j2` 파일 작성 — **content-generator-engineer** 소유
- Layer 2 rule validator / Layer 3 cross-reference validator 구현 — **validator-engineer** 소유
- spec-variant fixture (카테고리별 2~3개) 추가 — **pipeline-qa** 소유 (hash 계산 방식만 문서화 §3-3)
- Python 코드 (codex_client / prompt_loader / retry / cache / health / errors) 수정 — **Phase 1 에서 이미 고정**
- `critic.json` schema — 이번 범위 밖 (Phase 3)
