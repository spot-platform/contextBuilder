"""Job 8 — LLM critic 샘플링 평가 진입점 (validator-engineer Phase 3).

Usage (CLI):
    evaluate-critic --content-type feed \
        --payload '{"title":"...","summary":"..."}' \
        --spec '{"spot_id":"S_0001",...}' \
        --sample-reason random_10pct

stdout 으로 ``CriticResult.to_dict()`` 를 JSON 으로 출력한다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import click

from pipeline.spec.models import ContentSpec
from pipeline.validators.critic import evaluate_critic


def _load_json_arg(value: str) -> Dict[str, Any]:
    """``@path/to/file.json`` 이면 파일 로드, 아니면 inline JSON 파싱."""
    if not value:
        return {}
    if value.startswith("@"):
        return json.loads(Path(value[1:]).read_text(encoding="utf-8"))
    return json.loads(value)


@click.command("evaluate-critic")
@click.option(
    "--content-type",
    "content_type",
    required=True,
    type=click.Choice(["feed", "detail", "plan", "messages", "review"]),
)
@click.option(
    "--payload",
    "payload_arg",
    required=True,
    help="콘텐츠 payload JSON (inline 또는 @path).",
)
@click.option(
    "--spec",
    "spec_arg",
    required=True,
    help="ContentSpec JSON (inline 또는 @path).",
)
@click.option(
    "--sample-reason",
    "sample_reason",
    default="random_10pct",
    show_default=True,
)
@click.option(
    "--eval-focus",
    "eval_focus",
    default=None,
)
def evaluate_critic_command(
    content_type: str,
    payload_arg: str,
    spec_arg: str,
    sample_reason: str,
    eval_focus: Optional[str],
) -> None:
    """샘플링된 콘텐츠에 대한 critic 평가 (§5 Layer 4)."""
    payload = _load_json_arg(payload_arg)
    spec_dict = _load_json_arg(spec_arg)
    spec = ContentSpec.model_validate(spec_dict)

    result = evaluate_critic(
        spec.spot_id,
        content_type,
        payload,
        spec,
        sample_reason=sample_reason,
        eval_focus=eval_focus,
    )
    click.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover
    evaluate_critic_command()
