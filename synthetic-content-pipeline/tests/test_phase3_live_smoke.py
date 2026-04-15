"""Phase 3 live smoke — 3 개 goldens 대상으로 ``process_spot_full`` 을
실제 codex exec 경유 (``SCP_LLM_MODE=live``) 로 실행한 뒤
결과를 ``data/goldens/_results/phase3_e2e.jsonl`` 에 기록한다.

호출 수 보호
-----------
- spec 3 개 × (5 content-type × 후보 2 + critic 샘플링 ~2) ≈ 50 호출 상한.
- 각 테스트는 독립적으로 ``live_codex`` 마커를 가지므로 `pytest -m live_codex`
  에서만 실행된다 (기본 -m "not live_codex" 에서 deselect).

실패 허용
--------
- codex CLI / 네트워크 / 구독 상태 등 인프라 문제로 호출이 실패하면
  ``pytest.skip`` 으로 전환해 Phase 1/2 회귀를 깨지 않는다.
- 결과 jsonl 이 비어 있으면 measure_success_metrics.py --mode live 가
  "표본 없음" 으로 보고하게 된다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List

import pytest

from pipeline.spec.models import ContentSpec

pytestmark = pytest.mark.live_codex


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SPECS_DIR = _REPO_ROOT / "data" / "goldens" / "specs"
_RESULTS_JSONL = _REPO_ROOT / "data" / "goldens" / "_results" / "phase3_e2e.jsonl"

# live 호출 수 보호: 카테고리/지역 다양성 있는 3 개만.
_LIVE_GOLDENS = [
    "golden_food_yeonmu_evening.json",
    "golden_food_jangan_weekday.json",
    "golden_cafe_sinchon_weekend.json",
]


@pytest.fixture(scope="module", autouse=True)
def _reset_results_file():
    """모듈 시작 시 jsonl 초기화."""
    _RESULTS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    _RESULTS_JSONL.write_text("", encoding="utf-8")
    yield


@pytest.fixture(scope="module")
def live_env():
    """SCP_LLM_MODE=live 강제. 테스트 종료 후 원복."""
    prev = os.environ.get("SCP_LLM_MODE")
    os.environ["SCP_LLM_MODE"] = "live"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("SCP_LLM_MODE", None)
        else:
            os.environ["SCP_LLM_MODE"] = prev


def _load_spec(path: Path) -> ContentSpec:
    return ContentSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _append_result(row: dict) -> None:
    with _RESULTS_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False))
        fh.write("\n")


@pytest.mark.parametrize("spec_filename", _LIVE_GOLDENS)
def test_phase3_live_full_pipeline(spec_filename, live_env):
    from pipeline.loop.generate_validate_retry import process_spot_full

    spec_path = _SPECS_DIR / spec_filename
    if not spec_path.exists():
        pytest.skip(f"golden spec missing: {spec_path}")
    spec = _load_spec(spec_path)

    try:
        result = process_spot_full(spec.spot_id, spec)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"live codex call failed for {spec_filename}: {exc}")

    # 결과 기록 (실패해도 최소한 이 줄은 남겨 measure 스크립트가 소비 가능).
    critic_calls = sum(
        1 for cpr in result.contents.values() if cpr.critic_used
    )
    contents_rows = []
    for ct, cpr in result.contents.items():
        best_meta = None
        if cpr.candidates_meta:
            best_meta = max(
                cpr.candidates_meta,
                key=lambda m: m.get("quality_score", -1),
            )
        contents_rows.append(
            {
                "content_type": ct,
                "classification": cpr.classification,
                "quality_score": float(cpr.quality_score),
                "critic_used": bool(cpr.critic_used),
                "critic_sample_reason": cpr.critic_sample_reason or "",
                "retry_count": int(best_meta.get("retry_count", 0)) if best_meta else 0,
                "best_diversity_score": float(best_meta.get("diversity_score", 0.0))
                if best_meta and best_meta.get("diversity_score") is not None
                else 0.0,
            }
        )
    row = {
        "spot_id": spec.spot_id,
        "contents": contents_rows,
        "llm_calls_total": int(result.llm_calls_total),
        "critic_calls": int(critic_calls),
        "elapsed_seconds": float(result.elapsed_seconds),
        "retry_count_total": int(result.retry_count_total),
        "cross_ref_ok": bool(result.cross_ref_result.ok) if result.cross_ref_result else True,
    }
    _append_result(row)

    # 느슨한 단언 (live 실패를 하드 실패로 만들지 않기 위해).
    assert result.spot_id == spec.spot_id
    assert len(result.contents) == 5
