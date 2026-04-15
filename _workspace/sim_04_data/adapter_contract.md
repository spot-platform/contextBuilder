# Adapter Contract — sim_04_data

Engine <-> data adapters for Phase 1 of the spot-simulator. All functions are
pure; they receive loaded dicts as arguments and return a float in [0, 1].
The engine is responsible for loading data once at startup and passing the
dicts in on every call (no module-level state).

All adapters live in `spot-simulator/data/adapters.py`.

## Constants

| Name | Value | Purpose |
|---|---|---|
| `BUDGET_PENALTY_MAX_GAP` | 3 | Denominator for budget gap -> penalty |
| `RECENT_HOST_WINDOW` | 12 | Cooldown window (ticks/hours) after hosting |
| `RECENT_HOST_PENALTY_VALUE` | 0.5 | Flat penalty inside the cooldown window |

## Functions

### `region_create_affinity(agent, region_id, region_features) -> float`

| Aspect | Value |
|---|---|
| Inputs | `agent: AgentState`, `region_id: str`, `region_features: dict[str, dict]` |
| Returns | `float` in `[0.0, 1.0]` |
| Source | `region_features[region_id]["spot_create_affinity"]` |
| Missing region | Returns `0.0`, emits `warnings.warn` (engine keeps running) |
| Called from | `decide_action()` in `engine/decision.py`, `execute_create_spot()` |

Used in the CREATE_SPOT probability formula with weight `0.20`.

### `category_match(agent, spot, persona_templates) -> float`

| Aspect | Value |
|---|---|
| Inputs | `agent`, `spot`, `persona_templates` (unused Phase 1) |
| Returns | `float` in `{0.0, 1.0}` (Phase 1 boolean; Phase 2+ may return partial matches in [0, 1]) |
| Source | `spot.category in agent.interest_categories` |
| Missing spot.category | Returns `0.0` |
| Called from | `decide_action()` / `pick_best_spot()` |

Used in the JOIN_SPOT probability formula with weight `0.25`.

### `budget_penalty(agent, spot, persona_templates, region_features=None) -> float`

| Aspect | Value |
|---|---|
| Inputs | `agent`, `spot`, `persona_templates` (unused), `region_features` (optional for Phase 1 fallback) |
| Returns | `float` in `[0.0, 1.0]` |
| Phase 1 formula | `clamp(abs(agent.budget_level - spot_budget) / 3, 0, 1)` |
| Spot budget source | `spot.budget_level` if present; else `region_features[spot.region_id]["budget_avg_level"]`; else `0.0` penalty |
| Missing data | Returns `0.0` (never blocks actions) |
| Called from | `decide_action()` for JOIN_SPOT |

Used in the JOIN_SPOT probability formula with weight `-0.10`.

### `recent_host_penalty(agent, tick) -> float`

| Aspect | Value |
|---|---|
| Inputs | `agent`, `tick: int` |
| Returns | `0.0` or `RECENT_HOST_PENALTY_VALUE` (=0.5) |
| Rule | If `agent.hosted_spots` is non-empty AND `tick - agent.last_action_tick < 12`, returns 0.5 |
| Called from | `decide_action()` for CREATE_SPOT |

Used in the CREATE_SPOT probability formula with weight `-0.10`.

## Error Handling Summary

| Situation | Behavior |
|---|---|
| Unknown `region_id` in `region_create_affinity` | 0.0 + `warnings.warn` |
| Spot without `category` attribute | `category_match` -> 0.0 |
| Missing budget info on both agent/spot/region | `budget_penalty` -> 0.0 |
| Agent has no `hosted_spots` / `last_action_tick == -1` | `recent_host_penalty` -> 0.0 |

## Usage Example (engine side)

```python
from data.loader import (
    load_persona_templates,
    load_region_features,
    load_persona_region_affinity,
)
from data.adapters import (
    region_create_affinity,
    category_match,
    budget_penalty,
    recent_host_penalty,
)

templates = load_persona_templates("config/persona_templates.yaml")
regions   = load_region_features("data/region_features.json")
affinity  = load_persona_region_affinity("data/persona_region_affinity.json")

# in the tick loop:
ra = region_create_affinity(agent, agent.home_region_id, regions)
cm = category_match(agent, spot, templates)
bp = budget_penalty(agent, spot, templates, regions)
rhp = recent_host_penalty(agent, tick)
```
