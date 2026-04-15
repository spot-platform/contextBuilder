"""publisher — approved / conditional 콘텐츠를 synthetic_* 테이블에 insert.

Phase 4 본 구현 (pipeline-infra-architect, scp_01_infra_phase4_complete).

흐름:
    SpotProcessResult (loop.generate_validate_retry 의 결과)
        ↓ Publisher.publish_spot()
    synthetic_feed_content     ← cpr.contents['feed'].selected_candidate.payload
    synthetic_spot_detail      ← cpr.contents['detail'] + cpr.contents['plan'] (plan_json embed)
    synthetic_spot_messages    ← cpr.contents['messages']  (4 snippet → 4 row)
    synthetic_review           ← cpr.contents['review']
        ↓
    PublishResult(published_rows, skipped_rows, errors)

원칙:
    - rejected content type 은 publish 하지 않는다 (skipped 카운트).
    - plan 은 별도 테이블이 아니라 synthetic_spot_detail.plan_json 컬럼에 임베드.
    - messages 4 snippet → 4 row, message_type 컬럼에 키 이름 기록.
    - dataset_version 미지정 시 content_version_policy 에서 active 버전 자동 선택.
    - session.commit 은 호출자 책임. Publisher 는 add + flush 만 수행.
    - content_validation_log insert 는 publisher 책임 아님 (validator 가 이미 기록).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.db.models import (
    ContentVersionPolicy,
    SyntheticFeedContent,
    SyntheticReview,
    SyntheticSpotDetail,
    SyntheticSpotMessages,
)

# loop 결과 타입 힌트 (런타임 의존성 회피)
try:
    from pipeline.loop.generate_validate_retry import (  # noqa: F401
        ContentProcessResult,
        SpotProcessResult,
    )
except ImportError:  # pragma: no cover - 단위 테스트 환경
    ContentProcessResult = None  # type: ignore
    SpotProcessResult = None  # type: ignore


log = logging.getLogger(__name__)

# 4종 message snippet 키 (생성기 payload 와 동일).
_MESSAGE_KEYS = (
    "recruiting_intro",
    "join_approval",
    "day_of_notice",
    "post_thanks",
)

_DEFAULT_VERSION = "v_init"

# loop classification → publish 가능 여부.
_PUBLISHABLE_CLASSIFICATIONS = frozenset({"approved", "conditional"})


@dataclass
class PublishResult:
    """publish_spot 결과 요약."""

    spot_id: str
    dataset_version: str
    published_rows: Dict[str, int] = field(default_factory=dict)
    skipped_rows: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spot_id": self.spot_id,
            "dataset_version": self.dataset_version,
            "published_rows": dict(self.published_rows),
            "skipped_rows": dict(self.skipped_rows),
            "errors": list(self.errors),
        }


def _to_decimal(score: Optional[float]) -> Optional[Decimal]:
    """quality_score → Decimal(4,3). None → None."""
    if score is None:
        return None
    try:
        return Decimal(str(round(float(score), 3)))
    except (TypeError, ValueError):
        return None


class Publisher:
    """approved / conditional SpotProcessResult 를 synthetic_* 테이블에 insert.

    Parameters
    ----------
    session : sqlalchemy.orm.Session
        SQLAlchemy 2.0 session. 호출자가 begin/commit 책임.
    dataset_version : Optional[str]
        명시되면 그 값 사용. 없으면 content_version_policy.status='active' 행을 조회.
        active 가 없으면 ``"v_init"`` 폴백.
    """

    def __init__(
        self,
        session: Session,
        dataset_version: Optional[str] = None,
    ) -> None:
        self.session = session
        self.dataset_version = dataset_version or self._resolve_active_version()

    # ------------------------------------------------------------------
    # version 해석
    # ------------------------------------------------------------------

    def _resolve_active_version(self) -> str:
        """content_version_policy 에서 status='active' dataset_version 1건 조회."""
        stmt = (
            select(ContentVersionPolicy)
            .where(ContentVersionPolicy.status == "active")
            .order_by(ContentVersionPolicy.activation_date.desc().nullslast())
            .limit(1)
        )
        row = self.session.execute(stmt).scalar_one_or_none()
        if row is None:
            log.warning(
                "publisher: no active content_version_policy row — falling back to %s",
                _DEFAULT_VERSION,
            )
            return _DEFAULT_VERSION
        return row.dataset_version

    # ------------------------------------------------------------------
    # public entry
    # ------------------------------------------------------------------

    def publish_spot(self, spot_result: "SpotProcessResult") -> PublishResult:
        """SpotProcessResult.contents 5 종을 각 테이블에 insert.

        rejected 인 type 은 skipped_rows 에만 카운트.
        반환 후 호출자가 commit/rollback 결정.
        """
        result = PublishResult(
            spot_id=spot_result.spot_id,
            dataset_version=self.dataset_version,
        )

        contents = getattr(spot_result, "contents", {}) or {}

        # feed
        result.published_rows["feed"], result.skipped_rows["feed"] = self._safe_publish(
            "feed", contents.get("feed"), spot_result.spot_id, result
        )
        # detail (+ plan embed)
        result.published_rows["detail"], result.skipped_rows["detail"] = self._safe_publish(
            "detail",
            contents.get("detail"),
            spot_result.spot_id,
            result,
            plan_cpr=contents.get("plan"),
        )
        # plan 은 별도 테이블 row 가 없음. detail 에 묻혔지만 reject 추적용으로 카운트만.
        plan_cpr = contents.get("plan")
        if plan_cpr is not None and getattr(plan_cpr, "classification", None) == "rejected":
            result.skipped_rows["plan"] = result.skipped_rows.get("plan", 0) + 1
        else:
            result.skipped_rows.setdefault("plan", 0)
        result.published_rows.setdefault("plan", 0)  # plan 은 항상 0 row 자체 insert.

        # messages (4 row)
        result.published_rows["messages"], result.skipped_rows["messages"] = self._safe_publish(
            "messages", contents.get("messages"), spot_result.spot_id, result
        )
        # review
        result.published_rows["review"], result.skipped_rows["review"] = self._safe_publish(
            "review", contents.get("review"), spot_result.spot_id, result
        )

        # flush 만 수행 — commit 은 호출자 결정.
        try:
            self.session.flush()
        except Exception as exc:  # noqa: BLE001
            log.exception("publisher.flush failed: %s", exc)
            result.errors.append(f"flush_failed: {exc}")

        return result

    # ------------------------------------------------------------------
    # 내부 dispatch
    # ------------------------------------------------------------------

    def _safe_publish(
        self,
        content_type: str,
        cpr: Optional["ContentProcessResult"],
        spot_id: str,
        publish_result: PublishResult,
        *,
        plan_cpr: Optional["ContentProcessResult"] = None,
    ) -> tuple[int, int]:
        """단일 content type publish 래퍼.

        Returns (published_count, skipped_count).
        """
        if cpr is None:
            return 0, 0

        if not self._is_publishable(cpr):
            return 0, 1

        try:
            if content_type == "feed":
                published = self._publish_feed(spot_id, cpr)
            elif content_type == "detail":
                published = self._publish_detail(spot_id, cpr, plan_cpr=plan_cpr)
            elif content_type == "messages":
                published = self._publish_messages(spot_id, cpr)
            elif content_type == "review":
                published = self._publish_review(spot_id, cpr)
            else:
                log.warning("publisher: unsupported content_type=%s", content_type)
                return 0, 0
        except Exception as exc:  # noqa: BLE001
            log.exception("publish %s failed: %s", content_type, exc)
            publish_result.errors.append(f"{content_type}: {exc}")
            return 0, 0

        return published, 0

    @staticmethod
    def _is_publishable(cpr: "ContentProcessResult") -> bool:
        """classification 이 approved/conditional 인 경우만 publish."""
        classification = getattr(cpr, "classification", None)
        if classification not in _PUBLISHABLE_CLASSIFICATIONS:
            return False
        if getattr(cpr, "selected_candidate", None) is None:
            return False
        return True

    @staticmethod
    def _payload(cpr: "ContentProcessResult") -> Dict[str, Any]:
        cand = cpr.selected_candidate
        if cand is None:
            return {}
        return dict(cand.payload or {})

    # ------------------------------------------------------------------
    # type별 insert
    # ------------------------------------------------------------------

    def _publish_feed(self, spot_id: str, cpr: "ContentProcessResult") -> int:
        payload = self._payload(cpr)
        row = SyntheticFeedContent(
            dataset_version=self.dataset_version,
            spot_id=spot_id,
            title=str(payload.get("title", "")),
            summary=str(payload.get("summary", "")),
            cover_tags_json=payload.get("tags"),
            supporter_label=payload.get("supporter_label"),
            price_label=payload.get("price_label"),
            region_label=payload.get("region_label"),
            time_label=payload.get("time_label"),
            status=payload.get("status"),
            quality_score=_to_decimal(cpr.quality_score),
            validation_status=cpr.classification,
        )
        self.session.add(row)
        return 1

    def _publish_detail(
        self,
        spot_id: str,
        cpr: "ContentProcessResult",
        *,
        plan_cpr: Optional["ContentProcessResult"] = None,
    ) -> int:
        payload = self._payload(cpr)

        # plan 은 별도 테이블이 없으므로 detail.plan_json 에 embed.
        plan_json: Optional[Dict[str, Any]] = None
        if plan_cpr is not None and self._is_publishable(plan_cpr):
            plan_json = self._payload(plan_cpr)

        row = SyntheticSpotDetail(
            dataset_version=self.dataset_version,
            spot_id=spot_id,
            title=str(payload.get("title", "")),
            description=str(payload.get("description", "")),
            plan_json=plan_json,
            materials_json=payload.get("materials"),
            target_audience=payload.get("target_audience"),
            cost_breakdown_json=payload.get("cost_breakdown"),
            host_intro=payload.get("host_intro"),
            policy_notes=payload.get("policy_notes"),
            quality_score=_to_decimal(cpr.quality_score),
            validation_status=cpr.classification,
        )
        self.session.add(row)
        return 1

    def _publish_plan(self, spot_id: str, cpr: "ContentProcessResult") -> int:  # noqa: D401
        """plan 은 별도 테이블 없이 detail.plan_json 으로 embed.

        시그니처는 contract 유지 차원에서 남겨두지만, 직접 호출 경로는 없다.
        publish_spot() 가 _publish_detail 에 plan_cpr 를 넘긴다.
        """
        return 0

    def _publish_messages(self, spot_id: str, cpr: "ContentProcessResult") -> int:
        payload = self._payload(cpr)
        score = _to_decimal(cpr.quality_score)
        status = cpr.classification
        inserted = 0
        for message_type in _MESSAGE_KEYS:
            content = payload.get(message_type)
            if content is None:
                # snippet 누락 — skip 하고 다음 진행 (drop 가능).
                log.warning(
                    "publish_messages: missing snippet '%s' for spot=%s",
                    message_type,
                    spot_id,
                )
                continue
            row = SyntheticSpotMessages(
                dataset_version=self.dataset_version,
                spot_id=spot_id,
                message_type=message_type,
                speaker_type="host",
                speaker_id=None,
                content=str(content),
                created_at_simulated=None,
                quality_score=score,
                validation_status=status,
            )
            self.session.add(row)
            inserted += 1
        return inserted

    def _publish_review(self, spot_id: str, cpr: "ContentProcessResult") -> int:
        payload = self._payload(cpr)
        sentiment = payload.get("sentiment")
        sentiment_score: Optional[Decimal] = None
        if sentiment == "positive":
            sentiment_score = Decimal("0.800")
        elif sentiment == "neutral":
            sentiment_score = Decimal("0.500")
        elif sentiment == "negative":
            sentiment_score = Decimal("0.200")

        rating_value = payload.get("rating")
        try:
            rating_int: Optional[int] = int(rating_value) if rating_value is not None else None
        except (TypeError, ValueError):
            rating_int = None

        row = SyntheticReview(
            dataset_version=self.dataset_version,
            spot_id=spot_id,
            reviewer_agent_id=None,
            rating=rating_int,
            review_text=payload.get("review_text"),
            tags_json=payload.get("satisfaction_tags"),
            sentiment_score=sentiment_score,
            quality_score=_to_decimal(cpr.quality_score),
            validation_status=cpr.classification,
        )
        self.session.add(row)
        return 1


# ---------------------------------------------------------------------------
# 호환 함수 (Phase 1 스텁 시그니처 — 외부 코드에서 호출하면 deprecation 메시지)
# ---------------------------------------------------------------------------


def publish_dataset(dataset_version: str) -> Dict[str, Any]:  # pragma: no cover - 호환용
    """Deprecated — Publisher 클래스를 직접 사용하라."""
    log.warning(
        "publish_dataset() 는 Phase 1 스텁이다. Publisher(session, dataset_version) 를 사용하라."
    )
    return {
        "dataset_version": dataset_version,
        "status": "deprecated",
        "published_count": 0,
    }


__all__ = [
    "Publisher",
    "PublishResult",
    "publish_dataset",
]
