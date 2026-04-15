"""test_end_to_end_phase1 — goldens × FeedGenerator × schema × rule (stub 모드).

성공 기준:
- 모든 candidate 가 schema Layer 1 PASS.
- Layer 2 rule 은 goldens 의 expected 파일 기대치를 따른다:
  - expected["layer2_rule_pass"] == True  → rule PASS 여야 함.
  - expected["layer2_rule_pass"] == False → rule reject 발생이 예상값이며
    xfail 로 분류 (경계면 버그가 아님: stub default.json 의 지역/카테고리 편향 때문).
- 결과 메트릭은 data/goldens/_results/phase1_e2e.jsonl 에 기록.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import pytest

from pipeline.generators.feed import FeedGenerator
from pipeline.spec.models import ContentSpec
from pipeline.validators.rules import validate_feed_rules
from pipeline.validators.schema import validate_feed_schema


def _load_spec(path: Path) -> ContentSpec:
    with path.open("r", encoding="utf-8") as fh:
        return ContentSpec.model_validate(json.load(fh))


def _load_expected(goldens_dir: Path, spec_name: str) -> Dict:
    exp_path = goldens_dir / "expected" / spec_name
    if not exp_path.exists():
        return {"layer1_schema_pass": True, "layer2_rule_pass": True}
    with exp_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def results_log_path(goldens_dir) -> Path:
    p = goldens_dir / "_results" / "phase1_e2e.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    # 초기화
    p.write_text("", encoding="utf-8")
    return p


def test_goldens_exist(goldens_specs):
    assert len(goldens_specs) >= 5, f"goldens specs 최소 5개 필요 (found {len(goldens_specs)})"


@pytest.mark.parametrize(
    "spec_filename",
    [
        "golden_food_yeonmu_evening.json",
        "golden_food_jangan_weekday.json",
        "golden_cafe_sinchon_weekend.json",
        "golden_exercise_park_morning.json",
        "golden_culture_downtown_evening.json",
        "golden_edge_tiny_group.json",
        "golden_edge_tight_budget.json",
    ],
)
def test_e2e_golden_pair(spec_filename, goldens_dir, feed_schema_path, results_log_path):
    spec_path = goldens_dir / "specs" / spec_filename
    assert spec_path.exists(), f"golden spec missing: {spec_path}"
    spec = _load_spec(spec_path)
    expected = _load_expected(goldens_dir, spec_filename)

    gen = FeedGenerator()
    candidates = gen.generate(spec)
    assert len(candidates) == 2

    layer1_ok_both = True
    layer2_reasons: List[str] = []
    for c in candidates:
        s_res = validate_feed_schema(c.payload, feed_schema_path)
        r_res = validate_feed_rules(c.payload, spec)
        row = {
            "spec": spec_filename,
            "variant": c.variant,
            "schema_ok": s_res.ok,
            "schema_rejections": [r.reason for r in s_res.rejections],
            "rule_ok": r_res.ok,
            "rule_rejections": [r.reason for r in r_res.rejections],
            "length_bucket": c.meta.get("length_bucket"),
        }
        with results_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

        # Layer 1 schema 는 모두 PASS 가 성공 기준.
        assert s_res.ok, f"schema FAIL for {spec_filename}/{c.variant}: {row['schema_rejections']}"

        if not r_res.ok:
            layer2_reasons.extend([r.reason for r in r_res.hard_rejections])

    rule_should_pass = expected.get("layer2_rule_pass", True)
    if rule_should_pass:
        assert not layer2_reasons, (
            f"rule FAIL unexpected for {spec_filename}: {layer2_reasons}"
        )
    else:
        # expected 에 적힌 경계면 mismatch — xfail 처리
        pytest.xfail(
            f"expected rule mismatch for {spec_filename}: "
            f"{expected.get('layer2_expected_rule_mismatch')} "
            f"observed: {layer2_reasons}"
        )
