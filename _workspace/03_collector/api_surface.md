# T03 — API surface inventory

Public function and class signatures exposed by the collector and
collection-job modules. `integration-qa` consumes this when wiring up
the admin API (`POST /admin/full-rebuild`, `POST /admin/incremental-refresh`).

## `app.collectors.kakao_local_client`

```python
class KakaoApiError(RuntimeError):
    status_code: int | None

class KakaoLocalClient:
    BASE: str = "https://dapi.kakao.com"
    MAX_PAGES: int = 3
    PAGE_SIZE: int = 15

    def __init__(
        self,
        *,
        api_key: str,
        sleep_ms: int = 200,
        max_retries: int = 3,
        timeout: float = 10.0,
        read_timeout: float = 15.0,
    ) -> None: ...

    def coord2regioncode(self, x: float, y: float) -> list[dict]: ...

    def search_category(
        self,
        category_group_code: str,
        x: float,
        y: float,
        radius: int,
        *,
        sort: str = "distance",
    ) -> list[dict]: ...

    def search_keyword(
        self,
        query: str,
        *,
        x: float | None = None,
        y: float | None = None,
        radius: int | None = None,
        sort: str = "accuracy",
    ) -> list[dict]: ...

    def close(self) -> None: ...

def get_kakao_client(**overrides) -> KakaoLocalClient:
    """Factory. Pulls api_key from app.config.get_settings()."""
```

## `app.collectors.grid_strategy`

```python
DENSE_THRESHOLD: int = 40
DEFAULT_RADIUS_M: int = 1000
PROBE_CATEGORY: str = "FD6"

Cell = tuple[float, float, int]  # (center_lng, center_lat, radius_m)

def plan_cells(
    region: RegionMaster,
    probe_count: int | None = None,
    *,
    threshold: int = DENSE_THRESHOLD,
) -> list[Cell]: ...

def probe_and_plan(
    region: RegionMaster, client: KakaoLocalClient
) -> list[Cell]: ...
```

## `app.collectors.region_master_loader`

```python
def load_active(db: Session, target_city: str) -> list[RegionMaster]: ...
def load_by_codes(db: Session, region_codes: list[str]) -> list[RegionMaster]: ...
```

## `app.collectors.category_collector`

```python
CATEGORY_CODES: tuple[str, ...] = ("FD6", "CE7", "CT1", "AT4", "AC5")

def collect_region_categories(
    db: Session,
    client: KakaoLocalClient,
    region: RegionMaster,
    batch_id: str,
) -> int:
    """Returns number of upserted documents."""
```

## `app.collectors.keyword_collector`

```python
KEYWORD_TEMPLATES: tuple[str, ...] = (
    "맛집", "카페", "원데이클래스", "공방", "운동",
)
KEYWORD_RADIUS_M: int = 2000

def keyword_for(emd: str, template: str) -> str: ...

def collect_region_keywords(
    db: Session,
    client: KakaoLocalClient,
    region: RegionMaster,
    batch_id: str,
) -> int:
    """Returns number of upserted documents."""
```

## `app.collectors._upsert`

```python
def upsert_docs(
    db: Session,
    docs: Iterable[dict],
    *,
    region: RegionMaster,
    search_type: str,          # 'category' | 'keyword'
    batch_id: str,
    search_query: str | None = None,
) -> int: ...
```

## `app.jobs.bootstrap_regions`

```python
def run_bootstrap(
    *,
    region_csv: Path | None = None,
    mapping_json: Path | None = None,
) -> dict:
    """
    Returns:
        {
            "regions_loaded": int,
            "mappings_loaded": int,
            "region_csv": str,
            "mapping_json": str,
        }
    """
```

## `app.jobs.full_rebuild`

```python
def run_full_rebuild(
    target_city: str = "suwon",
    *,
    client: KakaoLocalClient | None = None,
) -> dict:
    """
    Returns:
        {
            "batch_id": "batch_YYYYMMDD_HHMMSS",
            "target_city": str,
            "regions_total": int,
            "regions_processed": int,
            "regions_failed": int,
            "places_upserted": int,
            "failed": list[{"region_id": int, "region_code": str, "error": str}],
        }
    """
```

Never raises on per-region error — inspect `failed` instead. If `client`
is passed in, the caller owns its lifecycle; otherwise the job creates
and closes one internally.

## `app.jobs.incremental_refresh`

```python
ACTIVE_WINDOW: timedelta = timedelta(days=7)
INACTIVE_WINDOW: timedelta = timedelta(days=30)

def run_incremental_refresh(
    target_city: str = "suwon",
    *,
    force: bool = False,
    client: KakaoLocalClient | None = None,
) -> dict:
    """
    Returns:
        {
            "batch_id": "inc_YYYYMMDD_HHMMSS",
            "target_city": str,
            "force": bool,
            "regions_candidates": int,
            "regions_targeted": int,
            "regions_skipped": int,
            "regions_processed": int,
            "regions_failed": int,
            "places_upserted": int,
            "failed": list[{"region_id": int, "region_code": str, "error": str}],
        }
    """
```

MVP refresh rule: refresh if `last_collected_at IS NULL` or older than
`ACTIVE_WINDOW` (7 days). `INACTIVE_WINDOW` exists for v1.1 when the
active/inactive signal from the real-service DB is wired in.
