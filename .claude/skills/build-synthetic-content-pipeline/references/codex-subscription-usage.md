# Codex 구독 CLI 사용 규약

synthetic-content-pipeline은 **사용자의 Codex 구독**을 LLM 백엔드로 사용한다. OpenAI / Anthropic API 키를 쓰지 않는다. 이 문서는 파이프라인 내 **모든** LLM 호출이 따라야 할 규약을 정의한다.

## 목차

1. 왜 CLI subprocess 방식인가
2. 선행 조건
3. 표준 호출 형태
4. 출력 스키마 강제
5. stub 모드 (CI / 단위 테스트)
6. live 모드 (통합 테스트 / 실행)
7. 동시성과 rate limit
8. 에러 분류
9. 캐시 전략
10. 금지 사항
11. 테스트 방법

---

## 1. 왜 CLI subprocess 방식인가

- 사용자가 보유한 Codex **구독료**를 그대로 사용하기 위함. API 키 기반 호출은 별도 크레딧이 나가므로 제외
- `codex login`으로 저장된 세션이 `~/.codex/`에 있고, `codex exec`가 이를 자동 사용
- JSON 스키마 강제(`--output-schema`)와 메시지 파일 출력(`-o`)으로 결정적 파싱 가능
- `--sandbox read-only`로 LLM이 로컬 파일을 수정하지 못하게 봉인

## 2. 선행 조건

1. `codex --version` 확인 (설치 여부)
2. `codex login status` 호출 → 성공 메시지 확인
3. `~/.codex/config.toml` 존재 여부 확인 (없어도 동작하지만 있으면 profile 재사용)
4. 기본 모델은 `gpt-5-codex`. content generator는 품질 우선, critic은 경량(`-m gpt-5-codex-mini`) 가능 (§10 비용 최적화 전략 4)
5. 환경변수:
   - `SCP_CODEX_TIMEOUT` (초, 기본 90)
   - `SCP_CODEX_CONCURRENCY` (기본 2)
   - `SCP_LLM_MODE` = `stub` | `live` (기본 `stub`)
   - `SCP_CODEX_MODEL_GEN`, `SCP_CODEX_MODEL_CRITIC`

## 3. 표준 호출 형태

`src/pipeline/llm/codex_client.py`가 **유일한** 호출 지점:

```python
import json, subprocess, tempfile, os, pathlib

def _invoke_codex(prompt: str, schema_path: pathlib.Path, model: str) -> dict:
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as msg_file:
        msg_path = msg_file.name
    cmd = [
        "codex", "exec",
        "--json",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox", "read-only",
        "--output-schema", str(schema_path),
        "-o", msg_path,
        "-m", model,
        prompt,
    ]
    timeout = int(os.environ.get("SCP_CODEX_TIMEOUT", "90"))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-30:])
        raise CodexCallError(proc.returncode, tail)
    raw = pathlib.Path(msg_path).read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise CodexSchemaError(str(e), raw[:500])
```

**규칙**:
- `--json`으로 이벤트 JSONL을 stdout으로 받아 debug 로그에 저장하되, 최종 응답은 **반드시 `-o` 파일에서** 읽는다 (stdout 파싱 금지)
- `--ephemeral`로 세션 파일 누적 방지
- `--sandbox read-only`로 LLM의 로컬 쓰기 차단
- `--skip-git-repo-check`로 파이프라인이 git 외부에서도 동작

## 4. 출력 스키마 강제

`src/pipeline/llm/schemas/` 하위에 content type별 JSON Schema 파일:

- `feed.json` — title(string, 12-40자), summary, tags(array), price_label, region_label, time_label, status(enum), supporter_label
- `detail.json` — description, target_audience, materials(array), host_intro, policy_notes
- `plan.json` — steps(array of {time, activity}, minItems 3)
- `messages.json` — 4종 snippet 필드
- `review.json` — rating(integer 1-5), review_text, tags, sentiment
- `critic.json` — naturalness_score, consistency_score, regional_fit_score, persona_fit_score, safety_score, reject(bool), reasons(array)

스키마 위반은 `codex exec`이 자체 reject하고 exit code ≠ 0 또는 JSON 파싱 실패로 이어진다. 이 경우 `retry.py`가 catch.

## 5. stub 모드 (CI / 단위 테스트)

`SCP_LLM_MODE=stub`일 때 `codex_client`는 **subprocess를 호출하지 않고** 고정 픽스처를 반환한다:

```python
if os.environ.get("SCP_LLM_MODE") == "stub":
    return _load_stub_response(template_id, variables)
```

- 픽스처 위치: `tests/fixtures/codex_stub/{template_id}/{hash(variables)[:8]}.json`
- pipeline-qa가 goldens에 대응하는 stub 응답을 수동 큐레이션
- 존재하지 않는 키는 **deterministic fallback** 픽스처 반환 (경고 로그)
- 이 모드로 전체 파이프라인이 오프라인에서도 end-to-end 테스트 가능

## 6. live 모드 (통합 테스트 / 실행)

`SCP_LLM_MODE=live`:

- 시작 시 `health.check_codex_login()` 1회 호출 → 실패 시 `sys.exit(2)`
- 첫 호출 전 **warmup 1회** (간단한 "ping" 프롬프트)를 돌려 세션 유효성 확인
- pytest에서는 `@pytest.mark.live_codex`로 분리. 기본 CI에서 skip
- nightly job에서만 전체 live smoke 실행

## 7. 동시성과 rate limit

- `asyncio.Semaphore(SCP_CODEX_CONCURRENCY)` 로 동시 subprocess 제한 (기본 2)
- 구독 한도는 명시되지 않으므로 **보수적으로 시작**
- stderr에서 다음 키워드 감지 시 `CodexRateLimitError`:
  - `rate limit`, `too many requests`, `quota`, `429`
- rate limit 감지 → 30초 backoff → 1회 재시도 → 재실패 시 상위(retry.py)로 에스컬레이트

## 8. 에러 분류

| 예외 | 상황 | 복구 전략 |
|------|------|-----------|
| `CodexLoginError` | health check 실패 / "not logged in" stderr | **fail fast** (`sys.exit(2)`). 파이프라인 정지 후 사용자 `codex login` 안내 |
| `CodexCallError(exit_code, stderr_tail)` | 일반 non-zero exit | 1회 재시도 → 실패 시 상위 |
| `CodexTimeoutError` | subprocess timeout | 1회 재시도 (프롬프트 변경 없이) |
| `CodexSchemaError` | JSON 파싱 실패 또는 스키마 불일치 | `retry.py`가 rejection feedback과 함께 재호출 |
| `CodexRateLimitError` | rate limit stderr 감지 | 30초 backoff + 1회 재시도 |

## 9. 캐시 전략

- 키: `sha256(template_id + template_version + json(variables))`
- 값: 응답 JSON + timestamp + 모델명
- 저장: `synthetic-content-pipeline/.cache/codex/` 하위 JSON 파일 또는 sqlite
- **hit이면 subprocess 호출 생략**. 테스트 반복 시 구독 비용 절약
- 프롬프트 버전이 올라가면 template_version이 바뀌므로 자연스럽게 무효화
- goldens 실행 결과는 별도 디렉토리에 보존 (§14 지표 재산출용)

## 10. 금지 사항

**파이프라인 어디에도 다음 패턴이 존재하면 CI 실패로 간주한다:**

- `import openai`, `import anthropic`, `from openai`, `from anthropic`
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` 환경변수 읽기
- `httpx.post("https://api.openai.com/...")`, `requests.post(...)` 등 직접 HTTP 호출
- `subprocess.run(["codex", ...])` 를 codex_client.py 외부에서 호출
- 프롬프트 문자열을 Python 코드에 하드코딩 (`config/prompts/` 밖)

pipeline-infra-architect의 `scripts/lint_no_api.py`가 위 패턴을 grep 기반으로 탐지한다.

## 11. 테스트 방법

### 단위 (stub)
```
SCP_LLM_MODE=stub pytest tests/ -m "not live_codex"
```

### 통합 smoke (live)
```
codex login status  # 사전 확인
SCP_LLM_MODE=live pytest tests/test_end_to_end_live.py -m live_codex
```

### 수동 1회 호출 확인
```
codex exec \
  --json \
  --skip-git-repo-check \
  --ephemeral \
  --sandbox read-only \
  -m gpt-5-codex \
  "You are a test. Respond with the single word: ok"
```

stdout에 이벤트 JSONL + 마지막에 "ok"가 나오면 세션 정상.

---

**요약**: 모든 LLM 호출은 `codex_client._invoke_codex()` 함수 단 하나를 거치고, 그 함수는 `codex exec`을 subprocess로 호출하며, `codex exec`은 사용자의 `codex login` 세션 = 구독을 사용한다. API 키는 어디에도 존재하지 않는다.
