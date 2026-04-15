"""tests/conftest.py — pipeline-qa 공용 fixtures.

모든 유닛 테스트는 기본적으로 ``SCP_LLM_MODE=stub`` 에서 실행된다 (autouse).
``live_codex`` 마커가 붙은 테스트만 이 autouse 를 무시하고 실제 환경을 건드린다.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Iterable, List

import pytest

# ---------------------------------------------------------------------------
# src/ 경로 주입 (pip install -e . 없이도 import 되도록)
# ---------------------------------------------------------------------------

_PIPELINE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_SRC))

# ---------------------------------------------------------------------------
# 공용 경로
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SIM_OUTPUT = (_REPO_ROOT.parent / "spot-simulator" / "output" / "event_log.jsonl").resolve()
_REGION_FEATURES = (_REPO_ROOT.parent / "spot-simulator" / "data" / "region_features.json").resolve()
_FEED_SCHEMA = _REPO_ROOT / "src" / "pipeline" / "llm" / "schemas" / "feed.json"
_GOLDENS_DIR = _REPO_ROOT / "data" / "goldens"


# ---------------------------------------------------------------------------
# autouse stub env
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def stub_env(request, monkeypatch):
    """``SCP_LLM_MODE=stub`` 강제 — ``live_codex`` 마커 테스트는 제외."""
    if "live_codex" in request.keywords:
        return
    monkeypatch.setenv("SCP_LLM_MODE", "stub")


# ---------------------------------------------------------------------------
# 경로 fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO_ROOT


@pytest.fixture(scope="session")
def event_log_path() -> Path:
    """spot-simulator/output/event_log.jsonl."""
    return _SIM_OUTPUT


@pytest.fixture(scope="session")
def region_features_path() -> Path:
    return _REGION_FEATURES


@pytest.fixture(scope="session")
def feed_schema_path() -> Path:
    """src/pipeline/llm/schemas/feed.json."""
    return _FEED_SCHEMA


@pytest.fixture(scope="session")
def goldens_dir() -> Path:
    return _GOLDENS_DIR


# ---------------------------------------------------------------------------
# sample spot ids fixture — 실존 CREATE_SPOT 이벤트 기반
# ---------------------------------------------------------------------------


def _scan_create_spot_ids(event_log: Path, n: int = 10) -> List[str]:
    """CREATE_SPOT (Phase 1 legacy) 또는 CREATE_TEACH_SPOT (Phase Peer-D) 스캔.

    Phase Peer-D 이후 기본 event_log 에는 CREATE_SPOT 이 없고 CREATE_TEACH_SPOT
    만 있다. 두 이벤트 타입 모두를 허용해 fixture 회귀를 막는다.
    """
    if not event_log.exists():
        return []
    ids: List[str] = []
    accept_types = {"CREATE_SPOT", "CREATE_TEACH_SPOT"}
    with event_log.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("event_type") in accept_types and evt.get("spot_id"):
                ids.append(evt["spot_id"])
                if len(ids) >= n:
                    break
    return ids


@pytest.fixture(scope="session")
def sample_spot_ids(event_log_path) -> List[str]:
    """event_log 에서 앞에서부터 CREATE_SPOT 이벤트 10개 spot_id."""
    ids = _scan_create_spot_ids(event_log_path, 10)
    if not ids:
        pytest.skip(f"event_log not found or empty: {event_log_path}")
    return ids


# ---------------------------------------------------------------------------
# in-memory sqlite session fixture (content_validation_log 테스트용)
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db():
    """in-memory sqlite + 모든 테이블 생성. caller 가 context 로 사용."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from pipeline.db.base import Base
    from pipeline.db import models  # noqa: F401 — metadata 로드

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# golden spec loader helper
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def goldens_specs(goldens_dir) -> List[Path]:
    """data/goldens/specs/*.json sorted."""
    specs_dir = goldens_dir / "specs"
    if not specs_dir.exists():
        return []
    return sorted(specs_dir.glob("*.json"))
