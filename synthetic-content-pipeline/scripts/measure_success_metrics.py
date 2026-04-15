"""§14 성공 지표 측정 스크립트.

7 개 지표:

    1. 1차 승인률          ≥ 0.70   retry_count==0 & classification=='approved'
    2. 최종 승인률          ≥ 0.95   classification ∈ {'approved','conditional'}
    3. 평균 quality_score   ≥ 0.80
    4. 배치 내 diversity    ≤ 0.60   (mean(1.0 - diversity_score))
    5. 스팟당 LLM 호출      ≤ 15
    6. 스팟당 소요 시간      ≤ 30 s
    7. Critic 비율          ≤ 0.20   (sum(critic_calls) / sum(total_calls))

사용
----
    # stub 모드: 인-프로세스로 7 개 goldens 전부 실행 (빠름).
    python scripts/measure_success_metrics.py --mode stub \
        --out _workspace/scp_05_qa/phase3_metrics.md

    # live 모드: data/goldens/_results/phase3_e2e.jsonl 을 읽어 통계 산출.
    python scripts/measure_success_metrics.py --mode live \
        --jsonl data/goldens/_results/phase3_e2e.jsonl \
        --out _workspace/scp_05_qa/phase3_metrics.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

# src/ 경로 주입
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@dataclass
class _SpotMetric:
    spot_id: str
    contents: List[Dict[str, Any]] = field(default_factory=list)
    llm_calls_total: int = 0
    critic_calls: int = 0
    elapsed_seconds: float = 0.0
    retry_count_total: int = 0
    cross_ref_ok: bool = True

    def to_jsonl_row(self) -> Dict[str, Any]:
        return {
            "spot_id": self.spot_id,
            "contents": self.contents,
            "llm_calls_total": self.llm_calls_total,
            "critic_calls": self.critic_calls,
            "elapsed_seconds": self.elapsed_seconds,
            "retry_count_total": self.retry_count_total,
            "cross_ref_ok": self.cross_ref_ok,
        }


def _spot_metric_from_result(result: Any) -> _SpotMetric:
    """SpotProcessResult → _SpotMetric (jsonl 직렬화 가능 형태)."""
    contents: List[Dict[str, Any]] = []
    critic_calls = 0
    for ct, cpr in result.contents.items():
        diversity_scores = cpr.layer_results.get("diversity") or {}
        # candidates_meta 의 best 를 채택
        best_meta = None
        if cpr.candidates_meta:
            best_meta = max(
                cpr.candidates_meta,
                key=lambda m: m.get("quality_score", -1),
            )
        contents.append(
            {
                "content_type": ct,
                "classification": cpr.classification,
                "quality_score": float(cpr.quality_score),
                "critic_used": bool(cpr.critic_used),
                "critic_sample_reason": cpr.critic_sample_reason or "",
                "retry_count": int(best_meta.get("retry_count", 0)) if best_meta else 0,
                "diversity_scores": diversity_scores,
                "best_diversity_score": float(best_meta.get("diversity_score", 0.0))
                if best_meta and best_meta.get("diversity_score") is not None
                else 0.0,
            }
        )
        if cpr.critic_used:
            critic_calls += 1
    cross_ok = result.cross_ref_result.ok if result.cross_ref_result else True
    return _SpotMetric(
        spot_id=result.spot_id,
        contents=contents,
        llm_calls_total=int(result.llm_calls_total),
        critic_calls=critic_calls,
        elapsed_seconds=float(result.elapsed_seconds),
        retry_count_total=int(result.retry_count_total),
        cross_ref_ok=cross_ok,
    )


def _load_jsonl(path: Path) -> List[_SpotMetric]:
    rows: List[_SpotMetric] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append(
                _SpotMetric(
                    spot_id=row.get("spot_id", ""),
                    contents=row.get("contents", []),
                    llm_calls_total=int(row.get("llm_calls_total", 0)),
                    critic_calls=int(row.get("critic_calls", 0)),
                    elapsed_seconds=float(row.get("elapsed_seconds", 0.0)),
                    retry_count_total=int(row.get("retry_count_total", 0)),
                    cross_ref_ok=bool(row.get("cross_ref_ok", True)),
                )
            )
    return rows


def _run_stub(spec_paths: Iterable[Path]) -> List[_SpotMetric]:
    """인-프로세스 stub 실행. 각 spec → process_spot_full → _SpotMetric."""
    os.environ.setdefault("SCP_LLM_MODE", "stub")
    from pipeline.loop.generate_validate_retry import process_spot_full
    from pipeline.spec.models import ContentSpec

    out: List[_SpotMetric] = []
    for sp in spec_paths:
        spec_data = json.loads(sp.read_text(encoding="utf-8"))
        spec = ContentSpec.model_validate(spec_data)
        t0 = time.time()
        result = process_spot_full(spec.spot_id, spec)
        # process_spot_full 는 metrics.end_spot 으로 elapsed 를 채우지만,
        # stub 환경에서 너무 빠르면 0 으로 측정될 수 있어 wall-clock 을 보정.
        if not result.elapsed_seconds:
            result.elapsed_seconds = round(time.time() - t0, 4)
        out.append(_spot_metric_from_result(result))
    return out


@dataclass
class _MetricRow:
    name: str
    target: str
    value: float
    passed: bool
    detail: str = ""


def _compute_metrics(spots: List[_SpotMetric]) -> Dict[str, Any]:
    if not spots:
        return {"rows": [], "raw": [], "total_spots": 0, "total_contents": 0}

    all_contents: List[Dict[str, Any]] = []
    for s in spots:
        all_contents.extend(s.contents)

    n_contents = len(all_contents)
    n_spots = len(spots)

    first_pass = sum(
        1
        for c in all_contents
        if int(c.get("retry_count", 0)) == 0 and c.get("classification") == "approved"
    )
    final_pass = sum(
        1
        for c in all_contents
        if c.get("classification") in ("approved", "conditional")
    )
    quality_mean = (
        sum(float(c.get("quality_score", 0.0)) for c in all_contents) / n_contents
        if n_contents
        else 0.0
    )
    # 배치 내 diversity = mean(1.0 - best_diversity_score) — 작을수록 좋음
    div_sum = sum(
        1.0 - float(c.get("best_diversity_score", 0.0)) for c in all_contents
    )
    div_mean = div_sum / n_contents if n_contents else 0.0

    calls_mean = sum(s.llm_calls_total for s in spots) / n_spots
    elapsed_mean = sum(s.elapsed_seconds for s in spots) / n_spots

    total_calls = sum(s.llm_calls_total for s in spots)
    total_critic = sum(s.critic_calls for s in spots)
    critic_ratio = total_critic / total_calls if total_calls else 0.0

    rows = [
        _MetricRow(
            name="1차 승인률 (no-retry approved)",
            target="≥ 0.70",
            value=round(first_pass / n_contents, 4) if n_contents else 0.0,
            passed=(n_contents > 0 and first_pass / n_contents >= 0.70),
            detail=f"{first_pass}/{n_contents}",
        ),
        _MetricRow(
            name="최종 승인률 (approved+conditional)",
            target="≥ 0.95",
            value=round(final_pass / n_contents, 4) if n_contents else 0.0,
            passed=(n_contents > 0 and final_pass / n_contents >= 0.95),
            detail=f"{final_pass}/{n_contents}",
        ),
        _MetricRow(
            name="평균 quality_score",
            target="≥ 0.80",
            value=round(quality_mean, 4),
            passed=(quality_mean >= 0.80),
            detail=f"n={n_contents}",
        ),
        _MetricRow(
            name="배치 내 diversity (1 - score 평균)",
            target="≤ 0.60",
            value=round(div_mean, 4),
            passed=(div_mean <= 0.60),
            detail="작을수록 좋음",
        ),
        _MetricRow(
            name="스팟당 LLM 호출",
            target="≤ 15",
            value=round(calls_mean, 3),
            passed=(calls_mean <= 15),
            detail=f"total={total_calls}",
        ),
        _MetricRow(
            name="스팟당 소요 시간 (s)",
            target="≤ 30",
            value=round(elapsed_mean, 4),
            passed=(elapsed_mean <= 30),
            detail=f"n={n_spots}",
        ),
        _MetricRow(
            name="Critic 비율",
            target="≤ 0.20",
            value=round(critic_ratio, 4),
            passed=(critic_ratio <= 0.20),
            detail=f"{total_critic}/{total_calls}",
        ),
    ]

    return {
        "rows": rows,
        "raw": [s.to_jsonl_row() for s in spots],
        "total_spots": n_spots,
        "total_contents": n_contents,
    }


def _format_markdown(
    summary: Dict[str, Any], mode: str, source_label: str
) -> str:
    rows: List[_MetricRow] = summary.get("rows", [])
    n_spots = summary.get("total_spots", 0)
    n_contents = summary.get("total_contents", 0)

    lines: List[str] = []
    lines.append("# Phase 3 — §14 Success Metrics")
    lines.append("")
    lines.append(f"- mode: **{mode}**")
    lines.append(f"- source: `{source_label}`")
    lines.append(f"- spots: {n_spots}")
    lines.append(f"- contents (rows): {n_contents}")
    lines.append("")
    if not rows:
        lines.append("⚠️  표본이 비어 있습니다. (jsonl 미로딩 또는 stub 실행 실패)")
        return "\n".join(lines) + "\n"

    lines.append("## 지표 표")
    lines.append("")
    lines.append("| # | 지표 | 목표 | 측정값 | PASS |")
    lines.append("|---|------|------|-------|------|")
    for i, r in enumerate(rows, 1):
        check = "✅" if r.passed else "❌"
        lines.append(
            f"| {i} | {r.name} | {r.target} | {r.value} ({r.detail}) | {check} |"
        )
    n_pass = sum(1 for r in rows if r.passed)
    lines.append("")
    lines.append(f"**합계**: {n_pass}/{len(rows)} 통과")
    lines.append("")

    # raw breakdown per spot
    lines.append("## Raw Per-Spot Breakdown")
    lines.append("")
    raw = summary.get("raw", [])
    lines.append("| spot_id | calls | critic | retry | elapsed | cross_ref | per-content classification |")
    lines.append("|---------|-------|--------|-------|---------|-----------|----------------------------|")
    for r in raw:
        per_ct = ", ".join(
            f"{c.get('content_type')}:{c.get('classification')}" for c in r.get("contents", [])
        )
        lines.append(
            f"| {r.get('spot_id')} | {r.get('llm_calls_total')} | {r.get('critic_calls')} | "
            f"{r.get('retry_count_total')} | {r.get('elapsed_seconds')} | "
            f"{'ok' if r.get('cross_ref_ok') else 'FAIL'} | {per_ct} |"
        )
    lines.append("")

    # caveat
    lines.append("## Caveats")
    lines.append("")
    lines.append(
        f"- 표본 크기 n={n_spots} 스팟, contents={n_contents} 행. "
        "통계적 의미가 작으므로 §14 합격선은 *경향성* 으로 해석한다."
    )
    lines.append(
        "- stub 모드는 critic/generator 모두 픽스처 default.json 으로 동작하므로 "
        "diversity 가 매우 낮게 (≈ 동일 텍스트) 측정될 수 있다 — 이 지표는 live 결과로 재측정 권장."
    )
    lines.append(
        "- LLM 호출 카운트는 process_spot_full 내부 metrics.record_call 으로 잡힌다. "
        "generator 의 내부 retry 호출은 record_call 에 직접 잡히지 않으므로 "
        "live 모드에서는 실제 codex exec 호출 수보다 작게 측정될 수 있다."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def _write_jsonl(path: Path, spots: List[_SpotMetric]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for s in spots:
            fh.write(json.dumps(s.to_jsonl_row(), ensure_ascii=False))
            fh.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--mode",
        choices=("stub", "live"),
        default="stub",
        help="stub: 인-프로세스 7 goldens 실행 / live: jsonl 결과 읽기",
    )
    p.add_argument(
        "--jsonl",
        type=Path,
        default=_REPO_ROOT / "data" / "goldens" / "_results" / "phase3_e2e.jsonl",
    )
    p.add_argument(
        "--specs-dir",
        type=Path,
        default=_REPO_ROOT / "data" / "goldens" / "specs",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=_REPO_ROOT / "_workspace" / "scp_05_qa" / "phase3_metrics.md",
    )
    p.add_argument(
        "--write-jsonl",
        action="store_true",
        help="stub 모드에서 phase3_e2e.jsonl 에도 결과 기록 (live 와 별도).",
    )
    args = p.parse_args(argv)

    if args.mode == "stub":
        spec_paths = sorted(args.specs_dir.glob("*.json"))
        spots = _run_stub(spec_paths)
        source_label = f"stub × {len(spec_paths)} goldens (in-process)"
        if args.write_jsonl:
            stub_jsonl = (
                _REPO_ROOT
                / "data"
                / "goldens"
                / "_results"
                / "phase3_e2e_stub.jsonl"
            )
            _write_jsonl(stub_jsonl, spots)
            print(f"wrote stub jsonl → {stub_jsonl}")
    else:
        spots = _load_jsonl(args.jsonl)
        source_label = str(args.jsonl)

    summary = _compute_metrics(spots)
    md = _format_markdown(summary, args.mode, source_label)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(md, encoding="utf-8")
    print(f"wrote metrics → {args.out}")
    print(f"spots={summary['total_spots']} contents={summary['total_contents']}")
    n_pass = sum(1 for r in summary["rows"] if r.passed)
    print(f"§14 metrics PASS: {n_pass}/{len(summary['rows'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
