# bridge_api.md — codex bridge 공개 시그니처

> 모든 LLM 호출은 **이 두 함수**를 통한다. 직접 `subprocess.run(["codex", ...])` 금지.
> 어겼는지 여부는 향후 `scripts/lint_no_api.py` 가 검사한다.

## 1. `codex_client.call_codex`

```python
from pipeline.llm.codex_client import call_codex
from pathlib import Path

response: dict = call_codex(
    template_id="feed:v1",                       # "<dir>:v<n>" (config/prompts/<dir>/v<n>.j2)
    variables={...},                              # prompt_contract.md 의 공용 변수 표준 준수
    schema_path=Path("src/pipeline/llm/schemas/feed.json"),
    model=None,                                   # None → SCP_CODEX_MODEL_GEN 또는 "gpt-5-codex"
    previous_rejections=None,                     # rejection feedback 루프에서만 전달
)
```

| 인자 | 타입 | 설명 |
|---|---|---|
| `template_id` | `str` | `feed:v1` 같은 `<dir>:v<n>` 형식. `prompt_loader.parse_template_id` 로 파싱 |
| `variables` | `Mapping[str, Any]` | Jinja2 컨텍스트. **`prompt_contract.md` 의 공용 변수 표준을 반드시 따른다** |
| `schema_path` | `pathlib.Path` | codex `--output-schema` 로 강제할 JSON Schema 파일 |
| `model` | `str \| None` | 미지정 시 env `SCP_CODEX_MODEL_GEN`, 그래도 없으면 `gpt-5-codex` |
| `previous_rejections` | `Sequence[Mapping] \| None` | 직전 거절 사유 리스트. 템플릿의 `{% if previous_rejections %}` 블록에서 참조 |

**반환**: `dict` — codex 응답 JSON. 스키마 통과를 가정. 실패는 예외로 표현.

### 모드 분기

| `SCP_LLM_MODE` | 동작 |
|---|---|
| `stub` (기본) | `tests/fixtures/codex_stub/<dir>/v<n>/<key[:8]>.json` lookup → 없으면 `default.json` fallback (warning 로그). subprocess 와 프롬프트 렌더 모두 생략 |
| `live` | 캐시 lookup → miss 시 `prompt_loader.render` → `_invoke_codex` 호출 → 캐시 저장 |

`SCP_LLM_CACHE=off` 면 캐시 건너뜀 (live 모드 한정).

---

## 2. `retry.generate_with_retry`

```python
from pipeline.llm.retry import generate_with_retry

def quick_validator(response: dict) -> tuple[bool, list[dict]]:
    # validator-engineer 가 Layer 1+2 를 묶어 구현해 전달
    return True, []

response = generate_with_retry(
    template_id="feed:v1",
    variables=ctx,
    schema_path=Path("src/pipeline/llm/schemas/feed.json"),
    quick_validator=quick_validator,
    max_retries=2,                       # 첫 호출 + 재시도 2회 = 최대 3회
)
```

### `quick_validator` 콜백 계약

```python
QuickValidator = Callable[[dict], tuple[bool, list[Mapping[str, Any]]]]
```

- `ok=True` → 즉시 반환
- `ok=False` → `rejections` 가 다음 호출의 `previous_rejections` 에 누적

### `rejection` 항목 형식

```python
{
    "rejected_field": "summary",          # 어떤 필드가 문제인가 (메타: "__schema__", "__cross__")
    "reason": "category_mismatch",        # 짧은 키워드
    "detail": "food 카테고리인데 '드로잉'이 포함됨",
    "instruction": "식사/카페/대화 중심으로 재생성",
}
```

### 특수 동작

| 상황 | 동작 |
|---|---|
| `CodexSchemaError` | rejection 자동 주입 (`rejected_field="__schema__"`, `reason="json_parse"`) 후 재시도 |
| `CodexRateLimitError` | 30s sleep 후 재시도 (max 1회). 두 번째는 raise |
| 재시도 소진 | 마지막 응답에 `_retry_exhausted=True` + `_history=[...]` 메타 부착해 반환 |
| 모든 시도가 schema error | `{"_retry_exhausted": True, "_history": [...]}` 반환 (실제 응답 없음) |

---

## 3. 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `SCP_LLM_MODE` | `stub` | `stub` \| `live` |
| `SCP_LLM_CACHE` | `on` | `off` 면 live 모드에서 캐시 lookup/store 모두 건너뜀 |
| `SCP_CODEX_MODEL_GEN` | `gpt-5-codex` | 생성기 모델 |
| `SCP_CODEX_TIMEOUT` | `90` (초) | `codex exec` subprocess 타임아웃 |
| `SCP_CODEX_CONCURRENCY` | `2` | `threading.BoundedSemaphore` 동시 codex 프로세스 수 |

API 키 환경변수(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`)는 **읽지 않는다**.

---

## 4. 에러 타입

| 예외 | 발생 상황 | 호출자 권장 동작 |
|---|---|---|
| `CodexLoginError` | `health.check_codex_login()` 실패 (codex CLI 없음 / 미로그인) | `ensure_codex_ready()` 가 자동으로 `sys.exit(2)` |
| `CodexCallError(exit_code, stderr_tail)` | `codex exec` non-zero exit (rate limit 외) | 1회 재시도 후 fail (호출자 책임) |
| `CodexTimeoutError(timeout)` | `codex exec` subprocess 타임아웃 | 1회 재시도 |
| `CodexSchemaError(message, raw_excerpt)` | 응답 JSON 파싱 실패 | `retry.py` 가 자동으로 catch → rejection 주입 후 재시도 |
| `CodexRateLimitError(stderr_tail)` | stderr 에 `rate limit`/`too many requests`/`quota`/`429` 키워드 | `retry.py` 가 30s 대기 후 1회 재시도 |

모두 `CodexBridgeError` 를 부모로 가진다.

---

## 5. 헬스체크

```python
from pipeline.llm.health import ensure_codex_ready
ensure_codex_ready()   # 파이프라인 진입 시 1회만. 내부 캐시 flag.
```

`check_codex_login()` 은 `codex login status` 를 10초 timeout subprocess 로 호출. exit 0 이면 True, 아니면 `CodexLoginError` raise. 실패 시 `ensure_codex_ready` 가 stderr 안내 후 `sys.exit(2)` 로 fail-fast.

---

## 6. 호출 예시 (Python)

```python
import os
os.environ.setdefault("SCP_LLM_MODE", "stub")  # 단위 테스트는 항상 stub

from pathlib import Path
from pipeline.llm.codex_client import call_codex

ROOT = Path(__file__).resolve().parents[3]  # synthetic-content-pipeline/
SCHEMA = ROOT / "src/pipeline/llm/schemas/feed.json"

variables = {
    "spot_id": "S_DEMO",
    "region_label": "수원시 연무동",
    "category": "food",
    "host_persona": {"type": "night_social", "tone": "친절", "communication_style": "가벼움"},
    "participants_expected_count": 4,
    "schedule_date": "2026-04-18",
    "schedule_time": "19:00",
    "schedule_day_type": "weekday",
    "schedule_time_slot": "evening",
    "budget_price_band": 1,
    "budget_cost_per_person": 18000,
    "activity_constraints": {"indoor": True, "beginner_friendly": True, "supporter_required": True},
    "plan_outline": ["인사", "식사", "마무리"],
    "activity_result": {
        "actual_participants": 3,
        "no_show_count": 1,
        "duration_actual_minutes": 110,
        "issues": [],
        "overall_sentiment": "positive",
    },
    "desired_length_bucket": "medium",
    "sample_variant": "primary",
}

response = call_codex("feed:v1", variables, SCHEMA)
```
