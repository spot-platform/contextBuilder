"""Jinja2 프롬프트 템플릿 로더 — config/prompts/{dir}/v{n}.j2 렌더링.

브리지는 렌더 경로만 책임진다. 프롬프트 본문은
content-generator-engineer / validator-engineer가 작성한다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from jinja2 import Environment, FileSystemLoader, StrictUndefined

# config/prompts/ 루트.
# 이 파일 위치: src/pipeline/llm/prompt_loader.py → parents[3] = synthetic-content-pipeline/
_PROMPTS_ROOT = Path(__file__).resolve().parents[3] / "config" / "prompts"

_TEMPLATE_ID_RE = re.compile(r"^(?P<dir>[a-z0-9_\-]+):v(?P<version>\d+)$")
_VERSION_FILENAME_RE = re.compile(r"^v(?P<version>\d+)\.j2$")

_env: Environment | None = None


def _get_env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(_PROMPTS_ROOT)),
            autoescape=False,
            keep_trailing_newline=True,
            undefined=StrictUndefined,
            trim_blocks=False,
            lstrip_blocks=False,
        )
    return _env


def parse_template_id(template_id: str) -> tuple[str, int]:
    """`feed:v1` → (`feed`, 1)."""
    m = _TEMPLATE_ID_RE.match(template_id)
    if not m:
        raise ValueError(
            f"invalid template_id {template_id!r}; expected '<dir>:v<n>' (e.g. 'feed:v1')"
        )
    return m.group("dir"), int(m.group("version"))


def get_latest_version(template_id_dir: str) -> int:
    """`config/prompts/<dir>/` 내 v{n}.j2 중 최대 n. 없으면 ValueError."""
    base = _PROMPTS_ROOT / template_id_dir
    if not base.is_dir():
        raise ValueError(f"prompt directory not found: {base}")
    versions: list[int] = []
    for entry in base.iterdir():
        if not entry.is_file():
            continue
        m = _VERSION_FILENAME_RE.match(entry.name)
        if m:
            versions.append(int(m.group("version")))
    if not versions:
        raise ValueError(f"no v<n>.j2 templates under {base}")
    return max(versions)


def template_path(template_id: str) -> Path:
    """`feed:v2` → `config/prompts/feed/v2.j2` 절대경로."""
    dir_name, version = parse_template_id(template_id)
    return _PROMPTS_ROOT / dir_name / f"v{version}.j2"


def render(
    template_id: str,
    variables: Mapping[str, Any],
    previous_rejections: Optional[Sequence[Mapping[str, Any]]] = None,
) -> str:
    """템플릿을 렌더한다.

    - `previous_rejections` 는 템플릿 내 `{% if previous_rejections %}` 블록에서 참조 가능
    - StrictUndefined 사용 → 누락 변수는 즉시 UndefinedError
    """
    dir_name, version = parse_template_id(template_id)
    rel_path = f"{dir_name}/v{version}.j2"
    env = _get_env()
    template = env.get_template(rel_path)
    context = dict(variables)
    context["previous_rejections"] = list(previous_rejections) if previous_rejections else []
    return template.render(**context)
