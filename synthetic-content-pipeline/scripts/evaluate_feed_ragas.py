"""Build and run a RAGAS comparison dataset for long Direct LLM vs contextBuilder.

Examples
--------
# 1) Build a deterministic stub dataset (no external LLM call)
python scripts/evaluate_feed_ragas.py build --out _workspace/ragas/feed_eval.jsonl

# 2) Build with live Codex generation
SCP_LLM_MODE=live python scripts/evaluate_feed_ragas.py build --out _workspace/ragas/feed_eval.jsonl

# 2b) Build 100 live benchmark cases
python scripts/build_eval_specs.py --n 100 --out data/eval/feed_specs_100.json
SCP_LLM_MODE=live python scripts/evaluate_feed_ragas.py build \
  --spec-json data/eval/feed_specs_100.json \
  --out _workspace/ragas/feed_eval_100.jsonl

# 3) Run RAGAS (requires evaluator deps + LLM credentials supported by RAGAS)
python scripts/evaluate_feed_ragas.py ragas \
  --provider anthropic \
  --model claude-3-5-haiku-latest \
  --input _workspace/ragas/feed_eval.jsonl \
  --out _workspace/ragas/feed_eval_report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pipeline.evaluation.ragas_feed import (  # noqa: E402
    evaluate_jsonl_with_ragas,
    make_ragas_record,
    write_jsonl,
)
from pipeline.evaluation.style_metrics import write_style_report  # noqa: E402
from pipeline.generators.base import sample_length_bucket  # noqa: E402
from pipeline.generators.feed import FeedGenerator  # noqa: E402
from pipeline.llm.codex_client import call_codex  # noqa: E402
from pipeline.spec.models import ContentSpec  # noqa: E402

# Reuse the existing POI-anchored demo as the first benchmark case. This keeps
# the evaluation script aligned with the current pipeline without requiring a DB.
sys.path.insert(0, str(ROOT / "scripts"))
from demo_poi_anchored_generate import build_demo_spec  # type: ignore  # noqa: E402


def _direct_feed_variables(case_index: int) -> dict[str, Any]:
    return {
        "region": "수원시",
        "case_index": case_index,
        "batch_size": 100,
    }


def _load_specs(spec_json: Path | None) -> list[ContentSpec]:
    if spec_json is None:
        return [build_demo_spec()]
    raw = json.loads(spec_json.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError("--spec-json must contain one ContentSpec object or a list of objects")
    return [ContentSpec.model_validate(item) for item in raw]


def build_dataset(out: Path, spec_json: Path | None = None) -> None:
    specs = _load_specs(spec_json)
    rows: list[dict[str, Any]] = []
    feed_gen = FeedGenerator()

    for idx, spec in enumerate(specs, 1):
        case_id = f"{idx:03d}_{spec.spot_id}"

        generation_mode = os.environ.get("SCP_LLM_MODE", "stub")
        if generation_mode.lower() == "live":
            proposed_candidates = feed_gen.generate(spec)
            proposed_candidate = next(
                (c for c in proposed_candidates if c.variant == "primary"),
                proposed_candidates[0],
            )
            proposed_payload = proposed_candidate.payload
            proposed_meta = {
                "generation_mode": generation_mode,
                "template_id": proposed_candidate.template_id,
                "variant": proposed_candidate.variant,
                "stub": proposed_candidate.meta.get("stub", False),
            }
        else:
            # Fixture fallback is intentionally generic and may not match the demo
            # spec. For a useful local smoke dataset, use the deterministic
            # placeholder rendered from the current ContentSpec.
            length_bucket = sample_length_bucket(spec.spot_id, "primary")
            variables = feed_gen.spec_to_variables(
                spec,
                variant="primary",
                length_bucket=length_bucket,
            )
            proposed_payload = feed_gen._placeholder_payload(variables)
            proposed_meta = {
                "generation_mode": generation_mode,
                "template_id": feed_gen.template_id,
                "variant": "primary",
                "stub": True,
            }

        rows.append(
            make_ragas_record(
                case_id=case_id,
                system="context_builder",
                spec=spec,
                payload=proposed_payload,
                meta=proposed_meta,
            )
        )

        try:
            direct_payload = call_codex(
                template_id="direct_feed:v1",
                variables=_direct_feed_variables(idx),
                schema_path=FeedGenerator.schema_path,
            )
        except Exception as exc:  # noqa: BLE001
            if generation_mode.lower() == "live":
                raise RuntimeError(
                    "Direct LLM baseline generation failed. "
                    "Live mode requires the Codex CLI in PATH. "
                    "Install it with `npm install -g @openai/codex` or rerun build without "
                    "`SCP_LLM_MODE=live` for stub smoke testing."
                ) from exc
            raise
        rows.append(
            make_ragas_record(
                case_id=case_id,
                system="direct_llm",
                spec=spec,
                payload=direct_payload,
                meta={
                    "generation_mode": os.environ.get("SCP_LLM_MODE", "stub"),
                    "template_id": "direct_feed:v1",
                    "baseline": "city_only_long_direct_llm",
                    "input_scope": "region_and_schema_only",
                    "case_index": idx,
                    "length_bucket": sample_length_bucket(spec.spot_id, "direct"),
                },
            )
        )

    write_jsonl(out, rows)
    print(f"wrote {len(rows)} rows → {out}")
    print("systems:", ", ".join(sorted({r["system"] for r in rows})))


def run_ragas(
    input_path: Path,
    out: Path,
    metrics: list[str],
    provider: str,
    model: str | None,
) -> None:
    if not input_path.exists():
        raise FileNotFoundError(
            f"RAGAS input dataset not found: {input_path}\n"
            "Run the build step first, for example:\n"
            "  uv run --extra dev python scripts/evaluate_feed_ragas.py build "
            "--out _workspace/ragas/feed_eval.jsonl"
        )
    report = evaluate_jsonl_with_ragas(
        input_path,
        out,
        metrics=metrics,
        provider=provider,
        model=model,
    )
    print(f"wrote RAGAS report → {out}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


def run_style(input_path: Path, out: Path) -> None:
    if not input_path.exists():
        raise FileNotFoundError(
            f"style metric input dataset not found: {input_path}\n"
            "Run the build step first."
        )
    report = write_style_report(input_path, out)
    print(f"wrote style metrics report → {out}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="generate long Direct LLM vs contextBuilder JSONL")
    p_build.add_argument("--out", type=Path, default=ROOT / "_workspace" / "ragas" / "feed_eval.jsonl")
    p_build.add_argument("--spec-json", type=Path, help="optional ContentSpec JSON object/list")

    p_ragas = sub.add_parser("ragas", help="run RAGAS over a prepared JSONL")
    p_ragas.add_argument("--input", type=Path, default=ROOT / "_workspace" / "ragas" / "feed_eval.jsonl")
    p_ragas.add_argument("--out", type=Path, default=ROOT / "_workspace" / "ragas" / "feed_eval_report.json")
    p_ragas.add_argument("--provider", choices=["openai", "anthropic"], default="openai")
    p_ragas.add_argument("--model", help="evaluator model, e.g. claude-3-5-haiku-latest")
    p_ragas.add_argument(
        "--metrics",
        nargs="+",
        default=["faithfulness", "context_precision_without_reference"],
        choices=[
            "faithfulness",
            "answer_relevancy",
            "context_precision_without_reference",
            "llm_context_precision_without_reference",
        ],
    )

    p_style = sub.add_parser("style", help="run deterministic style/template metrics over JSONL")
    p_style.add_argument("--input", type=Path, default=ROOT / "_workspace" / "ragas" / "feed_eval.jsonl")
    p_style.add_argument("--out", type=Path, default=ROOT / "_workspace" / "ragas" / "feed_style_report.json")

    args = parser.parse_args()
    if args.cmd == "build":
        build_dataset(args.out, spec_json=args.spec_json)
    elif args.cmd == "ragas":
        run_ragas(
            args.input,
            args.out,
            metrics=args.metrics,
            provider=args.provider,
            model=args.model,
        )
    elif args.cmd == "style":
        run_style(args.input, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
