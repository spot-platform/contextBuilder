"""qa_boundary_audit — 경계면 교차 검증 (pipeline-qa, Phase 1).

Phase 1 에서는 5쌍 중 1, 2, 3번을 구현한다:

1. ContentSpec pydantic ↔ DB models ↔ column_contract.md
   (generator 가 채우는 컬럼만 비교)
2. config/prompts/feed/v1.j2 변수 ↔ FeedGenerator.spec_to_variables 반환 키
   ↔ prompt_contract.md 공용 변수 표준
3. validators/rules.py 가 읽는 payload 필드 ↔ feed.json schema properties

실행:
    PYTHONPATH=src python3 scripts/qa_boundary_audit.py

출력:
    - stdout: 사람 읽기용 결과
    - _workspace/scp_05_qa/boundary_audit.md: markdown 표

exit code 0 = 모든 pair PASS / 1 = 하나라도 FAIL.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

# ---------------------------------------------------------------------------
# path bootstrap
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ---------------------------------------------------------------------------
# utilities
# ---------------------------------------------------------------------------


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)


def _flatten_pydantic_fields(model_cls) -> Set[str]:
    """ContentSpec 의 leaf 필드 이름 (host_persona.type 등 전개)."""
    # pydantic v2
    out: Set[str] = set()
    for name, field in model_cls.model_fields.items():
        out.add(name)
    return out


def _collect_sqlalchemy_columns(model_cls) -> Set[str]:
    return {c.name for c in model_cls.__table__.columns}


# ---------------------------------------------------------------------------
# Pair 1. ContentSpec ↔ SyntheticFeedContent ↔ column_contract.md
# ---------------------------------------------------------------------------


def _parse_contract_generator_columns(contract_path: Path, section_title: str) -> Set[str]:
    """column_contract.md 에서 '누가 채우나=content-generator-engineer' 컬럼 추출.

    section_title: 예 "## 1. `synthetic_feed_content`"
    """
    text = contract_path.read_text(encoding="utf-8")
    # section 블록 추출
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == section_title:
            start = i
            break
    if start is None:
        return set()
    block: List[str] = []
    for ln in lines[start + 1 :]:
        if ln.startswith("## "):
            break
        block.append(ln)

    columns: Set[str] = set()
    # 표 row 매칭: `| column | ... | owner | ... |`
    for ln in block:
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) < 3:
            continue
        col = cells[0].strip("` ")
        # header or separator row 스킵
        if col in ("column", ":---", "---") or re.match(r"^-+$", col):
            continue
        # "id, dataset_version, spot_id, created_at" 같은 묶음 처리
        col_names = [c.strip("` ") for c in col.split(",")]
        # owner cell 은 가변 — "content-generator-engineer" 또는 "**content-generator-engineer**" 등
        joined = ln.lower()
        if "content-generator-engineer" in joined:
            for c in col_names:
                if c:
                    columns.add(c)
    return columns


def audit_pair_1() -> Tuple[bool, str]:
    """ContentSpec ↔ synthetic_feed_content ↔ column_contract."""
    from pipeline.db.models import SyntheticFeedContent
    from pipeline.spec.models import ContentSpec

    spec_fields = _flatten_pydantic_fields(ContentSpec)
    db_columns = _collect_sqlalchemy_columns(SyntheticFeedContent)
    contract_gen_cols = _parse_contract_generator_columns(
        REPO / "_workspace" / "scp_01_infra" / "column_contract.md",
        "## 1. `synthetic_feed_content`",
    )

    # generator 가 SyntheticFeedContent 에 채워넣는 "생성기 소유" 컬럼 예상치
    # (infra / validator 소유 컬럼 제외)
    infra_owned = {
        "id",
        "dataset_version",
        "spot_id",
        "created_at",
        "quality_score",
        "validation_status",
    }
    generator_db_cols = db_columns - infra_owned

    # 결과 보고
    missing_in_contract = generator_db_cols - contract_gen_cols
    missing_in_db = contract_gen_cols - generator_db_cols

    # ContentSpec 쪽: ContentSpec 필드가 DB 에 대응되는지 정보용 비교
    # (완전 일치는 기대하지 않음 — ContentSpec 은 입력, DB 는 출력이라서)
    spec_related = {"spot_id", "region", "category", "host_persona"}
    spec_missing_in_db_note = sorted(spec_related - db_columns)

    lines: List[str] = []
    lines.append("### Pair 1. ContentSpec ↔ DB(synthetic_feed_content) ↔ column_contract.md\n")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---|")
    lines.append(f"| ContentSpec top-level fields | `{sorted(spec_fields)}` |")
    lines.append(f"| DB columns (all) | `{sorted(db_columns)}` |")
    lines.append(f"| DB columns (generator-owned set) | `{sorted(generator_db_cols)}` |")
    lines.append(f"| contract.md generator columns | `{sorted(contract_gen_cols)}` |")
    lines.append(f"| generator_db_cols - contract_cols (contract 누락 후보) | `{sorted(missing_in_contract)}` |")
    lines.append(f"| contract_cols - generator_db_cols (DB 누락 후보) | `{sorted(missing_in_db)}` |")
    lines.append(f"| (info) ContentSpec 필드 중 DB 에 직접 컬럼 없음 | `{spec_missing_in_db_note}` |")
    lines.append("")

    # pass 조건: contract ↔ db 양쪽 set difference 가 공집합
    ok = len(missing_in_contract) == 0 and len(missing_in_db) == 0
    verdict = "PASS" if ok else "FAIL"
    lines.append(f"**Pair 1 verdict: {verdict}**\n")
    if not ok:
        if missing_in_contract:
            lines.append(
                f"- DB 에는 있는데 contract 에 generator owner 로 기재되지 않은 컬럼: "
                f"`{sorted(missing_in_contract)}` → infra 또는 generator 담당 재확인."
            )
        if missing_in_db:
            lines.append(
                f"- contract 에는 있는데 DB 컬럼에 없는 이름: "
                f"`{sorted(missing_in_db)}` → 오타/리네임 의심. "
            )
        lines.append("")
    return ok, "\n".join(lines)


# ---------------------------------------------------------------------------
# Pair 2. Jinja2 template variables ↔ spec_to_variables 반환 ↔ prompt_contract.md
# ---------------------------------------------------------------------------


def _parse_prompt_contract_variables(contract_path: Path) -> Set[str]:
    """prompt_contract.md §2 블록 내 '| `var_name` | 타입 | ...' 표에서 var 이름만 추출.

    §2 (공용 변수 표준) 블록만 대상으로 한다 — §1 (Jinja2 env 옵션) 의
    autoescape / keep_trailing_newline / undefined 가 같은 표 형식이라
    전문 파싱 시 변수로 오인된다.
    """
    text = contract_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    # §2 ... ---  (다음 `## ` 블록 시작 전까지)
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("## 2."):
            start = i + 1
            break
    if start is None:
        return set()
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].strip().startswith("## ") and not lines[j].strip().startswith("## 2."):
            end = j
            break
    block = lines[start:end]
    pat = re.compile(r"^\|\s*`([a-zA-Z_][a-zA-Z0-9_]*)`\s*\|")
    out: Set[str] = set()
    for ln in block:
        m = pat.match(ln.strip())
        if m:
            out.add(m.group(1))
    # 내부 sub-key 블랙리스트 (host_persona 내부 type/tone 등)
    blacklist = {
        "type",
        "tone",
        "communication_style",
        "actual_participants",
        "no_show_count",
        "duration_actual_minutes",
        "issues",
        "overall_sentiment",
        "indoor",
        "beginner_friendly",
        "supporter_required",
    }
    return {v for v in out if v not in blacklist}


def _extract_j2_variables(template_path: Path) -> Set[str]:
    """jinja2 meta 로 undeclared variables 추출."""
    from jinja2 import Environment, FileSystemLoader, meta

    env = Environment(loader=FileSystemLoader(str(template_path.parent)))
    source = template_path.read_text(encoding="utf-8")
    ast = env.parse(source)
    return meta.find_undeclared_variables(ast)


def audit_pair_2() -> Tuple[bool, str]:
    """feed/v1.j2 ↔ FeedGenerator.spec_to_variables ↔ prompt_contract."""
    from pipeline.generators.feed import FeedGenerator
    from pipeline.spec.models import (
        ActivityConstraints,
        Budget,
        ContentSpec,
        HostPersona,
        Participants,
        Schedule,
    )

    tmpl_path = REPO / "config" / "prompts" / "feed" / "v1.j2"
    tmpl_vars = _extract_j2_variables(tmpl_path)

    # sample spec 으로 실제 반환 키 수집
    sample_spec = ContentSpec(
        spot_id="AUDIT",
        region="수원시 연무동",
        category="food",
        host_persona=HostPersona(type="supporter_teacher", tone="친절", communication_style="가벼움"),
        participants=Participants(expected_count=4, persona_mix=[]),
        schedule=Schedule(date="2026-04-18", start_time="19:00", duration_minutes=120),
        budget=Budget(price_band=2, expected_cost_per_person=18000),
        activity_constraints=ActivityConstraints(),
        plan_outline=["인사", "식사", "마무리"],
        activity_result=None,
    )
    gen = FeedGenerator()
    gen_vars = set(
        gen.spec_to_variables(sample_spec, variant="primary", length_bucket="medium").keys()
    )

    contract_vars = _parse_prompt_contract_variables(
        REPO / "_workspace" / "scp_02_codex" / "prompt_contract.md"
    )

    # 템플릿이 요구하는 변수 중 generator 가 제공 안 함
    template_requires_missing_from_gen = tmpl_vars - gen_vars - {"previous_rejections"}
    # generator 가 보내지만 표준 문서에 없는 키
    gen_extra_over_contract = gen_vars - contract_vars
    # 표준 문서에만 있고 generator 가 안 채우는 키
    contract_missing_from_gen = contract_vars - gen_vars

    # generator extra 중 feed 전용 보조 hint 는 허용 (prompt_contract.md §2 에
    # 공통이 아님, feed 템플릿이 선언해 사용): tone_examples, price_label_hint,
    # time_label_hint, supporter_label_hint
    feed_specific_allowed = {
        "tone_examples",
        "price_label_hint",
        "time_label_hint",
        "supporter_label_hint",
    }
    gen_extra_over_contract -= feed_specific_allowed

    lines: List[str] = []
    lines.append("### Pair 2. feed/v1.j2 ↔ spec_to_variables ↔ prompt_contract.md\n")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---|")
    lines.append(f"| Jinja2 템플릿 변수 | `{sorted(tmpl_vars)}` |")
    lines.append(f"| spec_to_variables 반환 키 | `{sorted(gen_vars)}` |")
    lines.append(f"| prompt_contract.md 공용 변수 | `{sorted(contract_vars)}` |")
    lines.append(f"| 템플릿 요구 - generator 제공 (누락 후보) | `{sorted(template_requires_missing_from_gen)}` |")
    lines.append(f"| generator 반환 - contract 표준 (확장 후보, feed-specific 제외) | `{sorted(gen_extra_over_contract)}` |")
    lines.append(f"| contract - generator (generator 누락 후보) | `{sorted(contract_missing_from_gen)}` |")
    lines.append("")

    ok = (
        len(template_requires_missing_from_gen) == 0
        and len(contract_missing_from_gen) == 0
    )
    verdict = "PASS" if ok else "FAIL"
    lines.append(f"**Pair 2 verdict: {verdict}**\n")
    if not ok:
        if template_requires_missing_from_gen:
            lines.append(
                f"- feed/v1.j2 가 쓰는 변수 중 spec_to_variables 에 없음: "
                f"`{sorted(template_requires_missing_from_gen)}` → generator 수정 필요."
            )
        if contract_missing_from_gen:
            lines.append(
                f"- prompt_contract.md 에 선언된 공용 변수인데 spec_to_variables 에 없음: "
                f"`{sorted(contract_missing_from_gen)}` → generator 누락."
            )
        lines.append("")
    return ok, "\n".join(lines)


# ---------------------------------------------------------------------------
# Pair 3. validators/rules.py payload 접근 ↔ feed.json schema properties
# ---------------------------------------------------------------------------


_PAYLOAD_ACCESS_RE = re.compile(
    r"payload\.get\(\s*[\"']([a-zA-Z_][a-zA-Z0-9_]*)[\"']|"  # payload.get("x")
    r"payload\[\s*[\"']([a-zA-Z_][a-zA-Z0-9_]*)[\"']\s*\]"  # payload["x"]
)


def _scan_rules_payload_keys(rules_py: Path) -> Set[str]:
    text = rules_py.read_text(encoding="utf-8")
    # 직접 접근 외에도 _payload_text_blob 내 literal 리스트 내 키도 포함
    keys: Set[str] = set()
    for m in _PAYLOAD_ACCESS_RE.finditer(text):
        keys.add(m.group(1) or m.group(2))
    # _payload_text_blob 안에서 튜플 리터럴 "title", "summary", ... 읽기
    blob_match = re.search(
        r"def _payload_text_blob.*?return\s", text, re.DOTALL
    )
    if blob_match:
        blob_text = blob_match.group(0)
        for m in re.finditer(r"[\"']([a-z_][a-z0-9_]*)[\"']", blob_text):
            keys.add(m.group(1))
    return keys


def audit_pair_3() -> Tuple[bool, str]:
    """rules.py payload 접근 ↔ feed.json schema properties."""
    rules_py = REPO / "src" / "pipeline" / "validators" / "rules.py"
    schema_path = REPO / "src" / "pipeline" / "llm" / "schemas" / "feed.json"

    accessed = _scan_rules_payload_keys(rules_py)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema_props = set((schema.get("properties") or {}).keys())

    # rules.py 가 읽지만 schema 에 없는 키 = validator 가 존재하지 않는 필드 참조
    not_in_schema = accessed - schema_props
    # false positive filter: 'title'/'summary'/'tags' 등은 모두 schema 에 있어야 정상
    # 제외할 공백 키워드 없음

    lines: List[str] = []
    lines.append("### Pair 3. validators/rules.py payload 접근 ↔ feed.json schema properties\n")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---|")
    lines.append(f"| rules.py 가 읽는 payload 키 | `{sorted(accessed)}` |")
    lines.append(f"| feed.json schema properties | `{sorted(schema_props)}` |")
    lines.append(f"| rules.py → schema 에 없는 키 (경고) | `{sorted(not_in_schema)}` |")
    lines.append("")

    ok = len(not_in_schema) == 0
    verdict = "PASS" if ok else "FAIL"
    lines.append(f"**Pair 3 verdict: {verdict}**\n")
    if not ok:
        lines.append(
            f"- validator 가 schema 에 없는 필드 `{sorted(not_in_schema)}` 를 읽음. "
            "schema 확장 또는 rules.py 정리 필요."
        )
        lines.append("")
    return ok, "\n".join(lines)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    sections: List[str] = ["# boundary_audit.md — pipeline-qa Phase 1\n"]
    all_ok = True

    for name, fn in [
        ("Pair 1", audit_pair_1),
        ("Pair 2", audit_pair_2),
        ("Pair 3", audit_pair_3),
    ]:
        try:
            ok, md = fn()
        except Exception as e:  # noqa: BLE001
            ok = False
            md = f"### {name}\n\n**ERROR**: {type(e).__name__}: {e}\n"
        sections.append(md)
        print(md)
        all_ok &= ok

    out_path = REPO / "_workspace" / "scp_05_qa" / "boundary_audit.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(sections), encoding="utf-8")
    print(f"\nwrote {_rel(out_path)}")
    print(f"\nOVERALL: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
