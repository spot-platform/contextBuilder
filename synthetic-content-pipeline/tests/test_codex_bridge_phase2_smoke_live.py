"""Phase 2 live smoke — **실제** codex exec 호출을 정확히 2회만.

호출 정책 (구독 보호):
    - feed:v1 1회 + detail:v1 1회 = 총 2 codex exec.
    - 절대 generate() 사용 금지 (primary+alternative 로 2배가 됨).
    - call_codex 를 직접 호출.

기본 실행에서는 `live_codex` 마커로 자동 skip 된다.
실행:
    PYTHONPATH=src SCP_LLM_MODE=live \\
        python3 -m pytest -m live_codex tests/test_codex_bridge_phase2_smoke_live.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pipeline.generators.detail import SpotDetailGenerator
from pipeline.generators.feed import FeedGenerator
from pipeline.spec.models import ContentSpec
from pipeline.validators.dispatch import run_individual

REPO = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = REPO / "src" / "pipeline" / "llm" / "schemas"

pytestmark = pytest.mark.live_codex


def _load_spec(path: Path) -> ContentSpec:
    return ContentSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def spec() -> ContentSpec:
    spec_path = REPO / "data" / "goldens" / "specs" / "golden_food_yeonmu_evening.json"
    return _load_spec(spec_path)


def test_live_feed_one_call(spec):
    """feed:v1 codex exec 1회. schema PASS + title 12~40자 확인."""
    os.environ["SCP_LLM_MODE"] = "live"
    gen = FeedGenerator()
    variables = gen.spec_to_variables(spec, variant="primary", length_bucket="short")

    from pipeline.llm.codex_client import call_codex

    payload = call_codex(
        template_id=gen.template_id,
        variables=variables,
        schema_path=gen.schema_path,
    )
    assert isinstance(payload, dict), "codex returned non-dict"
    assert not payload.get("_stub"), "live 모드인데 stub 응답 — bridge 확인 필요"

    title = payload.get("title", "")
    assert isinstance(title, str)
    assert 12 <= len(title) <= 40, f"feed.title 길이 위반: {len(title)}"

    # Layer 1 schema 도 호출해서 종합 확인.
    res = run_individual("feed", payload, spec, schema_root=SCHEMA_ROOT)
    schema_rejs = [r for r in res.rejections if r.layer == "schema"]
    assert not schema_rejs, (
        f"feed schema FAIL: {[(r.reason, r.rejected_field) for r in schema_rejs]}"
    )


def test_live_detail_one_call(spec):
    """detail:v1 codex exec 1회. schema PASS + food deny_keywords 미포함."""
    os.environ["SCP_LLM_MODE"] = "live"
    gen = SpotDetailGenerator()
    variables = gen.spec_to_variables(spec, variant="primary", length_bucket="medium")

    from pipeline.llm.codex_client import call_codex

    payload = call_codex(
        template_id=gen.template_id,
        variables=variables,
        schema_path=gen.schema_path,
    )
    assert isinstance(payload, dict)
    assert not payload.get("_stub"), "live 모드인데 stub 응답 — bridge 확인 필요"

    # Layer 1 schema check.
    res = run_individual("detail", payload, spec, schema_root=SCHEMA_ROOT)
    schema_rejs = [r for r in res.rejections if r.layer == "schema"]
    assert not schema_rejs, (
        f"detail schema FAIL: {[(r.reason, r.rejected_field) for r in schema_rejs]}"
    )

    # food deny_keywords 미포함 확인.
    from pipeline.validators.detail_rules import load_detail_rules

    rules = load_detail_rules()
    deny = (rules.get("categories", {}).get("food", {}) or {}).get("deny_keywords") or []
    blob_parts = []
    for k in (
        "title",
        "description",
        "activity_purpose",
        "progress_style",
        "host_intro",
    ):
        v = payload.get(k)
        if isinstance(v, str):
            blob_parts.append(v)
    blob = " ".join(blob_parts)
    hits = [kw for kw in deny if kw and kw in blob]
    assert not hits, f"detail 본문에 food deny_keywords 포함: {hits}"
