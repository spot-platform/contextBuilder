# 04_processor — dataflow (raw → normalized → feature → persona → spot)

End-to-end column flow for a single `dataset_version`. Every row here
describes one logical column/value and which step writes it.

## 1. raw → normalized

```
place_raw_kakao (collector)          place_normalized (processor STEP 3)
──────────────────────────────────   ─────────────────────────────────────
source_place_id            ───────→  source_place_id                (PK component)
region_id                  ───────→  region_id
place_name                 ───────→  name
category_group_code        ───┐
category_name              ───┼──→   category_mapper.map_place()
search_query               ───┘       │
                                      ├─→ primary_category
                                      ├─→ is_food / is_cafe / is_activity /
                                      │   is_park / is_culture / is_nightlife /
                                      │   is_lesson  (multi-tag booleans)
                                      ├─→ mapping_confidence
                                      └─→ sub_category (= raw.category_name)
x                          ───────→  lng
y                          ───────→  lat
address_name               ───────→  address_name
road_address_name          ───────→  road_address_name
collected_at               ───────→  collected_at
                                     source              = 'kakao'
                                     updated_at          = now()
                           (derived) is_night_friendly   = is_nightlife OR cat ∈ {주점,바,포차}
                           (derived) is_group_friendly   = is_activity OR is_lesson OR cat ∈ {파티,단체,모임}
```

**Dedup**: 1차는 `source_place_id` exact inside the fetched batch
(first-wins). 2차(좌표 10m + 이름 rapidfuzz ratio ≥ 0.80)는 **로그만**.

**Upsert key**: `(source, source_place_id)` — re-running normalize for
the same raw row overwrites without duplicating.

## 2. normalized → region_feature

```
place_normalized rows  ──group by region_id──▶  counts per region
                                                 │
                                                 ├─ food_count  / area_km2 → food_density
                                                 ├─ cafe_count  / area_km2 → cafe_density
                                                 ├─ activity_count / area_km2 → activity_density
                                                 ├─ nightlife_count / area_km2 → nightlife_density
                                                 └─ lesson_count / area_km2 → lesson_density

percentile_rank across WHOLE target_city once (critical!):
      food_density       → food_density_norm      (stored in feature_json.density_norm.food)
      cafe_density       → cafe_density_norm      (stored in feature_json.density_norm.cafe)
      activity_density   → activity_density_norm  (feature_json.density_norm.activity)
      nightlife_density  → nightlife_density_norm (feature_json.density_norm.nightlife)
      lesson_density     → lesson_density_norm    (feature_json.density_norm.lesson)

park_access_score      = min(1, park_count / 3)
culture_score          = min(1, culture_count / 5)
night_liveliness_score = sigmoid_normalize(nightlife_density, midpoint=0.5, steepness=4.0)

casual_meetup_score  = 0.40*food_norm + 0.35*cafe_norm    + 0.25*park_access
lesson_spot_score    = 0.50*lesson_norm + 0.30*culture    + 0.20*activity_norm
solo_activity_score  = 0.40*cafe_norm + 0.30*park_access  + 0.30*culture
group_activity_score = 0.40*activity_norm + 0.35*food_norm + 0.25*lesson_norm

kakao_raw_score      = mean(casual_meetup, lesson_spot, solo_activity, group_activity)
alpha_used           = 1.0             (MVP — no real data)
beta_used            = 0.0
real_data_score      = 0.0
blended_score        = alpha*kakao_raw + beta*real_data = kakao_raw_score

raw_place_count        = count(place_raw_kakao) per region
normalized_place_count = count(place_normalized) per region
feature_json           = {density_raw, density_norm, park_count, culture_count}
```

**Upsert key**: `(region_id, dataset_version)` — new version inserts a
fresh row.

## 3. region_feature → persona_region_weight

```
persona_types.json  ×  region_feature (per dataset_version)
                     │
                     ▼
category_match(persona, feature) =
    Σ  persona.category_preferences[k] * density_lookup(feature, k)

 where density_lookup:
   food/cafe/activity/nightlife/lesson → feature_json.density_norm[k]
   park                                → feature.park_access_score
   culture                             → feature.culture_score

affinity_score       = category_match
create_offer_score   = affinity * supply_factor   (MVP: 1.0)
create_request_score = affinity * demand_factor   (MVP: 1.0)
join_score           = affinity
explanation_json     = {formula, category_contributions, supply_factor,
                        demand_factor, time_match: null}
```

**Upsert key**: `(dataset_version, persona_type, region_id)`.

## 4. region_feature → spot_seed_dataset

```
SPOT_TYPES = {
    casual_meetup → [food, cafe],
    lesson        → [lesson, culture],
    activity      → [activity, park],
    night_social  → [nightlife, food],
    solo_healing  → [cafe, park, culture],
}

for region × spot_type × category:
    spot_suitability      = feature.<spot_type>_score
    expected_supply_score = category density lookup (same table as persona)
    expected_demand_score = 0.7 * spot_suitability + 0.3 * supply
    final_weight          = 0.55 * demand + 0.45 * supply    (clamped to [0,1])
    recommended_capacity  = fixed default per spot_type (MVP)
    recommended_time_slots = fixed default per spot_type (MVP)
    price_band            = fixed default per spot_type (MVP)
    payload_json          = {spot_suitability, formula}
```

**Upsert key**: `(dataset_version, region_id, spot_type, category)`.

## 5. Publish (dataset_version row)

```
publisher_service.publish():
    INSERT or SELECT dataset_version(version_name) status=building
    region_count = count(region_feature where dataset_version=...)
    place_count  = count(spot_seed_dataset where dataset_version=...)

    verify_quality():
        1. every active region has region_feature row               (hard)
        2. region_feature.raw_place_count > 0                       (hard)
        3. region_feature numeric fields finite                     (hard)
        4. persona_region_weight.affinity_score finite              (hard)
        5. spot_seed_dataset.final_weight ∈ [0, 1] and finite       (hard)
        6. row count swing > 50% vs previous success                (warning only)

    issues == []  →  status='success', built_at=now()
    issues != []  →  status='failed',  error_message=joined issues
    previous success rows are NEVER modified
```

## Column contract coverage (spot check)

This flow covers every `processor`-owned column in `column_contract.md`:

- `place_normalized`: all 7 base `is_*`, both derived, `primary_category`,
  `mapping_confidence`, lat/lng, address fields, `source='kakao'`.
- `region_feature`: 5 densities + 3 scores + 4 spot suitabilities +
  kakao/real/blended + alpha/beta + raw/normalized counts + feature_json.
- `persona_region_weight`: affinity + 3 derived + explanation_json.
- `spot_seed_dataset`: demand/supply, final_weight, capacity, time_slots,
  price_band, payload_json.
- `dataset_version`: version_name, build_type, target_city, built_at,
  region_count, place_count, status, error_message.

Not written by T04 (left to v1.1): `real_activity_agg.*`,
`region_feature.alpha_used/beta_used` move off 1.0/0.0,
`source_window_start/end` on `dataset_version`.
