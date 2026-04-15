---
name: build-kakao-collector
description: 카카오 Local API(search/category, search/keyword, coord2regioncode)용 클라이언트와 행정동 단위 배치 수집 파이프라인을 구현하는 스킬. Rate limit·재시도·grid 분할·페이지네이션·부분 실패 격리를 모두 포함한다. Kakao Local API로 장소를 수집하거나, local-context-builder의 bootstrap_regions / full_rebuild / incremental_refresh 잡을 구현할 때 반드시 이 스킬을 사용할 것.
---

# build-kakao-collector

카카오 Local API를 안정적으로 두드리고 행정동 단위로 장소를 수집하는 파이프라인을 구현하는 스킬. `collector-engineer` 에이전트가 사용한다.

## 언제 사용하는가

- 카카오 `search/category`, `search/keyword`, `coord2regioncode` API 클라이언트를 작성할 때
- 행정동 단위 grid 분할 전략을 구현할 때
- 수집 결과를 `place_raw_kakao` 같은 원본 테이블에 upsert하는 배치 잡을 작성할 때
- 기존 수집기에 rate limit 방어나 재시도 로직을 추가할 때

## 카카오 API 규약

| API | 경로 | 핵심 파라미터 | 반환 |
|---|---|---|---|
| 좌표→행정동 | `GET /v2/local/geo/coord2regioncode.{json}` | `x`, `y` | 행정동 정보 배열 |
| 카테고리 검색 | `GET /v2/local/search/category.{json}` | `category_group_code`, `x`, `y`, `radius`(m, ≤20000), `page`(1~3), `size`(≤15), `sort` | documents + meta.is_end |
| 키워드 검색 | `GET /v2/local/search/keyword.{json}` | `query`, `x`, `y`, `radius`, `page`, `size`, `sort` | documents + meta.is_end |

공통 헤더: `Authorization: KakaoAK {REST_API_KEY}`

**제약**:
- 카테고리 검색 최대 45건 (size=15 × page=3)
- 반경 최대 20km
- 일간 쿼터 존재 (앱당)
- 429: `Retry-After` 헤더 반환

## 클라이언트 구현 원칙

### 인증과 공통 요청

```python
# app/collectors/kakao_local_client.py
import httpx, asyncio, time
from app.config import Settings

class KakaoLocalClient:
    BASE = "https://dapi.kakao.com"

    def __init__(self, settings: Settings, *, sleep_ms: int = 200):
        self._key = settings.kakao_rest_api_key
        self._sleep = sleep_ms / 1000
        self._client = httpx.Client(
            base_url=self.BASE,
            headers={"Authorization": f"KakaoAK {self._key}"},
            timeout=httpx.Timeout(10.0, read=15.0),
        )
```

### 재시도 + rate limit

- **429**: `Retry-After` 헤더 있으면 그 값 + jitter, 없으면 `2 ** attempt` 초 대기. 최대 3회
- **5xx**: 지수 backoff 3회
- **네트워크 예외**: 2회 재시도
- **4xx (429 제외)**: 즉시 예외

모든 호출 후 `time.sleep(self._sleep)`로 200ms 여유. `httpx.AsyncClient`를 써도 되지만 배치 특성상 동기로 충분.

### 페이지네이션

```python
def search_category(self, category_group_code, x, y, radius, sort="distance"):
    results = []
    for page in range(1, 4):  # 1,2,3
        r = self._get("/v2/local/search/category.json", params={
            "category_group_code": category_group_code,
            "x": x, "y": y, "radius": radius, "page": page, "size": 15, "sort": sort,
        })
        docs = r["documents"]
        results.extend(docs)
        if r["meta"]["is_end"] or len(docs) < 15:
            break
    return results
```

키워드 검색도 동일 패턴.

## Grid 분할 전략

플랜 §5-3 로직을 그대로 구현.

```python
# app/collectors/grid_strategy.py
def plan_cells(region, probe_result_count: int | None = None) -> list[tuple[float, float, int]]:
    """
    returns: [(center_lng, center_lat, radius_m), ...]

    규칙:
      - probe 결과가 없으면 기본: [(region.center_lng, region.center_lat, 1000)]
      - probe가 40건 이상이면 bbox를 2x2로 분할, 각 셀 중심 + 반경 (셀 대각선/2)
      - 재귀 최대 3단계
    """
```

MVP 고정값: **2×2, 최대 1단계 분할**. 이후 확장 여지만 남겨둔다.

**주의**: grid 분할은 probe 결과를 보고 판단하는 게 아니라, 처음부터 region.area_km2 또는 예상 밀도를 기반으로 **선제 분할**하는 옵션도 있다. MVP는 선제 2×2를 쓰지 말고, probe → 필요 시 분할의 적응형 방식을 쓴다.

## 수집 잡 구조

### full_rebuild.py 파이프라인

```python
def run_full_rebuild(target_city: str):
    batch_id = f"batch_{datetime.utcnow():%Y%m%d_%H%M%S}"
    with SessionLocal() as db:
        regions = db.scalars(
            select(RegionMaster).where(
                RegionMaster.target_city == target_city,
                RegionMaster.is_active.is_(True),
            )
        ).all()

    failed_regions = []
    for region in regions:
        try:
            _collect_region(region, batch_id)
        except Exception as e:
            failed_regions.append((region.id, str(e)))
            logger.exception("region %s failed", region.region_code)
            continue

    logger.info("full_rebuild done, failed=%d", len(failed_regions))
    return {"batch_id": batch_id, "failed": failed_regions}
```

**핵심**: `except Exception` 로 region 실패를 격리. 배치 전체가 죽지 않음.

### _collect_region 내부

1. `grid_strategy.plan_cells(region)` 호출 (probe 포함)
2. 각 cell × 카테고리 5개 조합으로 `search_category` 호출
3. 각 `{emd} + keyword` 조합으로 `search_keyword` 호출
4. 결과를 `place_raw_kakao`에 upsert (unique: `(source_place_id, region_id)`)
5. `raw_json`에 원본 저장
6. `region.last_collected_at = now()`

### 카테고리/키워드 세트 (MVP)

```python
CATEGORY_CODES = ["FD6", "CE7", "CT1", "AT4", "AC5"]

KEYWORD_TEMPLATES = ["맛집", "카페", "원데이클래스", "공방", "운동"]

def keyword_for(emd: str, template: str) -> str:
    return f"{emd} {template}"
```

v1.1에서 키워드 9개로 확장.

### upsert 구현

PostgreSQL `ON CONFLICT` 활용:

```python
from sqlalchemy.dialects.postgresql import insert
stmt = insert(PlaceRawKakao).values(**row).on_conflict_do_update(
    index_elements=["source_place_id", "region_id"],
    set_={"collected_at": func.now(), "raw_json": row["raw_json"], "batch_id": row["batch_id"]},
)
db.execute(stmt)
```

## 테스트 전략

**실제 API를 때리지 않는다.** `respx` 또는 `httpx.MockTransport`로 mock.

```python
# tests/test_kakao_client.py
import respx, httpx, pytest
from app.collectors.kakao_local_client import KakaoLocalClient

@respx.mock
def test_429_backoff():
    respx.get("https://dapi.kakao.com/v2/local/search/category.json").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, json={"documents": [], "meta": {"is_end": True}}),
        ]
    )
    client = _mk_client()
    assert client.search_category("FD6", 127.0, 37.0, 1000) == []
```

grid_strategy 테스트는 순수 함수 테스트. region fixture에 면적과 bbox만 주고 cell 리스트 검증.

## 부분 실패와 관측

- 실패 region은 `logger.exception` + 결과 리턴
- 배치 종료 시 실패 요약을 로그로 남김
- 추후 integration-qa가 `alerts.py`에서 임계치 초과 감지를 붙일 수 있도록 **반환값으로 실패 목록 제공**

## 체크리스트

- [ ] `KakaoLocalClient`에 `search_category`, `search_keyword`, `coord2regioncode` 존재
- [ ] 429/5xx 재시도, 200ms sleep, 페이지네이션
- [ ] `grid_strategy.plan_cells` 구현 + 단위 테스트
- [ ] `place_raw_kakao`에 `search_type`, `search_query`, `batch_id`, `raw_json` 채움
- [ ] upsert가 unique 제약 `(source_place_id, region_id)` 기준으로 동작
- [ ] 한 region 실패해도 다음 region 진행
- [ ] 실제 API 호출 없이 테스트 통과
- [ ] 함수 시그니처 인벤토리를 `_workspace/03_collector/api_surface.md`에 기록

## 안티패턴

- 테스트에서 실제 카카오 API 호출 — CI/로컬에서 쿼터 소모 + 불안정
- region 1개 실패 시 `raise`로 배치 전체 중단
- rate limit 없이 연속 호출 → 429 폭탄
- `raw_json`을 저장 안 함 — 나중에 카테고리 매핑 디버깅 불가
- search_type을 빠뜨림 — 키워드/카테고리 수집분 구분 불가
- grid 분할을 무조건 2×2로 만 돌림 — 작은 행정동에서 쓸데없이 4배 호출
