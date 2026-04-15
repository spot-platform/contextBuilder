# Phase 3 delta — codex-bridge-engineer

> 완료 마크: `scp_02_codex_phase3_complete`
> 범위: critic 스키마 + stub fixture 2 개 + prompt_contract §11 확장. **Python 코드 변경 없음.**

## 1. 이번 Phase 에 추가된 파일

| 경로 | 역할 |
|---|---|
| `src/pipeline/llm/schemas/critic.json` | Layer 4 LLM critic 평가 결과 스키마 (draft-07, strict mode) |
| `tests/fixtures/codex_stub/critic/v1/default.json` | stub fallback — `reject=false` 통과 케이스 |
| `tests/fixtures/codex_stub/critic/v1/critic_reject_sample.json` | stub variant — `reject=true` 거절 케이스 (pipeline-qa 가 테스트에서 명시 로드) |
| `_workspace/scp_02_codex/prompt_contract.md` §11 추가 | critic 템플릿 변수 규약 + 호출 예시 + 모델 환경변수 |
| `_workspace/scp_02_codex/phase3_delta.md` | (이 파일) |

## 2. critic.json 이 다른 5 개 schema 와 다른 점

1. **입력 대상이 spec 이 아니라 payload 이다.** 생성기 schema (feed/detail/plan/messages/review) 는 "스팟 스펙으로부터 새로 만들 JSON 구조" 를 정의한다. critic.json 은 "이미 만들어진 payload 한 건에 대한 평가 결과" 를 정의한다 — 즉 **메타 스키마**.
2. **§2 공용 16 변수를 사용하지 않는다.** 대신 critic 전용 5 변수 (`content_type`, `content_payload`, `content_spec_summary`, `eval_focus`, `sample_reason`) 를 사용한다. `spec_to_variables` 어댑터를 거치지 않는다.
3. **rejection_feedback 루프에 참여하지 않는다.** critic 의 결과는 Layer 4 → Layer 6 scoring 으로 직행한다. critic 자체가 재시도 대상이 아니므로 `previous_rejections` 블록을 템플릿에 포함할 필요가 없다.
4. **모델 환경변수가 분리되어 있다.** `SCP_CODEX_MODEL_CRITIC` (기본 `gpt-5-codex`). §10 전략 4 에 따라 차후 경량 모델로 교체 가능하나 MVP 는 동일.
5. **샘플링 호출만 한다.** 전체 생성 호출의 10~20% 범위 (§10 비용 관리). 따라서 stub fixture 설계에서도 "항상 통과 + 때때로 거절" 두 케이스만 준비했다.

## 3. strict mode 준수 검증 결과

### 3-1. schema self-validate

```
$ cd synthetic-content-pipeline
$ PYTHONPATH=src python3 -c "
import json
from jsonschema import Draft7Validator
s = json.load(open('src/pipeline/llm/schemas/critic.json'))
Draft7Validator.check_schema(s)
req = set(s['required'])
props = set(s['properties'].keys())
print('schema_ok, strict_ok=', req == props, 'props-req=', props - req)
"
schema_ok, strict_ok= True props-req= set()
```

- `Draft7Validator.check_schema` 통과 — schema 자체는 draft-07 문법적으로 유효
- `required == properties.keys()` 가 `True` — 즉 **7 개 필드가 모두 required 에 포함** 되어 있음:
  `naturalness_score`, `consistency_score`, `regional_fit_score`, `persona_fit_score`, `safety_score`, `reject`, `reasons`
- `additionalProperties: false` 명시
- Phase 2 detail.json 의 `policy_notes` 누락 교훈이 완전히 반영되었음 — optional 느낌의 필드 (`reasons` 는 통과 케이스에선 빈 배열이 기본) 도 required 에 포함

### 3-2. fixture validate (생성 결과가 스키마를 통과하는가)

```
$ PYTHONPATH=src python3 -c "
import json
from jsonschema import Draft7Validator
s = json.load(open('src/pipeline/llm/schemas/critic.json'))
for f in ['default.json','critic_reject_sample.json']:
    fx = json.load(open(f'tests/fixtures/codex_stub/critic/v1/{f}'))
    errs = list(Draft7Validator(s).iter_errors(fx))
    print(f, 'errs=', len(errs))
"
default.json errs= 0
critic_reject_sample.json errs= 0
```

- 두 fixture 모두 스키마 에러 0 — pipeline-qa 가 어느 쪽을 로드하든 Layer 4 evaluator 의 JSON 파싱이 안전하게 통과

### 3-3. stub mode smoke (codex_client 가 fixture 를 정상 로드)

```
$ SCP_LLM_MODE=stub PYTHONPATH=src python3 -c "
from pipeline.llm.codex_client import call_codex
from pathlib import Path
r = call_codex('critic:v1', {'content_type':'feed'}, Path('src/pipeline/llm/schemas/critic.json'))
print(list(r.keys()))
"
stub fallback: no fixture for key 0d449e78, using default.json (template=critic:v1)
['naturalness_score', 'consistency_score', 'regional_fit_score', 'persona_fit_score', 'safety_score', 'reject', 'reasons']
```

- `SCP_LLM_MODE=stub` 에서 `critic:v1` 템플릿 ID 를 `call_codex` 로 호출하면 브리지가 `tests/fixtures/codex_stub/critic/v1/default.json` 을 로드해 반환
- 반환 dict 의 키가 스키마 required 7 개와 정확히 일치
- "no fixture for key 0d449e78" warning 은 정상 — pipeline-qa 가 특정 variables 조합에 대해 정확한 fixture 를 추가하면 자동 매칭

## 4. 건드리지 않은 파일 (명시)

이번 phase 에서 다음 파일은 **전혀 수정하지 않았다**. Phase 1/2 에서 확정된 브리지 본체는 건드리지 않는다는 원칙을 유지.

- `src/pipeline/llm/codex_client.py`
- `src/pipeline/llm/prompt_loader.py`
- `src/pipeline/llm/retry.py`
- `src/pipeline/llm/cache.py`
- `src/pipeline/llm/health.py`
- 생성기 5 종 스키마 (`feed.json`, `detail.json`, `plan.json`, `messages.json`, `review.json`)
- 생성기 5 종 fixture

critic 호출이 기존 `call_codex` API 를 **재사용** 하므로, 브리지 본체에 critic 전용 분기가 필요하지 않다. 템플릿 ID 만 `"critic:v1"` 로 오면 기존 경로 그대로 흘러간다.

## 5. 다음 단계 (인계)

| 수신자 | 할 일 |
|---|---|
| `content-generator-engineer` | `config/prompts/critic/v1.j2` 템플릿 본문 작성. 변수는 §11-2 5 개만 참조. 출력은 §11-3 스키마 형태를 유도하는 지시문 작성 (JSON 강제는 `--output-schema` 로 처리되므로 프롬프트는 "이런 관점에서 평가하라" 수준으로 충분) |
| `validator-engineer` | Layer 4 evaluator 에서 `call_codex("critic:v1", critic_variables, Path(".../critic.json"))` 호출. 샘플링 정책 (랜덤 10%, 경계 점수, 새 카테고리/지역) 구현 |
| `pipeline-qa` | Phase 3 테스트에서 `default.json` (통과) + `critic_reject_sample.json` (거절) 두 fixture 를 모두 로드해 evaluator 분기 검증. 필요 시 특정 variables hash 매칭 fixture 추가 |

## 6. 전체 schema 현황 (Phase 3 완료 시점)

| 파일 | strict_ok | required 개수 |
|---|---|---|
| `feed.json` | ✔ | 8 |
| `detail.json` | ✔ (Phase 2 policy_notes 수정 후) | 9 |
| `plan.json` | ✔ | 3 |
| `messages.json` | ✔ | 4 |
| `review.json` | ✔ | 6 |
| `critic.json` | ✔ (이번 phase) | 7 |

6 종 스키마 모두 OpenAI strict JSON schema mode 호환 상태.
