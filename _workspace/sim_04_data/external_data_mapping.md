# External Data Mapping — local-context-builder -> spot-simulator

Phase 1 uses dummy JSON files that mirror the shape of the eventual
`local-context-builder` publish output. This table locks the column/key
mapping so Phase 2 can flip to the real DB source without touching the
engine or adapters.

## region_features

Source: `local-context-builder` table `region_features` (per plan §6~§11)
Target: `spot-simulator/data/region_features.json`
Keyed by: `region_id`

| local-context-builder column | simulator JSON key      | Notes |
|---|---|---|
| `emd_cd`                     | `region_id`             | Primary key |
| `emd_nm`                     | `region_name`           | Display name (Korean) |
| `target_city`                | `target_city`           | e.g. `suwon` |
| `density_food_norm`          | `density_food`          | 0..1 |
| `density_cafe_norm`          | `density_cafe`          | 0..1 |
| `density_bar_norm`           | `density_bar`           | 0..1 |
| `density_exercise_norm`      | `density_exercise`      | 0..1 |
| `density_nature_norm`        | `density_nature`        | 0..1 |
| `night_friendliness`         | `night_friendliness`    | 0..1 |
| `group_friendliness`         | `group_friendliness`    | 0..1 |
| `spot_create_affinity`       | `spot_create_affinity`  | 0..1 — consumed by `region_create_affinity()` |
| `budget_avg_level`           | `budget_avg_level`      | int 1..3 — consumed by `budget_penalty()` fallback |

## persona_region_weights

Source: `local-context-builder` table `persona_region_weights`
Target: `spot-simulator/data/persona_region_affinity.json`
Shape: `{ persona_type: { region_id: { create_mult, join_mult } } }`

| local-context-builder column | simulator JSON key | Notes |
|---|---|---|
| `persona_type`               | outer key          | Must match persona_templates.yaml |
| `emd_cd`                     | inner key          | Must exist in region_features |
| `create_score_weight`        | `create_mult`      | float in `[0.5, 1.5]` |
| `join_score_weight`          | `join_mult`        | float in `[0.5, 1.5]` |

## persona_templates (curated, not DB-sourced)

Source: manually curated (there is no DB table for personas themselves;
they are archetype definitions that QA and product agree on).
Target: `spot-simulator/config/persona_templates.yaml`

| Conceptual field        | yaml key               | Notes |
|---|---|---|
| persona type            | top-level key          | `night_social`, `weekend_explorer`, `planner`, `spontaneous`, `homebody` |
| host tendency           | `host_score`           | 0..1 — seeds `AgentState.host_score` |
| join tendency           | `join_score`           | 0..1 — seeds `AgentState.join_score` |
| home emd code           | `home_region`          | Must match `region_features[*].region_id` |
| preferred categories    | `preferred_categories` | list[str] |
| time preference matrix  | `time_preferences`     | flat `{"<day_type>_<time_slot>": weight}` with all 14 keys (`weekday`/`weekend` x `dawn`/`morning`/`late_morning`/`lunch`/`afternoon`/`evening`/`night`) |
| budget level            | `budget_level`         | int 1..3 |

## Invariants Enforced at Load Time

1. `load_persona_templates()` requires all six keys on every persona — any
   missing key raises `KeyError` and the simulator refuses to start.
2. `load_region_features()` re-keys the dict by `region_id` (falling back to
   the JSON key if absent) to guarantee `dict[region_id] -> entry`.
3. `load_persona_region_affinity()` verifies each persona_type maps to a
   dict; it does NOT verify that region_ids exist in `region_features`
   (the agent factory handles that via `_top_active_regions` fallback).

## Phase 2+ Migration Notes

- Replace JSON loads with SQL queries against the `local-context-builder`
  schema. All adapters stay identical because they only see the normalised
  dicts returned by the loaders.
- If new columns appear (e.g. `crowd_level`, `noise_level`), add them as
  additional keys on `region_features` entries. Adapters that don't need
  them will ignore them; new adapters can read them without a migration.
- `persona_region_affinity` will likely become a wide numpy matrix when
  agent counts cross 5,000; the adapter interface can stay the same if we
  wrap the matrix in a `dict`-like view keyed by `persona_type`.
