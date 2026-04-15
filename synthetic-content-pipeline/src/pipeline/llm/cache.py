"""Codex 응답 파일 캐시 — sha256(template_id+version+variables) → JSON 응답."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

# 캐시 루트는 패키지 최상위(synthetic-content-pipeline/.cache/codex/) 로 고정.
# 이 파일 위치: src/pipeline/llm/cache.py → parents[3] = synthetic-content-pipeline/
_CACHE_ROOT = Path(__file__).resolve().parents[3] / ".cache" / "codex"


def _is_cache_disabled() -> bool:
    return os.environ.get("SCP_LLM_CACHE", "on").lower() == "off"


def _canonical_variables(variables: Mapping[str, Any]) -> str:
    """deterministic JSON 직렬화 — key 정렬 + UTF-8."""
    return json.dumps(variables, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def make_key(template_id: str, template_version: int, variables: Mapping[str, Any]) -> str:
    """캐시 키 계산. 프롬프트 버전이 바뀌면 자연스럽게 무효화된다."""
    payload = f"{template_id}|v{template_version}|{_canonical_variables(variables)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _path_for(key: str) -> Path:
    return _CACHE_ROOT / key[:2] / f"{key}.json"


def lookup(key: str) -> Optional[dict]:
    """캐시 hit이면 응답 dict 반환, 아니면 None.

    SCP_LLM_CACHE=off 면 항상 None.
    """
    if _is_cache_disabled():
        return None
    path = _path_for(key)
    if not path.exists():
        return None
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        return envelope.get("response")
    except (OSError, json.JSONDecodeError):
        return None


def store(
    key: str,
    response: dict,
    model: str,
    template_version: int,
) -> None:
    """응답을 envelope에 감싸 디스크에 저장."""
    if _is_cache_disabled():
        return
    path = _path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "response": response,
        "timestamp": _dt.datetime.utcnow().isoformat() + "Z",
        "model": model,
        "template_version": template_version,
    }
    path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
