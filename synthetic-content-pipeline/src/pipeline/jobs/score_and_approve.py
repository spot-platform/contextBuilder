"""Job 9 — quality_score 산정 + 승인 진입점 (validator-engineer Phase 3).

CLI (batch):
    score-and-approve \
        --candidates candidates.jsonl \
        --critic critic.jsonl \
        --decisions decisions.jsonl

입력 파일:
- ``candidates.jsonl``: {spot_id, content_type, payload, layer123: {ok, warnings}, diversity_score}
- ``critic.jsonl``: {spot_id, content_type, ... CriticResult.to_dict() 필드}

출력 파일 (``decisions.jsonl``):
- {spot_id, content_type, payload, quality_score, classification, critic_used}
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Mapping, Optional

import click

from pipeline.validators.critic import CriticResult
from pipeline.validators.scoring import compute_quality_score


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def _critic_key(row: Mapping[str, Any]) -> tuple:
    return (row.get("spot_id"), row.get("content_type"))


def _row_to_critic(row: Mapping[str, Any]) -> Optional[CriticResult]:
    if not row:
        return None
    try:
        return CriticResult(
            naturalness_score=float(row.get("naturalness_score", 0.85)),
            consistency_score=float(row.get("consistency_score", 0.85)),
            regional_fit_score=float(row.get("regional_fit_score", 0.85)),
            persona_fit_score=float(row.get("persona_fit_score", 0.85)),
            safety_score=float(row.get("safety_score", 0.95)),
            reject=bool(row.get("reject", False)),
            reasons=list(row.get("reasons") or []),
            sampled=bool(row.get("sampled", True)),
            sample_reason=str(row.get("sample_reason", "")),
            fallback=bool(row.get("fallback", False)),
        )
    except (TypeError, ValueError):
        return None


def _layer_from_row(row: Mapping[str, Any]) -> SimpleNamespace:
    layer123 = row.get("layer123") or {}
    ok = bool(layer123.get("ok", True))
    warnings = list(layer123.get("warnings") or [])
    return SimpleNamespace(
        ok=ok,
        warnings=warnings,
        rejections=[],
        meta=layer123.get("meta", {}),
    )


@click.command("score-and-approve")
@click.option(
    "--candidates",
    "candidates_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--critic",
    "critic_path",
    required=False,
    type=click.Path(path_type=Path),
    default=None,
)
@click.option(
    "--decisions",
    "decisions_path",
    required=True,
    type=click.Path(path_type=Path),
)
def score_and_approve_command(
    candidates_path: Path,
    critic_path: Optional[Path],
    decisions_path: Path,
) -> None:
    """후보 콘텐츠에 quality_score 산정 후 decision 을 쓴다 (§5 Layer 6)."""
    critic_map: Dict[tuple, CriticResult] = {}
    if critic_path and critic_path.exists():
        for row in _iter_jsonl(critic_path):
            cr = _row_to_critic(row)
            if cr is not None:
                critic_map[_critic_key(row)] = cr

    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with decisions_path.open("w", encoding="utf-8") as out:
        for cand in _iter_jsonl(candidates_path):
            key = _critic_key(cand)
            critic = critic_map.get(key)
            layer = _layer_from_row(cand)
            diversity_score = float(cand.get("diversity_score", 0.85))
            score, breakdown = compute_quality_score(critic, layer, diversity_score)
            decision = {
                "spot_id": cand.get("spot_id"),
                "content_type": cand.get("content_type"),
                "payload": cand.get("payload"),
                "quality_score": score,
                "classification": breakdown["classification"],
                "critic_used": breakdown["critic_used"],
                "breakdown": breakdown["components"],
            }
            out.write(json.dumps(decision, ensure_ascii=False) + "\n")
            written += 1
    click.echo(f"score_and_approve: wrote {written} decisions → {decisions_path}")


if __name__ == "__main__":  # pragma: no cover
    score_and_approve_command()
