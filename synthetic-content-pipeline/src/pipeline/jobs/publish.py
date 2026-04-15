"""Job 10 — publish_synthetic_content (Phase 4 본 구현).

사용 예:

    # 1) loop 결과 JSON 파일을 그대로 publish (pipeline-qa 테스트 경로)
    python -m pipeline.jobs.publish \\
        --spot-id spot_food_yeonmu_evening \\
        --spot-result-json /tmp/spot_result.json \\
        --dataset-version v_init

    # 2) spec → process_spot_full → publish 풀 파이프라인
    python -m pipeline.jobs.publish \\
        --spot-id spot_food_yeonmu_evening \\
        --spec-json data/goldens/specs/golden_food_yeonmu_evening.json

    # 3) dry-run (rollback)
    python -m pipeline.jobs.publish \\
        --spot-id spot_food_yeonmu_evening \\
        --spot-result-json /tmp/spot_result.json \\
        --dry-run

기본 DB:
    SCP_DB_URL 환경 변수 사용. 미설정 시 ``sqlite:///./pipeline.db`` 폴백.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import click
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pipeline.db.base import Base
from pipeline.db import models  # noqa: F401 — metadata 등록
from pipeline.publish.publisher import Publisher, PublishResult

log = logging.getLogger(__name__)


_DEFAULT_DB_URL = "sqlite:///./pipeline.db"


def _resolve_db_url() -> str:
    return os.environ.get("SCP_DB_URL", _DEFAULT_DB_URL)


def _load_spot_result_from_json(path: Path) -> Any:
    """SpotProcessResult.to_dict() JSON → 가벼운 reconstruction.

    pipeline-qa 가 process_spot_full 결과를 to_dict() 로 dump 한 파일을
    그대로 다시 publish 할 수 있도록, 필요한 속성만 가진 shim 객체를 만든다.
    """
    data = json.loads(path.read_text(encoding="utf-8"))

    @dataclass
    class _CandidateShim:
        payload: Dict[str, Any]

    @dataclass
    class _ContentShim:
        spot_id: str
        content_type: str
        selected_candidate: Optional[_CandidateShim]
        quality_score: float
        classification: str

    @dataclass
    class _SpotShim:
        spot_id: str
        contents: Dict[str, _ContentShim]
        approved: bool

    contents: Dict[str, _ContentShim] = {}
    for ctype, c in (data.get("contents") or {}).items():
        payload = c.get("payload")
        cand = _CandidateShim(payload=payload) if payload is not None else None
        contents[ctype] = _ContentShim(
            spot_id=data.get("spot_id", ""),
            content_type=ctype,
            selected_candidate=cand,
            quality_score=float(c.get("quality_score", 0.0) or 0.0),
            classification=str(c.get("classification") or "rejected"),
        )

    return _SpotShim(
        spot_id=data.get("spot_id", ""),
        contents=contents,
        approved=bool(data.get("approved", False)),
    )


def _build_spot_result_from_spec(spec_path: Path, spot_id: str) -> Any:
    """spec JSON → ContentSpec → process_spot_full() 결과."""
    from pipeline.spec.models import ContentSpec
    from pipeline.loop.generate_validate_retry import process_spot_full

    spec_data = json.loads(spec_path.read_text(encoding="utf-8"))
    spec = ContentSpec.model_validate(spec_data)
    return process_spot_full(spot_id, spec)


@click.command("publish")
@click.option("--spot-id", "spot_id", required=True, help="대상 spot id")
@click.option(
    "--spot-result-json",
    "spot_result_json",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="SpotProcessResult.to_dict() JSON 파일. 있으면 spec 무시.",
)
@click.option(
    "--spec-json",
    "spec_json",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="ContentSpec JSON. spot-result-json 이 없을 때 process_spot_full 호출.",
)
@click.option(
    "--dataset-version",
    "dataset_version",
    default=None,
    help="명시 시 그 버전, 생략 시 active 버전 자동 선택.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="rollback 모드. DB 변경 없이 PublishResult JSON 만 출력.",
)
@click.option(
    "--db-url",
    "db_url",
    default=None,
    help="SQLAlchemy DB URL. 생략 시 SCP_DB_URL 또는 sqlite:///./pipeline.db",
)
def publish_command(
    spot_id: str,
    spot_result_json: Optional[Path],
    spec_json: Optional[Path],
    dataset_version: Optional[str],
    dry_run: bool,
    db_url: Optional[str],
) -> None:
    """approved 콘텐츠 → synthetic_* 테이블 publish."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if spot_result_json is None and spec_json is None:
        raise click.UsageError("--spot-result-json 또는 --spec-json 중 하나는 필수.")

    if spot_result_json is not None:
        spot_result = _load_spot_result_from_json(spot_result_json)
    else:
        assert spec_json is not None
        spot_result = _build_spot_result_from_spec(spec_json, spot_id)

    # spot_id 가 SpotProcessResult 와 다르면 CLI 입력으로 덮어쓴다.
    if getattr(spot_result, "spot_id", "") != spot_id:
        log.warning(
            "spot_id mismatch: cli=%s file=%s — using cli value",
            spot_id,
            getattr(spot_result, "spot_id", ""),
        )
        spot_result.spot_id = spot_id

    url = db_url or _resolve_db_url()
    engine = create_engine(url, future=True)
    # in-memory sqlite 또는 처음 실행하는 경우를 위해 metadata create_all 시도.
    Base.metadata.create_all(engine)

    with Session(engine, expire_on_commit=False) as session:
        publisher = Publisher(session, dataset_version=dataset_version)
        result: PublishResult = publisher.publish_spot(spot_result)

        if dry_run:
            session.rollback()
            log.info("dry-run: rolled back")
        else:
            session.commit()
            log.info("publish committed")

    payload = result.to_dict()
    payload["dry_run"] = bool(dry_run)
    click.echo(json.dumps(payload, ensure_ascii=False))


# 별칭 — 기존 import 호환성.
main = publish_command


if __name__ == "__main__":
    publish_command()
