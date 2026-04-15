"""build_peer_goldens — Phase F peer goldens 10건 생성.

peer event_log (spot-simulator/output/event_log.jsonl) 에서 target combo 별로
한 spot_id 를 골라 build_content_spec(mode='peer') 결과를 JSON 으로 덤프.

- 총 10개 combo. 보드게임/workshop/cafe 와 타로/1:1/cafe 는
  origination_mode=='request_matched' 인 spot 을 우선 선택 (없으면 offer fallback).
- 결과 파일: data/goldens/specs/peer_<skill>_<mode>_<venue>.json

실행:
    python3 scripts/build_peer_goldens.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pipeline.spec.builder import build_content_spec  # noqa: E402

EVENT_LOG = ROOT.parent / "spot-simulator" / "output" / "event_log.jsonl"
OUT_DIR = ROOT / "data" / "goldens" / "specs"
EXP_DIR = ROOT / "data" / "goldens" / "peer_expected"

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

# request_matched 우선 대상. Peer event_log 관찰 결과 request_matched 는
# '영어 프리토킹 / small_group / cafe' combo 에만 집중 분포 (160건) —
# 시뮬레이터의 CREATE_SKILL_REQUEST 가 해당 combo 로 편향된 결과.
# 따라서 request_matched 대표 샘플은 영어 combo 에서 뽑는다.
PREFER_REQ_MATCHED = {
    ("영어 프리토킹", "small_group", "cafe"),
}

# 영어 offer 샘플을 추가로 확보하기 위한 second-pick (같은 combo 로 offer 1 건 더).
EXTRA_OFFER_COMBO = ("영어 프리토킹", "small_group", "cafe")
EXTRA_OFFER_SLUG = "peer_english_smallgroup_cafe_offer"


def _slug(skill: str, mode: str, venue: str) -> str:
    skill_en = {
        "영어 프리토킹": "english",
        "보드게임": "boardgame",
        "핸드드립": "handdrip",
        "홈베이킹": "homebaking",
        "드로잉": "drawing",
        "러닝": "running",
        "원예": "gardening",
        "타로": "tarot",
        "우쿨렐레": "ukulele",
        "캘리그라피": "calligraphy",
    }.get(skill, skill.replace(" ", ""))
    mode_slug = mode.replace(":", "on").replace("_", "")
    return f"peer_{skill_en}_{mode_slug}_{venue}"


def _collect_candidates(limit_per_combo: int = 150) -> Dict[Tuple[str, str, str], List[str]]:
    """event_log 전체 스캔해서 combo 별 spot_id 리스트 확보 (순서 = tick 순)."""
    buckets: Dict[Tuple[str, str, str], List[str]] = {k: [] for k in TARGETS}
    with open(EVENT_LOG, encoding="utf-8") as f:
        for line in f:
            ev = json.loads(line)
            if ev.get("event_type") != "CREATE_TEACH_SPOT":
                continue
            p = ev.get("payload") or {}
            key = (p.get("skill"), p.get("teach_mode"), p.get("venue_type"))
            if key in buckets and len(buckets[key]) < limit_per_combo:
                buckets[key].append(ev["spot_id"])
    return buckets


def _pick_spec(candidates: List[str], prefer_req_matched: bool):
    first_offer = None
    first_req = None
    for sid in candidates:
        try:
            spec = build_content_spec(EVENT_LOG, sid, mode="peer")
        except Exception:
            continue
        if spec.fee_breakdown is None:
            continue
        if spec.origination_mode == "request_matched" and first_req is None:
            first_req = (sid, spec)
        if spec.origination_mode == "offer" and first_offer is None:
            first_offer = (sid, spec)
        if first_offer and first_req:
            break
    if prefer_req_matched and first_req is not None:
        return first_req
    if first_offer is not None:
        return first_offer
    return first_req


def _expected_floor(spec) -> dict:
    """goldens expected — 제약 상/하한. validator/§14 tolerance 검사용."""
    fb = spec.fee_breakdown
    return {
        "spot_id": spec.spot_id,
        "origination_mode": spec.origination_mode,
        "skill_topic": spec.skill_topic,
        "teach_mode": spec.teach_mode,
        "venue_type": spec.venue_type,
        "fee_breakdown_total": fb.total if fb else None,
        "peer_labor_fee": fb.peer_labor_fee if fb else None,
        "peer_labor_share_min": 0.40,
        "passthrough_total": fb.passthrough_total if fb else None,
        "price_band": spec.budget.price_band,
        "notes": {
            "title_max_len": 40,
            "summary_max_len": 120,
            "forbidden_tokens": ["할인", "특가", "!!", "100%"],
        },
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    EXP_DIR.mkdir(parents=True, exist_ok=True)
    buckets = _collect_candidates()
    written = 0
    used_spot_ids: set[str] = set()
    for combo in TARGETS:
        ids = buckets.get(combo, [])
        if not ids:
            print(f"SKIP {combo}: no candidates in event_log")
            continue
        result = _pick_spec(ids, prefer_req_matched=combo in PREFER_REQ_MATCHED)
        if result is None:
            print(f"SKIP {combo}: no valid spec")
            continue
        sid, spec = result
        used_spot_ids.add(sid)
        slug = _slug(*combo)
        out_path = OUT_DIR / f"{slug}.json"
        out_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        exp_path = EXP_DIR / f"{slug}.json"
        exp_path.write_text(
            json.dumps(_expected_floor(spec), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        fb = spec.fee_breakdown
        print(
            f"OK {combo} -> {sid} | {spec.origination_mode} | region={spec.region} "
            f"| total={fb.total if fb else '-'} peer_labor={fb.peer_labor_fee if fb else '-'}"
        )
        written += 1

    # EXTRA: 영어 combo offer 샘플 1 건 추가 확보 (origination 대비 검증용).
    extra_ids = [sid for sid in buckets.get(EXTRA_OFFER_COMBO, []) if sid not in used_spot_ids]
    extra = _pick_spec(extra_ids, prefer_req_matched=False)
    if extra is not None and extra[1].origination_mode == "offer":
        sid, spec = extra
        out_path = OUT_DIR / f"{EXTRA_OFFER_SLUG}.json"
        out_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        exp_path = EXP_DIR / f"{EXTRA_OFFER_SLUG}.json"
        exp_path.write_text(
            json.dumps(_expected_floor(spec), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        fb = spec.fee_breakdown
        print(
            f"OK (extra) {EXTRA_OFFER_COMBO} -> {sid} | {spec.origination_mode} "
            f"| region={spec.region} | total={fb.total if fb else '-'}"
        )
        written += 1
    else:
        print(f"SKIP extra offer for {EXTRA_OFFER_COMBO}: no offer-mode candidate")

    print(f"\nwrote {written} goldens")
    return 0 if written >= 10 else 1


if __name__ == "__main__":
    sys.exit(main())
