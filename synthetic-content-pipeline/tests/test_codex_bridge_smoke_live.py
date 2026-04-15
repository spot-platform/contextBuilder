"""Live smoke — **실제** codex exec 호출 1회만.

- 기본 실행에서는 `-m "not live_codex"` 로 skip 된다.
- nightly / phase gate 에서만 명시적으로:
    pytest -m live_codex tests/test_codex_bridge_smoke_live.py --maxfail=1
  로 호출한다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pipeline.generators.feed import FeedGenerator
from pipeline.spec.models import ContentSpec


pytestmark = pytest.mark.live_codex


def _load_spec(path: Path) -> ContentSpec:
    with path.open("r", encoding="utf-8") as fh:
        return ContentSpec.model_validate(json.load(fh))


def test_live_feed_one_shot(goldens_dir, feed_schema_path):
    """연무동 golden 1건만 live 호출. title 12~40자 + schema 통과 확인.

    ChatGPT 구독 보호를 위해 `generate()` (2회 호출) 대신 ``call_codex`` 를 직접
    1회만 호출한다.
    """
    os.environ["SCP_LLM_MODE"] = "live"
    spec_path = goldens_dir / "specs" / "golden_food_yeonmu_evening.json"
    spec = _load_spec(spec_path)

    gen = FeedGenerator()
    variables = gen.spec_to_variables(spec, variant="primary", length_bucket="short")

    from pipeline.llm.codex_client import call_codex

    payload = call_codex(
        template_id=gen.template_id,
        variables=variables,
        schema_path=gen.schema_path,
    )
    assert isinstance(payload, dict)
    # _stub 플래그가 있으면 실패 (live 모드 확인)
    assert not payload.get("_stub"), "SCP_LLM_MODE=live 였는데 stub 경로 타짐 — bridge 확인 필요"

    title = payload.get("title", "")
    assert isinstance(title, str)
    assert 12 <= len(title) <= 40, f"title 길이 위반: {len(title)}"

    # schema 통과 확인
    from pipeline.validators.schema import validate_feed_schema

    res = validate_feed_schema(payload, feed_schema_path)
    assert res.ok, f"schema FAIL: {[r.reason for r in res.rejections]}"
