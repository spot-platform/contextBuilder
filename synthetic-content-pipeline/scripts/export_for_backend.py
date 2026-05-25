"""export_for_backend.py — mvp_feed.db → 백엔드 전달용 JSON export

백엔드 FeedItem 엔티티 스키마 기준으로 변환 (capstone-domain/.../FeedItem.java).

FeedCategory enum: 음악, 요리, 운동, 공예, 언어, 기타
FeedItemStatus:    OPEN, MATCHED, CLOSED
PostType:          OFFER, REQUEST, RENT
isAi:              true (AI 합성 콘텐츠 마커)

출력:
    _workspace/backend_export/
        ai_feed_items.json      ← FeedItem 형식 (백엔드 import용)
        export_manifest.json    ← 메타 정보

실행:
    python3 scripts/export_for_backend.py
    python3 scripts/export_for_backend.py --db-path _workspace/mvp_feed.db \\
                                          --event-log ../../spot-simulator/output/event_log.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "_workspace" / "mvp_feed.db"
DEFAULT_EVENT_LOG = ROOT.parent / "spot-simulator" / "output" / "event_log.jsonl"
OUT_DIR = ROOT / "_workspace" / "backend_export"

# ── skill_topic → FeedCategory 매핑 ──────────────────────────────────────────
SKILL_TO_CATEGORY = {
    "기타":          "음악",
    "우쿨렐레":      "음악",
    "피아노 기초":   "음악",
    "홈쿡":          "요리",
    "홈베이킹":      "요리",
    "핸드드립":      "요리",
    "러닝":          "운동",
    "요가 입문":     "운동",
    "볼더링":        "운동",
    "가벼운 등산":   "운동",
    "드로잉":        "공예",
    "스마트폰 사진": "공예",
    "캘리그라피":    "공예",
    "영어 프리토킹": "언어",
    "코딩 입문":     "기타",
    "원예":          "기타",
    "보드게임":      "기타",
    "타로":          "기타",
}


def _parse_price(price_label: str) -> int:
    """'1인 약 7,556원' → 7556 (가장 큰 숫자를 가격으로 판단)"""
    if not price_label:
        return 0
    nums = re.findall(r"[\d,]+", price_label)
    if not nums:
        return 0
    candidates = [int(n.replace(",", "")) for n in nums]
    return max(candidates)


def _parse_json_field(val) -> object:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


def _to_plan_v3(plan_json_str: str | None) -> dict | None:
    """우리 plan_json → PlanV3 형식 (steps[] + total_duration_minutes)"""
    if not plan_json_str:
        return None
    plan = _parse_json_field(plan_json_str)
    if not isinstance(plan, dict):
        return None
    steps = plan.get("steps", [])
    # total_duration_minutes가 없으면 steps 수 × 30분으로 추정
    if "total_duration_minutes" not in plan:
        plan["total_duration_minutes"] = max(len(steps) * 30, 60)
    return plan


def _to_price_breakdown(cost_breakdown_str: str | None, price: int) -> dict | None:
    """우리 cost_breakdown_json ([{item, amount}]) → PriceBreakdown 형식"""
    if not cost_breakdown_str:
        return None
    items = _parse_json_field(cost_breakdown_str)
    if not isinstance(items, list):
        return None
    included_items = [
        {"name": it.get("item", "항목"), "value": f"{it.get('amount', 0):,}원 포함"}
        for it in items
    ]
    return {
        "base_fee": price,
        "included_items": included_items,
        "optional_addons": [],
        "refund_policy": {
            "cutoff_hours": 24,
            "note": "모임 24시간 전 전액 환불",
        },
        "summary_line": f"1인 {price:,}원",
    }


def _to_preparation(materials_str: str | None) -> dict | None:
    """우리 materials_json (string[]) → Preparation 형식"""
    if not materials_str:
        return {"host_provides": [], "partner_brings": [], "weather_contingency": None,
                "safety_notes": [], "host_tip": None}
    materials = _parse_json_field(materials_str)
    if not isinstance(materials, list):
        materials = []
    return {
        "host_provides": [],
        "partner_brings": materials,
        "weather_contingency": None,
        "safety_notes": [],
        "host_tip": None,
    }


def _load_spot_meta_from_event_log(event_log_path: Path) -> dict[str, dict]:
    """event_log에서 spot_id → {skill, venue_type, teach_mode, fee} 추출"""
    meta: dict[str, dict] = {}
    if not event_log_path.exists():
        print(f"  [WARN] event_log not found: {event_log_path}")
        return meta
    with event_log_path.open(encoding="utf-8") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event_type") != "CREATE_TEACH_SPOT":
                continue
            sid = ev.get("spot_id")
            if not sid or sid in meta:
                continue
            p = ev.get("payload", {})
            meta[sid] = {
                "skill": p.get("skill", ""),
                "venue_type": p.get("venue_type", ""),
                "teach_mode": p.get("teach_mode", ""),
                "fee": p.get("fee", 0),
            }
    return meta


def export_feed_items(conn: sqlite3.Connection, event_log_path: Path) -> list[dict]:
    """
    synthetic_feed_content + synthetic_spot_detail → FeedItem 형식 변환
    백엔드 FeedItem 엔티티 필드 기준.
    """
    spot_meta = _load_spot_meta_from_event_log(event_log_path)

    # detail 미리 인덱싱
    detail_map: dict[str, dict] = {}
    for row in conn.execute("SELECT * FROM synthetic_spot_detail"):
        d = dict(row)
        detail_map[d["spot_id"]] = d

    feed_items = []
    for row in conn.execute("SELECT * FROM synthetic_feed_content ORDER BY rowid"):
        f = dict(row)
        sid = f["spot_id"]
        detail = detail_map.get(sid, {})
        meta = spot_meta.get(sid, {})

        price = _parse_price(f.get("price_label", ""))

        skill = meta.get("skill", "")
        category = SKILL_TO_CATEGORY.get(skill, "기타")

        plan_v3 = _to_plan_v3(detail.get("plan_json"))
        price_breakdown = _to_price_breakdown(detail.get("cost_breakdown_json"), price)
        preparation = _to_preparation(detail.get("materials_json"))

        feed_items.append({
            # ── FeedItem 핵심 필드 ──────────────────────────────────────
            "spot_id": sid,                            # 우리 식별자
            "title": f.get("title", ""),
            "description": detail.get("description") or f.get("summary", ""),
            "location": f.get("region_label", ""),     # "수원시 연무동" 형식
            "authorNickname": "AI 호스트",              # 합성 콘텐츠 호스트
            "authorRole": "SUPPORTER",
            "price": price,                            # int (원)
            "type": "OFFER",
            "status": "OPEN",
            "category": category,                      # FeedCategory enum
            "maxParticipants": 6,                      # 기본값
            "isAi": True,                              # AI 합성 마커
            # ── 위치 (lat/lng 추가 제공) ───────────────────────────────
            "latitude": float(f["latitude"]) if f.get("latitude") else None,
            "longitude": float(f["longitude"]) if f.get("longitude") else None,
            # ── JSON 컨텍스트 필드 ─────────────────────────────────────
            "planJson": json.dumps(plan_v3, ensure_ascii=False) if plan_v3 else None,
            "priceBreakdownJson": json.dumps(price_breakdown, ensure_ascii=False) if price_breakdown else None,
            "preparationJson": json.dumps(preparation, ensure_ascii=False) if preparation else None,
            "venueAnchorsJson": None,   # POI 실제 상호명 → PR#6 머지 후 LLM 재실행 시 추가 예정
            "primaryPinJson": None,
            # ── 부가 정보 ──────────────────────────────────────────────
            "skill_topic": skill,              # 백엔드에 없음, 참고용
            "teach_mode": meta.get("teach_mode", ""),  # 참고용
            "venue_type": meta.get("venue_type", ""),  # 참고용
            "quality_score": float(f["quality_score"]) if f.get("quality_score") else None,
            "validation_status": f.get("validation_status"),
            "host_intro": detail.get("host_intro"),
            "policy_notes": detail.get("policy_notes"),
            "target_audience": detail.get("target_audience"),
        })

    return feed_items


def main() -> None:
    ap = argparse.ArgumentParser(description="mvp_feed.db → 백엔드 FeedItem JSON export")
    ap.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    ap.add_argument("--event-log", type=Path, default=DEFAULT_EVENT_LOG)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args()

    if not args.db_path.exists():
        print(f"FATAL: DB not found: {args.db_path}")
        raise SystemExit(1)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(args.db_path))
    conn.row_factory = sqlite3.Row

    print(f"DB:        {args.db_path}")
    print(f"event_log: {args.event_log}")

    feed_items = export_feed_items(conn, args.event_log)
    conn.close()

    exported_at = datetime.now(timezone.utc).isoformat()

    # ── ai_feed_items.json ────────────────────────────────────────────────────
    out = {
        "exported_at": exported_at,
        "total": len(feed_items),
        "schema_version": "FeedItem_v1",
        "items": feed_items,
    }
    feed_path = args.out_dir / "ai_feed_items.json"
    feed_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n  [{len(feed_items):>3}건] FeedItem JSON → {feed_path.name}")

    # ── 카테고리 분포 확인 ──────────────────────────────────────────────────
    cat_dist: dict[str, int] = {}
    for item in feed_items:
        c = item["category"]
        cat_dist[c] = cat_dist.get(c, 0) + 1

    # ── export_manifest.json ─────────────────────────────────────────────────
    manifest = {
        "exported_at": exported_at,
        "source_db": str(args.db_path),
        "total_spots": len(feed_items),
        "category_distribution": cat_dist,
        "files": {
            "feed_items": "ai_feed_items.json",
        },
        "backend_import_notes": {
            "entity": "FeedItem (capstone-domain/src/.../feed/entity/FeedItem.java)",
            "isAi": "true — AI 합성 콘텐츠 구분자",
            "type": "OFFER (전체)",
            "status": "OPEN (전체)",
            "category": f"FeedCategory enum — {cat_dist}",
            "location": "region_label 문자열 ('수원시 연무동' 형태)",
            "price": "int (원) — price_label 파싱",
            "planJson": "PlanV3 형식 (steps[]/total_duration_minutes)",
            "priceBreakdownJson": "PriceBreakdown 형식 (base_fee/included_items/optional_addons/refund_policy)",
            "preparationJson": "Preparation 형식 (host_provides/partner_brings/...)",
            "venueAnchorsJson": "null — PR#6 머지 + LLM 재실행 후 실제 상호명 데이터 추가 예정",
            "import_api_needed": "FeedController에 bulk import endpoint 없음 → 백엔드 팀과 협의 필요",
        },
    }
    manifest_path = args.out_dir / "export_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  manifest → {manifest_path.name}")
    print(f"\n카테고리 분포: {cat_dist}")
    print(f"출력 디렉토리: {args.out_dir}")
    print("\nDONE")


if __name__ == "__main__":
    main()
