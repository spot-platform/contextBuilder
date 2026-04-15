# sim_02_models — Column Contract

Task: `sim_02_models_phase2_complete` (Phase 1 section preserved intact)
Agent: `sim-model-designer`
Date: 2026-04-14 (Phase 2 append)

This document is the **source of truth** for dataclass field ownership across
phases. Downstream agents (`sim-engine-engineer`, `sim-data-integrator`,
`sim-analyst-qa`) MUST read this before adding or referencing fields.

## Append-only rule

Fields are **never removed or renamed** across phases. Phase 2/3 additions
are appended at the bottom of each dataclass so earlier phase tests continue
to pass. Status enum values are also append-only.

---

## `models.agent.AgentState`

Location: `spot-simulator/models/agent.py`

| Field                | Type                      | Default             | Phase | Notes |
|----------------------|---------------------------|---------------------|-------|-------|
| `agent_id`           | `str`                     | required            | 1     | External id, e.g. `A_001`. Used as key in all lookup dicts. |
| `persona_type`       | `str`                     | required            | 1     | One of the keys in `config/persona_templates.yaml` (filled by sim-data-integrator). |
| `home_region_id`     | `str`                     | required            | 1     | Falls back as `region_id` for agent-only events (see `make_event`). |
| `active_regions`     | `list[str]`               | required            | 1     | Top-k regions from persona-region affinity (plan §5). |
| `interest_categories`| `list[str]`               | required            | 1     | Used by `category_match()` (Phase 1) and `calculate_satisfaction()` (Phase 3). |
| `host_score`         | `float`                   | required (0..1)     | 1     | Disposition, set at init from persona. |
| `join_score`         | `float`                   | required (0..1)     | 1     | Disposition, set at init from persona. |
| `fatigue`            | `float`                   | required (0..1)     | 1     | Dynamic. Mutated by `decay_fatigue` and `after_*` handlers. |
| `social_need`        | `float`                   | required (0..1)     | 1     | Dynamic. Mutated by `grow_social_need` and `after_*` handlers. |
| `current_state`      | `str`                     | required            | 1     | One of `"idle" | "hosting" | "joined" | "checked_in"`. |
| `schedule_weights`   | `dict[str, float]`        | required            | 1     | Keys MUST match `engine.time_utils.schedule_key(tick)` — format `"{day_type}_{time_slot}"`. |
| `budget_level`       | `int` (1..3)              | required            | 1     | Consumed by `budget_penalty()` adapter in engine.decision. |
| `last_action_tick`   | `int`                     | `-1`                | 1     | Used by `recent_host_penalty()` (plan §2.6). |
| `hosted_spots`       | `list[str]`               | `[]` (factory)      | 1     | Append on successful `CREATE_SPOT`. |
| `joined_spots`       | `list[str]`               | `[]` (factory)      | 1     | Append on successful `JOIN_SPOT`. |

**Phase 2 additions (LANDED in Phase 2, see "Phase 2 additions" section
below for the table):** `trust_score`, `prev_trust`, `confirmed_spots`,
`checked_in_spots`, `noshow_spots`, `checked_in_for()`.

**Phase 3 additions (plan §4, DO NOT add in Phase 2):**
- `trust_threshold: float` — used in `calculate_satisfaction()` for
  host-trust fit scoring.

---

## `models.spot.SpotStatus`

Location: `spot-simulator/models/spot.py`

`StrEnum` so log serialization emits the raw string value and comparisons
like `spot.status == "OPEN"` stay ergonomic.

| Value      | Phase | Transitions in                       |
|------------|-------|--------------------------------------|
| `OPEN`        | 1 | initial state                        |
| `MATCHED`     | 1 | from `OPEN` when `len(participants) >= min_participants` |
| `CANCELED`    | 1 | from `OPEN` (Phase 2 48h timeout); also from `CONFIRMED` in Phase 2 (everyone CANCEL_JOIN'd) |
| `CONFIRMED`   | 2 | from `MATCHED` when `scheduled_tick - tick <= 2` (plan §3.4) |
| `IN_PROGRESS` | 2 | from `CONFIRMED` when `tick >= scheduled_tick` |
| `COMPLETED`   | 2 | from `IN_PROGRESS` when `tick >= scheduled_tick + duration` and no_show ratio <= 0.5 |
| `DISPUTED`    | 2 | from `IN_PROGRESS` when no_show ratio > 0.5 |

**Phase 3 additions (reserved, not yet added):** `SETTLED`, `FORCE_SETTLED`.

Phase 1 code must never hardcode strings for these values — always reference
the enum (`SpotStatus.OPEN`) so the Phase 2 expansion is a pure append.

---

## `models.spot.Spot`

Location: `spot-simulator/models/spot.py`

| Field             | Type              | Default              | Phase | Notes |
|-------------------|-------------------|----------------------|-------|-------|
| `spot_id`         | `str`             | required             | 1     | e.g. `S_001`. |
| `host_agent_id`   | `str`             | required             | 1     | AgentState.agent_id of the creator. |
| `region_id`       | `str`             | required             | 1     | Mirrored into EventLog.region_id. |
| `category`        | `str`             | required             | 1     | e.g. `food`, `cafe`, `exercise`. |
| `capacity`        | `int`             | required             | 1     | Max participants. |
| `min_participants`| `int`             | required             | 1     | Threshold for `OPEN -> MATCHED`. |
| `scheduled_tick`  | `int`             | required             | 1     | Start tick of the meetup. |
| `created_at_tick` | `int`             | required             | 1     | Used by Phase 2 timeout rule. |
| `status`          | `SpotStatus`      | `SpotStatus.OPEN`    | 1     | See transitions table above. |
| `participants`    | `list[str]`       | `[]` (factory)       | 1     | List of agent_ids. |

**Phase 2 additions (LANDED):** `duration`, `confirmed_at_tick`,
`started_at_tick`, `completed_at_tick`, `disputed_at_tick`, `canceled_at_tick`,
`checked_in: set[str]`, `noshow: set[str]`. See "Phase 2 additions" section.
**Phase 3 additions (reserved):** `avg_satisfaction: float`, `noshow_count: int`.

---

## `models.event.EventLog`

Location: `spot-simulator/models/event.py`

| Field        | Type           | Default              | Phase | Notes |
|--------------|----------------|----------------------|-------|-------|
| `event_id`   | `int`          | from monotonic counter | 1   | Allocated by `make_event` via `_alloc_event_id()`. |
| `tick`       | `int`          | required             | 1     | Simulation tick (hour). |
| `event_type` | `str`          | required             | 1     | Phase 1: `CREATE_SPOT`, `JOIN_SPOT`, `NO_ACTION`, `SPOT_MATCHED`. |
| `agent_id`   | `str | None`   | required             | 1     | `None` for agentless events like `SPOT_MATCHED`. |
| `spot_id`    | `str | None`   | required             | 1     | `None` for non-spot events like `NO_ACTION`. |
| `region_id`  | `str | None`   | required             | 1     | Resolved by `make_event`: explicit > spot.region_id > agent.home_region_id. |
| `payload`    | `dict`         | `{}` (factory)       | 1     | Free-form extras (e.g. `{"reason": "dispute_timeout"}`). |

**Phase 2 additions to `event_type` (LANDED, see "Phase 2 event types"
section below for the authoritative list and the `PHASE2_EVENT_TYPES`
constant re-exported from `models`):** `CANCEL_JOIN`, `CHECK_IN`, `NO_SHOW`,
`COMPLETE_SPOT`, `SPOT_TIMEOUT`, `SPOT_CONFIRMED`, `SPOT_STARTED`,
`SPOT_COMPLETED`, `SPOT_DISPUTED`.
**Phase 3 additions (reserved):** `SPOT_SETTLED`, `FORCE_SETTLED`,
`REVIEW_WRITTEN`.

### `make_event` contract

```python
make_event(tick, event_type, *, agent=None, spot=None, region_id=None, payload=None)
```

- Duck-typed: any object with `.agent_id` / `.spot_id` / `.region_id` works.
  This avoids circular imports from `engine` back into `models`.
- `region_id` resolution order:
  1. explicit `region_id=` kwarg
  2. `spot.region_id`
  3. `agent.home_region_id` (fallback for agent-only events)
  4. `None`
- `payload=None` is normalized to `{}` so downstream code never sees `None`.

### Determinism: `reset_event_counter(start=1)`

- The module maintains `_next_event_id = itertools.count(1)` at import time.
- `reset_event_counter(1)` is the canonical call at the START of a
  simulation run (alongside `random.seed(sim_config.seed)`) so that two runs
  with the same seed produce byte-identical `event_log.jsonl` files.
- Counter starts are configurable (e.g. `reset_event_counter(1000)`) for
  scenario tests that want namespaced id ranges.

### `serialize_event(e)` contract

- Returns a single-line UTF-8 JSON string (no trailing newline — the JSONL
  writer adds it).
- Uses `sort_keys=True` so diffs of two seed-identical runs are empty.
- `ensure_ascii=False` so Korean text in `payload` / `region_id` survives.

---

## `schedule_weights` key format specification

The single authoritative contract between `models` and `engine`:

```python
key = f"{get_day_type(tick)}_{get_time_slot(tick)}"
# e.g. "weekday_evening", "weekend_lunch", "weekday_dawn"
```

All 14 keys produced by the Phase 1 TIME_SLOTS × {weekday, weekend}:

```
weekday_dawn         weekend_dawn
weekday_morning      weekend_morning
weekday_late_morning weekend_late_morning
weekday_lunch        weekend_lunch
weekday_afternoon    weekend_afternoon
weekday_evening      weekend_evening
weekday_night        weekend_night
```

`sim-data-integrator` MUST populate every persona's `time_preferences` with
(a subset of) these 14 keys; missing keys default to `0.1` in
`engine.decision` (plan §2.6).

---

## Downstream handoff notes

- **sim-engine-engineer** — import the enum, not the string literal. Use
  `engine.time_utils.schedule_key(tick)` when looking up schedule weights.
  Call `reset_event_counter()` once at the start of `run_simulation()`.
- **sim-data-integrator** — `init_agent_from_persona()` must supply ALL
  required (non-default) AgentState fields. Defaults cover only
  `last_action_tick`, `hosted_spots`, `joined_spots`.
- **sim-analyst-qa** — Tunable constants live at the top of
  `engine/decay.py`. Import overrides via `engine.decay.FATIGUE_DECAY_MULT`
  etc. Distribution sanity checks on `fatigue` / `social_need` are the
  canonical way to spot decay divergence.

---

## Phase 2 additions

All fields below are **appended** to the Phase 1 dataclasses — existing
Phase 1 field order, types, and defaults are untouched. Defaults are chosen
so Phase 1 constructors (which never pass these arguments) still build a
valid object. Phase 2 regression gate: Phase 1 tests/main/validate must keep
producing byte-identical output, which they do because every new field has a
default and no existing field is mutated.

### `AgentState` (Phase 2 fields)

| Field                | Type         | Default        | Owner       | Notes |
|----------------------|--------------|----------------|-------------|-------|
| `trust_score`        | `float`      | `0.5`          | engine/decision, settlement | Host reliability 0..1. Seeded at 0.5 for unseen hosts; consumed by `calc_social_join_modifier` (plan §3.5). |
| `prev_trust`         | `float`      | `0.5`          | engine/lifecycle (Phase 3 settlement) | Snapshot of `trust_score` BEFORE settlement delta. Phase 2 keeps it synced to `trust_score` at init; Phase 3 uses `trust_score - prev_trust` for `SettlementResult.host_trust_delta`. |
| `confirmed_spots`    | `list[str]`  | `[]` (factory) | engine/lifecycle | Spot_ids the agent is CONFIRMED into but not yet started. Populated on `SPOT_CONFIRMED`, removed on CHECK_IN / NO_SHOW / CANCEL_JOIN. |
| `checked_in_spots`   | `set[str]`   | `∅` (factory)  | engine/lifecycle | Spot_ids the agent has CHECK_IN'd to. `set` for O(1) lookup from `AgentState.checked_in_for`. |
| `noshow_spots`       | `set[str]`   | `∅` (factory)  | engine/lifecycle | Spot_ids the agent was marked NO_SHOW in. Used by Phase 3 participant trust penalty. |

**Method added:**
```python
def checked_in_for(self, spot_id: str) -> bool:
    return spot_id in self.checked_in_spots
```
Used by Phase 3 `process_settlement` (plan §4.3) to filter CHECKED_IN vs
NO_SHOW participants. Phase 2 can also use it for lifecycle completion
accounting.

### `Spot` (Phase 2 fields)

| Field                 | Type             | Default         | Owner             | Notes |
|-----------------------|------------------|-----------------|-------------------|-------|
| `duration`            | `int`            | `2`             | data-integrator / engine | Ticks the spot stays IN_PROGRESS before the completion check runs. 1~3 realistic per plan §3.4 example. Default 2 so Phase 1 constructors stay valid. Overridable at Spot creation time. |
| `confirmed_at_tick`   | `int \| None`    | `None`          | engine/lifecycle  | Set when status transitions MATCHED→CONFIRMED. |
| `started_at_tick`     | `int \| None`    | `None`          | engine/lifecycle  | Set when status transitions CONFIRMED→IN_PROGRESS. |
| `completed_at_tick`   | `int \| None`    | `None`          | engine/lifecycle  | Set when status transitions IN_PROGRESS→COMPLETED. |
| `disputed_at_tick`    | `int \| None`    | `None`          | engine/lifecycle  | Set when status transitions IN_PROGRESS→DISPUTED. Phase 3 `resolve_disputes` uses this for the 6h/24h timeout rule (plan §4.5). |
| `canceled_at_tick`    | `int \| None`    | `None`          | engine/lifecycle  | Set on OPEN timeout cancel OR CONFIRMED all-participants-cancel. |
| `checked_in`          | `set[str]`       | `∅` (factory)   | engine/lifecycle  | Participant agent_ids who CHECK_IN'd. `set` so `len(spot.checked_in)` mirrors `count_checked_in(spot)` from plan §3.4. |
| `noshow`              | `set[str]`       | `∅` (factory)   | engine/lifecycle  | Participant agent_ids marked NO_SHOW. `len(spot.noshow)` should equal `len(spot.participants) - len(spot.checked_in)` at completion time. |

**Note on `duration` semantics:** plan §3.4 uses
`tick >= spot.scheduled_tick + spot.duration` as the "completion check"
gate. So `duration=2` means the spot runs for 2 ticks (2 hours) after its
scheduled start.

### `SpotStatus` (Phase 2 enum values)

Append-only additions: `CONFIRMED`, `IN_PROGRESS`, `COMPLETED`, `DISPUTED`.
See the transitions table earlier in this doc. Phase 3 will append
`SETTLED` and `FORCE_SETTLED`.

### Phase 2 event types

Authoritative constant: `models.PHASE2_EVENT_TYPES: set[str]` (re-exported
from `models/__init__.py`; source of truth is `models/event.py`).

| Event type       | Emitted by                              | agent_id | spot_id | payload hints |
|------------------|-----------------------------------------|----------|---------|---------------|
| `CANCEL_JOIN`    | engine/decision (agent action)          | set      | set     | `{"reason": "..."}` optional |
| `CHECK_IN`       | engine/decision or lifecycle            | set      | set     | — |
| `NO_SHOW`        | engine/lifecycle completion check        | set      | set     | — |
| `COMPLETE_SPOT`  | engine/decision (host completes)         | set      | set     | — |
| `SPOT_TIMEOUT`   | engine/lifecycle (OPEN 48h cancel)       | `None`   | set     | `{"reason": "open_timeout"}` |
| `SPOT_CONFIRMED` | engine/lifecycle (MATCHED→CONFIRMED)     | `None`   | set     | — |
| `SPOT_STARTED`   | engine/lifecycle (CONFIRMED→IN_PROGRESS) | `None`   | set     | — |
| `SPOT_COMPLETED` | engine/lifecycle (IN_PROGRESS→COMPLETED) | `None`   | set     | `{"noshow_ratio": ...}` optional |
| `SPOT_DISPUTED`  | engine/lifecycle (IN_PROGRESS→DISPUTED)  | `None`   | set     | `{"noshow_ratio": ...}` optional |

`EventLog` structure is unchanged — `event_type` is still a free-form `str`.
`PHASE2_EVENT_TYPES` is a **descriptive** set so tests (and
`analysis.run_validate --phase 2`) can assert "every entry in this set
appeared at least once" as a gate, and so sim-engine-engineer has a single
import point (`from models import PHASE2_EVENT_TYPES`) instead of scattering
string literals.

### Append-only compliance

- No Phase 1 field was removed, renamed, or had its type changed.
- All Phase 2 fields have defaults.
- `EventLog` structure unchanged (no new fields).
- `models/__init__.py` re-exports `PHASE2_EVENT_TYPES` alongside existing
  Phase 1 exports — existing imports still resolve.
- Phase 1 `main.py --phase 1` + `analysis.run_validate --phase 1` produce
  byte-identical results to pre-change (630 events, 210/247/122 split,
  GATE PASS).

### Known drift-check test failures (expected, owned by sim-analyst-qa)

`tests/test_models.py` contains two Phase-1-only sentinels that **will fail
as designed** once Phase 2 fields land:

- `test_agent_state_column_contract_field_set` — asserts
  `set(AgentState.__annotations__.keys()) == <Phase 1 set>`.
- `test_spot_phase1_enum_values` — asserts
  `{s.value for s in SpotStatus} == {"OPEN", "MATCHED", "CANCELED"}`.

These are drift sentinels, not regression checks. They require updating the
expected sets to include the Phase 2 fields/values listed above. Per the
Phase 2 task constraints, sim-model-designer does NOT touch `tests/`;
sim-analyst-qa owns this update. Every other Phase 1 test (23/25) still
passes, and all runtime Phase 1 gates (main.py, run_validate) still pass.

---

## Phase 3 additions

Task: `sim_02_models_phase3_complete`
Date: 2026-04-14 (Phase 3 append)

All fields below are **appended** to the Phase 1/2 dataclasses — existing
Phase 1/2 field order, types, and defaults are untouched. Defaults are chosen
so Phase 1/2 constructors (which never pass these arguments) still build a
valid object. Phase 3 regression gate: `python3 main.py --phase 2` continues
to produce the post-retry-4 byte-identical
`md5(output/event_log.jsonl) == ea8c17ec0030e06f05e375f466bcbee3`, which it
does because every new field has a default and no existing field is mutated.

### `AgentState` (Phase 3 fields)

| Field                  | Type          | Default        | Owner                              | Notes |
|------------------------|---------------|----------------|------------------------------------|-------|
| `trust_threshold`      | `float`       | `0.5`          | engine/decision, settlement        | Minimum host trust the agent will tolerate when deciding to join. Plan §4.4 `calculate_satisfaction` uses `abs(agent.trust_threshold - host.trust_score)` as the host-trust fit term. Default mirrors the seeded `trust_score` so Phase 1/2 read paths stay neutral. |
| `review_spots`         | `list[str]`   | `[]` (factory) | engine/settlement                  | Spot_ids this agent has WRITE_REVIEW'd. Append on `WRITE_REVIEW` emit. List (not set) so write order is preserved for analysis. |
| `saved_spots`          | `list[str]`   | `[]` (factory) | engine/decision (SAVE_SPOT action) | Spot_ids the agent bookmarked via SAVE_SPOT. Distinct from `joined_spots` / `confirmed_spots` — purely a watchlist. |
| `satisfaction_history` | `list[float]` | `[]` (factory) | engine/settlement                  | Running log of `calculate_satisfaction` outputs (plan §4.4) for spots this agent participated in. Append once per settled spot the agent was CHECKED_IN at. |

### `Spot` (Phase 3 fields)

| Field              | Type           | Default | Owner             | Notes |
|--------------------|----------------|---------|-------------------|-------|
| `avg_satisfaction` | `float \| None` | `None`  | engine/settlement | Populated by `process_settlement` (plan §4.3). `None` is the "not yet settled" sentinel; `resolve_disputes` 6h rule reads it (plan §4.5). |
| `noshow_count`     | `int`          | `0`     | engine/settlement | Convenience mirror of `len(spot.noshow)` set at settlement time. Plan §4.4 `calculate_satisfaction` reads `spot.noshow_count` directly, so the engine MUST set this before invoking the satisfaction function. |
| `settled_at_tick`  | `int \| None`   | `None`  | engine/settlement | Tick at which the spot transitioned COMPLETED→SETTLED, DISPUTED→SETTLED (6h rule), or DISPUTED→FORCE_SETTLED (24h rule). |
| `force_settled`    | `bool`         | `False` | engine/settlement | True iff settlement went through the 24h dispute-timeout path (plan §4.5). False for normal SETTLED transitions. |
| `review_count`     | `int`          | `0`     | engine/settlement | Number of WRITE_REVIEW events emitted for this spot. Incremented per generated review in `process_settlement` step 2. |

### `SpotStatus` (Phase 3 enum values)

Append-only additions: `SETTLED`, `FORCE_SETTLED`.

| Value           | Phase | Transitions in                                                      |
|-----------------|-------|---------------------------------------------------------------------|
| `SETTLED`       | 3     | from `COMPLETED` via `process_settlement` (plan §4.3); also from `DISPUTED` via `resolve_disputes` 6h rule when `avg_satisfaction >= 0.5` (plan §4.5) |
| `FORCE_SETTLED` | 3     | from `DISPUTED` via `resolve_disputes` 24h timeout rule (plan §4.5); host trust penalized by 0.12 |

### Phase 3 event types

Authoritative constant: `models.PHASE3_EVENT_TYPES: set[str]` (re-exported
from `models/__init__.py`; source of truth is `models/event.py`).

| Event type         | Emitted by                              | agent_id | spot_id | payload hints |
|--------------------|-----------------------------------------|----------|---------|---------------|
| `WRITE_REVIEW`     | engine/settlement (per checked-in agent, prob 0.3 + 0.4 * |sat - 0.5|) | set | set | `{"satisfaction": float}` |
| `SETTLE`           | engine/decision (host action) or engine/settlement wrapper | set | set | — |
| `SPOT_SETTLED`     | engine/settlement (COMPLETED→SETTLED, or DISPUTED→SETTLED via 6h rule) | `None` | set | `{"avg_satisfaction": ...}` optional |
| `FORCE_SETTLED`    | engine/settlement (DISPUTED→FORCE_SETTLED via 24h timeout) | `None` | set | `{"reason": "dispute_timeout"}` |
| `DISPUTE_RESOLVED` | engine/settlement.resolve_disputes 6h rule before `SPOT_SETTLED` | `None` | set | `{"avg_satisfaction": ...}` optional |
| `VIEW_FEED`        | engine/decision (agent action)           | set      | `None`  | — (no spot binding) |
| `SAVE_SPOT`        | engine/decision (agent action)           | set      | set     | — |

`EventLog` structure is unchanged — `event_type` is still a free-form `str`.
`PHASE3_EVENT_TYPES` is descriptive and has no overlap with
`PHASE2_EVENT_TYPES`. Use both sets for "any phase event happened" gates.

### `models.settlement.SettlementResult` (NEW, Phase 3)

Location: `spot-simulator/models/settlement.py`

Pure data container returned by `engine.settlement.process_settlement` per
plan §4.3. No methods, no logic — `dataclasses.asdict` serialization is the
recommended way to fold this into analysis sidecars.

| Field              | Type    | Notes |
|--------------------|---------|-------|
| `spot_id`          | `str`   | Settled spot id. |
| `completed_count`  | `int`   | Number of CHECKED_IN participants. |
| `noshow_count`     | `int`   | `len(participants) - completed_count`. Mirrored into `Spot.noshow_count` at settlement time. |
| `avg_satisfaction` | `float` | Mean of `calculate_satisfaction` over CHECKED_IN participants. `0.0` when no checked_in agents (plan §4.3 step 1). |
| `host_trust_delta` | `float` | `host.trust_score - host.prev_trust` AFTER the trust update step. Positive on growth, negative on penalty. |
| `status`           | `str`   | `"SETTLED"` or `"FORCE_SETTLED"`. String (not enum) so `asdict` produces JSON-safe payloads. Mirrors `Spot.status.value`. |
| `settled_at_tick`  | `int`   | Tick at which settlement ran. Mirrors `Spot.settled_at_tick`. |

### `models.settlement.Review` (NEW, Phase 3)

Location: `spot-simulator/models/settlement.py`

Pure data container returned by `engine.settlement.generate_review`
(plan §4.3 step 2). `make_review_event(tick, agent, spot, review)` lifts
this into a `WRITE_REVIEW` EventLog row in `engine/settlement.py`.

| Field               | Type    | Notes |
|---------------------|---------|-------|
| `reviewer_agent_id` | `str`   | Writer's agent_id. |
| `spot_id`           | `str`   | The spot being reviewed. |
| `satisfaction`      | `float` | 0..1 from `calculate_satisfaction(agent, spot)` (plan §4.4). |
| `tick`              | `int`   | Settlement tick (when the review was written). |

### Append-only compliance

- No Phase 1/2 field was removed, renamed, or had its type changed.
- All Phase 3 fields have defaults, defaults factories, or are on a NEW
  dataclass (`SettlementResult`, `Review`) that is opt-in via import.
- `EventLog` structure unchanged (no new fields).
- `models/__init__.py` re-exports `PHASE3_EVENT_TYPES`, `SettlementResult`,
  `Review` alongside Phase 1/2 exports — existing imports still resolve.
- Phase 1 `main.py --phase 1` + `analysis.run_validate --phase 1` produce
  byte-identical results to pre-change (630 events, 210/247/122 split,
  GATE PASS).
- Phase 2 `main.py --phase 2` produces byte-identical
  `output/event_log.jsonl` (`md5 == ea8c17ec0030e06f05e375f466bcbee3`,
  41,925 events) and `analysis.run_validate --phase 2` GATE PASS.

### Phase 3 drift sentinel (added to `tests/test_models.py`)

Per the Phase 2 plan, `tests/test_models.py` uses a per-phase sentinel
split. Phase 3 added two new tests following the same pattern, plus
`PHASE3_AGENT_FIELDS` / `PHASE3_SPOT_STATUSES` constants:

- `test_agent_state_phase3_field_set` — asserts
  `PHASE3_AGENT_FIELDS ⊆ AgentState.__annotations__` AND every actual
  field belongs to `PHASE1 ∪ PHASE2 ∪ PHASE3`.
- `test_spot_phase3_enum_values` — asserts
  `PHASE3_SPOT_STATUSES ⊆ {s.value for s in SpotStatus}` AND every actual
  enum value belongs to `PHASE1 ∪ PHASE2 ∪ PHASE3`.

The pre-existing Phase 2 sentinels (`test_agent_state_phase2_field_set`,
`test_spot_phase2_enum_values`) had their `known` union expanded to include
`PHASE3_*` so the "no unexpected drift" check survives Phase 3 expansion.
This is the same minimal touch sim-analyst-qa applied for Phase 2 (one-line
union extension + new `_phase3_` test). Choice rationale: leaving the
sentinels failing for QA to fix would block the Phase 3 regression-gate
verification step, since `pytest tests/` would not be green. Adding them
here keeps the deliverable self-contained and the QA hand-off clean.

Result: `pytest tests/` → **40 passed** (38 Phase 2 baseline + 2 new
Phase 3 sentinels).
