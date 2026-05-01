"""RAGAS dataset helpers for Direct LLM vs contextBuilder feed evaluation.

The goal is paper-friendly evaluation, not product scoring. We treat the
intermediate artifacts produced by contextBuilder (local attributes,
simulation-derived constraints, curated POI/plan candidates) as RAG contexts
and compare whether generated feed cards are grounded in and relevant to the
same user scenario.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

from pipeline.spec.models import ContentSpec

SystemName = Literal["direct_llm", "context_builder"]


def feed_payload_to_answer(payload: Mapping[str, Any]) -> str:
    """Flatten a feed JSON payload into the answer text evaluated by RAGAS."""
    parts: list[str] = []
    for key in ("title", "summary", "price_label", "region_label", "time_label"):
        value = payload.get(key)
        if value:
            parts.append(f"{key}: {value}")
    tags = payload.get("tags")
    if isinstance(tags, list) and tags:
        parts.append("tags: " + ", ".join(str(t) for t in tags))
    supporter = payload.get("supporter_label")
    if supporter:
        parts.append(f"supporter_label: {supporter}")
    if not parts:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return "\n".join(parts)


def spec_to_question(spec: ContentSpec) -> str:
    """Build the user scenario / question column for RAGAS."""
    skill = spec.skill_topic or spec.category
    return (
        "다음 사용자 시나리오에 맞는 로컬 커뮤니티 앱 피드 카드를 생성하라. "
        f"지역={spec.region}, 스킬={skill}, 참가자={spec.participants.expected_count}명, "
        f"일정={spec.schedule.date} {spec.schedule.start_time}, "
        f"예상 참가비={spec.budget.expected_cost_per_person}원."
    )


def spec_to_contexts(spec: ContentSpec) -> list[str]:
    """Map contextBuilder intermediate artifacts to RAGAS retrieval contexts.

    These chunks are used for both systems during evaluation; only the
    generation process differs.
    """
    skill = spec.skill_topic or spec.category
    contexts: list[str] = [
        (
            "local_and_activity_attributes: "
            f"region={spec.region}; category={spec.category}; skill_topic={skill}; "
            f"venue_type={spec.venue_type}; teach_mode={spec.teach_mode}; "
            f"beginner_friendly={spec.activity_constraints.beginner_friendly}; "
            f"indoor={spec.activity_constraints.indoor}."
        ),
        (
            "simulation_constraints: "
            f"expected_participants={spec.participants.expected_count}; "
            f"schedule={spec.schedule.date} {spec.schedule.start_time}; "
            f"duration_minutes={spec.schedule.duration_minutes}; "
            f"price_band={spec.budget.price_band}; "
            f"expected_cost_per_person={spec.budget.expected_cost_per_person}; "
            f"plan_outline={' -> '.join(spec.plan_outline)}."
        ),
        (
            "host_persona_and_taste: "
            f"host_type={spec.host_persona.type}; tone={spec.host_persona.tone}; "
            f"communication_style={spec.host_persona.communication_style}; "
            f"taste_facets={', '.join(spec.taste_facets)}; "
            f"recent_obsession={spec.recent_obsession}; "
            f"curiosity_hooks={', '.join(spec.curiosity_hooks)}."
        ),
    ]

    if spec.venue_anchors:
        contexts.append(
            "curated_poi_candidates: "
            + "; ".join(
                f"{p.role}={p.name}({p.primary_category}, {p.address})"
                for p in spec.venue_anchors
            )
        )
    if spec.plan_steps:
        contexts.append(
            "curated_plan_steps: "
            + " -> ".join(
                f"{s.time} {s.place.name if s.place else '장소 미정'}에서 {s.activity}({s.intent})"
                for s in spec.plan_steps
            )
        )
    if spec.price_breakdown:
        contexts.append(
            "price_context: "
            + json.dumps(spec.price_breakdown.model_dump(), ensure_ascii=False, sort_keys=True)
        )
    if spec.preparation:
        contexts.append(
            "preparation_context: "
            + json.dumps(spec.preparation.model_dump(), ensure_ascii=False, sort_keys=True)
        )
    return contexts


def make_ragas_record(
    *,
    case_id: str,
    system: SystemName,
    spec: ContentSpec,
    payload: Mapping[str, Any],
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one JSONL row compatible with RAGAS evaluate()."""
    return {
        "case_id": case_id,
        "system": system,
        "question": spec_to_question(spec),
        "answer": feed_payload_to_answer(payload),
        "contexts": spec_to_contexts(spec),
        "metadata": {
            "spot_id": spec.spot_id,
            "region": spec.region,
            "skill_topic": spec.skill_topic or spec.category,
            **dict(meta or {}),
        },
    }


def write_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    rows: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row.get("contexts"), list):
                raise ValueError(f"{p}:{line_no} contexts must be a list[str]")
            rows.append(row)
    return rows


def summarize_by_system(rows: Sequence[Mapping[str, Any]], score_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate RAGAS result rows by system for a compact paper table."""
    buckets: dict[str, dict[str, list[float]]] = {}
    for original, scored in zip(rows, score_rows):
        system = str(original.get("system"))
        bucket = buckets.setdefault(system, {})
        for key, value in scored.items():
            if key in {"question", "answer", "contexts", "metadata", "case_id", "system"}:
                continue
            if isinstance(value, (int, float)):
                bucket.setdefault(key, []).append(float(value))

    summary: dict[str, Any] = {}
    for system, metrics in buckets.items():
        summary[system] = {
            metric: {
                "mean": round(sum(values) / len(values), 4),
                "n": len(values),
            }
            for metric, values in metrics.items()
            if values
        }
    return summary


def evaluate_jsonl_with_ragas(
    input_path: str | Path,
    output_path: str | Path,
    *,
    metrics: Sequence[str] = ("faithfulness", "context_precision_without_reference"),
    provider: str = "openai",
    model: str | None = None,
) -> dict[str, Any]:
    """Run RAGAS over a prepared JSONL file and save row scores + summary.

    This function imports RAGAS lazily so the normal pipeline/test install does
    not require evaluator dependencies. Install with `pip install -e .[eval]`.
    """
    rows = read_jsonl(input_path)
    try:
        from datasets import Dataset  # type: ignore
        from ragas import evaluate  # type: ignore
        from ragas.metrics import (  # type: ignore
            LLMContextPrecisionWithoutReference,
            answer_relevancy,
            faithfulness,
        )
    except ImportError as exc:  # pragma: no cover - dependency-gated path
        raise RuntimeError(
            "RAGAS evaluation dependencies are missing. Install with: "
            "pip install -e .[eval]"
        ) from exc

    metric_map = {
        "faithfulness": faithfulness,
        "answer_relevancy": answer_relevancy,
        # RAGAS >=0.4 `context_precision` requires a human/reference answer.
        # This paper setup is reference-free, so use the official no-reference
        # variant and expose it under an explicit CLI name.
        "context_precision_without_reference": LLMContextPrecisionWithoutReference(),
        "llm_context_precision_without_reference": LLMContextPrecisionWithoutReference(),
    }
    selected = [metric_map[name] for name in metrics]
    evaluator_llm = None
    normalized_provider = provider.lower()
    if normalized_provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic  # type: ignore
        except ImportError as exc:  # pragma: no cover - dependency-gated path
            raise RuntimeError(
                "Anthropic evaluator dependencies are missing. Install with: "
                "pip install -e .[eval]"
            ) from exc
        evaluator_llm = ChatAnthropic(
            model=model or "claude-3-5-haiku-latest",
            temperature=0,
            timeout=60,
            max_retries=2,
        )
    elif normalized_provider == "openai":
        if model:
            try:
                from langchain_openai import ChatOpenAI  # type: ignore
            except ImportError as exc:  # pragma: no cover - dependency-gated path
                raise RuntimeError(
                    "OpenAI evaluator dependencies are missing. Install with: "
                    "pip install -e .[eval]"
                ) from exc
            evaluator_llm = ChatOpenAI(model=model, temperature=0, timeout=60, max_retries=2)
        else:
            evaluator_llm = None  # RAGAS default OpenAI evaluator.
    else:
        raise ValueError(f"unsupported provider: {provider!r}; expected openai|anthropic")
    dataset = Dataset.from_list(
        [
            {
                # RAGAS 0.4 uses user_input/response/retrieved_contexts.
                # Older question/answer/contexts names are kept in our JSONL
                # for readability, but evaluation receives the current schema.
                "user_input": row["question"],
                "response": row["answer"],
                "retrieved_contexts": row["contexts"],
            }
            for row in rows
        ]
    )
    result = evaluate(
        dataset,
        metrics=selected,
        llm=evaluator_llm,
        raise_exceptions=True,
    )
    scored_rows = result.to_pandas().to_dict(orient="records")

    enriched = []
    for original, scored in zip(rows, scored_rows):
        enriched.append({**original, "scores": scored})

    summary = summarize_by_system(rows, scored_rows)
    report = {
        "input": str(input_path),
        "metrics": list(metrics),
        "provider": normalized_provider,
        "model": model,
        "summary": summary,
        "rows": enriched,
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
