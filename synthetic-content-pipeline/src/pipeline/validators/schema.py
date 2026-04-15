"""Layer 1 — Schema Validation.

플랜 §5 Layer 1 표 전부를 구현한다.

검증 축은 두 가지다:

1. **JSON Schema** (``codex-bridge`` 가 작성한 ``feed.json``):
   - jsonschema.Draft7Validator 로 모든 에러 수집.
   - 각 에러는 ``Rejection(layer="schema", reason="schema_violation")`` 로 변환.

2. **추가 길이/문장 규칙** — schema만으로 부족한 케이스:
   - 제목 12~40자 (schema와 중복 안전망)
   - summary 1~2 문장 (마침표/물음표/느낌표 개수로 판정)
   - price_label, region_label, time_label, supporter_label null/empty 금지
   - 필수 필드 존재

Phase 1 이후 다른 content_type (detail / message / review) schema가 추가되면
``validate_*_schema`` 함수를 추가만 하면 된다 — feed 전용 규칙은 이 모듈 안에
함수 단위로 격리되어 있다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import jsonschema
from jsonschema import Draft7Validator

from pipeline.validators.types import Rejection, ValidationResult

# ---------------------------------------------------------------------------
# 상수 — 플랜 §5 Layer 1 표
# ---------------------------------------------------------------------------

#: feed title 길이 (플랜 §3 Feed Preview 표).
FEED_TITLE_MIN_LEN = 12
FEED_TITLE_MAX_LEN = 40

#: summary 문장 수 (플랜 §3: "1~2문장").
FEED_SUMMARY_MIN_SENTENCES = 1
FEED_SUMMARY_MAX_SENTENCES = 2

#: 문장 분리에 사용할 종결 부호 (한국어/영어 공통).
_SENTENCE_END_RE = re.compile(r"[.!?。！？]+")

#: feed 필수 필드 — schema와 중복 안전망. schema 파일이 비어있을 때도 동작 보장.
FEED_REQUIRED_FIELDS = (
    "title",
    "summary",
    "tags",
    "price_label",
    "region_label",
    "time_label",
    "status",
    "supporter_label",
)

#: 비어있으면 안 되는 필드 (null / "" / [] 금지).
FEED_NON_EMPTY_FIELDS = (
    "title",
    "summary",
    "price_label",
    "region_label",
    "time_label",
    "status",
    "supporter_label",
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _format_field_path(absolute_path: Any) -> str:
    """jsonschema 의 absolute_path (deque) 를 ``tags[0]`` 같은 문자열로 변환."""
    parts: List[str] = []
    for token in absolute_path:
        if isinstance(token, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{token}]"
            else:
                parts.append(f"[{token}]")
        else:
            parts.append(str(token))
    return ".".join(parts) if parts else "__root__"


def _count_sentences(text: str) -> int:
    """종결 부호로 문장 수 추정. 종결부호 없으면 1로 간주."""
    if not text or not text.strip():
        return 0
    matches = _SENTENCE_END_RE.findall(text)
    if not matches:
        return 1
    return len(matches)


def _load_schema(schema_path: Path) -> Optional[Dict[str, Any]]:
    """schema 파일 로드. 파일 없으면 None (fallback 모드)."""
    if not schema_path.exists():
        return None
    with schema_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# JSON Schema 검증
# ---------------------------------------------------------------------------


def _schema_errors_to_rejections(
    payload: Dict[str, Any], schema: Dict[str, Any]
) -> List[Rejection]:
    """jsonschema iter_errors → Rejection 목록."""
    rejections: List[Rejection] = []
    validator = Draft7Validator(schema)
    for err in validator.iter_errors(payload):
        field_path = _format_field_path(err.absolute_path)
        rejections.append(
            Rejection(
                layer="schema",
                rejected_field=field_path or "__schema__",
                reason="schema_violation",
                detail=err.message,
                instruction=(
                    f"feed.json schema에 맞춰 '{field_path}' 필드를 수정하라. "
                    f"schema validator: {err.validator}={err.validator_value}"
                ),
                severity="reject",
            )
        )
    return rejections


# ---------------------------------------------------------------------------
# 추가 길이/문장 규칙
# ---------------------------------------------------------------------------


def _check_required_fields(payload: Dict[str, Any]) -> List[Rejection]:
    """필수 필드 존재 + non-empty 체크."""
    rejections: List[Rejection] = []
    for field in FEED_REQUIRED_FIELDS:
        if field not in payload:
            rejections.append(
                Rejection(
                    layer="schema",
                    rejected_field=field,
                    reason="missing_required_field",
                    detail=f"필수 필드 '{field}' 가 payload에 없음",
                    instruction=f"'{field}' 필드를 포함해서 다시 생성하라.",
                )
            )
    for field in FEED_NON_EMPTY_FIELDS:
        if field in payload:
            value = payload[field]
            is_empty = value is None or (isinstance(value, str) and not value.strip())
            if is_empty:
                rejections.append(
                    Rejection(
                        layer="schema",
                        rejected_field=field,
                        reason="empty_required_field",
                        detail=f"'{field}' 가 null/빈 문자열",
                        instruction=f"'{field}' 에 의미 있는 값을 채워라 (null/빈 문자열 금지).",
                    )
                )
    return rejections


def _check_title_length(payload: Dict[str, Any]) -> List[Rejection]:
    """제목 12~40자."""
    title = payload.get("title")
    if not isinstance(title, str):
        return []
    n = len(title)
    if n < FEED_TITLE_MIN_LEN:
        return [
            Rejection(
                layer="schema",
                rejected_field="title",
                reason="title_too_short",
                detail=f"제목 길이 {n}자 < {FEED_TITLE_MIN_LEN}자",
                instruction=(
                    f"제목을 {FEED_TITLE_MIN_LEN}~{FEED_TITLE_MAX_LEN}자 사이로 다시 작성하라. "
                    "지역명·인원·활동을 포함하면 길이를 맞추기 쉽다."
                ),
            )
        ]
    if n > FEED_TITLE_MAX_LEN:
        return [
            Rejection(
                layer="schema",
                rejected_field="title",
                reason="title_too_long",
                detail=f"제목 길이 {n}자 > {FEED_TITLE_MAX_LEN}자",
                instruction=(
                    f"제목을 {FEED_TITLE_MIN_LEN}~{FEED_TITLE_MAX_LEN}자 사이로 줄여라. "
                    "수식어·중복 표현을 제거하라."
                ),
            )
        ]
    return []


def _check_summary_sentences(payload: Dict[str, Any]) -> List[Rejection]:
    """summary 1~2 문장 (종결부호 카운트)."""
    summary = payload.get("summary")
    if not isinstance(summary, str):
        return []
    n = _count_sentences(summary)
    if n < FEED_SUMMARY_MIN_SENTENCES:
        return [
            Rejection(
                layer="schema",
                rejected_field="summary",
                reason="summary_no_sentence",
                detail="summary에 종결 부호가 없거나 비어있음",
                instruction="summary를 마침표로 끝나는 1~2 문장으로 다시 작성하라.",
            )
        ]
    if n > FEED_SUMMARY_MAX_SENTENCES:
        return [
            Rejection(
                layer="schema",
                rejected_field="summary",
                reason="summary_too_many_sentences",
                detail=f"summary 문장 수 {n} > {FEED_SUMMARY_MAX_SENTENCES}",
                instruction=(
                    f"summary를 {FEED_SUMMARY_MIN_SENTENCES}~{FEED_SUMMARY_MAX_SENTENCES}문장 "
                    "이내로 압축하라."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_feed_schema(
    payload: Dict[str, Any],
    schema_path: Path,
) -> ValidationResult:
    """feed 콘텐츠의 Layer 1 검증.

    Args:
        payload: feed JSON dict (LLM 출력 그대로).
        schema_path: ``src/pipeline/llm/schemas/feed.json`` 경로.

    Returns:
        ValidationResult — 모든 schema/길이/문장 위반을 모은 결과.

    Notes:
        - schema_path 가 없으면 jsonschema 검증을 건너뛰고
          내장 길이/문장 규칙으로만 판정한다 (codex-bridge 미작성 fallback).
        - meta["used_schema"] 로 schema 적용 여부를 기록한다.
    """
    rejections: List[Rejection] = []
    meta: Dict[str, Any] = {"schema_path": str(schema_path)}

    schema = _load_schema(schema_path)
    if schema is None:
        meta["used_schema"] = False
        meta["schema_warning"] = "schema_file_not_found_fallback_to_internal_rules_only"
    else:
        meta["used_schema"] = True
        rejections.extend(_schema_errors_to_rejections(payload, schema))

    # 추가 안전망 — schema와 중복돼도 OK (schema 미존재 fallback 시 유일한 검증선).
    rejections.extend(_check_required_fields(payload))
    rejections.extend(_check_title_length(payload))
    rejections.extend(_check_summary_sentences(payload))

    return ValidationResult.from_rejections("schema", rejections, meta=meta)


# ---------------------------------------------------------------------------
# Phase 2 — detail / plan / messages / review schema validators
# ---------------------------------------------------------------------------
#
# feed 와 달리 detail/plan/messages/review 는 "schema 로 못 잡는 문장 수·내용
# 일관성" 은 Layer 2 rule 모듈(``detail_rules.py`` 등)이 책임진다. 이 모듈의
# Phase 2 확장은 순수하게 JSON Schema (Draft7Validator) 검증 + 필수 필드 fallback
# 만 담당한다. 기존 ``validate_feed_schema`` 와 동일한 패턴.

#: content_type → 내장 fallback 필수 필드. schema.json 이 없거나 Draft7Validator 가
#: 부팅 전이어도 최소한의 sanity check 가 동작하게 한다.
_FALLBACK_REQUIRED_FIELDS: Dict[str, tuple] = {
    "detail": (
        "title",
        "description",
        "activity_purpose",
        "progress_style",
        "materials",
        "target_audience",
        "cost_breakdown",
        "host_intro",
    ),
    "plan": ("steps", "total_duration_minutes"),
    "messages": (
        "recruiting_intro",
        "join_approval",
        "day_of_notice",
        "post_thanks",
    ),
    "review": (
        "rating",
        "review_text",
        "satisfaction_tags",
        "recommend",
        "will_rejoin",
        "sentiment",
    ),
}


def _fallback_required_rejections(
    payload: Dict[str, Any], layer_name: str
) -> List[Rejection]:
    """``_FALLBACK_REQUIRED_FIELDS`` 기반 필수 필드 존재/non-null 체크."""
    required = _FALLBACK_REQUIRED_FIELDS.get(layer_name, ())
    rejections: List[Rejection] = []
    for field in required:
        if field not in payload:
            rejections.append(
                Rejection(
                    layer="schema",
                    rejected_field=f"{layer_name}:{field}",
                    reason="missing_required_field",
                    detail=f"{layer_name} payload 에 필수 필드 '{field}' 가 없음",
                    instruction=(
                        f"{layer_name} 응답에 '{field}' 필드를 포함해서 다시 생성하라."
                    ),
                )
            )
            continue
        value = payload[field]
        # None / empty string / empty list 를 모두 걸러낸다.
        is_empty = value is None
        if isinstance(value, str) and not value.strip():
            is_empty = True
        if isinstance(value, (list, tuple)) and len(value) == 0 and field != "materials":
            # materials 만 Phase 2 schema 에서 0개 허용 (minItems=0).
            is_empty = True
        if is_empty:
            rejections.append(
                Rejection(
                    layer="schema",
                    rejected_field=f"{layer_name}:{field}",
                    reason="empty_required_field",
                    detail=f"'{field}' 가 null / 빈 문자열 / 빈 배열",
                    instruction=(
                        f"{layer_name}.{field} 에 의미 있는 값을 채워서 다시 생성하라."
                    ),
                )
            )
    return rejections


def _validate_json_schema(
    payload: Dict[str, Any],
    schema_path: Path,
    layer_name: str,
) -> ValidationResult:
    """detail/plan/messages/review 공통 Draft7 Schema validator.

    Args:
        payload: 검증 대상 LLM 응답 dict.
        schema_path: ``src/pipeline/llm/schemas/<layer_name>.json`` 경로.
        layer_name: "detail" | "plan" | "messages" | "review". rejection prefix 용.

    Returns:
        ValidationResult(layer="schema", ...). 모든 jsonschema 위반을 하나의
        Rejection 으로 변환하며, schema 파일이 없으면 내장 필수 필드 fallback
        으로 대체한다.
    """
    rejections: List[Rejection] = []
    meta: Dict[str, Any] = {
        "schema_path": str(schema_path),
        "content_type": layer_name,
    }

    schema = _load_schema(schema_path)
    if schema is None:
        meta["used_schema"] = False
        meta["schema_warning"] = "schema_file_not_found_fallback_to_internal_rules_only"
    else:
        meta["used_schema"] = True
        # feed 와 동일한 패턴: Draft7Validator.iter_errors 로 모든 에러 수집.
        validator = Draft7Validator(schema)
        for err in validator.iter_errors(payload):
            field_path = _format_field_path(err.absolute_path)
            rejections.append(
                Rejection(
                    layer="schema",
                    rejected_field=f"{layer_name}:{field_path}" if field_path != "__root__" else f"{layer_name}:__schema__",
                    reason="schema_violation",
                    detail=err.message,
                    instruction=(
                        f"{layer_name}.json schema 에 맞춰 '{field_path}' 필드를 수정하라. "
                        f"schema validator: {err.validator}={err.validator_value}"
                    ),
                    severity="reject",
                )
            )

    # 내장 fallback (필수 필드 non-null) — schema 있든 없든 안전망으로 항상 실행.
    rejections.extend(_fallback_required_rejections(payload, layer_name))

    return ValidationResult.from_rejections("schema", rejections, meta=meta)


def validate_detail_schema(
    payload: Dict[str, Any], schema_path: Path
) -> ValidationResult:
    """SpotDetail Layer 1 JSON Schema 검증."""
    return _validate_json_schema(payload, schema_path, "detail")


def validate_plan_schema(
    payload: Dict[str, Any], schema_path: Path
) -> ValidationResult:
    """SpotPlan Layer 1 JSON Schema 검증."""
    return _validate_json_schema(payload, schema_path, "plan")


def validate_messages_schema(
    payload: Dict[str, Any], schema_path: Path
) -> ValidationResult:
    """SpotCommunicationSnippets Layer 1 JSON Schema 검증."""
    return _validate_json_schema(payload, schema_path, "messages")


def validate_review_schema(
    payload: Dict[str, Any], schema_path: Path
) -> ValidationResult:
    """SpotReview Layer 1 JSON Schema 검증."""
    return _validate_json_schema(payload, schema_path, "review")


__all__ = [
    "validate_feed_schema",
    "validate_detail_schema",
    "validate_plan_schema",
    "validate_messages_schema",
    "validate_review_schema",
    "FEED_TITLE_MIN_LEN",
    "FEED_TITLE_MAX_LEN",
    "FEED_SUMMARY_MIN_SENTENCES",
    "FEED_SUMMARY_MAX_SENTENCES",
    "FEED_REQUIRED_FIELDS",
    "FEED_NON_EMPTY_FIELDS",
]
