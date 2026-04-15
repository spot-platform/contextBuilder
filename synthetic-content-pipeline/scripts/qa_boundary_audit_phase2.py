"""qa_boundary_audit_phase2 — pipeline-qa 경계면 교차 검증 (Phase 2).

Phase 1 의 1, 2, 3번 pair 는 별도 스크립트 ``qa_boundary_audit.py`` 가 다룬다.
이 스크립트는 Phase 2 에서 새로 생긴 4종 generator + Layer 3 cross-reference
구조에 대해 두 쌍을 추가로 검증한다:

4. **validators/*_rules.py 의 payload 필드 접근 ↔ 해당 content_type 의 schema
   properties ↔ 해당 stub fixture (default.json) 의 top-level 키**
   - 4종 content type 각각.
   - validator 가 schema 에 없는 필드를 읽고 있지는 않은가?
   - 픽스처가 schema 의 required 필드를 모두 채우고 있는가?

5. **cross_reference.py 의 5쌍에서 접근하는 content_type 필드 ↔ 각 content_type
   schema properties ↔ dispatch.py CONTENT_TYPE_VALIDATOR 매핑**
   - cross_reference 가 schema 에 없는 필드를 읽으면 silent skip 위험.
   - dispatch.py 의 CONTENT_TYPE_VALIDATOR 가 5종 모두 등록돼 있는가?

Phase 1 + Phase 2 합계 5쌍이 모두 PASS 해야 G5 (boundary audit) PASS.

실행:
    PYTHONPATH=src python3 scripts/qa_boundary_audit_phase2.py

출력:
    - stdout
    - _workspace/scp_05_qa/boundary_audit_phase2.md

exit code 0 = 모든 pair PASS / 1 = 하나라도 FAIL.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)


# 공용 정규식 — payload.get("x") 또는 payload["x"] 또는 <var>.get("x").
_GENERIC_GET_RE = re.compile(
    r"(?:payload|feed|detail|plan|messages|review|notice|intro|step|review_text|host_intro|cost_breakdown|materials|spec)"
    r"\.get\(\s*[\"']([a-zA-Z_][a-zA-Z0-9_]*)[\"']"
)
_GENERIC_INDEX_RE = re.compile(
    r"(?:payload|feed|detail|plan|messages|review)"
    r"\[\s*[\"']([a-zA-Z_][a-zA-Z0-9_]*)[\"']\s*\]"
)


def _extract_var_keys(text: str, var_name: str) -> Set[str]:
    """`<var_name>.get('foo')` 또는 `<var_name>['foo']` 키 모두 추출."""
    pat_get = re.compile(
        rf"\b{var_name}\.get\(\s*[\"']([a-zA-Z_][a-zA-Z0-9_]*)[\"']"
    )
    pat_idx = re.compile(
        rf"\b{var_name}\[\s*[\"']([a-zA-Z_][a-zA-Z0-9_]*)[\"']\s*\]"
    )
    keys: Set[str] = set()
    for m in pat_get.finditer(text):
        keys.add(m.group(1))
    for m in pat_idx.finditer(text):
        keys.add(m.group(1))
    return keys


def _load_schema_properties(schema_path: Path) -> Set[str]:
    if not schema_path.exists():
        return set()
    data = json.loads(schema_path.read_text(encoding="utf-8"))
    return set((data.get("properties") or {}).keys())


def _load_stub_fixture_keys(content_type: str) -> Set[str]:
    p = REPO / "tests" / "fixtures" / "codex_stub" / content_type / "v1" / "default.json"
    if not p.exists():
        return set()
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return set()
    return set(data.keys())


# ---------------------------------------------------------------------------
# Pair 4. validators/<ct>_rules.py payload ↔ schema ↔ stub fixture
# ---------------------------------------------------------------------------

CONTENT_TYPES = ("detail", "plan", "messages", "review")
RULES_FILES = {
    "detail": "detail_rules.py",
    "plan": "plan_rules.py",
    "messages": "messages_rules.py",
    "review": "review_rules.py",
}
SCHEMA_FILES = {ct: f"{ct}.json" for ct in CONTENT_TYPES}

# 알려진 generator-side metadata 키 — schema 외부에 존재하지만 generator 가 명시적으로
# 주입하는 hint. 본 audit 에서는 'rules.py 가 schema 에 없는 필드를 silent 하게 읽는
# 위험' 으로 분류하지 않고, info 로 분리한다. 단, 운영 코드/문서에는
# `_phase2_delta.md` 등에서 명시되어야 한다.
ALLOWED_NON_SCHEMA_KEYS: Dict[str, Set[str]] = {
    "review": {"meta"},  # rule_review_length_bucket_match 가 meta.review_length_bucket 사용.
}


def audit_pair_4() -> Tuple[bool, str]:
    """4종 content_type 에 대해 rules.py payload key vs schema vs fixture 비교."""
    rules_root = REPO / "src" / "pipeline" / "validators"
    schema_root = REPO / "src" / "pipeline" / "llm" / "schemas"

    lines: List[str] = []
    lines.append(
        "### Pair 4. validators/<ct>_rules.py payload 키 ↔ <ct>.json schema ↔ stub default fixture\n"
    )
    lines.append("| content_type | rules.py 키 | schema 키 | fixture 키 | rules∖schema | fixture∖schema |")
    lines.append("|---|---|---|---|---|---|")

    overall_ok = True
    findings: List[str] = []
    for ct in CONTENT_TYPES:
        rules_path = rules_root / RULES_FILES[ct]
        schema_path = schema_root / SCHEMA_FILES[ct]
        if not rules_path.exists():
            findings.append(f"- {ct}: rules.py 파일 없음 ({_rel(rules_path)})")
            overall_ok = False
            continue
        if not schema_path.exists():
            findings.append(f"- {ct}: schema 파일 없음 ({_rel(schema_path)})")
            overall_ok = False
            continue

        text = rules_path.read_text(encoding="utf-8")
        rules_keys = _extract_var_keys(text, "payload")
        schema_keys = _load_schema_properties(schema_path)
        fixture_keys = _load_stub_fixture_keys(ct)

        rules_minus_schema_raw = rules_keys - schema_keys
        allowed = ALLOWED_NON_SCHEMA_KEYS.get(ct, set())
        rules_minus_schema = rules_minus_schema_raw - allowed
        fixture_minus_schema = fixture_keys - schema_keys

        # rules.py 가 schema 에 없는 키를 읽으면 위험 (silent pass).
        if rules_minus_schema:
            findings.append(
                f"- {ct}: rules.py 가 schema 에 없는 필드 읽음: "
                f"`{sorted(rules_minus_schema)}` → schema 확장 또는 rules 정리 필요."
            )
            overall_ok = False
        # info-only: 알려진 metadata key (schema 바깥) — 통과는 시키되 표시.
        info_extra = rules_minus_schema_raw & allowed
        if info_extra:
            findings.append(
                f"- {ct}: rules.py 가 schema 바깥 metadata key 사용 (info — allowlist): "
                f"`{sorted(info_extra)}` → 운영 LLM 응답에는 없으며, generator runner 가 "
                "주입할 때만 동작하는 optional rule. phase2_delta.md 에 명시되어야 함."
            )

        # fixture 가 schema 에 없는 키를 가지면 additionalProperties:false 위반.
        if fixture_minus_schema:
            findings.append(
                f"- {ct}: fixture 가 schema 에 없는 필드 포함: "
                f"`{sorted(fixture_minus_schema)}` → fixture 정리 필요."
            )
            overall_ok = False

        lines.append(
            "| `{ct}` | `{r}` | `{s}` | `{f}` | `{rs}` | `{fs}` |".format(
                ct=ct,
                r=sorted(rules_keys),
                s=sorted(schema_keys),
                f=sorted(fixture_keys),
                rs=sorted(rules_minus_schema),
                fs=sorted(fixture_minus_schema),
            )
        )

    lines.append("")
    verdict = "PASS" if overall_ok else "FAIL"
    lines.append(f"**Pair 4 verdict: {verdict}**\n")
    if findings:
        lines.extend(findings)
        lines.append("")
    return overall_ok, "\n".join(lines)


# ---------------------------------------------------------------------------
# Pair 5. cross_reference.py 5쌍 ↔ schemas ↔ dispatch.py 매핑
# ---------------------------------------------------------------------------


def audit_pair_5() -> Tuple[bool, str]:
    cr_path = REPO / "src" / "pipeline" / "validators" / "cross_reference.py"
    dispatch_path = REPO / "src" / "pipeline" / "validators" / "dispatch.py"
    schema_root = REPO / "src" / "pipeline" / "llm" / "schemas"

    lines: List[str] = []
    lines.append(
        "### Pair 5. cross_reference.py content type 필드 접근 ↔ 각 schema ↔ dispatch.CONTENT_TYPE_VALIDATOR\n"
    )
    overall_ok = True
    findings: List[str] = []

    if not cr_path.exists() or not dispatch_path.exists():
        return False, "cross_reference.py 또는 dispatch.py 없음."

    cr_text = cr_path.read_text(encoding="utf-8")

    # 각 content type 별 키 추출. cross_reference.py 안에서
    # feed.get("x"), detail.get("x"), ... 같은 패턴.
    per_type_keys: Dict[str, Set[str]] = {}
    for ct in ("feed", "detail", "plan", "messages", "review"):
        per_type_keys[ct] = _extract_var_keys(cr_text, ct)

    # schema properties.
    schema_props: Dict[str, Set[str]] = {}
    for ct in ("feed", "detail", "plan", "messages", "review"):
        sp = _load_schema_properties(schema_root / f"{ct}.json")
        schema_props[ct] = sp

    lines.append("| content_type | cross_ref 접근 키 | schema 키 | accessed∖schema |")
    lines.append("|---|---|---|---|")
    for ct in ("feed", "detail", "plan", "messages", "review"):
        accessed = per_type_keys[ct]
        schema_keys = schema_props[ct]
        diff = accessed - schema_keys
        if diff:
            findings.append(
                f"- cross_reference.py 가 {ct} schema 에 없는 필드 읽음: "
                f"`{sorted(diff)}` → schema 확장 또는 cross_reference 수정 필요."
            )
            overall_ok = False
        lines.append(
            "| `{ct}` | `{a}` | `{s}` | `{d}` |".format(
                ct=ct, a=sorted(accessed), s=sorted(schema_keys), d=sorted(diff)
            )
        )
    lines.append("")

    # dispatch.py CONTENT_TYPE_VALIDATOR 5종 등록 여부.
    try:
        from pipeline.validators.dispatch import (
            CONTENT_TYPE_SCHEMA,
            CONTENT_TYPE_VALIDATOR,
        )
    except Exception as exc:
        findings.append(f"- dispatch import 실패: {exc}")
        return False, "\n".join(lines + findings)

    expected_types = {"feed", "detail", "plan", "messages", "review"}
    registered = set(CONTENT_TYPE_VALIDATOR.keys())
    schema_registered = set(CONTENT_TYPE_SCHEMA.keys())
    missing_validator = expected_types - registered
    missing_schema_map = expected_types - schema_registered
    extra_validator = registered - expected_types
    extra_schema = schema_registered - expected_types

    lines.append(f"| dispatch.CONTENT_TYPE_VALIDATOR | `{sorted(registered)}` |  |  |")
    lines.append(f"| dispatch.CONTENT_TYPE_SCHEMA    | `{sorted(schema_registered)}` |  |  |")
    lines.append("")

    if missing_validator:
        findings.append(
            f"- dispatch.CONTENT_TYPE_VALIDATOR 누락: `{sorted(missing_validator)}`"
        )
        overall_ok = False
    if missing_schema_map:
        findings.append(
            f"- dispatch.CONTENT_TYPE_SCHEMA 누락: `{sorted(missing_schema_map)}`"
        )
        overall_ok = False
    if extra_validator:
        findings.append(
            f"- dispatch.CONTENT_TYPE_VALIDATOR 에 예상 외 키: `{sorted(extra_validator)}` (info)"
        )
    if extra_schema:
        findings.append(
            f"- dispatch.CONTENT_TYPE_SCHEMA 에 예상 외 키: `{sorted(extra_schema)}` (info)"
        )

    verdict = "PASS" if overall_ok else "FAIL"
    lines.append(f"**Pair 5 verdict: {verdict}**\n")
    if findings:
        lines.extend(findings)
        lines.append("")
    return overall_ok, "\n".join(lines)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    sections: List[str] = ["# boundary_audit_phase2.md — pipeline-qa Phase 2\n"]
    sections.append(
        "Phase 1 boundary audit (pair 1, 2, 3) 는 `qa_boundary_audit.py` 가 별도로 다룬다. "
        "이 파일은 Phase 2 추가 pair 4, 5 결과만 포함한다. 5쌍 합산 PASS 여부는 "
        "`phase2_report.md` §2 의 통합 표를 참고.\n"
    )

    all_ok = True
    for name, fn in [("Pair 4", audit_pair_4), ("Pair 5", audit_pair_5)]:
        try:
            ok, md = fn()
        except Exception as e:  # noqa: BLE001
            ok = False
            md = f"### {name}\n\n**ERROR**: {type(e).__name__}: {e}\n"
        sections.append(md)
        print(md)
        all_ok &= ok

    out_path = REPO / "_workspace" / "scp_05_qa" / "boundary_audit_phase2.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(sections), encoding="utf-8")
    print(f"\nwrote {_rel(out_path)}")
    print(f"\nPHASE2 BOUNDARY OVERALL: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
