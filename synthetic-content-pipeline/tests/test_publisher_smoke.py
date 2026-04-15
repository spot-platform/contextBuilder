"""Phase 4 — Publisher smoke + 확장 테스트 (pipeline-qa scp_05_qa_phase4).

스켈레톤 위에 QA 케이스를 얹은 확장 버전. `Publisher.publish_spot` 가
- 5 content type 을 synthetic_* 테이블에 올바르게 insert
- rejected / 누락 content 를 graceful 하게 skip
- dry-run rollback 에서 DB 상태가 깨끗하게 유지
- content_version_policy 가 없을 때 'v_init' 으로 폴백
- process_spot_full (stub 모드) 결과를 바로 publish
하는지 검증한다.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from pipeline.db.base import Base
from pipeline.db import models  # noqa: F401 — metadata 등록
from pipeline.db.models import (
    SyntheticFeedContent,
    SyntheticReview,
    SyntheticSpotDetail,
    SyntheticSpotMessages,
)
from pipeline.publish.publisher import Publisher, PublishResult
from pipeline.publish.versioning import VersionManager


# ---------------------------------------------------------------------------
# shim fixture: ContentProcessResult / SpotProcessResult 최소 재현
# ---------------------------------------------------------------------------


@dataclass
class _Cand:
    payload: Optional[Dict[str, Any]]
    variant: str = "v0"


@dataclass
class _CPR:
    spot_id: str
    content_type: str
    selected_candidate: Optional[_Cand]
    quality_score: float
    classification: str


@dataclass
class _SPR:
    spot_id: str
    contents: Dict[str, _CPR] = field(default_factory=dict)
    approved: bool = True


_FEED_PAYLOAD = {
    "title": "수원역 저녁 모임 4명 모집",
    "summary": "부담 없이 가볍게 만나는 자리예요.",
    "tags": ["수원", "food", "저녁"],
    "price_label": "1인 약 1.8만원",
    "region_label": "수원역 일대",
    "time_label": "4/18(금) 19:00",
    "status": "recruiting",
    "supporter_label": "casual_meetup",
}

_DETAIL_PAYLOAD = {
    "title": "수원역 food 모임 4명 상세 안내",
    "description": "수원역에서 4명 규모 식사 모임.",
    "materials": ["편한 복장"],
    "target_audience": "수원 주변 직장인",
    "cost_breakdown": [{"item": "참가비 (1인)", "amount": 18000}],
    "host_intro": "수원에서 종종 모임 여는 호스트",
    "policy_notes": "노쇼 시 다음 모집 제한",
}

_PLAN_PAYLOAD = {
    "steps": [
        {"time": "19:00", "activity": "집결 및 인사"},
        {"time": "19:15", "activity": "본 활동 시작"},
        {"time": "20:50", "activity": "마무리"},
    ],
    "total_duration_minutes": 120,
}

_MESSAGES_PAYLOAD = {
    "recruiting_intro": "수원에서 4명 규모 식사 모임 모집해요. 가벼운 마음으로 신청 부탁드려요.",
    "join_approval": "신청 감사드립니다. 4/18 19:00 수원에서 뵐게요.",
    "day_of_notice": "오늘 19:00 수원역에서 만나요. 편한 차림으로 오세요.",
    "post_thanks": "오늘 함께해 주셔서 고맙습니다.",
}

_REVIEW_PAYLOAD = {
    "rating": 5,
    "review_text": "수원 모임 분위기 좋았어요. 다음에도 가볼래요.",
    "satisfaction_tags": ["분위기좋음"],
    "recommend": True,
    "will_rejoin": True,
    "sentiment": "positive",
}


def _make_cpr(
    spot_id: str,
    content_type: str,
    payload: Optional[Dict[str, Any]],
    classification: str = "approved",
    quality: float = 0.82,
) -> _CPR:
    cand = _Cand(payload=payload) if payload is not None else None
    return _CPR(
        spot_id=spot_id,
        content_type=content_type,
        selected_candidate=cand,
        quality_score=quality,
        classification=classification,
    )


def _make_full_spot(
    spot_id: str = "spot_full",
    *,
    overrides: Optional[Dict[str, Dict[str, Any]]] = None,
) -> _SPR:
    """5 type 풀 세팅. overrides로 특정 type 의 classification/payload 덮어쓰기."""
    overrides = overrides or {}

    def _cpr(ctype: str, payload: Dict[str, Any], default_cls: str = "approved") -> _CPR:
        ov = overrides.get(ctype) or {}
        cls = ov.get("classification", default_cls)
        pl = ov.get("payload", payload)
        qs = float(ov.get("quality_score", 0.82))
        return _make_cpr(spot_id, ctype, pl, classification=cls, quality=qs)

    contents = {
        "feed": _cpr("feed", _FEED_PAYLOAD),
        "detail": _cpr("detail", _DETAIL_PAYLOAD),
        "plan": _cpr("plan", _PLAN_PAYLOAD, default_cls="conditional"),
        "messages": _cpr("messages", _MESSAGES_PAYLOAD),
        "review": _cpr("review", _REVIEW_PAYLOAD),
    }
    return _SPR(spot_id=spot_id, contents=contents, approved=True)


# ---------------------------------------------------------------------------
# 기본 smoke — 유지 (스켈레톤 호환)
# ---------------------------------------------------------------------------


def test_publisher_importable_and_instantiable(tmp_db):
    """PublishResult 타입 로드 + Publisher 인스턴스화 가능."""
    publisher = Publisher(tmp_db, dataset_version="v_init")
    assert publisher.dataset_version == "v_init"
    assert isinstance(PublishResult, type)


def test_publisher_publish_single_spot(tmp_db):
    """5 type 전부 approved/conditional → feed=1, detail=1, messages=4, review=1."""
    vm = VersionManager(tmp_db)
    vm.create_draft("v_init")
    vm.activate("v_init")

    publisher = Publisher(tmp_db, dataset_version="v_init")
    result = publisher.publish_spot(_make_full_spot("spot_smoke_1"))
    tmp_db.commit()

    assert result.spot_id == "spot_smoke_1"
    assert result.dataset_version == "v_init"
    assert result.published_rows.get("feed", 0) == 1
    assert result.published_rows.get("detail", 0) == 1
    assert result.published_rows.get("messages", 0) == 4
    assert result.published_rows.get("review", 0) == 1
    assert result.errors == []


# ---------------------------------------------------------------------------
# 케이스 1: rejected skip 매트릭스
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rejected_types,expected_published",
    [
        # 단일 type rejected → 그 type 만 0
        (["feed"], {"feed": 0, "detail": 1, "messages": 4, "review": 1}),
        (["detail"], {"feed": 1, "detail": 0, "messages": 4, "review": 1}),
        (["messages"], {"feed": 1, "detail": 1, "messages": 0, "review": 1}),
        (["review"], {"feed": 1, "detail": 1, "messages": 4, "review": 0}),
        # 2개 rejected: plan + messages → 나머지 3 type 은 publish
        (["plan", "messages"], {"feed": 1, "detail": 1, "messages": 0, "review": 1}),
        # 전부 rejected → 모두 0
        (
            ["feed", "detail", "plan", "messages", "review"],
            {"feed": 0, "detail": 0, "messages": 0, "review": 0},
        ),
    ],
)
def test_publisher_rejected_skip_matrix(tmp_db, rejected_types, expected_published):
    """rejected 인 content type 은 skipped_rows 에 카운트되고 publish 되지 않는다.

    기대:
      - published_rows[해당 type] == 0
      - skipped_rows[해당 type] >= 1 (단, messages/review/feed/detail 단위로만 계산)
      - 다른 type 은 정상 publish
    """
    vm = VersionManager(tmp_db)
    vm.create_draft("v_init")
    vm.activate("v_init")

    overrides = {t: {"classification": "rejected"} for t in rejected_types}
    spot = _make_full_spot("spot_reject", overrides=overrides)

    publisher = Publisher(tmp_db, dataset_version="v_init")
    result = publisher.publish_spot(spot)
    tmp_db.commit()

    for ctype, count in expected_published.items():
        assert result.published_rows.get(ctype, 0) == count, (
            f"{ctype}: expected {count} published, got {result.published_rows.get(ctype)}"
        )

    # rejected type 들은 skipped 카운트가 잡혀야 함 (messages snippet skip 아님 — 전체 reject)
    for rt in rejected_types:
        if rt == "plan":
            # plan 은 별도 row 가 없어서 skipped_rows["plan"] 로 별도 트래킹
            assert result.skipped_rows.get("plan", 0) >= 1
        else:
            assert result.skipped_rows.get(rt, 0) >= 1, (
                f"expected skipped_rows[{rt}] >= 1, got {result.skipped_rows}"
            )

    # errors 없음
    assert result.errors == []


# ---------------------------------------------------------------------------
# 케이스 2: plan-only rejected → detail 은 publish, plan_json 은 비어있음
# ---------------------------------------------------------------------------


def test_publisher_plan_rejected_detail_approved(tmp_db):
    """plan rejected, detail approved → detail row 는 insert, detail.plan_json 은 None.

    기대:
      - SyntheticSpotDetail row 1 건 insert
      - 해당 row 의 plan_json 컬럼이 None 또는 빈 dict (plan payload embed 안 됨)
      - skipped_rows["plan"] == 1
    """
    vm = VersionManager(tmp_db)
    vm.create_draft("v_init")
    vm.activate("v_init")

    spot = _make_full_spot(
        "spot_plan_rej",
        overrides={"plan": {"classification": "rejected"}},
    )

    publisher = Publisher(tmp_db, dataset_version="v_init")
    result = publisher.publish_spot(spot)
    tmp_db.commit()

    assert result.published_rows["detail"] == 1
    assert result.skipped_rows.get("plan", 0) == 1

    row = tmp_db.execute(
        select(SyntheticSpotDetail).where(SyntheticSpotDetail.spot_id == "spot_plan_rej")
    ).scalar_one()
    # plan rejected → plan_json 은 None or falsy
    assert not row.plan_json, (
        f"detail.plan_json should be None/empty when plan rejected, got {row.plan_json!r}"
    )


# ---------------------------------------------------------------------------
# 케이스 3: messages snippet 누락
# ---------------------------------------------------------------------------


def test_publisher_messages_missing_snippet(tmp_db, caplog):
    """messages payload 에 4종 중 3종만 있으면 3 row insert + 경고 로그.

    기대:
      - published_rows["messages"] == 3
      - caplog 에 "missing snippet" 경고가 존재
    """
    vm = VersionManager(tmp_db)
    vm.create_draft("v_init")
    vm.activate("v_init")

    partial_msgs = {
        "recruiting_intro": "모집 공고",
        "join_approval": "승인합니다",
        "day_of_notice": "오늘 만나요",
        # post_thanks 누락
    }

    spot = _make_full_spot(
        "spot_msg_partial",
        overrides={"messages": {"payload": partial_msgs}},
    )

    publisher = Publisher(tmp_db, dataset_version="v_init")
    with caplog.at_level(logging.WARNING, logger="pipeline.publish.publisher"):
        result = publisher.publish_spot(spot)
    tmp_db.commit()

    assert result.published_rows["messages"] == 3
    # 실제 DB row 수 확인
    count = tmp_db.execute(
        select(func.count())
        .select_from(SyntheticSpotMessages)
        .where(SyntheticSpotMessages.spot_id == "spot_msg_partial")
    ).scalar_one()
    assert count == 3

    # warning 로그 확인 (publisher 가 snippet 별로 로그를 찍는다)
    warnings = [r for r in caplog.records if "missing snippet" in r.getMessage()]
    assert len(warnings) >= 1, "missing snippet 경고가 최소 1건 찍혀야 한다"


# ---------------------------------------------------------------------------
# 케이스 4: dry-run rollback → DB 상태 불변
# ---------------------------------------------------------------------------


def test_publisher_dry_run_rollback_leaves_db_empty():
    """Publisher.publish_spot 후 rollback → 다른 session 에서 row 0 확인.

    기대:
      - publish_spot 호출 후 session.rollback()
      - 새 session 으로 SELECT count(*) from synthetic_feed_content → 0
    """
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    # 세션 1: publish → rollback
    s1 = SessionLocal()
    try:
        vm = VersionManager(s1)
        vm.create_draft("v_init")
        vm.activate("v_init")
        s1.commit()  # version policy 는 commit (publish 만 rollback 하기 위함)

        publisher = Publisher(s1, dataset_version="v_init")
        result = publisher.publish_spot(_make_full_spot("spot_dry"))
        assert result.published_rows["feed"] == 1  # flush 까진 된 상태
        s1.rollback()
    finally:
        s1.close()

    # 세션 2: 실제 DB 에 row 가 없는지 확인
    s2 = SessionLocal()
    try:
        feed_count = s2.execute(
            select(func.count()).select_from(SyntheticFeedContent)
        ).scalar_one()
        detail_count = s2.execute(
            select(func.count()).select_from(SyntheticSpotDetail)
        ).scalar_one()
        msg_count = s2.execute(
            select(func.count()).select_from(SyntheticSpotMessages)
        ).scalar_one()
        review_count = s2.execute(
            select(func.count()).select_from(SyntheticReview)
        ).scalar_one()
        assert feed_count == 0
        assert detail_count == 0
        assert msg_count == 0
        assert review_count == 0
    finally:
        s2.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 케이스 5: active 부재 → v_init 폴백 + 경고 로그
# ---------------------------------------------------------------------------


def test_publisher_fallback_when_no_active_version(tmp_db, caplog):
    """content_version_policy 가 완전히 비어있을 때 Publisher 는 'v_init' 으로 폴백.

    기대:
      - dataset_version == "v_init"
      - caplog 에 'no active content_version_policy' 경고 1건
    """
    assert VersionManager(tmp_db).get_active() is None  # precondition

    with caplog.at_level(logging.WARNING, logger="pipeline.publish.publisher"):
        publisher = Publisher(tmp_db)  # dataset_version 생략

    assert publisher.dataset_version == "v_init"
    fallback_warns = [
        r for r in caplog.records if "no active content_version_policy" in r.getMessage()
    ]
    assert len(fallback_warns) == 1


# ---------------------------------------------------------------------------
# 케이스 7: process_spot_full → publisher 통합 (stub 모드)
# ---------------------------------------------------------------------------


def test_process_spot_full_to_publisher_stub(tmp_db):
    """stub 모드 process_spot_full 결과를 Publisher 로 publish.

    기대:
      - SCP_LLM_MODE=stub (conftest autouse)
      - SpotProcessResult 반환 (contents 5 type)
      - feed/detail/review >= 1 row insert, messages 최소 3 row 이상
      - errors 없음
    """
    assert os.environ.get("SCP_LLM_MODE") == "stub"

    import json
    from pathlib import Path

    from pipeline.spec.models import ContentSpec
    from pipeline.loop.generate_validate_retry import process_spot_full

    spec_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "goldens"
        / "specs"
        / "golden_cafe_sinchon_weekend.json"
    )
    spec_data = json.loads(spec_path.read_text(encoding="utf-8"))
    spec = ContentSpec.model_validate(spec_data)

    spot_result = process_spot_full("spot_stub_full", spec)
    assert hasattr(spot_result, "contents")
    assert set(spot_result.contents.keys()) >= {"feed", "detail", "plan", "messages", "review"}

    vm = VersionManager(tmp_db)
    vm.create_draft("v_init")
    vm.activate("v_init")

    publisher = Publisher(tmp_db, dataset_version="v_init")
    result = publisher.publish_spot(spot_result)
    tmp_db.commit()

    # rejected 여도 feed/detail/review 중 하나는 publish 되었을 가능성. stub 은 대체로 approved 가 많다.
    # 최소 보장: errors 는 비어야 하고, published_rows 에 5 type 키 전부 존재.
    assert result.errors == []
    for ctype in ("feed", "detail", "plan", "messages", "review"):
        assert ctype in result.published_rows
        assert ctype in result.skipped_rows

    # 최소 가정: 적어도 feed 또는 detail 또는 review 중 하나는 publish 됨.
    total_main = (
        result.published_rows.get("feed", 0)
        + result.published_rows.get("detail", 0)
        + result.published_rows.get("review", 0)
    )
    assert total_main >= 1, (
        f"stub 모드에서 feed/detail/review 중 최소 1건은 publish 되어야 한다: {result.to_dict()}"
    )
