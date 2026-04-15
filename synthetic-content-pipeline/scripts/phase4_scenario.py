"""phase4_scenario.py — §9 FSM 전체 전환 시나리오 end-to-end.

시나리오 (stub 모드, in-memory sqlite):
    1. v1 draft → active
    2. 첫 번째 스팟 publish (v1)
    3. v2 draft 생성
    4. v2 activate → v1 자동 deprecated
    5. 두 번째 스팟 publish (v2)
    6. archive_expired(grace_days=0) → v1 archived
    7. 최종 상태 JSON 출력

실행:
    PYTHONPATH=src python3 scripts/phase4_scenario.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# 경로 주입 (PYTHONPATH 없이도 실행되도록)
_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

os.environ.setdefault("SCP_LLM_MODE", "stub")

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from pipeline.db.base import Base
from pipeline.db import models  # noqa: F401 — metadata register
from pipeline.db.models import (
    ContentVersionPolicy,
    SyntheticFeedContent,
    SyntheticReview,
    SyntheticSpotDetail,
    SyntheticSpotMessages,
)
from pipeline.publish.publisher import Publisher, PublishResult
from pipeline.publish.versioning import VersionManager, VersionStatus
from pipeline.spec.models import ContentSpec
from pipeline.loop.generate_validate_retry import process_spot_full, SpotProcessResult


_SPEC_PATH = _REPO / "data" / "goldens" / "specs" / "golden_cafe_sinchon_weekend.json"


def _history_snapshot(session) -> list[Dict[str, Any]]:
    rows = session.execute(
        select(ContentVersionPolicy).order_by(ContentVersionPolicy.created_at.asc())
    ).scalars().all()
    return [
        {
            "dataset_version": r.dataset_version,
            "status": r.status,
            "activation_date": r.activation_date.isoformat() if r.activation_date else None,
            "deprecation_date": r.deprecation_date.isoformat() if r.deprecation_date else None,
            "replacement_version": r.replacement_version,
            "transition_strategy": r.transition_strategy,
        }
        for r in rows
    ]


def _row_counts(session) -> Dict[str, Dict[str, int]]:
    """dataset_version 별 synthetic_* row count."""
    out: Dict[str, Dict[str, int]] = {}
    for model, key in (
        (SyntheticFeedContent, "feed"),
        (SyntheticSpotDetail, "detail"),
        (SyntheticSpotMessages, "messages"),
        (SyntheticReview, "review"),
    ):
        rows = session.execute(
            select(model.dataset_version, func.count()).group_by(model.dataset_version)
        ).all()
        for dv, cnt in rows:
            out.setdefault(dv, {"feed": 0, "detail": 0, "messages": 0, "review": 0})
            out[dv][key] = int(cnt)
    return out


def run_scenario() -> Dict[str, Any]:
    """§9 전환 시나리오 실행 후 결과 JSON 반환."""
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    spec_data = json.loads(_SPEC_PATH.read_text(encoding="utf-8"))
    spec = ContentSpec.model_validate(spec_data)

    # stub 모드로 SpotProcessResult 1회만 생성 (속도)
    spot_result: SpotProcessResult = process_spot_full("spot_phase4", spec)

    timeline: list[Dict[str, Any]] = []
    publish_results: list[Dict[str, Any]] = []

    session = SessionLocal()
    try:
        vm = VersionManager(session)

        # 1. v1 draft → active
        vm.create_draft("v1", transition_strategy="immediate")
        vm.activate("v1")
        session.commit()
        timeline.append({"step": "v1_activated", "history": _history_snapshot(session)})

        # 2. 첫 번째 spot publish (v1)
        spot_result.spot_id = "spot_v1_first"
        publisher_v1 = Publisher(session)  # active=v1 자동 선택
        result1 = publisher_v1.publish_spot(spot_result)
        session.commit()
        publish_results.append(result1.to_dict())

        # 3. v2 draft
        vm.create_draft("v2", transition_strategy="immediate")
        session.commit()
        timeline.append({"step": "v2_drafted", "history": _history_snapshot(session)})

        # 4. v2 activate → v1 자동 deprecated
        vm.activate("v2")
        session.commit()
        timeline.append({"step": "v2_activated", "history": _history_snapshot(session)})

        # 5. 두 번째 spot publish (v2)
        spot_result.spot_id = "spot_v2_second"
        publisher_v2 = Publisher(session)  # active=v2 자동
        result2 = publisher_v2.publish_spot(spot_result)
        session.commit()
        publish_results.append(result2.to_dict())

        # 6. archive_expired(grace_days=0) → v1 archived
        archived = vm.archive_expired(grace_days=0)
        session.commit()
        timeline.append(
            {
                "step": "archive_expired",
                "archived": archived,
                "history": _history_snapshot(session),
            }
        )

        # 7. 최종 row 카운트
        row_counts = _row_counts(session)
        final_history = _history_snapshot(session)
    finally:
        session.close()
        engine.dispose()

    return {
        "scenario": "phase4_full_transition",
        "spot_result_spot_id": "spot_phase4 (reused via spot_id overwrite)",
        "timeline": timeline,
        "publish_results": publish_results,
        "row_counts_by_version": row_counts,
        "final_history": final_history,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def main() -> int:
    out = run_scenario()
    print(json.dumps(out, ensure_ascii=False, indent=2))

    # 빠른 sanity assertion (스크립트가 기대대로 동작했는지)
    versions = {v["dataset_version"]: v["status"] for v in out["final_history"]}
    assert versions.get("v1") == VersionStatus.ARCHIVED.value, (
        f"v1 expected archived, got {versions.get('v1')}"
    )
    assert versions.get("v2") == VersionStatus.ACTIVE.value, (
        f"v2 expected active, got {versions.get('v2')}"
    )
    # v1 / v2 둘 다에 publish row 가 기록되어 있어야 한다.
    assert "v1" in out["row_counts_by_version"] or "v_init" in out["row_counts_by_version"], (
        f"v1 rows missing: {out['row_counts_by_version']}"
    )
    assert "v2" in out["row_counts_by_version"], (
        f"v2 rows missing: {out['row_counts_by_version']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
