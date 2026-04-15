"""Layer 2 — SpotPlan 전용 rule validation.

구현 rule:
    1. rule_total_duration_match     — total_duration_minutes ≈ spec.schedule.duration_minutes
    2. rule_step_count_range         — 3~7 개
    3. rule_step_time_monotonic      — steps[].time 이 시간 순 (HH:MM / +N분 파싱)
    4. rule_first_step_is_intro      — 첫 step 에 "인사/도착/집결" 계열 (warn)

`plan.steps[].time` pattern 은 schema 가 이미 ``^(HH:MM|+N분)$`` 로 제한한다.
rule_step_time_monotonic 은 이 pattern 을 다시 파싱해 누적 분으로 비교한다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from pipeline.spec.models import ContentSpec
from pipeline.validators.types import Rejection, ValidationResult

DEFAULT_RULES_DIR = Path("config/rules")

_HHMM_RE = re.compile(r"^(?P<hh>[0-2]\d):(?P<mm>[0-5]\d)$")
_PLUS_RE = re.compile(r"^\+(?P<n>\d{1,3})분$")


def load_plan_rules(rules_dir: Optional[Path] = None) -> Dict[str, Any]:
    """``plan_rules.yaml`` 로드."""
    base = Path(rules_dir) if rules_dir else DEFAULT_RULES_DIR
    path = base / "plan_rules.yaml"
    data: Dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    return {"plan": data}


def _parse_time_token(token: str) -> Optional[Tuple[str, int]]:
    """HH:MM 또는 +N분 → (kind, 누적분 반환 준비값).

    반환:
        ("absolute", total_minutes) — HH:MM 기준
        ("relative", delta_minutes) — +N분 기준
        None — 파싱 실패
    """
    if not isinstance(token, str):
        return None
    m = _HHMM_RE.match(token)
    if m:
        hh = int(m.group("hh"))
        mm = int(m.group("mm"))
        return ("absolute", hh * 60 + mm)
    m = _PLUS_RE.match(token)
    if m:
        return ("relative", int(m.group("n")))
    return None


# ---------------------------------------------------------------------------
# Rule 1. total_duration ≈ spec.schedule.duration_minutes
# ---------------------------------------------------------------------------


def rule_total_duration_match(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """plan.total_duration_minutes 가 spec.schedule.duration_minutes 와 ±tol 이내인지."""
    cfg = rules.get("plan") or {}
    tol = int(cfg.get("duration_tolerance_minutes", 5))
    expected = spec.schedule.duration_minutes
    actual = payload.get("total_duration_minutes")
    if not isinstance(actual, int):
        return [
            Rejection(
                layer="rule",
                rejected_field="plan:total_duration_minutes",
                reason="total_duration_missing",
                detail="total_duration_minutes 가 int 가 아님",
                instruction=(
                    f"total_duration_minutes 를 {expected} (±{tol}) 정수로 기입하라."
                ),
            )
        ]
    if abs(actual - expected) > tol:
        return [
            Rejection(
                layer="rule",
                rejected_field="plan:total_duration_minutes",
                reason="total_duration_mismatch",
                detail=(
                    f"total_duration_minutes={actual} 이 "
                    f"spec.schedule.duration_minutes={expected} 와 차이 {abs(actual-expected)}분 "
                    f"(허용 ±{tol})"
                ),
                instruction=(
                    f"total_duration_minutes 를 정확히 {expected} 으로 맞추고, "
                    "steps 시간도 이 범위 안에서 재분배하라."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Rule 2. step 개수
# ---------------------------------------------------------------------------


def rule_step_count_range(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """steps 개수 3~7 (schema 와 중복 안전망)."""
    cfg = rules.get("plan") or {}
    lo = int(cfg.get("step_min_count", 3))
    hi = int(cfg.get("step_max_count", 7))
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return []
    n = len(steps)
    if n < lo or n > hi:
        return [
            Rejection(
                layer="rule",
                rejected_field="plan:steps",
                reason="step_count_out_of_range",
                detail=f"steps 개수 {n} 가 {lo}~{hi} 범위 밖",
                instruction=(
                    f"steps 개수를 {lo}~{hi} 개로 맞추어 다시 작성하라."
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Rule 3. step 시간 단조 증가
# ---------------------------------------------------------------------------


def rule_step_time_monotonic(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """steps[].time 이 시간 순서대로 증가하는지 (HH:MM / +N분 모두 지원)."""
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        return []
    running: Optional[int] = None  # 누적 분 (스팟 시작 기준).
    rejections: List[Rejection] = []
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        token = step.get("time")
        parsed = _parse_time_token(token) if isinstance(token, str) else None
        if parsed is None:
            rejections.append(
                Rejection(
                    layer="rule",
                    rejected_field=f"plan:steps[{idx}].time",
                    reason="step_time_unparseable",
                    detail=f"steps[{idx}].time='{token}' 파싱 실패",
                    instruction=(
                        "step time 은 HH:MM (예: 19:30) 또는 +N분 (예: +15분) 형식만 허용."
                    ),
                )
            )
            continue
        kind, value = parsed
        if kind == "absolute":
            minute = value
        else:  # relative
            base = running if running is not None else 0
            minute = base + value
        if running is not None and minute < running:
            rejections.append(
                Rejection(
                    layer="rule",
                    rejected_field=f"plan:steps[{idx}].time",
                    reason="step_time_not_monotonic",
                    detail=(
                        f"steps[{idx}].time={token} 이 직전 step 보다 이른 시각"
                    ),
                    instruction=(
                        "steps 시간을 오름차순으로 재배열하라 (HH:MM 또는 +N분 누적)."
                    ),
                )
            )
        running = minute
    return rejections


# ---------------------------------------------------------------------------
# Rule 4. 첫 step intro 성격 (warn)
# ---------------------------------------------------------------------------


def rule_first_step_is_intro(
    payload: Dict[str, Any], spec: ContentSpec, rules: Dict[str, Any]
) -> List[Rejection]:
    """첫 step 의 activity 에 '인사/도착/집결/소개' 계열 키워드 — 없으면 warn."""
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps:
        return []
    first = steps[0]
    if not isinstance(first, dict):
        return []
    activity = first.get("activity")
    if not isinstance(activity, str):
        return []
    cfg = rules.get("plan") or {}
    keywords = cfg.get("intro_step_keywords") or []
    if not keywords:
        return []
    hit = any(kw in activity for kw in keywords)
    if hit:
        return []
    return [
        Rejection(
            layer="rule",
            rejected_field="plan:steps[0].activity",
            reason="first_step_not_intro",
            detail=(
                f"첫 step activity='{activity}' 에 intro 키워드 없음 "
                f"(candidates={keywords})"
            ),
            instruction=(
                "첫 step 은 '가볍게 인사', '장소 집결 및 자기소개' 처럼 "
                "도착·인사 내용으로 시작하도록 조정하라."
            ),
            severity="warn",
        )
    ]


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

_PLAN_RULE_FUNCTIONS = (
    rule_total_duration_match,
    rule_step_count_range,
    rule_step_time_monotonic,
    rule_first_step_is_intro,
)


def validate_plan_rules(
    payload: Dict[str, Any],
    spec: ContentSpec,
    *,
    rules: Optional[Dict[str, Any]] = None,
    rules_dir: Optional[Path] = None,
) -> ValidationResult:
    """SpotPlan 4개 rule 일괄 실행."""
    if rules is None:
        rules = load_plan_rules(rules_dir)

    all_rejections: List[Rejection] = []
    rule_stats: Dict[str, int] = {}
    for fn in _PLAN_RULE_FUNCTIONS:
        out = fn(payload, spec, rules)
        rule_stats[fn.__name__] = len(out)
        all_rejections.extend(out)

    meta = {
        "rule_stats": rule_stats,
        "spec_duration": spec.schedule.duration_minutes,
    }
    return ValidationResult.from_rejections("rule", all_rejections, meta=meta)


__all__ = [
    "validate_plan_rules",
    "load_plan_rules",
    "rule_total_duration_match",
    "rule_step_count_range",
    "rule_step_time_monotonic",
    "rule_first_step_is_intro",
]
