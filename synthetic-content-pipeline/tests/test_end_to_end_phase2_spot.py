"""Phase 2 end-to-end — 단일 스팟 5 content full pipeline (stub 모드).

흐름:
    1. golden spec 로드 (golden_food_yeonmu_evening — stub default fixture 와 정합)
    2. 5 generator 순차 실행 (feed, detail, plan, messages, review) — stub 모드
    3. 후보 2개 중 primary 선택
    4. dispatch.run_individual × 5 (Layer 1 + Layer 2)
    5. dispatch.run_cross_reference (Layer 3)

성공 기준:
    - Layer 1 schema PASS 5/5
    - Layer 2 rule PASS 5/5 (혹은 정당한 stub 편향 reject 만)
    - Layer 3 cross-ref ok=True (혹은 정당한 reject 만)

결과는 data/goldens/_results/phase2_e2e.jsonl 에 한 줄씩 기록.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from pipeline.generators.detail import SpotDetailGenerator
from pipeline.generators.feed import FeedGenerator
from pipeline.generators.messages import MessagesGenerator
from pipeline.generators.plan import SpotPlanGenerator
from pipeline.generators.review import ReviewGenerator
from pipeline.spec.models import ContentSpec
from pipeline.validators.dispatch import (
    CONTENT_TYPE_SCHEMA,
    run_cross_reference,
    run_individual,
)

REPO = Path(__file__).resolve().parents[1]
SPECS_DIR = REPO / "data" / "goldens" / "specs"
SCHEMA_ROOT = REPO / "src" / "pipeline" / "llm" / "schemas"
RESULTS_PATH = REPO / "data" / "goldens" / "_results" / "phase2_e2e.jsonl"

GENERATOR_REGISTRY = [
    ("feed", FeedGenerator),
    ("detail", SpotDetailGenerator),
    ("plan", SpotPlanGenerator),
    ("messages", MessagesGenerator),
    ("review", ReviewGenerator),
]


def _load_spec(path: Path) -> ContentSpec:
    return ContentSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture(scope="module", autouse=True)
def _reset_results():
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text("", encoding="utf-8")
    yield


def _append_jsonl(row: Dict[str, Any]) -> None:
    with RESULTS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


@pytest.fixture(scope="module")
def yeonmu_spec() -> ContentSpec:
    spec_path = SPECS_DIR / "golden_food_yeonmu_evening.json"
    if not spec_path.exists():
        pytest.skip(f"golden spec missing: {spec_path}")
    return _load_spec(spec_path)


def test_e2e_phase2_single_spot_full_pipeline(yeonmu_spec):
    """5 content × full Layer 1+2+3 pipeline."""
    bundle: Dict[str, Dict[str, Any]] = {}
    individual_results: Dict[str, Dict[str, Any]] = {}

    layer1_pass_count = 0
    layer2_pass_count = 0
    layer1_fail: List[str] = []
    layer2_fail: List[str] = []

    for content_type, gen_cls in GENERATOR_REGISTRY:
        gen = gen_cls()
        candidates = gen.generate(yeonmu_spec)
        assert len(candidates) == 2, (
            f"{content_type} generator returned {len(candidates)} candidates"
        )
        primary = candidates[0]
        assert primary.variant == "primary"
        bundle[content_type] = primary.payload

        res = run_individual(
            content_type, primary.payload, yeonmu_spec, schema_root=SCHEMA_ROOT
        )
        # schema 단계는 res.meta["schema_meta"] 에 분리 기록되며, rule 단계 rejection 은
        # res.rejections 에 함께 들어 있다.
        schema_rejections = [r for r in res.rejections if r.layer == "schema"]
        rule_rejections = [r for r in res.rejections if r.layer == "rule"]

        if not schema_rejections:
            layer1_pass_count += 1
        else:
            layer1_fail.extend(
                f"{content_type}:{r.reason}:{r.rejected_field}" for r in schema_rejections
            )
        # rule rejections 중 hard reject 만 fail 로 카운트 (warn 은 통과).
        rule_hard = [r for r in rule_rejections if r.severity == "reject"]
        if not rule_hard:
            layer2_pass_count += 1
        else:
            layer2_fail.extend(
                f"{content_type}:{r.reason}:{r.rejected_field}" for r in rule_hard
            )
        individual_results[content_type] = {
            "schema_ok": not schema_rejections,
            "rule_ok": not rule_hard,
            "schema_rejections": [r.reason for r in schema_rejections],
            "rule_rejections": [r.reason for r in rule_hard],
            "warnings": [r.reason for r in rule_rejections if r.severity == "warn"],
        }

    cross_res = run_cross_reference(bundle, yeonmu_spec)
    cross_row = {
        "step": "cross_reference",
        "ok": cross_res.ok,
        "executed_pairs": cross_res.meta.get("executed_pairs"),
        "skipped_pairs": cross_res.meta.get("skipped_pairs"),
        "rejections": [
            {
                "field": r.rejected_field,
                "reason": r.reason,
                "severity": r.severity,
            }
            for r in cross_res.rejections
        ],
    }

    e2e_row = {
        "spec": "golden_food_yeonmu_evening.json",
        "spot_id": yeonmu_spec.spot_id,
        "individual": individual_results,
        "layer1_pass": layer1_pass_count,
        "layer2_pass": layer2_pass_count,
        "cross_reference": cross_row,
    }
    _append_jsonl(e2e_row)

    # Hard 성공 기준:
    # - Layer 1: 5/5 PASS (schema 는 stub fixture 가 반드시 schema 통과해야 함).
    assert layer1_pass_count == 5, (
        f"Layer 1 schema PASS={layer1_pass_count}/5; failures={layer1_fail}"
    )
    # - Layer 2 / Layer 3 는 stub fixture 와 spec 이 거의 동일 (yeonmu/food/19:00/18000) 이므로
    #   반드시 통과해야 한다. 통과하지 못하면 정당한 사유와 함께 xfail 로 분리.
    assert layer2_pass_count == 5, (
        f"Layer 2 rule PASS={layer2_pass_count}/5; failures={layer2_fail}"
    )
    assert cross_res.ok, (
        f"Layer 3 cross-ref FAIL: "
        f"{[(r.rejected_field, r.reason) for r in cross_res.hard_rejections]}"
    )


def test_results_file_has_one_row(yeonmu_spec):
    """e2e 테스트 직후 결과 파일이 1행을 포함해야 한다."""
    # 위 테스트가 먼저 실행되어야 결과가 채워진다 — pytest 는 정의 순서대로 실행.
    if not RESULTS_PATH.exists():
        pytest.skip("results file missing")
    rows = [
        line for line in RESULTS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(rows) == 1
    parsed = json.loads(rows[0])
    assert parsed["layer1_pass"] == 5
