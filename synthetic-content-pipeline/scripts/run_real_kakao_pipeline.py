"""실제 카카오 Local API → place_normalized 동등 JSON dump.

Auto Mode: lcb 의 SQLAlchemy / Postgres / Alembic 인프라 없이도 동작.
lcb 의 KakaoLocalClient 와 category_mapping_rule 룰을 *그대로* 적용해서
실제 수원 POI 데이터를 ``data/poi/poi_normalized_real.json`` 으로 저장한다.

차이점 (정공법 대비):
- DB 미사용 → in-memory dedup
- region_master 는 lcb CSV 시드 직접 로드
- category_mapping_rule 는 lcb JSON 시드 직접 로드
- normalize_places 의 map_place / _derived_tags 로직은 동일 알고리즘으로 재구현

산출물 스키마는 ``synthetic-content-pipeline/data/poi/poi_normalized_fallback.json``
와 1:1 동일 (PoiRecord 19 필드).
"""
from __future__ import annotations

import csv
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Sequence

import httpx

LCB_ROOT = Path(__file__).resolve().parents[2] / "local-context-builder"
SCP_ROOT = Path(__file__).resolve().parents[1]

REGION_CSV = LCB_ROOT / "data" / "region_master_suwon.csv"
CATEGORY_MAPPING_JSON = LCB_ROOT / "data" / "category_mapping_seed.json"

OUT_PATH = SCP_ROOT / "data" / "poi" / "poi_normalized_real.json"

# 기본 수집 대상 — 수원의 도심 / 외곽 / 도서관 등 다양한 region 3 개.
TARGET_EMD = ("연무동", "인계동", "매탄1동")

# Kakao Local search/category 코드 (lcb plan §5-4).
CATEGORY_CODES: tuple[str, ...] = ("FD6", "CE7", "CT1", "AT4", "AC5")

# 키워드 검색 — lcb keyword_collector 의 핵심 패턴 (체육시설/공원).
KEYWORD_QUERIES: tuple[str, ...] = (
    "공원",
    "산책로",
    "체육관",
    "필라테스",
    "요가",
    "클라이밍",
    "스튜디오",
    "공방",
    "음악연습실",
    "주점",
    "와인바",
)

INTERNAL_TAGS: tuple[str, ...] = (
    "food", "cafe", "activity", "park", "culture", "nightlife", "lesson",
)

_PRIMARY_PRIORITY: tuple[str, ...] = (
    "nightlife", "lesson", "culture", "activity", "park", "food", "cafe",
)

_NIGHT_KEYWORDS = ("주점", "바", "포차", "술집")
_GROUP_KEYWORDS = ("파티", "단체", "모임", "파티룸")


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("real_kakao")


# ---------------------------------------------------------------------------
# Kakao client (lcb KakaoLocalClient 와 동등 — DB-free)
# ---------------------------------------------------------------------------


class KakaoApiError(RuntimeError):
    pass


class KakaoLocalClient:
    BASE = "https://dapi.kakao.com"
    MAX_PAGES = 3
    PAGE_SIZE = 15

    def __init__(self, api_key: str, *, sleep_ms: int = 200, max_retries: int = 3):
        self._client = httpx.Client(
            base_url=self.BASE,
            headers={
                "Authorization": f"KakaoAK {api_key}",
                "KA": "sdk/1.0.0 os/python lang/ko origin/http%3A%2F%2Flocalhost",
            },
            timeout=httpx.Timeout(10.0, read=15.0),
        )
        self._sleep_s = sleep_ms / 1000.0
        self._max_retries = max_retries

    def search_category(self, code, x, y, radius, *, sort="distance"):
        return self._paginate(
            "/v2/local/search/category.json",
            {"category_group_code": code, "x": x, "y": y, "radius": radius, "sort": sort},
        )

    def search_keyword(self, query, *, x=None, y=None, radius=None, sort="accuracy"):
        params: dict[str, Any] = {"query": query, "sort": sort}
        if x is not None: params["x"] = x
        if y is not None: params["y"] = y
        if radius is not None: params["radius"] = radius
        return self._paginate("/v2/local/search/keyword.json", params)

    def close(self): self._client.close()

    def _paginate(self, path: str, base_params: dict) -> list[dict]:
        docs: list[dict] = []
        for page in range(1, self.MAX_PAGES + 1):
            params = {**base_params, "page": page, "size": self.PAGE_SIZE}
            payload = self._get(path, params)
            page_docs = payload.get("documents") or []
            docs.extend(page_docs)
            meta = payload.get("meta") or {}
            if meta.get("is_end") or len(page_docs) < self.PAGE_SIZE:
                break
        return docs

    def _get(self, path: str, params: dict) -> dict:
        last: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                r = self._client.get(path, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last = e
                self._backoff(attempt)
                continue
            s = r.status_code
            if s == 200:
                time.sleep(self._sleep_s)
                return r.json()
            if s == 429:
                ra = float(r.headers.get("Retry-After", "1") or "1")
                log.warning("kakao 429 retry_after=%.1fs", ra)
                time.sleep(max(ra, 0.5))
                continue
            if 500 <= s < 600:
                self._backoff(attempt)
                last = KakaoApiError(f"5xx: {s}")
                continue
            raise KakaoApiError(f"{s}: {r.text[:300]}")
        if isinstance(last, KakaoApiError):
            raise last
        raise KakaoApiError(f"retries exhausted: {last!r}")

    def _backoff(self, attempt: int):
        time.sleep((2 ** attempt) + random.uniform(0, 0.25))


# ---------------------------------------------------------------------------
# Mapping rules (lcb category_mapping_rule 동등)
# ---------------------------------------------------------------------------


@dataclass
class _Rule:
    kakao_category_group_code: Optional[str]
    kakao_category_pattern: Optional[str]
    keyword_pattern: Optional[str]
    internal_tag: str
    confidence: float
    priority: int


def _load_rules() -> list[_Rule]:
    with CATEGORY_MAPPING_JSON.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    rules = [
        _Rule(
            kakao_category_group_code=r.get("kakao_category_group_code"),
            kakao_category_pattern=r.get("kakao_category_pattern"),
            keyword_pattern=r.get("keyword_pattern"),
            internal_tag=r["internal_tag"],
            confidence=float(r.get("confidence") or 1.0),
            priority=int(r.get("priority") or 0),
        )
        for r in raw
    ]
    rules.sort(key=lambda x: -x.priority)  # priority DESC
    return rules


def _like(value: Optional[str], pattern: Optional[str]) -> bool:
    if value is None or pattern is None:
        return False
    v = value.lower()
    p = pattern.lower()
    starts = p.startswith("%")
    ends = p.endswith("%")
    core = p.strip("%")
    if not core:
        return False
    if starts and ends: return core in v
    if starts: return v.endswith(core)
    if ends: return v.startswith(core)
    return core == v


def _rule_matches(rule: _Rule, doc: dict, search_query: Optional[str]) -> bool:
    code = doc.get("category_group_code") or ""
    cname = doc.get("category_name")
    if rule.kakao_category_group_code and rule.kakao_category_group_code == code:
        return True
    if rule.kakao_category_pattern and _like(cname, rule.kakao_category_pattern):
        return True
    if rule.keyword_pattern and _like(search_query, rule.keyword_pattern):
        return True
    return False


def _map_place(doc: dict, rules: Sequence[_Rule], search_query: Optional[str]):
    tags: dict[str, bool] = {}
    confs: list[float] = []
    for rule in rules:
        if rule.internal_tag not in INTERNAL_TAGS:
            continue
        if _rule_matches(rule, doc, search_query):
            key = f"is_{rule.internal_tag}"
            if key not in tags:
                tags[key] = True
                confs.append(rule.confidence)
    primary = next(
        (n for n in _PRIMARY_PRIORITY if tags.get(f"is_{n}")), None
    )
    if primary is None:
        return "other", {}, 0.0
    return primary, tags, max(confs) if confs else 1.0


def _derived_tags(tags: dict, category_name: Optional[str]):
    cat = (category_name or "").lower()
    night = bool(tags.get("is_nightlife") or any(k in cat for k in _NIGHT_KEYWORDS))
    group = bool(
        tags.get("is_activity") or tags.get("is_lesson")
        or any(k in cat for k in _GROUP_KEYWORDS)
    )
    return night, group


# ---------------------------------------------------------------------------
# Region master loading
# ---------------------------------------------------------------------------


@dataclass
class _Region:
    region_code: str
    sigungu: str
    emd: str
    center_lng: float
    center_lat: float


def _load_target_regions(target_emds: Sequence[str]) -> list[_Region]:
    out: list[_Region] = []
    target_set = set(target_emds)
    with REGION_CSV.open("r", encoding="utf-8") as fh:
        # skip comment lines
        body = "\n".join(line for line in fh if not line.startswith("#") and line.strip())
    reader = csv.DictReader(body.splitlines())
    for row in reader:
        if row["emd"] in target_set and row.get("center_lng") and row.get("center_lat"):
            out.append(
                _Region(
                    region_code=row["region_code"],
                    sigungu=row["sigungu"],
                    emd=row["emd"],
                    center_lng=float(row["center_lng"]),
                    center_lat=float(row["center_lat"]),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def run_pipeline(
    api_key: str,
    target_emds: Sequence[str] = TARGET_EMD,
    radius: int = 800,
) -> dict:
    rules = _load_rules()
    regions = _load_target_regions(target_emds)
    if not regions:
        raise SystemExit(f"no matching regions for {target_emds!r}")
    log.info("regions: %s", [r.emd for r in regions])

    client = KakaoLocalClient(api_key)
    raw_buf: list[tuple[_Region, dict, str, Optional[str]]] = []
    # raw_buf: (region, doc, search_type, search_query)

    try:
        for region in regions:
            log.info("--- collecting %s ---", region.emd)
            for code in CATEGORY_CODES:
                docs = client.search_category(
                    code, x=region.center_lng, y=region.center_lat, radius=radius,
                )
                log.info("  category %s → %d docs", code, len(docs))
                for d in docs:
                    raw_buf.append((region, d, "category", None))
            for q in KEYWORD_QUERIES:
                docs = client.search_keyword(
                    q, x=region.center_lng, y=region.center_lat, radius=radius,
                )
                if docs:
                    log.info("  keyword '%s' → %d docs", q, len(docs))
                for d in docs:
                    raw_buf.append((region, d, "keyword", q))
    finally:
        client.close()

    # Dedup by source_place_id (Kakao 'id' field).
    seen: set[str] = set()
    normalized: list[dict] = []
    mapped_count = 0
    unmapped_count = 0

    for region, doc, _stype, sq in raw_buf:
        pid = str(doc.get("id"))
        if pid in seen or not pid or pid == "None":
            continue
        seen.add(pid)

        primary, tags, confidence = _map_place(doc, rules, sq)
        if primary == "other":
            unmapped_count += 1
            continue
        mapped_count += 1
        night_friendly, group_friendly = _derived_tags(tags, doc.get("category_name"))

        try:
            lat = float(doc["y"])
            lng = float(doc["x"])
        except (KeyError, ValueError, TypeError):
            continue

        normalized.append(
            {
                "place_id": int(pid) if pid.isdigit() else hash(pid) & 0x7FFFFFFF,
                "region_emd": region.emd,
                "name": doc.get("place_name") or "",
                "primary_category": primary,
                "sub_category": doc.get("category_name") or None,
                "lat": lat,
                "lng": lng,
                "address_name": doc.get("address_name") or None,
                "road_address_name": doc.get("road_address_name") or None,
                "is_food": tags.get("is_food", False),
                "is_cafe": tags.get("is_cafe", False),
                "is_activity": tags.get("is_activity", False),
                "is_park": tags.get("is_park", False),
                "is_culture": tags.get("is_culture", False),
                "is_nightlife": tags.get("is_nightlife", False),
                "is_lesson": tags.get("is_lesson", False),
                "is_night_friendly": night_friendly,
                "is_group_friendly": group_friendly,
                "mapping_confidence": confidence,
            }
        )

    payload = {
        "lcb_dataset_version": f"real_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filter": {
            "target_emds": list(target_emds),
            "radius_m": radius,
        },
        "stats": {
            "raw_collected": len(raw_buf),
            "unique_after_dedup": len(seen),
            "mapped": mapped_count,
            "unmapped_other": unmapped_count,
            "in_dump": len(normalized),
        },
        "places": normalized,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info(
        "saved %s places -> %s (mapped=%d unmapped=%d)",
        len(normalized), OUT_PATH, mapped_count, unmapped_count,
    )
    return payload


def main() -> int:
    api_key = os.getenv("KAKAO_REST_API_KEY")
    if not api_key:
        print("ERROR: KAKAO_REST_API_KEY env var is required", file=sys.stderr)
        return 1
    payload = run_pipeline(api_key)
    print("\n=== Per-region distribution ===")
    by_region: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for p in payload["places"]:
        by_region[p["region_emd"]] = by_region.get(p["region_emd"], 0) + 1
        by_category[p["primary_category"]] = by_category.get(p["primary_category"], 0) + 1
    for k, v in sorted(by_region.items()):
        print(f"  {k}: {v}")
    print("=== Per-category distribution ===")
    for k, v in sorted(by_category.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
