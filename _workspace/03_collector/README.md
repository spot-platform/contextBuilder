# T03 — Kakao collector pipeline

Implements plan §5 (Kakao API strategy) and §6 STEP 1–2 (collection
pipeline). Scope limited to the MVP range declared in §15.

## Modules created

| Path | Purpose |
|---|---|
| `app/collectors/kakao_local_client.py` | Synchronous httpx client with 429/5xx retry, 200ms pacing, pagination, and wrappers for `search/category`, `search/keyword`, `coord2regioncode`. Factory: `get_kakao_client()`. |
| `app/collectors/grid_strategy.py` | `plan_cells(region, probe_count)` and `probe_and_plan(region, client)`. MVP splits a dense region (≥40 probe docs) into a fixed 2×2 grid, otherwise returns a single 1km cell. |
| `app/collectors/_upsert.py` | Shared `upsert_docs()` that writes `place_raw_kakao` rows with PostgreSQL `ON CONFLICT` on `(source_place_id, region_id)`. Handles coord string→float coercion. |
| `app/collectors/category_collector.py` | `collect_region_categories(db, client, region, batch_id)` — sweeps the 5 MVP category codes across every grid cell. |
| `app/collectors/keyword_collector.py` | `collect_region_keywords(db, client, region, batch_id)` — runs 5 MVP keyword templates against `{emd} + keyword`. |
| `app/collectors/region_master_loader.py` | `load_active(db, target_city)` / `load_by_codes(db, codes)`. Enforces `is_active=True` + `target_city` scoping. |
| `app/jobs/bootstrap_regions.py` | `run_bootstrap()` — thin wrapper that re-uses `scripts/load_region_master.py` and `scripts/load_category_mapping.py`. |
| `app/jobs/full_rebuild.py` | `run_full_rebuild(target_city='suwon')` — produces a `batch_id`, loops every active region with per-region session isolation, advances `last_collected_at`, and returns a failure list. |
| `app/jobs/incremental_refresh.py` | `run_incremental_refresh(target_city, force=False)` — MVP skeleton. Treats every active region on the 7-day cadence; `force=True` ignores the watermark. |
| `tests/test_kakao_client.py` | respx-backed unit tests: 200 path, pagination short-circuit, full pagination cap, 429 + Retry-After, 5xx retry-then-success, 5xx retry exhaustion, hard 4xx, `coord2regioncode`, missing key. |
| `tests/test_grid_strategy.py` | Pure-function tests for `plan_cells`: no probe, below threshold, dense with bbox (2×2 shape & centres), dense without bbox (warn + fallback), threshold boundary. |

## Category / keyword sets (MVP, plan §15)

```python
CATEGORY_CODES = ("FD6", "CE7", "CT1", "AT4", "AC5")  # food, cafe, culture, tourism, academy
KEYWORD_TEMPLATES = ("맛집", "카페", "원데이클래스", "공방", "운동")
```

Plan §5-5 lists 9 keyword templates; v1.1 is expected to restore the
other 4 (`술집/바`, `공원/산책`, `스터디카페`, `전시/갤러리`).

## Running

```bash
# 1. Seed the region master + category mapping (idempotent)
python -c "from app.jobs.bootstrap_regions import run_bootstrap; print(run_bootstrap())"

# 2. Full Kakao sweep for Suwon
python -c "from app.jobs.full_rebuild import run_full_rebuild; print(run_full_rebuild())"

# 3. Incremental (respects 7-day staleness window)
python -c "from app.jobs.incremental_refresh import run_incremental_refresh; print(run_incremental_refresh())"

# Force every active region regardless of last_collected_at:
python -c "from app.jobs.incremental_refresh import run_incremental_refresh; print(run_incremental_refresh(force=True))"
```

`run_full_rebuild` / `run_incremental_refresh` return a summary dict
with `batch_id`, `regions_processed`, `regions_failed`, `places_upserted`,
and a `failed` list containing `{region_id, region_code, error}` for
any region that raised during its sweep.

## Tests

```bash
python -m pytest tests/test_kakao_client.py tests/test_grid_strategy.py
```

No test makes a real HTTP call — respx intercepts every request. A
conftest fixture also monkeypatches `time.sleep` so retries run
instantly.

## place_raw_kakao column responsibility

Per `_workspace/02_schema/column_contract.md`, this T03 implementation
writes every collector-owned field in `place_raw_kakao`:

- `region_id` — from the region row being processed.
- `source_place_id`, `place_name` — always required; docs missing
  either are skipped with a warning.
- `category_name`, `category_group_code`, `category_group_name`,
  `phone`, `address_name`, `road_address_name`, `place_url`,
  `distance` — copied verbatim from the Kakao document.
- `x`, `y` — coerced from string to float (Kakao returns strings).
- `raw_json` — the original Kakao document dict, stored intact.
- `search_type` — `'category'` or `'keyword'` (never null).
- `search_query` — set only for keyword sweeps (`{emd} {template}`).
- `batch_id` — `batch_YYYYMMDD_HHMMSS` for full rebuilds,
  `inc_YYYYMMDD_HHMMSS` for incrementals.
- `collected_at` — `NOW()` via `ON CONFLICT` `SET` so re-upsert
  refreshes the timestamp.

Upsert conflict target is the unique constraint
`uq_place_raw_source_region` on `(source_place_id, region_id)`.

## Notes for downstream agents

- `raw_json` is a plain dict (stored as JSONB). Keys follow Kakao's
  response schema exactly — `id`, `place_name`, `category_name`,
  `category_group_code`, `x`, `y`, `place_url`, `distance`, ....
- `x`/`y` columns are floats **even though** Kakao returns strings. If
  the processor needs the raw strings it must read `raw_json`.
- T02 seed CSV ships placeholder `(0.0, 0.0)` coordinates for regions
  where real centroids are not filled in yet. The collector will sweep
  those regions, but Kakao results will be garbage until real
  coordinates are loaded — fill `data/region_master_suwon.csv` before
  running `full_rebuild` in production.
- Per-region session isolation means a failing region does not taint
  sibling regions' transactions. The summary's `failed` list is the
  source of truth for alerting.
