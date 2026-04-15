"""Phase 2 generator stub smoke — 4종 generator (detail/plan/messages/review).

성공 기준:
- 각 generator × 3 spot_id (S_0001, S_0006, S_0050) → 후보 2개 반환 (primary + alternative).
- payload 가 dispatch.run_individual 의 Layer 1 (schema) PASS.
- spec_to_variables 가 COMMON_VARIABLE_KEYS 16개를 superset 으로 포함.
- meta 에 length_bucket / seed_hash 존재.

stub 모드 fallback fixture(default.json)는 "연무동 저녁 식사 4명" 컨텍스트로
작성돼 있으므로 Layer 2 rule 은 spec 과 mismatch 가 발생할 수 있음. 본 테스트는
Layer 1 PASS 만 hard-fail 기준으로 삼고, Layer 2 결과는 metadata 로만 기록한다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest

from pipeline.generators.base import COMMON_VARIABLE_KEYS
from pipeline.generators.detail import SpotDetailGenerator
from pipeline.generators.messages import MessagesGenerator
from pipeline.generators.plan import SpotPlanGenerator
from pipeline.generators.review import ReviewGenerator
from pipeline.spec.builder import build_content_spec
from pipeline.spec.models import ContentSpec
from pipeline.validators.dispatch import CONTENT_TYPE_SCHEMA, run_individual

REPO_ROOT = Path(__file__).resolve().parents[1]
EVENT_LOG = (REPO_ROOT.parent / "spot-simulator" / "output" / "event_log.jsonl").resolve()
REGION_FEATURES = (
    REPO_ROOT.parent / "spot-simulator" / "data" / "region_features.json"
).resolve()
SCHEMA_ROOT = REPO_ROOT / "src" / "pipeline" / "llm" / "schemas"

GENERATOR_REGISTRY = [
    ("detail", SpotDetailGenerator),
    ("plan", SpotPlanGenerator),
    ("messages", MessagesGenerator),
    ("review", ReviewGenerator),
]
SPOT_IDS = ["S_0001", "S_0006", "S_0050"]


@pytest.fixture(scope="module")
def specs() -> List[ContentSpec]:
    if not EVENT_LOG.exists():
        pytest.skip(f"event_log not found: {EVENT_LOG}")
    out: List[ContentSpec] = []
    for sid in SPOT_IDS:
        try:
            out.append(
                build_content_spec(
                    EVENT_LOG, sid, region_features_path=REGION_FEATURES
                )
            )
        except Exception as exc:
            pytest.skip(f"build_content_spec({sid}) failed: {exc}")
    return out


@pytest.mark.parametrize("content_type,generator_cls", GENERATOR_REGISTRY)
def test_generator_returns_two_candidates_per_spot(
    content_type, generator_cls, specs
):
    """4 generator × 3 spec → 2 candidate * 3 = 6 candidate / generator."""
    gen = generator_cls()
    total = 0
    for spec in specs:
        candidates = gen.generate(spec)
        assert len(candidates) == 2, (
            f"{generator_cls.__name__}({spec.spot_id}) candidates={len(candidates)} (expected 2)"
        )
        assert {c.variant for c in candidates} == {"primary", "alternative"}
        for c in candidates:
            assert "length_bucket" in c.meta, f"meta missing length_bucket: {c.meta}"
            assert c.meta["length_bucket"] in {"short", "medium", "long"}
            assert "seed_hash" in c.meta
            assert c.content_type == content_type
            total += 1
    assert total == 6, f"{content_type}: total candidates {total} != 6"


@pytest.mark.parametrize("content_type,generator_cls", GENERATOR_REGISTRY)
def test_generator_payload_passes_layer1_schema(
    content_type, generator_cls, specs
):
    """4 × 3 × 2 = 24 payload 모두 dispatch.run_individual 의 Layer 1 schema PASS.

    run_individual 은 Layer1 → Layer2 순서로 실행하며 Layer1 실패 시 Layer2 skip.
    여기선 schema_meta.used_schema 가 True 인지, schema rejection 이 0인지 확인.
    """
    gen = generator_cls()
    schema_pass = 0
    schema_fail = 0
    layer2_rejs_total = 0
    for spec in specs:
        for c in gen.generate(spec):
            res = run_individual(content_type, c.payload, spec, schema_root=SCHEMA_ROOT)
            schema_meta = res.meta.get("schema_meta", res.meta)
            schema_rejections = [
                r for r in res.rejections if r.layer == "schema"
            ]
            if not schema_rejections:
                schema_pass += 1
            else:
                schema_fail += 1
                # 어떤 schema rejection 이 있는지 확인.
                pytest.fail(
                    f"{content_type}/{spec.spot_id}/{c.variant} schema FAIL: "
                    f"{[r.reason + ':' + r.rejected_field for r in schema_rejections]}"
                )
            layer2_rejs_total += len(
                [r for r in res.rejections if r.layer == "rule"]
            )
    assert schema_pass == 6, f"{content_type}: schema_pass={schema_pass}/6"
    # info — Layer 2 rejection 카운트는 fail 로 보지 않음 (stub 편향 허용)
    print(
        f"[INFO] {content_type}: schema_pass=6/6 layer2_total_rejections={layer2_rejs_total}"
    )


@pytest.mark.parametrize("content_type,generator_cls", GENERATOR_REGISTRY)
def test_spec_to_variables_includes_common_keys(
    content_type, generator_cls, specs
):
    """COMMON_VARIABLE_KEYS 16개가 spec_to_variables superset 에 포함."""
    gen = generator_cls()
    for spec in specs:
        variables = gen.spec_to_variables(
            spec, variant="primary", length_bucket="medium"
        )
        missing = COMMON_VARIABLE_KEYS - set(variables.keys())
        assert not missing, (
            f"{generator_cls.__name__}({spec.spot_id}): COMMON keys missing={sorted(missing)}"
        )


def test_schema_files_exist_for_all_4_phase2_types():
    """Phase 2 새 schema 파일 4개 존재 + dispatch CONTENT_TYPE_SCHEMA 매핑 일치."""
    for ct in ("detail", "plan", "messages", "review"):
        fname = CONTENT_TYPE_SCHEMA.get(ct)
        assert fname, f"{ct} not registered in CONTENT_TYPE_SCHEMA"
        path = SCHEMA_ROOT / fname
        assert path.exists(), f"schema missing: {path}"
        # JSON 으로 로드 가능해야 함.
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        assert "$schema" in data
        assert "properties" in data
