"""Content-type → validator 디스패처.

validator-engineer Phase 2.

파이프라인 다른 컴포넌트(생성기 루프, loop/generate_validate_retry.py, job 엔트리)
는 이 모듈만 import 해서:

    from pipeline.validators.dispatch import run_individual, run_cross_reference

두 함수로 Layer 1 / Layer 2 / Layer 3 을 호출한다. content type 이 늘거나 rule
모듈이 바뀌어도 여기서만 매핑을 고치면 된다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from pipeline.spec.models import ContentSpec
from pipeline.validators.cross_reference import (
    load_cross_reference_rules,
    validate_cross_reference,
)
from pipeline.validators.detail_rules import load_detail_rules, validate_detail_rules
from pipeline.validators.messages_rules import (
    load_messages_rules,
    validate_messages_rules,
)
from pipeline.validators.plan_rules import load_plan_rules, validate_plan_rules
from pipeline.validators.review_rules import load_review_rules, validate_review_rules
from pipeline.validators.rules import load_feed_rules, validate_feed_rules
from pipeline.validators.schema import (
    validate_detail_schema,
    validate_feed_schema,
    validate_messages_schema,
    validate_plan_schema,
    validate_review_schema,
)
from pipeline.validators.types import Rejection, ValidationResult

# ---------------------------------------------------------------------------
# 경로 상수
# ---------------------------------------------------------------------------

SCHEMA_ROOT = Path("src/pipeline/llm/schemas")
RULES_ROOT = Path("config/rules")

# content_type → schema 파일명.
CONTENT_TYPE_SCHEMA: Dict[str, str] = {
    "feed": "feed.json",
    "detail": "detail.json",
    "plan": "plan.json",
    "messages": "messages.json",
    "review": "review.json",
}

SchemaValidator = Callable[[Dict[str, Any], Path], ValidationResult]
RuleValidator = Callable[..., ValidationResult]
RuleLoader = Callable[[Optional[Path]], Dict[str, Any]]

# content_type → (schema_fn, rule_fn, rule_loader).
CONTENT_TYPE_VALIDATOR: Dict[str, Tuple[SchemaValidator, RuleValidator, RuleLoader]] = {
    "feed": (validate_feed_schema, validate_feed_rules, load_feed_rules),
    "detail": (validate_detail_schema, validate_detail_rules, load_detail_rules),
    "plan": (validate_plan_schema, validate_plan_rules, load_plan_rules),
    "messages": (
        validate_messages_schema,
        validate_messages_rules,
        load_messages_rules,
    ),
    "review": (validate_review_schema, validate_review_rules, load_review_rules),
}


# ---------------------------------------------------------------------------
# 개별 검증 (Layer 1 + Layer 2)
# ---------------------------------------------------------------------------


def run_individual(
    content_type: str,
    payload: Dict[str, Any],
    spec: ContentSpec,
    *,
    schema_root: Optional[Path] = None,
    rules_dir: Optional[Path] = None,
) -> ValidationResult:
    """단일 content type 의 Layer 1(schema) → Layer 2(rule) 순서 검증.

    schema 가 깨졌으면 rule 은 skip (rule 결과는 meta 에 "skipped" 로 표시).

    Returns:
        Layer 1 실패 시: schema 결과 (rule 미실행).
        Layer 1 통과 시: 두 Layer 의 rejection 을 합친 새로운 ValidationResult.
    """
    if content_type not in CONTENT_TYPE_VALIDATOR:
        raise ValueError(f"unknown content_type: {content_type}")

    schema_fn, rule_fn, rule_loader = CONTENT_TYPE_VALIDATOR[content_type]
    root = schema_root or SCHEMA_ROOT
    schema_path = root / CONTENT_TYPE_SCHEMA[content_type]

    schema_res = schema_fn(payload, schema_path)
    if not schema_res.ok:
        # rule 은 의미 없음 — schema 결과만 반환.
        schema_res.meta["rule_skipped_reason"] = "schema_failed"
        return schema_res

    rules = rule_loader(rules_dir)
    rule_res = rule_fn(payload, spec, rules=rules)

    # 두 Layer 병합 — loop 쪽이 한 번의 ValidationResult 로 다루도록.
    merged_rejections = list(schema_res.rejections) + list(rule_res.rejections)
    merged_meta: Dict[str, Any] = {
        "content_type": content_type,
        "schema_meta": schema_res.meta,
        "rule_meta": rule_res.meta,
    }
    # layer 식별자는 "rule" 로 유지 (loop 가 재시도 대상을 구분하는 용).
    return ValidationResult.from_rejections("rule", merged_rejections, meta=merged_meta)


# ---------------------------------------------------------------------------
# 교차 검증 (Layer 3)
# ---------------------------------------------------------------------------


def run_cross_reference(
    spot_bundle: Dict[str, Dict[str, Any]],
    spec: ContentSpec,
    *,
    rules_dir: Optional[Path] = None,
) -> ValidationResult:
    """5 content type 번들 → Layer 3 cross-reference 검증.

    Args:
        spot_bundle: ``{"feed": ..., "detail": ..., "plan": ..., "messages": ..., "review": ...}``.
            일부 키가 빠져 있으면 관련 pair 는 skip.
        spec: ContentSpec (ground truth).
        rules_dir: cross_reference.yaml 위치 override.

    Returns:
        ValidationResult(layer="cross_ref", ...).
    """
    rules = load_cross_reference_rules(rules_dir)
    return validate_cross_reference(
        spot_id=spec.spot_id,
        feed=spot_bundle.get("feed"),
        detail=spot_bundle.get("detail"),
        plan=spot_bundle.get("plan"),
        messages=spot_bundle.get("messages"),
        review=spot_bundle.get("review"),
        spec=spec,
        rules=rules,
    )


__all__ = [
    "CONTENT_TYPE_SCHEMA",
    "CONTENT_TYPE_VALIDATOR",
    "run_individual",
    "run_cross_reference",
]
