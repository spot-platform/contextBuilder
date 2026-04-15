# model_index.md — T02 schema inventory

Single-glance reference for every table/model shipped in T02. Produced
by `schema-designer`; consumed by `collector-engineer`,
`processor-engineer`, and `integration-qa`.

All models inherit from `app.db.Base` and are re-exported from
`app/models/__init__.py`. The initial Alembic migration is
`migrations/versions/0001_initial_schema.py` (revision `0001_initial`).

## Table → model → file

| # | Table | Model class | File | Cols | Plan §|
|---|---|---|---|---|---|
| 1 | `region_master` | `RegionMaster` | `app/models/region.py` | 18 | 4-1 |
| 2 | `place_raw_kakao` | `PlaceRawKakao` | `app/models/place_raw.py` | 19 | 4-2 |
| 3 | `category_mapping_rule` | `CategoryMappingRule` | `app/models/category_mapping_rule.py` | 9 | 4-3 |
| 4 | `place_normalized` | `PlaceNormalized` | `app/models/place_normalized.py` | 22 | 4-4 |
| 5 | `region_feature` | `RegionFeature` | `app/models/region_feature.py` | 25 | 4-5 |
| 6 | `real_activity_agg` | `RealActivityAgg` | `app/models/real_activity_agg.py` | 20 | 4-6 |
| 7 | `persona_region_weight` | `PersonaRegionWeight` | `app/models/persona_region_weight.py` | 10 | 4-7 |
| 8 | `spot_seed_dataset` | `SpotSeedDataset` | `app/models/spot_seed.py` | 13 | 4-8 |
| 9 | `dataset_version` | `DatasetVersion` | `app/models/dataset_version.py` | 14 | 4-9 |

Column counts above include `id` and all `created_at`/`updated_at`
columns exactly as they appear in the DDL.

## Foreign keys

Every FK points at `region_master.id`. No FK is declared with
`ondelete`/`onupdate`; the plan DDL does not specify cascade behaviour
and batch jobs only soft-invalidate rows via `is_active` anyway.

| Child table | Column | → Parent |
|---|---|---|
| `place_raw_kakao` | `region_id` | `region_master.id` |
| `place_normalized` | `region_id` | `region_master.id` |
| `region_feature` | `region_id` | `region_master.id` |
| `real_activity_agg` | `region_id` | `region_master.id` |
| `persona_region_weight` | `region_id` | `region_master.id` |
| `spot_seed_dataset` | `region_id` | `region_master.id` |

No `relationship()` helpers declared on purpose — batch jobs join
explicitly with `select().join(...)` to keep N+1 risk out of the
picture.

## UNIQUE constraints

| Table | Columns | Constraint name |
|---|---|---|
| `region_master` | `region_code` | implicit (column-level `unique=True`) |
| `place_raw_kakao` | `source_place_id, region_id` | `uq_place_raw_source_region` |
| `place_normalized` | `source, source_place_id` | `uq_place_norm_source` |
| `region_feature` | `region_id, dataset_version` | `uq_region_feature_region_version` |
| `real_activity_agg` | `region_id, window_start, window_end` | `uq_real_activity_region_window` |
| `persona_region_weight` | `dataset_version, persona_type, region_id` | `uq_persona_region_weight` |
| `spot_seed_dataset` | `dataset_version, region_id, spot_type, category` | `uq_spot_seed_version_region_type_category` |
| `dataset_version` | `version_name` | implicit (column-level `unique=True`) |

`category_mapping_rule` has no UNIQUE per plan §4-3 — the seed script
deletes matching tuples before insert to stay idempotent.

## Indexes

All named exactly as in plan §4.

| Index | Table | Columns |
|---|---|---|
| `idx_region_target_city` | `region_master` | `target_city` |
| `idx_region_active` | `region_master` | `is_active` |
| `idx_region_last_collected` | `region_master` | `last_collected_at` |
| `idx_place_raw_region` | `place_raw_kakao` | `region_id` |
| `idx_place_raw_source_id` | `place_raw_kakao` | `source_place_id` |
| `idx_place_raw_batch` | `place_raw_kakao` | `batch_id` |
| `idx_place_norm_region` | `place_normalized` | `region_id` |
| `idx_place_norm_category` | `place_normalized` | `primary_category` |
| `idx_dataset_version_status` | `dataset_version` | `status` |

## Migration & seed commands

```bash
# Apply the initial schema.
alembic upgrade head

# Load seed data (DB must already be migrated).
python -m scripts.load_region_master
python -m scripts.load_category_mapping
```

`load_region_master.py` tags every row with `target_city='suwon'` and
`is_active=True` per plan §4-1. `load_category_mapping.py` reads
`data/category_mapping_seed.json` and replaces any existing rule with
the same identifying tuple.
