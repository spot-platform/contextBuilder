"""Phase 4 — VersionManager smoke + 확장 테스트 (pipeline-qa scp_05_qa_phase4).

기존 스켈레톤 위에 QA 케이스를 얹은 확장 버전.
- 라이프사이클 (draft → active → deprecated → archived) 정상 흐름
- atomic switch 트랜잭션 격리 (예외 발생 시 rollback)
- compute_synthetic_ratio 임계 경계값 (9/10/29/30/49/50)
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import SQLAlchemyError

from pipeline.db.models import ContentVersionPolicy
from pipeline.publish.versioning import (
    TransitionStrategy,
    VersionManager,
    VersionStatus,
)


# ---------------------------------------------------------------------------
# 기본 스켈레톤 — 유지
# ---------------------------------------------------------------------------


def test_versioning_full_lifecycle(tmp_db):
    """draft → active → (새 버전 activate) → deprecated → archive."""
    vm = VersionManager(tmp_db)

    p1 = vm.create_draft("v1")
    assert p1.status == VersionStatus.DRAFT.value

    vm.activate("v1")
    active = vm.get_active()
    assert active is not None
    assert active.dataset_version == "v1"
    assert active.status == VersionStatus.ACTIVE.value

    vm.create_draft("v2")
    vm.activate("v2")
    statuses = {p.dataset_version: p.status for p in vm.list_versions()}
    assert statuses["v1"] == VersionStatus.DEPRECATED.value
    assert statuses["v2"] == VersionStatus.ACTIVE.value

    vm.archive("v1")
    statuses = {p.dataset_version: p.status for p in vm.list_versions()}
    assert statuses["v1"] == VersionStatus.ARCHIVED.value


def test_versioning_archive_expired_grace(tmp_db):
    """deprecation_date 가 grace_days 보다 오래 되었으면 archived."""
    vm = VersionManager(tmp_db)
    vm.create_draft("v_old")
    vm.activate("v_old")
    vm.create_draft("v_new")
    vm.activate("v_new")

    old = vm._fetch("v_old")
    assert old is not None
    old.deprecation_date = datetime.utcnow() - timedelta(days=31)
    tmp_db.flush()

    archived = vm.archive_expired(grace_days=30)
    assert "v_old" in archived


def test_versioning_enums_importable():
    assert VersionStatus.ACTIVE.value == "active"
    assert TransitionStrategy.IMMEDIATE.value == "immediate"


# ---------------------------------------------------------------------------
# 케이스 6: atomic switch 트랜잭션 격리
# ---------------------------------------------------------------------------


def test_versioning_atomic_switch_normal(tmp_db):
    """정상 flow: activate('v2') 단일 트랜잭션에서 v1 deprecated + v2 active.

    기대:
      - activate 호출 후 list_versions() 에서 v1=deprecated, v2=active
      - v1.replacement_version == "v2"
      - v1.deprecation_date 는 not None, v2.activation_date 는 not None
    """
    vm = VersionManager(tmp_db)
    vm.create_draft("v1")
    vm.activate("v1")
    vm.create_draft("v2")

    # 원자적 활성화
    vm.activate("v2")

    v1 = vm._fetch("v1")
    v2 = vm._fetch("v2")
    assert v1 is not None and v2 is not None
    assert v1.status == VersionStatus.DEPRECATED.value
    assert v2.status == VersionStatus.ACTIVE.value
    assert v1.replacement_version == "v2"
    assert v1.deprecation_date is not None
    assert v2.activation_date is not None


def test_versioning_atomic_switch_rollback_on_exception(tmp_db, monkeypatch):
    """activate 도중 session.flush 에서 예외가 나면 rollback 후 상태 복원.

    기대:
      - monkeypatch 로 첫 flush 호출 시 SQLAlchemyError 발생
      - activate('v2') 가 예외로 종료
      - rollback 후 v1.status == 'active', v2.status == 'draft' (전혀 안 바뀜)
    """
    vm = VersionManager(tmp_db)
    vm.create_draft("v1")
    vm.activate("v1")
    tmp_db.commit()  # 여기까지 확정

    vm.create_draft("v2")
    tmp_db.commit()

    v1_pre = vm._fetch("v1")
    v2_pre = vm._fetch("v2")
    assert v1_pre.status == VersionStatus.ACTIVE.value
    assert v2_pre.status == VersionStatus.DRAFT.value

    # flush 에서 예외 주입
    original_flush = tmp_db.flush
    call_count = {"n": 0}

    def _exploding_flush(*args, **kwargs):
        call_count["n"] += 1
        raise SQLAlchemyError("injected: atomic switch must rollback")

    monkeypatch.setattr(tmp_db, "flush", _exploding_flush)

    with pytest.raises(SQLAlchemyError):
        vm.activate("v2")

    # session 에 남은 변경을 rollback → 원래 committed 상태로 복귀
    monkeypatch.setattr(tmp_db, "flush", original_flush)
    tmp_db.rollback()

    v1_post = vm._fetch("v1")
    v2_post = vm._fetch("v2")
    assert v1_post.status == VersionStatus.ACTIVE.value, (
        f"rollback 후 v1 은 active 여야 한다, got {v1_post.status}"
    )
    assert v2_post.status == VersionStatus.DRAFT.value, (
        f"rollback 후 v2 는 draft 여야 한다, got {v2_post.status}"
    )
    assert call_count["n"] >= 1, "flush 는 최소 1번 호출되어야 한다"


# ---------------------------------------------------------------------------
# 케이스 8: compute_synthetic_ratio 임계 경계 (9/10/29/30/49/50)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "real_count,expected_ratio",
    [
        (9, 1.0),    # 임계 10 직전
        (10, 0.5),   # 임계 10 진입
        (29, 0.5),   # 임계 30 직전
        (30, 0.2),   # 임계 30 진입
        (49, 0.2),   # 임계 50 직전
        (50, 0.0),   # 임계 50 진입
    ],
)
def test_compute_synthetic_ratio_boundary(tmp_db, real_count, expected_ratio):
    """§9-2 임계 경계값 — 9/10/29/30/49/50 각각의 비중.

    기대:
      count < 10  → 1.0
      10 ≤ count < 30 → 0.5
      30 ≤ count < 50 → 0.2
      50 ≤ count      → 0.0
    """
    vm = VersionManager(tmp_db)
    assert vm.compute_synthetic_ratio(real_count) == expected_ratio


def test_compute_synthetic_ratio_mid_range(tmp_db):
    """기존 스켈레톤 스팟 체크 — 중간 값 5/15/35/55 도 동일 기준."""
    vm = VersionManager(tmp_db)
    assert vm.compute_synthetic_ratio(5) == 1.0
    assert vm.compute_synthetic_ratio(15) == 0.5
    assert vm.compute_synthetic_ratio(35) == 0.2
    assert vm.compute_synthetic_ratio(55) == 0.0
