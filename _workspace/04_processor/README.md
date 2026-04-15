# 04_processor — pipeline execution order & function inventory

T04 owns plan §6 STEP 3 through §11 STEP 10. All code lives under
`local-context-builder/app/processors/`, `app/services/`, and
`app/jobs/`. The collector (T03) writes the raw tables; everything
downstream is ours.

## Execution order (single build)

```
normalize_places.process_batch(db, batch_id=None)
  → build_region_features.build(db, dataset_version, target_city)
  → merge_real_data.run(target_city)              # v1.1 stub, no-op today
  → build_persona_region_weights.build(db, dataset_version, target_city)
  → build_spot_weights.build(db, dataset_version, target_city)
  → publisher_service.publish(db, dataset_version, target_city)
```

The orchestrator entry point is `app.jobs.build_all_features.run`
which chains every step and returns a summary dict. To re-verify an
existing version, call `app.jobs.publish_dataset.run` directly.

## Function inventory

| Module | Function | Purpose |
|---|---|---|
| `app/processors/category_mapper.py` | `load_rules(db)` | Fetch active `category_mapping_rule` rows, priority DESC |
| | `map_place(raw, rules)` | `(primary_category, tag_dict, confidence)` for one raw row |
| `app/processors/normalize_places.py` | `process_batch(db, batch_id)` | Normalize raw→`place_normalized`, upsert, dedup, tag |
| `app/processors/build_region_features.py` | `build(db, dataset_version, target_city)` | Write `region_feature` for city |
| `app/processors/build_persona_region_weights.py` | `build(db, dataset_version, target_city, persona_file)` | Write `persona_region_weight` |
| `app/processors/build_spot_weights.py` | `build(db, dataset_version, target_city)` | Write `spot_seed_dataset` |
| `app/services/scoring_service.py` | `percentile_rank(values)` | City-wide percentile ranks in `[0, 1]` |
| | `sigmoid_normalize(x, midpoint, steepness)` | Logistic squash |
| | `clip01(x)` | Clamp to `[0, 1]`, NaN/∞ → 0 |
| | `weighted_avg(pairs)` | Safe weighted average |
| `app/services/feature_service.py` | `get_weights(real_agg)` | alpha/beta selection (plan §8-6) |
| `app/services/publisher_service.py` | `verify_quality(db, v, city)` | Quality gate, returns list of issue strings |
| | `publish(db, v, city, build_type)` | `dataset_version` lifecycle |
| `app/jobs/merge_real_data.py` | `run(target_city)` | v1.1 stub |
| `app/jobs/build_all_features.py` | `run(target_city, version=None)` | Full pipeline |
| `app/jobs/publish_dataset.py` | `run(city, version, build_type)` | Publish only |

## Upsert keys

| Table | Unique constraint | Writer |
|---|---|---|
| `place_normalized` | `uq_place_norm_source` (`source`, `source_place_id`) | processor |
| `region_feature` | `uq_region_feature_region_version` (`region_id`, `dataset_version`) | processor |
| `persona_region_weight` | `uq_persona_region_weight` (`dataset_version`, `persona_type`, `region_id`) | processor |
| `spot_seed_dataset` | `uq_spot_seed_version_region_type_category` (`dataset_version`, `region_id`, `spot_type`, `category`) | processor |

Every processor step uses Postgres `INSERT ... ON CONFLICT DO UPDATE`
on these constraints so re-running the same `dataset_version` is
idempotent.

## MVP vs v1.1 boundary

| Area | MVP behavior | v1.1 plan |
|---|---|---|
| `merge_real_data` | no-op stub — logs and returns `{"status":"skipped"}` | full read-only aggregation via `app/db_readonly.py` |
| `feature_service.get_weights` | reachable from v1.1 callers, always safe | wired into `build_region_features` once real data exists |
| `persona_region_weight.time_match` | stored as `null` inside `explanation_json` | computed from `real_activity_agg.time_slot_distribution` |
| `spot_seed_dataset` capacity / time_slots / price_band | static defaults per `spot_type` | inferred from `real_activity_agg` |
| `raw_place_count` | sourced from `place_raw_kakao` counts | same (no change needed) |

## Unmapped-rate alarm

`normalize_places.process_batch` emits `WARNING` when `other / total >
0.15`. The log line is plain text (``unmapped ratio %.1f%% exceeds
15%% threshold``) — integration-qa should pick it up either from the
log sink or by inspecting the returned summary dict
(`{"mapped":..., "unmapped":...}`).

## Tests

| File | Scope |
|---|---|
| `tests/test_category_mapper.py` | Rule priority / multi-tag / unmapped fallback |
| `tests/test_normalize.py` | Derived tag semantics; mapper→normalizer shape contract |
| `tests/test_region_features.py` | `percentile_rank`, `clip01`, `sigmoid_normalize`, `weighted_avg` + composite formula smoke |
| `tests/test_publisher.py` | Quality gate pass + 5 failure modes via scripted fake session |

All tests are pure-Python; they do not require Postgres. Integration
tests (end-to-end) are integration-qa's job.
