"""versioning — content_version_policy CRUD + §9 전환 트리거.

Phase 4 본 구현 (pipeline-infra-architect, scp_01_infra_phase4_complete).

상태 머신 (FSM):

        create_draft()          activate()              deprecate() / activate(other)
    ─────────────────►  draft  ─────────────►  active  ─────────────────────────────►  deprecated
                                                                                              │
                                                                                              │ archive() / archive_expired()
                                                                                              ▼
                                                                                          archived

규칙:
    - 동시에 두 버전이 active 가 될 수 없다 (atomic switch).
    - active → deprecated 만 가능. draft → deprecated 는 금지.
    - deprecated → archived 는 deprecation_date + grace_days 경과 후만.
    - archived 로 한 번 가면 다시 살릴 수 없다.

§9-2 비중 트리거: real_spot_count 가 임계 (10/30/50) 를 넘을 때 synthetic 비중 산정.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.db.models import ContentVersionPolicy

log = logging.getLogger(__name__)


# Python 3.11 호환 — StrEnum 은 3.11 에 추가됨. 안전하게 str + Enum 사용.
class VersionStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class TransitionStrategy(str, Enum):
    IMMEDIATE = "immediate"
    GRADUAL = "gradual"
    AB_TEST = "ab_test"


# §9-2 임계 (real_spot_count → synthetic 비중)
_THRESHOLDS = (
    (50, 0.0),  # >= 50 → synthetic 제거
    (30, 0.2),
    (10, 0.5),
)
_DEFAULT_RATIO = 1.0  # < 10


class VersionManager:
    """content_version_policy 라이프사이클 + §9 전환 트리거.

    Parameters
    ----------
    session : sqlalchemy.orm.Session
        commit 은 호출자 책임. VersionManager 는 add + flush 만 수행.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_draft(
        self,
        dataset_version: str,
        *,
        transition_strategy: str = TransitionStrategy.IMMEDIATE.value,
        real_content_threshold: int = 10,
    ) -> ContentVersionPolicy:
        """새 draft 버전 row 를 만든다. 같은 dataset_version 이 이미 있으면 ValueError."""
        existing = self._fetch(dataset_version)
        if existing is not None:
            raise ValueError(
                f"version already exists: dataset_version={dataset_version} "
                f"(status={existing.status})"
            )
        row = ContentVersionPolicy(
            dataset_version=dataset_version,
            status=VersionStatus.DRAFT.value,
            activation_date=None,
            deprecation_date=None,
            replacement_version=None,
            transition_strategy=transition_strategy,
            real_content_threshold=real_content_threshold,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def activate(self, dataset_version: str) -> None:
        """draft → active. 기존 active 가 있으면 atomic 으로 deprecated 전환.

        §9-3 흐름:
            1. 새 버전이 이미 draft 상태로 존재해야 함 (create_draft 선행)
            2. 기존 active 버전 (있다면) → deprecated (deprecation_date=now, replacement_version=새)
            3. 새 버전 → active (activation_date=now)
            4. 단일 트랜잭션으로 처리 (호출자가 commit)

        Errors:
            ValueError: 대상 버전이 존재하지 않거나 draft 상태가 아닐 때.
        """
        target = self._fetch(dataset_version)
        if target is None:
            raise ValueError(f"version not found: {dataset_version}")
        if target.status != VersionStatus.DRAFT.value:
            raise ValueError(
                f"only draft can be activated; {dataset_version}.status={target.status}"
            )

        now = datetime.utcnow()

        # 기존 active 들 deprecate (정상 상태에선 1 개지만, 안전망으로 다건 처리).
        current_active = self._fetch_active_rows()
        for row in current_active:
            if row.dataset_version == dataset_version:
                continue
            row.status = VersionStatus.DEPRECATED.value
            row.deprecation_date = now
            row.replacement_version = dataset_version
            log.info(
                "version atomic switch: %s active→deprecated (replaced by %s)",
                row.dataset_version,
                dataset_version,
            )

        # 새 버전 활성화.
        target.status = VersionStatus.ACTIVE.value
        target.activation_date = now
        target.replacement_version = None
        log.info("version atomic switch: %s draft→active", dataset_version)

        self.session.flush()

    def deprecate(self, dataset_version: str) -> None:
        """active → deprecated. deprecation_date=now."""
        row = self._fetch(dataset_version)
        if row is None:
            raise ValueError(f"version not found: {dataset_version}")
        if row.status != VersionStatus.ACTIVE.value:
            raise ValueError(
                f"only active can be deprecated; {dataset_version}.status={row.status}"
            )
        row.status = VersionStatus.DEPRECATED.value
        row.deprecation_date = datetime.utcnow()
        self.session.flush()

    def archive(self, dataset_version: str) -> None:
        """deprecated → archived. deprecation_date 기준 grace 검사 없이 강제 전환.

        대량 정리 용도의 ``archive_expired`` 와 분리 — 명시적 호출.
        """
        row = self._fetch(dataset_version)
        if row is None:
            raise ValueError(f"version not found: {dataset_version}")
        if row.status != VersionStatus.DEPRECATED.value:
            raise ValueError(
                f"only deprecated can be archived; {dataset_version}.status={row.status}"
            )
        row.status = VersionStatus.ARCHIVED.value
        self.session.flush()

    def archive_expired(self, *, grace_days: int = 30) -> List[str]:
        """deprecation_date + grace_days 가 경과한 deprecated 버전을 archived 로 전환.

        Returns
        -------
        list[str] : archived 로 전환된 dataset_version 목록.
        """
        cutoff = datetime.utcnow() - timedelta(days=grace_days)
        stmt = select(ContentVersionPolicy).where(
            ContentVersionPolicy.status == VersionStatus.DEPRECATED.value
        )
        archived: List[str] = []
        for row in self.session.execute(stmt).scalars():
            if row.deprecation_date is None:
                continue
            if row.deprecation_date <= cutoff:
                row.status = VersionStatus.ARCHIVED.value
                archived.append(row.dataset_version)
                log.info("version archived: %s (deprecated %s)", row.dataset_version, row.deprecation_date)
        if archived:
            self.session.flush()
        return archived

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------

    def get_active(self) -> Optional[ContentVersionPolicy]:
        """active 버전 1건. 없으면 None.

        가장 최근 activation_date 우선.
        """
        stmt = (
            select(ContentVersionPolicy)
            .where(ContentVersionPolicy.status == VersionStatus.ACTIVE.value)
            .order_by(ContentVersionPolicy.activation_date.desc().nullslast())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_versions(self, status: Optional[str] = None) -> List[ContentVersionPolicy]:
        """모든 버전 또는 특정 status 의 버전 목록."""
        stmt = select(ContentVersionPolicy)
        if status is not None:
            stmt = stmt.where(ContentVersionPolicy.status == status)
        stmt = stmt.order_by(ContentVersionPolicy.created_at.asc())
        return list(self.session.execute(stmt).scalars())

    def _fetch(self, dataset_version: str) -> Optional[ContentVersionPolicy]:
        stmt = select(ContentVersionPolicy).where(
            ContentVersionPolicy.dataset_version == dataset_version
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def _fetch_active_rows(self) -> List[ContentVersionPolicy]:
        stmt = select(ContentVersionPolicy).where(
            ContentVersionPolicy.status == VersionStatus.ACTIVE.value
        )
        return list(self.session.execute(stmt).scalars())

    # ------------------------------------------------------------------
    # §9-2 자동 전환 트리거
    # ------------------------------------------------------------------

    def compute_synthetic_ratio(
        self,
        real_spot_count: int,
        policy: Optional[ContentVersionPolicy] = None,
    ) -> float:
        """§9-2 임계: real_spot_count 에 따른 synthetic 비중.

            count >= 50 → 0.0
            count >= 30 → 0.2
            count >= 10 → 0.5
            else        → 1.0

        ``policy.real_content_threshold`` 가 주어지면 첫 진입 임계로 사용.
        예: threshold=20 이면 count >= 20 부터 0.5 가 적용된다.
        50/30 임계는 비례적으로 유지.
        """
        threshold = None
        if policy is not None and policy.real_content_threshold is not None:
            threshold = int(policy.real_content_threshold)

        if threshold is not None:
            # 사용자 정의 임계: 0.5 진입점만 이동, 30/50 임계는 5x/3x 비율 유지.
            mid = max(threshold * 3, threshold + 1)
            high = max(threshold * 5, mid + 1)
            thresholds = ((high, 0.0), (mid, 0.2), (threshold, 0.5))
        else:
            thresholds = _THRESHOLDS

        for limit, ratio in thresholds:
            if real_spot_count >= limit:
                return ratio
        return _DEFAULT_RATIO

    def apply_transition_triggers(
        self,
        real_spot_counts: Mapping[str, int],
    ) -> List[Dict[str, Any]]:
        """카테고리/지역 조합별 real_spot_count → synthetic 비중 계산 결과 리스트.

        실제 DB 업데이트는 별도 meta 테이블이 없으므로 수행하지 않는다.
        결과는 메트릭/모니터링 잡이 사용한다.

        Returns
        -------
        list of {key, real_spot_count, synthetic_ratio, dataset_version, action}
        """
        active = self.get_active()
        results: List[Dict[str, Any]] = []
        for key, count in real_spot_counts.items():
            ratio = self.compute_synthetic_ratio(count, active)
            action = "keep"
            if ratio == 0.0:
                action = "archive_pending"
            elif ratio < 1.0:
                action = "scale_down"
            results.append(
                {
                    "key": key,
                    "real_spot_count": int(count),
                    "synthetic_ratio": ratio,
                    "dataset_version": active.dataset_version if active else None,
                    "action": action,
                }
            )
        return results


# ---------------------------------------------------------------------------
# Phase 1 스텁 호환 함수 (외부 코드에서 호출 가능)
# ---------------------------------------------------------------------------


def evaluate_synthetic_share(real_spot_count: int) -> float:
    """§9-2 임계값 기반 synthetic 비중 (호환용 — 새 코드는 ``VersionManager`` 사용)."""
    for limit, ratio in _THRESHOLDS:
        if real_spot_count >= limit:
            return ratio
    return _DEFAULT_RATIO


def should_archive(real_spot_count: int) -> bool:
    """real_spot_count >= 50 → True (호환용)."""
    return real_spot_count >= 50


__all__ = [
    "VersionManager",
    "VersionStatus",
    "TransitionStrategy",
    "evaluate_synthetic_share",
    "should_archive",
]
