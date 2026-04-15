"""Job 6 — schema + rule 개별 검증 진입점.

validator-engineer Phase 1 (scp_04_val_phase1_complete) 산출.

사용 예:
    pipeline validate-individual \
        --content-type feed \
        --content-json /tmp/feed.json \
        --spec-json /tmp/spec.json \
        --no-db
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import click

from pipeline.spec.models import ContentSpec
from pipeline.validators.rules import validate_feed_rules
from pipeline.validators.schema import validate_feed_schema
from pipeline.validators.types import ValidationResult

# ---------------------------------------------------------------------------
# 경로 기본값
# ---------------------------------------------------------------------------

DEFAULT_FEED_SCHEMA_PATH = Path("src/pipeline/llm/schemas/feed.json")


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _summary_status(schema_res: ValidationResult, rule_res: Optional[ValidationResult]) -> str:
    if not schema_res.ok:
        return "failed"
    if rule_res is None:
        return "passed"
    if not rule_res.ok:
        return "failed"
    if rule_res.warnings:
        return "warning"
    return "passed"


def _insert_log_row(
    *,
    content_type: str,
    content_id: str,
    schema_res: ValidationResult,
    rule_res: Optional[ValidationResult],
) -> None:
    """content_validation_log 에 schema/rule 결과를 두 row 로 insert."""
    # 임포트는 함수 내부에서 — `--no-db` 옵션일 때 SQLAlchemy 부팅 비용 회피.
    from pipeline.db.models import ContentValidationLog
    from pipeline.db.session import get_session

    session = get_session()
    try:
        session.add(
            ContentValidationLog(
                content_type=content_type,
                content_id=content_id,
                validator_type="schema",
                score=None,
                status="passed" if schema_res.ok else "failed",
                reason_json=schema_res.to_dict(),
                created_at=datetime.utcnow(),
            )
        )
        if rule_res is not None:
            session.add(
                ContentValidationLog(
                    content_type=content_type,
                    content_id=content_id,
                    validator_type="rule",
                    score=None,
                    status="passed" if rule_res.ok else "failed",
                    reason_json=rule_res.to_dict(),
                    created_at=datetime.utcnow(),
                )
            )
        session.commit()
    finally:
        session.close()


@click.command("validate-individual")
@click.option(
    "--content-type",
    "content_type",
    type=click.Choice(["feed"]),  # Phase 1 = feed only.
    default="feed",
    show_default=True,
)
@click.option(
    "--content-json",
    "content_json",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="검증 대상 콘텐츠 JSON 경로",
)
@click.option(
    "--spec-json",
    "spec_json",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="ContentSpec JSON 경로 (build_content_spec 출력)",
)
@click.option(
    "--schema-path",
    "schema_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=DEFAULT_FEED_SCHEMA_PATH,
    show_default=True,
    help="JSON Schema 경로 (feed.json)",
)
@click.option(
    "--content-id",
    "content_id",
    default=None,
    help="content_validation_log 에 기록할 content id (없으면 spot_id 사용)",
)
@click.option(
    "--no-db",
    "no_db",
    is_flag=True,
    default=False,
    help="content_validation_log 기록을 건너뜀 (dry-run)",
)
def validate_individual_command(
    content_type: str,
    content_json: Path,
    spec_json: Path,
    schema_path: Path,
    content_id: Optional[str],
    no_db: bool,
) -> None:
    """단일 콘텐츠 schema → rule 순서 검증.

    종료 코드:
        0 = passed (warning 포함)
        1 = failed
    """
    payload = _load_json(content_json)
    spec_dict = _load_json(spec_json)
    spec = ContentSpec.model_validate(spec_dict)

    schema_res = validate_feed_schema(payload, schema_path)

    # schema 가 깨졌으면 rule 은 skip (의미 없음).
    rule_res: Optional[ValidationResult] = None
    if schema_res.ok:
        rule_res = validate_feed_rules(payload, spec)

    status = _summary_status(schema_res, rule_res)
    out = {
        "content_type": content_type,
        "content_id": content_id or spec.spot_id,
        "spot_id": spec.spot_id,
        "status": status,
        "schema": schema_res.to_dict(),
        "rule": rule_res.to_dict() if rule_res is not None else None,
    }
    click.echo(json.dumps(out, ensure_ascii=False))

    if not no_db:
        try:
            _insert_log_row(
                content_type=content_type,
                content_id=content_id or spec.spot_id,
                schema_res=schema_res,
                rule_res=rule_res,
            )
        except Exception as exc:  # pragma: no cover - DB 부재 환경 대응.
            click.echo(
                json.dumps({"db_insert_error": str(exc)}, ensure_ascii=False),
                err=True,
            )

    sys.exit(0 if status != "failed" else 1)


if __name__ == "__main__":
    validate_individual_command()
