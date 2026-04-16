"""batch_publish_mvp_500 — MVP 500 spot live codex 배치 + 좌표 + 영구 DB.

batch_publish_peer_500.py 의 확장판:
  1. **live 모드** — SCP_LLM_MODE=live (기본값) 로 codex exec 호출.
  2. **영구 DB** — sqlite:///_workspace/mvp_feed.db 파일에 영구 저장.
  3. **다양성 샘플링** — event_log 전체 CREATE_TEACH_SPOT 에서 combo × region
     균등에 가까운 random 샘플로 500개 선택 (seed 고정).
  4. **좌표** — ContentSpec.latitude/longitude 를 통해 feed/detail row 에
     자동 기록 (publisher 가 spec.latitude/longitude 를 읽어 DB insert).

출력:
    _workspace/mvp_feed.db                    ← 최종 DB
    _workspace/scp_05_qa/phase_mvp_batch_stats.json

실행:
    python3 scripts/batch_publish_mvp_500.py --limit 500
    python3 scripts/batch_publish_mvp_500.py --limit 500 --mode stub   # dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# --mode CLI 옵션이 환경변수 기본값을 덮어쓰도록 하기 위해 argparse 먼저 처리.
_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--mode", choices=["live", "stub"], default="live")
_pre_args, _ = _pre.parse_known_args()
os.environ["SCP_LLM_MODE"] = _pre_args.mode

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
DB_PATH = ROOT / "_workspace" / "mvp_feed.db"


def _diverse_sample(limit: int, seed: int) -> List[str]:
    """event_log 전체에서 combo × region 균등에 가까운 random 샘플.

    전략: 모든 CREATE_TEACH_SPOT 을 (skill, teach_mode, venue_type, region_id)
    키로 bucket 에 넣고, bucket 별로 round-robin 으로 하나씩 빼낸다. 모든
    bucket 을 한 번 돌면 다시 첫 bucket 부터. limit 에 도달할 때까지 반복.
    """
    buckets: Dict[tuple, List[str]] = defaultdict(list)
    with EVENT_LOG.open(encoding="utf-8") as f:
        for line in f:
            ev = json.loads(line)
            if ev.get("event_type") != "CREATE_TEACH_SPOT":
                continue
            p = ev.get("payload") or {}
            key = (
                p.get("skill"),
                p.get("teach_mode"),
                p.get("venue_type"),
                ev.get("region_id"),
            )
            buckets[key].append(ev["spot_id"])

    rng = random.Random(seed)
    # bucket 내부 셔플 + bucket 순서도 셔플 (결정성은 seed 로 확보).
    bucket_items: List[tuple] = list(buckets.items())
    rng.shuffle(bucket_items)
    for _, ids in bucket_items:
        rng.shuffle(ids)

    picked: List[str] = []
    cursors = [0] * len(bucket_items)
    while len(picked) < limit:
        made_progress = False
        for i, (_, ids) in enumerate(bucket_items):
            if cursors[i] < len(ids):
                picked.append(ids[cursors[i]])
                cursors[i] += 1
                made_progress = True
                if len(picked) >= limit:
                    break
        if not made_progress:
            break

    print(
        f"sampled {len(picked)} spots from {len(bucket_items)} unique "
        f"(skill, mode, venue, region) buckets"
    )
    return picked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--dataset-version", default="v3_mvp")
    ap.add_argument("--seed", type=int, default=20260416)
    ap.add_argument("--mode", choices=["live", "stub"], default="live")
    ap.add_argument(
        "--db-path",
        type=Path,
        default=DB_PATH,
        help="SQLite file path (default: _workspace/mvp_feed.db)",
    )
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    args.db_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"mode:            {os.environ.get('SCP_LLM_MODE')}")
    print(f"event_log:       {EVENT_LOG}")
    print(f"db_path:         {args.db_path}")
    print(f"dataset_version: {args.dataset_version}")

    spot_ids = _diverse_sample(args.limit, args.seed)
    if not spot_ids:
        print("FATAL: no spots sampled from event_log")
        return 1

    # 영구 DB 준비 (기존 파일 있으면 스키마 재생성을 위해 drop).
    if args.db_path.exists():
        print(f"removing existing db: {args.db_path}")
        args.db_path.unlink()
    engine = create_engine(f"sqlite:///{args.db_path}", future=True)
    Base.metadata.create_all(engine)

    stats: Dict[str, Any] = {
        "spots_total": 0,
        "spots_processed": 0,
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
        "region_counts": {},
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
                    stats["errors_sample"].append(
                        f"spec/{sid}: {type(exc).__name__}: {exc}"
                    )
                continue

            combo_key = f"{spec.skill_topic}|{spec.teach_mode}|{spec.venue_type}"
            stats["combo_counts"][combo_key] = stats["combo_counts"].get(combo_key, 0) + 1
            stats["origination_counts"][spec.origination_mode] = (
                stats["origination_counts"].get(spec.origination_mode, 0) + 1
            )
            region_key = spec.region or "unknown"
            stats["region_counts"][region_key] = stats["region_counts"].get(region_key, 0) + 1

            try:
                rng = random.Random(args.seed + idx)
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

            if result.approved:
                stats["final_approved"] += 1
                if result.retry_count_total == 0:
                    stats["approved_first_pass"] += 1

            try:
                pub_result = publisher.publish_spot(result)
                for ct, n in pub_result.published_rows.items():
                    stats["publish_rows"][ct] = stats["publish_rows"].get(ct, 0) + n
                feed_cpr = result.contents.get("feed")
                if feed_cpr and feed_cpr.classification in ("approved", "conditional"):
                    stats["published_feed_status"][feed_cpr.classification] = (
                        stats["published_feed_status"].get(feed_cpr.classification, 0) + 1
                    )
            except Exception as exc:
                if len(stats["errors_sample"]) < 5:
                    stats["errors_sample"].append(
                        f"publish/{sid}: {type(exc).__name__}: {exc}"
                    )

            if (idx + 1) % 25 == 0:
                elapsed = time.time() - t_start
                eta = elapsed / (idx + 1) * (len(spot_ids) - idx - 1)
                print(
                    f"  {idx + 1}/{len(spot_ids)} approved={stats['final_approved']} "
                    f"errors={stats['spots_errors']} elapsed={elapsed:.0f}s eta={eta:.0f}s"
                )

        session.commit()

        # sanity: feed 테이블에서 lat/lng 채워진 row 수
        with_coords = session.execute(
            select(func.count()).select_from(SyntheticFeedContent).where(
                SyntheticFeedContent.latitude.is_not(None)
            )
        ).scalar_one()
        total_feed = session.execute(
            select(func.count()).select_from(SyntheticFeedContent)
        ).scalar_one()
        print(f"\nfeed rows with coordinates: {with_coords}/{total_feed}")

    t_total = time.time() - t_start
    stats["total_elapsed_seconds"] = round(t_total, 2)
    if stats["quality_scores"]:
        stats["avg_quality_score"] = round(
            statistics.mean(stats["quality_scores"]), 4
        )
    if stats["llm_calls_per_spot"]:
        stats["avg_llm_calls_per_spot"] = round(
            statistics.mean(stats["llm_calls_per_spot"]), 2
        )
        stats["max_llm_calls_per_spot"] = max(stats["llm_calls_per_spot"])
    if stats["elapsed_per_spot"]:
        stats["avg_elapsed_per_spot"] = round(
            statistics.mean(stats["elapsed_per_spot"]), 3
        )

    stats_path = OUT_DIR / "phase_mvp_batch_stats.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n")
    print(f"\nstats written: {stats_path}")
    print(f"db written:    {args.db_path}")
    print(f"total elapsed: {t_total:.1f}s")
    print(
        f"summary: processed={stats['spots_processed']} "
        f"approved_first_pass={stats['approved_first_pass']} "
        f"final_approved={stats['final_approved']} "
        f"feed_published={stats['publish_rows']['feed']} "
        f"avg_quality={stats.get('avg_quality_score')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
