"""Job 7 — 스팟 단위 cross-reference 검증 진입점.

validator-engineer Phase 2 (``scp_04_val_phase2_complete``).

사용 예:
    pipeline validate-cross-reference \
        --spot-bundle-json /tmp/spot_bundle.json \
        --spec-json /tmp/spec.json \
        --no-db

spot_bundle.json 은 다음 구조의 dict:
    {
        "feed":     { ... },
        "detail":   { ... },
        "plan":     { ... },
        "messages": { ... },
        "review":   { ... }
    }
일부 키가 빠져도 허용 — 관련 pair 는 skip 된다.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import click

from pipeline.spec.models import ContentSpec
from pipeline.validators.dispatch import run_cross_reference
from pipeline.validators.types import ValidationResult


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _insert_log_row(
    *,
    content_id: str,
    result: ValidationResult,
) -> None:
    """content_validation_log 에 validator_type='cross_ref' 로 insert."""
    from pipeline.db.models import ContentValidationLog
    from pipeline.db.session import get_session

    session = get_session()
    try:
        session.add(
            ContentValidationLog(
                content_type="bundle",
                content_id=content_id,
                validator_type="cross_ref",
                score=None,
                status="passed" if result.ok else "failed",
                reason_json=result.to_dict(),
                created_at=datetime.utcnow(),
            )
        )
        session.commit()
    finally:
        session.close()


@click.command("validate-cross-reference")
@click.option(
    "--spot-bundle-json",
    "spot_bundle_json",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="{feed,detail,plan,messages,review} 5 payload 를 담은 번들 JSON",
)
@click.option(
    "--spec-json",
    "spec_json",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="ContentSpec JSON (build_content_spec 출력)",
)
@click.option(
    "--content-id",
    "content_id",
    default=None,
    help="log 기록 content_id (없으면 spec.spot_id)",
)
@click.option(
    "--no-db",
    "no_db",
    is_flag=True,
    default=False,
    help="content_validation_log 기록 건너뜀 (dry-run)",
)
def validate_cross_reference_command(
    spot_bundle_json: Path,
    spec_json: Path,
    content_id: str | None,
    no_db: bool,
) -> None:
    """스팟 번들 Layer 3 cross-reference 검증.

    종료 코드:
        0 = ok (warn 포함)
        1 = reject (hard rejection 있음)
    """
    bundle_raw = _load_json(spot_bundle_json)
    # bundle 은 dict[str, dict] — 각 content type payload.
    bundle: Dict[str, Dict[str, Any]] = {
        k: v for k, v in bundle_raw.items() if isinstance(v, dict)
    }

    spec_dict = _load_json(spec_json)
    spec = ContentSpec.model_validate(spec_dict)

    result = run_cross_reference(bundle, spec)

    out = {
        "spot_id": spec.spot_id,
        "content_id": content_id or spec.spot_id,
        "status": "passed" if result.ok else "failed",
        "result": result.to_dict(),
    }
    click.echo(json.dumps(out, ensure_ascii=False))

    if not no_db:
        try:
            _insert_log_row(
                content_id=content_id or spec.spot_id,
                result=result,
            )
        except Exception as exc:  # pragma: no cover
            click.echo(
                json.dumps({"db_insert_error": str(exc)}, ensure_ascii=False),
                err=True,
            )

    sys.exit(0 if result.ok else 1)


if __name__ == "__main__":
    validate_cross_reference_command()
