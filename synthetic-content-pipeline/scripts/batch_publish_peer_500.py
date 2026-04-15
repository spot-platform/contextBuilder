"""batch_publish_peer_500 — Phase F 500 spot stub publish 배치.

peer event_log (spot-simulator/output/event_log.jsonl) 의 앞 500 CREATE_TEACH_SPOT
이벤트를 읽어 ContentSpec 을 빌드하고, SCP_LLM_MODE=stub 으로 loop + publisher
를 실행한다. in-memory SQLite 에 synthetic_* 테이블을 만들고 publish 한 뒤
§14 지표 일부 + 분포 지표를 stdout / JSON 으로 남긴다.

출력:
    _workspace/scp_05_qa/phase_peer_batch_stats.json

실행:
    python3 scripts/batch_publish_peer_500.py --limit 500
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("SCP_LLM_MODE", "stub")

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from pipeline.db.base import Base  # noqa: E402
from pipeline.db.models import (  # noqa: E402
    ContentVersionPolicy,
    SyntheticFeedContent,
    SyntheticReview,
    SyntheticSpotDetail,
    SyntheticSpotMessages,
)
from pipeline.loop.generate_validate_retry import process_spot_full  # noqa: E402
from pipeline.publish.publisher import Publisher  # noqa: E402
from pipeline.publish.versioning import VersionManager  # noqa: E402
from pipeline.spec.builder import build_content_spec  # noqa: E402

EVENT_LOG = ROOT.parent / "spot-simulator" / "output" / "event_log.jsonl"
OUT_DIR = ROOT / "_workspace" / "scp_05_qa"


def _collect_spot_ids(limit: int) -> List[str]:
    ids: List[str] = []
    with open(EVENT_LOG, encoding="utf-8") as f:
        for line in f:
            ev = json.loads(line)
            if ev.get("event_type") == "CREATE_TEACH_SPOT":
                ids.append(ev["spot_id"])
                if len(ids) >= limit:
                    break
    return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--dataset-version", default="v2_peer")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    spot_ids = _collect_spot_ids(args.limit)
    print(f"collected {len(spot_ids)} spot_ids from event_log")

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    stats: Dict[str, Any] = {
        "spots_total": 0,
        "spots_processed": 0,
        "spots_approved": 0,
        "spots_partial": 0,
        "spots_errors": 0,
        "publish_rows": {"feed": 0, "detail": 0, "plan": 0, "messages": 0, "review": 0},
        "published_feed_status": {"approved": 0, "conditional": 0},
        "per_content_classification": {
            ct: {"approved": 0, "conditional": 0, "rejected": 0}
            for ct in ("feed", "detail", "plan", "messages", "review")
        },
        "approved_first_pass": 0,
        "final_approved": 0,
        "llm_calls_per_spot": [],
        "retries_per_spot": [],
        "elapsed_per_spot": [],
        "quality_scores": [],
        "critic_used_calls": 0,
        "combo_counts": {},
        "origination_counts": {"offer": 0, "request_matched": 0},
        "fee_by_combo": {},  # "{skill}|{mode}|{venue}" -> [total_fee, ...]
        "peer_labor_by_combo": {},
        "passthrough_by_combo": {},
        "errors_sample": [],
    }

    t_start = time.time()
    with Session(engine) as session:
        vm = VersionManager(session)
        vm.create_draft(args.dataset_version)
        vm.activate(args.dataset_version)
        session.flush()
        publisher = Publisher(session, dataset_version=args.dataset_version)

        for idx, sid in enumerate(spot_ids):
            stats["spots_total"] += 1
            try:
                spec = build_content_spec(EVENT_LOG, sid, mode="peer")
            except Exception as exc:
                stats["spots_errors"] += 1
                if len(stats["errors_sample"]) < 5:
                    stats["errors_sample"].append(f"spec/{sid}: {type(exc).__name__}: {exc}")
                continue

            # combo & origination tallying
            combo_key = f"{spec.skill_topic}|{spec.teach_mode}|{spec.venue_type}"
            stats["combo_counts"][combo_key] = stats["combo_counts"].get(combo_key, 0) + 1
            stats["origination_counts"][spec.origination_mode] = (
                stats["origination_counts"].get(spec.origination_mode, 0) + 1
            )
            fb = spec.fee_breakdown
            if fb is not None:
                stats["fee_by_combo"].setdefault(combo_key, []).append(fb.total)
                stats["peer_labor_by_combo"].setdefault(combo_key, []).append(fb.peer_labor_fee)
                stats["passthrough_by_combo"].setdefault(combo_key, []).append(fb.passthrough_total)

            try:
                rng = random.Random(42 + idx)
                result = process_spot_full(sid, spec, rng=rng)
            except Exception as exc:
                stats["spots_errors"] += 1
                if len(stats["errors_sample"]) < 5:
                    stats["errors_sample"].append(
                        f"loop/{sid}: {type(exc).__name__}: {exc}"
                    )
                continue

            stats["spots_processed"] += 1
            stats["llm_calls_per_spot"].append(result.llm_calls_total)
            stats["retries_per_spot"].append(result.retry_count_total)
            stats["elapsed_per_spot"].append(result.elapsed_seconds)

            # critic_used 집계
            for cpr in result.contents.values():
                if cpr.critic_used:
                    stats["critic_used_calls"] += 1
                if cpr.quality_score > 0:
                    stats["quality_scores"].append(cpr.quality_score)
                ct = cpr.content_type
                cls = cpr.classification or "rejected"
                if ct in stats["per_content_classification"]:
                    bucket = stats["per_content_classification"][ct]
                    bucket[cls] = bucket.get(cls, 0) + 1

            # approved (first pass = 재시도 0)
            if result.approved:
                stats["final_approved"] += 1
                if result.retry_count_total == 0:
                    stats["approved_first_pass"] += 1

            try:
                pub_result = publisher.publish_spot(result)
                for ct, n in pub_result.published_rows.items():
                    stats["publish_rows"][ct] = stats["publish_rows"].get(ct, 0) + n
                # approved/conditional 집계
                feed_cpr = result.contents.get("feed")
                if feed_cpr and feed_cpr.classification in ("approved", "conditional"):
                    stats["published_feed_status"][feed_cpr.classification] = (
                        stats["published_feed_status"].get(feed_cpr.classification, 0) + 1
                    )
            except Exception as exc:
                if len(stats["errors_sample"]) < 5:
                    stats["errors_sample"].append(f"publish/{sid}: {type(exc).__name__}: {exc}")

            if (idx + 1) % 100 == 0:
                print(
                    f"  progress: {idx + 1}/{len(spot_ids)} "
                    f"(approved={stats['final_approved']} errors={stats['spots_errors']})"
                )

        session.commit()

        # DB 레벨 count 재확인
        stats["db_counts"] = {
            "feed": session.query(SyntheticFeedContent).count(),
            "detail": session.query(SyntheticSpotDetail).count(),
            "messages": session.query(SyntheticSpotMessages).count(),
            "review": session.query(SyntheticReview).count(),
        }

        # sample 3 rows
        sample_rows: List[Dict[str, Any]] = []
        for row in session.query(SyntheticFeedContent).limit(3).all():
            sample_rows.append(
                {
                    "spot_id": row.spot_id,
                    "title": (row.title or "")[:60],
                    "price_label": row.price_label,
                    "validation_status": row.validation_status,
                }
            )
        stats["sample_rows"] = sample_rows

    stats["wall_time_seconds"] = round(time.time() - t_start, 2)

    # 파생 지표
    calls = stats["llm_calls_per_spot"] or [0]
    scores = stats["quality_scores"] or [0.0]
    elapsed = stats["elapsed_per_spot"] or [0.0]
    stats["llm_calls_per_spot_mean"] = round(statistics.mean(calls), 2)
    stats["llm_calls_per_spot_max"] = max(calls)
    stats["elapsed_per_spot_mean"] = round(statistics.mean(elapsed), 4)
    stats["elapsed_per_spot_max"] = round(max(elapsed), 4)
    stats["quality_score_mean"] = round(statistics.mean(scores), 4)
    stats["quality_score_min"] = round(min(scores), 4)
    stats["quality_score_max"] = round(max(scores), 4)
    if stats["spots_processed"]:
        stats["first_pass_ratio"] = round(
            stats["approved_first_pass"] / stats["spots_processed"], 4
        )
        stats["final_approved_ratio"] = round(
            stats["final_approved"] / stats["spots_processed"], 4
        )
    else:
        stats["first_pass_ratio"] = None
        stats["final_approved_ratio"] = None

    total_calls = sum(calls)
    stats["critic_ratio"] = (
        round(stats["critic_used_calls"] / total_calls, 4) if total_calls else None
    )

    out_path = OUT_DIR / "phase_peer_batch_stats.json"
    out_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {out_path}")

    print("\n=== summary ===")
    print(f" spots_total: {stats['spots_total']}")
    print(f" spots_processed: {stats['spots_processed']}")
    print(f" spots_errors: {stats['spots_errors']}")
    print(f" final_approved: {stats['final_approved']} ({stats.get('final_approved_ratio')})")
    print(f" first_pass: {stats['approved_first_pass']} ({stats.get('first_pass_ratio')})")
    print(f" publish_rows: {stats['publish_rows']}")
    print(f" db_counts: {stats['db_counts']}")
    print(f" quality_score mean: {stats['quality_score_mean']}")
    print(f" llm_calls/spot mean: {stats['llm_calls_per_spot_mean']} max: {stats['llm_calls_per_spot_max']}")
    print(f" elapsed/spot mean: {stats['elapsed_per_spot_mean']}s")
    print(f" critic_ratio: {stats['critic_ratio']}")
    print(f" origination_counts: {stats['origination_counts']}")
    print(f" combos: {len(stats['combo_counts'])} unique")
    print(f" sample rows:")
    for row in stats.get("sample_rows", []):
        print(f"   {row}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
