# column_contract.md — T02 column responsibility contract

This is the **authoritative column-level contract** between:

- `collector-engineer` — owns Kakao Local API ingest (writes
  `place_raw_kakao`, advances `region_master.last_collected_at`).
- `processor-engineer` — owns normalization, feature building,
  real-data blending, persona scoring, and final seed output (writes
  `place_normalized`, `region_feature`, `real_activity_agg`,
  `persona_region_weight`, `spot_seed_dataset`, `dataset_version`).
- `integration-qa` — reads everything for validation and publish gates.

If a column's "writer" is not listed for you, **do not write it**.
If your agent needs a field that isn't here, flag it to orchestrator
rather than silently widening the schema.

All NOT NULL / DEFAULT / type info is copied verbatim from plan §4 —
`NO` means the column is `NOT NULL` at the DDL level; `YES` means the
column accepts NULL.

---

## region_master (plan §4-1)

Seeded from `data/region_master_suwon.csv` by
`scripts/load_region_master.py`. Collector advances
`last_collected_at`; nothing else on this table moves after seeding.

| Column | Type | NULL | Writer | Reader |
|---|---|---|---|---|
| `id` | BIGSERIAL PK | NO | DB | all |
| `region_code` | VARCHAR(20) UNIQUE | NO | seed-script | all |
| `sido` | VARCHAR(20) | NO | seed-script | all |
| `sigungu` | VARCHAR(20) | NO | seed-script | all |
| `emd` | VARCHAR(30) | NO | seed-script | all |
| `center_lng` | DOUBLE | NO | seed-script | collector (grid split), processor |
| `center_lat` | DOUBLE | NO | seed-script | collector (grid split), processor |
| `bbox_min_lng` | DOUBLE | YES | seed-script | collector (grid split) |
| `bbox_min_lat` | DOUBLE | YES | seed-script | collector (grid split) |
| `bbox_max_lng` | DOUBLE | YES | seed-script | collector (grid split) |
| `bbox_max_lat` | DOUBLE | YES | seed-script | collector (grid split) |
| `area_km2` | DOUBLE | YES | seed-script | processor (density denom) |
| `grid_level` | SMALLINT default 0 | YES | seed-script | collector |
| `target_city` | VARCHAR(20) | YES | seed-script | all (scoping) |
| `is_active` | BOOL default TRUE | YES | seed-script | collector (filter), processor |
| `last_collected_at` | TIMESTAMPTZ | YES | **collector** | processor, qa |
| `created_at` | TIMESTAMPTZ default NOW() | YES | DB | qa |
| `updated_at` | TIMESTAMPTZ default NOW() | YES | seed-script | qa |

## place_raw_kakao (plan §4-2)

Collector writes one row per `(source_place_id, region_id)`. Processor
reads everything, optionally pulls additional fields out of `raw_json`
when it needs something not yet promoted to a dedicated column.

| Column | Type | NULL | Writer | Reader |
|---|---|---|---|---|
| `id` | BIGSERIAL PK | NO | DB | processor |
| `region_id` | BIGINT FK | NO | collector | processor |
| `source_place_id` | VARCHAR(30) | NO | collector | processor |
| `place_name` | VARCHAR(200) | NO | collector | processor |
| `category_name` | VARCHAR(200) | YES | collector | processor (mapping rule) |
| `category_group_code` | VARCHAR(10) | YES | collector | processor (mapping rule) |
| `category_group_name` | VARCHAR(50) | YES | collector | processor (debug) |
| `phone` | VARCHAR(30) | YES | collector | processor |
| `address_name` | VARCHAR(300) | YES | collector | processor |
| `road_address_name` | VARCHAR(300) | YES | collector | processor |
| `x` (lng) | DOUBLE | NO | collector | processor |
| `y` (lat) | DOUBLE | NO | collector | processor |
| `place_url` | VARCHAR(500) | YES | collector | processor |
| `distance` | VARCHAR(20) | YES | collector | processor |
| `raw_json` | JSONB | YES | collector | processor (optional fallback) |
| `search_type` | VARCHAR(20) | NO | collector (`'category'`\|`'keyword'`) | processor |
| `search_query` | VARCHAR(100) | YES | collector (keyword runs only) | processor (mapping rule) |
| `collected_at` | TIMESTAMPTZ default NOW() | YES | DB | qa |
| `batch_id` | VARCHAR(50) | YES | collector | qa |

UNIQUE(`source_place_id`, `region_id`) — collector must upsert on this
key, not blind-insert.

## category_mapping_rule (plan §4-3)

Data-driven config. Seeded once from
`data/category_mapping_seed.json`; the only other writer is a human
via the seed script.

| Column | Type | NULL | Writer | Reader |
|---|---|---|---|---|
| `id` | SERIAL PK | NO | DB | all |
| `kakao_category_group_code` | VARCHAR(10) | YES | seed-script | processor |
| `kakao_category_pattern` | VARCHAR(200) | YES | seed-script | processor |
| `keyword_pattern` | VARCHAR(200) | YES | seed-script | processor |
| `internal_tag` | VARCHAR(30) | NO | seed-script | processor |
| `confidence` | DOUBLE default 1.0 | YES | seed-script | processor |
| `priority` | INT default 0 | YES | seed-script | processor |
| `is_active` | BOOL default TRUE | YES | seed-script | processor |
| `notes` | TEXT | YES | seed-script | qa |

## place_normalized (plan §4-4)

Processor STEP 3 output. Multi-tag booleans — several may be `true`
simultaneously; `primary_category` is the dominant one chosen by
priority/confidence from `category_mapping_rule`.

| Column | Type | NULL | Writer | Reader |
|---|---|---|---|---|
| `id` | BIGSERIAL PK | NO | DB | processor |
| `region_id` | BIGINT FK | NO | processor | processor, qa |
| `source` | VARCHAR(20) default `'kakao'` | YES | processor | processor |
| `source_place_id` | VARCHAR(30) | NO | processor | processor |
| `name` | VARCHAR(200) | NO | processor | processor, qa |
| `primary_category` | VARCHAR(30) | NO | processor | processor (feature) |
| `sub_category` | VARCHAR(100) | YES | processor | qa |
| `lng` | DOUBLE | NO | processor | processor |
| `lat` | DOUBLE | NO | processor | processor |
| `address_name` | VARCHAR(300) | YES | processor | qa |
| `road_address_name` | VARCHAR(300) | YES | processor | qa |
| `is_food` | BOOL default FALSE | YES | processor | processor (feature) |
| `is_cafe` | BOOL default FALSE | YES | processor | processor (feature) |
| `is_activity` | BOOL default FALSE | YES | processor | processor (feature) |
| `is_park` | BOOL default FALSE | YES | processor | processor (feature) |
| `is_culture` | BOOL default FALSE | YES | processor | processor (feature) |
| `is_nightlife` | BOOL default FALSE | YES | processor | processor (feature) |
| `is_lesson` | BOOL default FALSE | YES | processor | processor (feature) |
| `is_night_friendly` | BOOL default FALSE | YES | processor (derived) | processor (feature) |
| `is_group_friendly` | BOOL default FALSE | YES | processor (derived) | processor (feature) |
| `mapping_confidence` | DOUBLE default 1.0 | YES | processor | qa |
| `collected_at` | TIMESTAMPTZ | YES | processor (from raw) | qa |
| `updated_at` | TIMESTAMPTZ default NOW() | YES | processor | qa |

UNIQUE(`source`, `source_place_id`). Processor must upsert on this key
— the same raw row can be re-normalized many times across dataset
versions.

## region_feature (plan §4-5)

Processor STEP 4 writes initial row. STEP 6 updates the blended /
alpha / beta columns after real data is merged. Keyed by
`(region_id, dataset_version)` — a new dataset version ALWAYS inserts a
fresh row, never overwrites an earlier version.

| Column | Type | NULL | Writer | Reader |
|---|---|---|---|---|
| `id` | BIGSERIAL PK | NO | DB | processor |
| `region_id` | BIGINT FK | NO | processor (STEP 4) | processor, qa |
| `dataset_version` | VARCHAR(50) | NO | processor (STEP 4) | processor, qa |
| `food_density` | DOUBLE default 0 | YES | processor (STEP 4) | processor (persona) |
| `cafe_density` | DOUBLE default 0 | YES | processor (STEP 4) | processor (persona) |
| `activity_density` | DOUBLE default 0 | YES | processor (STEP 4) | processor (persona) |
| `nightlife_density` | DOUBLE default 0 | YES | processor (STEP 4) | processor (persona) |
| `lesson_density` | DOUBLE default 0 | YES | processor (STEP 4) | processor (persona) |
| `park_access_score` | DOUBLE default 0 | YES | processor (STEP 4) | processor (persona) |
| `culture_score` | DOUBLE default 0 | YES | processor (STEP 4) | processor (persona) |
| `night_liveliness_score` | DOUBLE default 0 | YES | processor (STEP 4) | processor (persona) |
| `casual_meetup_score` | DOUBLE default 0 | YES | processor (STEP 4) | processor (spot seed) |
| `lesson_spot_score` | DOUBLE default 0 | YES | processor (STEP 4) | processor (spot seed) |
| `solo_activity_score` | DOUBLE default 0 | YES | processor (STEP 4) | processor (spot seed) |
| `group_activity_score` | DOUBLE default 0 | YES | processor (STEP 4) | processor (spot seed) |
| `kakao_raw_score` | DOUBLE | YES | processor (STEP 4) | processor (STEP 6) |
| `real_data_score` | DOUBLE | YES | processor (STEP 6) | processor |
| `blended_score` | DOUBLE | YES | processor (STEP 6) | processor, qa |
| `alpha_used` | DOUBLE | YES | processor (STEP 6) | qa (audit) |
| `beta_used` | DOUBLE | YES | processor (STEP 6) | qa (audit) |
| `raw_place_count` | INT default 0 | YES | processor (STEP 4) | qa |
| `normalized_place_count` | INT default 0 | YES | processor (STEP 4) | qa |
| `feature_json` | JSONB | YES | processor | processor, qa |
| `created_at` | TIMESTAMPTZ default NOW() | YES | DB | qa |

UNIQUE(`region_id`, `dataset_version`).

## real_activity_agg (plan §4-6)

Built by processor STEP 5 from the read-only real-service DB
(`app/db_readonly.py`). Keyed by
`(region_id, window_start, window_end)`.

| Column | Type | NULL | Writer | Reader |
|---|---|---|---|---|
| `id` | BIGSERIAL PK | NO | DB | processor |
| `region_id` | BIGINT FK | NO | processor (STEP 5) | processor (STEP 6) |
| `window_start` | DATE | NO | processor (STEP 5) | processor, qa |
| `window_end` | DATE | NO | processor (STEP 5) | processor, qa |
| `real_spot_count` | INT default 0 | YES | processor (STEP 5) | processor (STEP 6) |
| `real_join_count` | INT default 0 | YES | processor (STEP 5) | processor (STEP 6) |
| `real_completion_count` | INT default 0 | YES | processor (STEP 5) | processor (STEP 6) |
| `real_cancel_count` | INT default 0 | YES | processor (STEP 5) | processor (STEP 6) |
| `real_noshow_count` | INT default 0 | YES | processor (STEP 5) | processor (STEP 6) |
| `completion_rate` | DOUBLE | YES | processor (STEP 5) | processor (STEP 6) |
| `cancel_rate` | DOUBLE | YES | processor (STEP 5) | processor (STEP 6) |
| `noshow_rate` | DOUBLE | YES | processor (STEP 5) | processor (STEP 6) |
| `real_food_spot_ratio` | DOUBLE default 0 | YES | processor (STEP 5) | processor (STEP 6) |
| `real_activity_spot_ratio` | DOUBLE default 0 | YES | processor (STEP 5) | processor (STEP 6) |
| `real_lesson_spot_ratio` | DOUBLE default 0 | YES | processor (STEP 5) | processor (STEP 6) |
| `real_night_spot_ratio` | DOUBLE default 0 | YES | processor (STEP 5) | processor (STEP 6) |
| `time_slot_distribution` | JSONB | YES | processor (STEP 5) | processor (spot seed) |
| `real_avg_group_size` | DOUBLE | YES | processor (STEP 5) | processor |
| `real_hot_score` | DOUBLE | YES | processor (STEP 5) | processor (STEP 6) |
| `created_at` | TIMESTAMPTZ default NOW() | YES | DB | qa |

## persona_region_weight (plan §4-7)

Processor STEP 8 output. Keyed by
`(dataset_version, persona_type, region_id)`.

| Column | Type | NULL | Writer | Reader |
|---|---|---|---|---|
| `id` | BIGSERIAL PK | NO | DB | processor |
| `dataset_version` | VARCHAR(50) | NO | processor (STEP 8) | processor, qa |
| `persona_type` | VARCHAR(50) | NO | processor (STEP 8) | processor, qa |
| `region_id` | BIGINT FK | NO | processor (STEP 8) | processor |
| `affinity_score` | DOUBLE | NO | processor (STEP 8) | processor (spot seed), api |
| `create_offer_score` | DOUBLE | YES | processor (STEP 8) | api |
| `create_request_score` | DOUBLE | YES | processor (STEP 8) | api |
| `join_score` | DOUBLE | YES | processor (STEP 8) | api |
| `explanation_json` | JSONB | YES | processor (STEP 8) | qa (debug) |
| `created_at` | TIMESTAMPTZ default NOW() | YES | DB | qa |

## spot_seed_dataset (plan §4-8)

Processor STEP 9 output — the actual publish artifact. Keyed by
`(dataset_version, region_id, spot_type, category)`.

| Column | Type | NULL | Writer | Reader |
|---|---|---|---|---|
| `id` | BIGSERIAL PK | NO | DB | api |
| `dataset_version` | VARCHAR(50) | NO | processor (STEP 9) | api, qa |
| `region_id` | BIGINT FK | NO | processor (STEP 9) | api |
| `spot_type` | VARCHAR(50) | NO | processor (STEP 9) | api |
| `category` | VARCHAR(30) | NO | processor (STEP 9) | api |
| `expected_demand_score` | DOUBLE | YES | processor (STEP 9) | api |
| `expected_supply_score` | DOUBLE | YES | processor (STEP 9) | api |
| `recommended_capacity` | INT | YES | processor (STEP 9) | api |
| `recommended_time_slots` | JSONB | YES | processor (STEP 9) | api |
| `price_band` | VARCHAR(20) | YES | processor (STEP 9) | api |
| `final_weight` | DOUBLE | NO | processor (STEP 9) | api (ranking) |
| `payload_json` | JSONB | YES | processor (STEP 9) | api |
| `created_at` | TIMESTAMPTZ default NOW() | YES | DB | qa |

## dataset_version (plan §4-9)

Batch run registry. Processor inserts with `status='building'` at the
start of a run, flips to `success`/`failed` at the end. Admin API (qa)
reads for health checks.

| Column | Type | NULL | Writer | Reader |
|---|---|---|---|---|
| `id` | BIGSERIAL PK | NO | DB | all |
| `version_name` | VARCHAR(50) UNIQUE | NO | processor | all |
| `build_type` | VARCHAR(20) | NO | processor (`'full'`\|`'incremental'`) | qa |
| `target_city` | VARCHAR(20) | YES | processor | qa |
| `built_at` | TIMESTAMPTZ | YES | processor (on success) | qa |
| `source_window_start` | DATE | YES | processor (STEP 5 window) | qa |
| `source_window_end` | DATE | YES | processor (STEP 5 window) | qa |
| `region_count` | INT | YES | processor | qa |
| `place_count` | INT | YES | processor | qa |
| `status` | VARCHAR(20) default `'building'` | YES | processor (`building`→`success`\|`failed`) | api, qa |
| `error_message` | TEXT | YES | processor (on failure) | qa |
| `notes` | TEXT | YES | processor, operator | qa |
| `created_at` | TIMESTAMPTZ default NOW() | YES | DB | qa |

---

## Invariants other agents must honour

1. **No JSONB fallback abuse.** If a piece of data gets promoted to a
   dedicated column later, processor must start writing both during a
   transition window. Do not silently stop updating a column.
2. **Dataset version discipline.** `region_feature`,
   `persona_region_weight`, and `spot_seed_dataset` are append-only per
   version. Re-running STEP 4 for the same `dataset_version` must
   upsert; building a new version must insert a new row set.
3. **Suwon scoping.** Every batch job must filter by
   `region_master.is_active = TRUE` and, when applicable,
   `target_city = 'suwon'`. Do not assume other cities are empty.
4. **Upsert keys.** Collector upserts on
   `(source_place_id, region_id)`. Processor upserts place_normalized
   on `(source, source_place_id)`. Any conflict handling MUST use
   these names from the `UNIQUE` constraints list in `model_index.md`.
