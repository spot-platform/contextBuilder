"""Codex CLI 브리지 — 파이프라인의 유일한 LLM 호출 진입점.

이 모듈만 `codex exec` subprocess를 호출할 수 있다. 다른 모듈은
import 해서 사용해야 한다 (lint_no_api.py 가 추후 검사).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess  # ALLOW_CODEX_SUBPROCESS — codex_client 만 호출 허용
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from pipeline.llm import cache as cache_mod
from pipeline.llm import prompt_loader
from pipeline.llm.errors import (
    CodexCallError,
    CodexRateLimitError,
    CodexSchemaError,
    CodexTimeoutError,
)

log = logging.getLogger(__name__)

_RATE_LIMIT_KEYWORDS = ("rate limit", "too many requests", "quota", "429")

# 기본 모델을 빈 문자열로 둔다 — codex CLI 가 사용자 구독에서
# 기본 할당된 모델(예: ChatGPT Plus 구독자용)을 자동 선택하도록 맡긴다.
# `gpt-5-codex` 등 고정 모델명은 구독 유형에 따라 400 에러가 날 수 있다.
# 명시적으로 다른 모델이 필요하면 SCP_CODEX_MODEL_GEN / -m 로 주입.
_DEFAULT_MODEL = ""
_DEFAULT_TIMEOUT = 90
_DEFAULT_CONCURRENCY = 2

_concurrency_sem: threading.BoundedSemaphore | None = None
_concurrency_lock = threading.Lock()


def _get_semaphore() -> threading.BoundedSemaphore:
    global _concurrency_sem
    with _concurrency_lock:
        if _concurrency_sem is None:
            n = int(os.environ.get("SCP_CODEX_CONCURRENCY", str(_DEFAULT_CONCURRENCY)))
            n = max(1, n)
            _concurrency_sem = threading.BoundedSemaphore(n)
    return _concurrency_sem


def _resolve_model(model: Optional[str]) -> str:
    """모델 해상도. 빈 문자열이면 codex CLI default 사용 (-m 생략)."""
    if model:
        return model
    env = os.environ.get("SCP_CODEX_MODEL_GEN", _DEFAULT_MODEL)
    return env or ""


def _resolve_timeout() -> int:
    return int(os.environ.get("SCP_CODEX_TIMEOUT", str(_DEFAULT_TIMEOUT)))


def _is_stub_mode() -> bool:
    return os.environ.get("SCP_LLM_MODE", "stub").lower() == "stub"


# ---------------------------------------------------------------------------
# stub mode
# ---------------------------------------------------------------------------

# tests/fixtures/codex_stub/{template_id_dir}/v{n}/{key[:8]}.json
# 파일이 없으면 default.json fallback.
_STUB_ROOT = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "codex_stub"


def _load_stub_response(template_id: str, variables: Mapping[str, Any]) -> dict:
    dir_name, version = prompt_loader.parse_template_id(template_id)
    stub_dir = _STUB_ROOT / dir_name / f"v{version}"
    key = cache_mod.make_key(template_id, version, variables)
    candidate = stub_dir / f"{key[:8]}.json"
    if candidate.exists():
        log.debug("stub hit: %s", candidate)
        return json.loads(candidate.read_text(encoding="utf-8"))
    fallback = stub_dir / "default.json"
    if fallback.exists():
        log.warning(
            "stub fallback: no fixture for key %s, using default.json (template=%s)",
            key[:8],
            template_id,
        )
        return json.loads(fallback.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        f"no stub fixture for {template_id} (looked for {candidate} and {fallback})"
    )


# ---------------------------------------------------------------------------
# live mode — codex exec subprocess
# ---------------------------------------------------------------------------


def _detect_rate_limit(stderr: str) -> bool:
    low = stderr.lower()
    return any(kw in low for kw in _RATE_LIMIT_KEYWORDS)


def _invoke_codex(prompt: str, schema_path: Path, model: str) -> dict:
    """`codex exec` subprocess 호출. 유일한 진입점.

    스키마 강제 출력은 `--output-schema` 로, 최종 메시지 파일은 `-o` 로 받는다.
    """
    if not schema_path.exists():
        raise CodexCallError(-1, f"schema file not found: {schema_path}")

    msg_fd, msg_path = tempfile.mkstemp(suffix=".json", prefix="codex_msg_")
    os.close(msg_fd)
    try:
        cmd = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--output-schema",
            str(schema_path),
            "-o",
            msg_path,
        ]
        # model 이 비어있으면 -m 을 생략하고 codex CLI default 에 맡긴다.
        # ChatGPT 구독에서는 gpt-5-codex 같은 특정 모델명이 거부될 수 있다.
        if model:
            cmd.extend(["-m", model])
        cmd.append(prompt)
        timeout = _resolve_timeout()
        sem = _get_semaphore()
        with sem:
            try:
                proc = subprocess.run(  # ALLOW_CODEX_SUBPROCESS
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    stdin=subprocess.DEVNULL,
                )
            except FileNotFoundError as e:
                raise CodexCallError(-1, f"codex CLI not found: {e}") from e
            except subprocess.TimeoutExpired as e:
                raise CodexTimeoutError(timeout) from e

        if proc.returncode != 0:
            tail = "\n".join(proc.stderr.strip().splitlines()[-30:])
            if _detect_rate_limit(proc.stderr):
                raise CodexRateLimitError(tail)
            raise CodexCallError(proc.returncode, tail)

        try:
            raw = Path(msg_path).read_text(encoding="utf-8")
        except OSError as e:
            raise CodexCallError(-1, f"failed to read codex output file: {e}") from e

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise CodexSchemaError(str(e), raw[:500]) from e
    finally:
        try:
            os.unlink(msg_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def call_codex(
    template_id: str,
    variables: Mapping[str, Any],
    schema_path: Path,
    model: Optional[str] = None,
    previous_rejections: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict:
    """파이프라인 전역에서 사용하는 LLM 호출 함수.

    Parameters
    ----------
    template_id : str
        ``"feed:v1"`` 형식. ``parse_template_id`` 참고
    variables : Mapping[str, Any]
        프롬프트 컨텍스트. ``prompt_contract.md`` 의 공용 변수 표준을 따른다
    schema_path : Path
        codex `--output-schema` 로 강제할 JSON Schema 파일 경로
    model : str | None
        None 이면 env ``SCP_CODEX_MODEL_GEN`` 또는 기본값 ``gpt-5-codex``
    previous_rejections : Sequence[Mapping] | None
        rejection feedback 루프에서 전달되는 직전 거절 사유 목록.
        템플릿 안에서 ``{% if previous_rejections %}`` 블록으로 참조

    Returns
    -------
    dict
        codex 응답 JSON (스키마 통과를 가정. 파싱 실패는 ``CodexSchemaError``)
    """
    _, version = prompt_loader.parse_template_id(template_id)
    resolved_model = _resolve_model(model)

    # stub 모드: subprocess 건너뛰고 픽스처 반환. 프롬프트 렌더도 생략 (CI 결정성).
    if _is_stub_mode():
        return _load_stub_response(template_id, variables)

    # live 모드 캐시 lookup
    cache_key = cache_mod.make_key(template_id, version, variables)
    cached = cache_mod.lookup(cache_key)
    if cached is not None:
        log.debug("cache hit: %s", cache_key[:12])
        return cached

    # 프롬프트 렌더
    prompt = prompt_loader.render(template_id, variables, previous_rejections=previous_rejections)

    # codex exec 호출
    response = _invoke_codex(prompt, schema_path, resolved_model)

    # 캐시 저장
    cache_mod.store(cache_key, response, model=resolved_model, template_version=version)
    return response
