"""phase_peer_live_samples — Phase F live codex 샘플 수집.

peer event_log 에서 target combo 별로 1 개 spot 을 골라 feed 한 개씩만 live
codex 호출 (``SCP_LLM_MODE=live``, cache off) 하여 결과를 JSONL 로 저장.

- **feed 1 건만** 호출 (호출 수 보호): combo 당 1 call.
- 최대 10 combos (= 최대 10 live call).
- 실패 (rate limit / timeout / schema 오류) 는 fallback=True 로 기록하고 다음 combo 진행.

출력:
    _workspace/scp_05_qa/phase_peer_live_samples.jsonl

실행:
    python3 scripts/phase_peer_live_samples.py [--limit 10]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ["SCP_LLM_MODE"] = "live"
os.environ["SCP_LLM_CACHE"] = "off"  # live 재현 강제

from pipeline.generators.feed import FeedGenerator  # noqa: E402
from pipeline.llm.codex_client import call_codex  # noqa: E402
from pipeline.spec.builder import build_content_spec  # noqa: E402

EVENT_LOG = ROOT.parent / "spot-simulator" / "output" / "event_log.jsonl"
OUT_PATH = ROOT / "_workspace" / "scp_05_qa" / "phase_peer_live_samples.jsonl"

TARGETS: List[Tuple[str, str, str]] = [
    ("영어 프리토킹", "small_group", "cafe"),
    ("보드게임", "workshop", "cafe"),
    ("핸드드립", "1:1", "home"),
    ("홈베이킹", "small_group", "home"),
    ("드로잉", "workshop", "home"),
    ("러닝", "small_group", "park"),
    ("원예", "small_group", "home"),
    ("타로", "1:1", "cafe"),
    ("우쿨렐레", "small_group", "cafe"),
    ("캘리그라피", "workshop", "home"),
]


def _pick_spot(target: Tuple[str, str, str]) -> Optional[str]:
    with open(EVENT_LOG, encoding="utf-8") as f:
        for line in f:
            ev = json.loads(line)
            if ev.get("event_type") != "CREATE_TEACH_SPOT":
                continue
            p = ev.get("payload") or {}
            key = (p.get("skill"), p.get("teach_mode"), p.get("venue_type"))
            if key == target:
                return ev["spot_id"]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10, help="max combos to call live")
    args = ap.parse_args()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("", encoding="utf-8")

    targets = TARGETS[: args.limit]
    print(f"live target combos: {len(targets)}")

    gen = FeedGenerator()
    stats = {"ok": 0, "fail": 0, "skipped": 0}
    results: List[Dict[str, Any]] = []

    for combo in targets:
        sid = _pick_spot(combo)
        if sid is None:
            print(f"SKIP {combo}: no spot in event_log")
            stats["skipped"] += 1
            continue

        try:
            spec = build_content_spec(EVENT_LOG, sid, mode="peer")
        except Exception as exc:
            print(f"FAIL build_spec {combo}/{sid}: {exc}")
            stats["fail"] += 1
            continue

        variables = gen.spec_to_variables(spec, variant="primary", length_bucket="medium")
        # feed 전용 보조 변수는 spec_to_variables 가 이미 주입함.

        t0 = time.time()
        fallback = False
        error: Optional[str] = None
        payload: Optional[Dict[str, Any]] = None
        try:
            payload = call_codex(
                template_id=gen.template_id,
                variables=variables,
                schema_path=gen.schema_path,
            )
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            fallback = True
        elapsed = round(time.time() - t0, 2)

        fb = spec.fee_breakdown
        row: Dict[str, Any] = {
            "combo": "/".join(combo),
            "skill": combo[0],
            "teach_mode": combo[1],
            "venue_type": combo[2],
            "spot_id": sid,
            "region": spec.region,
            "origination_mode": spec.origination_mode,
            "fee_breakdown": (
                {
                    "peer_labor_fee": fb.peer_labor_fee,
                    "material_cost": fb.material_cost,
                    "venue_rental": fb.venue_rental,
                    "equipment_rental": fb.equipment_rental,
                    "total": fb.total,
                }
                if fb
                else None
            ),
            "expected_cost_per_person": spec.budget.expected_cost_per_person,
            "elapsed_seconds": elapsed,
            "fallback": fallback,
            "error": error,
            "payload": payload,
        }

        with OUT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False))
            fh.write("\n")
        results.append(row)

        if fallback:
            print(f"FAIL {combo} -> {sid}: {error} ({elapsed}s)")
            stats["fail"] += 1
        else:
            title = (payload or {}).get("title", "")
            price = (payload or {}).get("price_label", "")
            print(f"OK   {combo} -> {sid} | {title[:40]} | {price} ({elapsed}s)")
            stats["ok"] += 1

    print()
    print(f"=== live samples summary: ok={stats['ok']} fail={stats['fail']} skipped={stats['skipped']}")
    print(f"wrote {OUT_PATH}")
    return 0 if stats["ok"] >= 5 else 1


if __name__ == "__main__":
    sys.exit(main())
