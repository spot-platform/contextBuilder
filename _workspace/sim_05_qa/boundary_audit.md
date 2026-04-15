# Boundary Audit — Phase 1

Task: `sim_05_qa_phase1_complete`
Agent: `sim-analyst-qa`
Date: 2026-04-14

Cross-boundary invariants that MUST hold for Phase 2 to safely append new
functionality. This file is the companion to `phase1_report.md` and is
re-run at the end of every Phase.

---

## 1. `AgentState` — column_contract.md vs actual `models/agent.py`

Source of truth: `_workspace/sim_02_models/column_contract.md` §`models.agent.AgentState`.
Actual dataclass: `spot-simulator/models/agent.py`.

Expected field set (from contract) = actual `AgentState.__annotations__` (verified by `tests/test_models.py::test_agent_state_column_contract_field_set`):

| Contract field        | Type in contract     | Type in source        | Default                 | Status |
|-----------------------|----------------------|-----------------------|-------------------------|--------|
| `agent_id`            | `str`                | `str`                 | required                | OK     |
| `persona_type`        | `str`                | `str`                 | required                | OK     |
| `home_region_id`      | `str`                | `str`                 | required                | OK     |
| `active_regions`      | `list[str]`          | `list[str]`           | required                | OK     |
| `interest_categories` | `list[str]`          | `list[str]`           | required                | OK     |
| `host_score`          | `float` 0..1         | `float`               | required                | OK     |
| `join_score`          | `float` 0..1         | `float`               | required                | OK     |
| `fatigue`             | `float` 0..1         | `float`               | required                | OK     |
| `social_need`         | `float` 0..1         | `float`               | required                | OK     |
| `current_state`       | `str`                | `str`                 | required                | OK     |
| `schedule_weights`    | `dict[str, float]`   | `dict[str, float]`    | required                | OK     |
| `budget_level`        | `int` 1..3           | `int`                 | required                | OK     |
| `last_action_tick`    | `int`                | `int`                 | `-1`                    | OK     |
| `hosted_spots`        | `list[str]`          | `list[str]`           | `[]` (factory)          | OK     |
| `joined_spots`        | `list[str]`          | `list[str]`           | `[]` (factory)          | OK     |

**Phase 2 future fields** (`trust_score`, `prev_trust`, `checked_in_for`) correctly absent from Phase 1 — append-only rule preserved.

**Verdict:** no drift. Column contract matches dataclass 1:1.

---

## 2. Adapter signatures — adapter_contract.md vs actual `data/adapters.py`

Source of truth: `_workspace/sim_04_data/adapter_contract.md` §Functions.

| Adapter                   | Contract signature                                                                | Source signature                                                                                                     | Status         |
|---------------------------|-----------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------|----------------|
| `region_create_affinity`  | `(agent, region_id, region_features) -> float`                                    | `(agent: Any, region_id: str, region_features: dict[str, dict[str, Any]]) -> float`                                  | OK             |
| `category_match`          | `(agent, spot, persona_templates) -> float`                                       | `(agent: Any, spot: Any, persona_templates: dict[str, dict[str, Any]]) -> float`                                     | OK             |
| `budget_penalty`          | `(agent, spot, persona_templates, region_features=None) -> float`                 | `(agent: Any, spot: Any, persona_templates: dict, region_features: dict | None = None) -> float`                     | OK             |
| `recent_host_penalty`     | `(agent, tick) -> float`                                                          | `(agent: Any, tick: int) -> float`                                                                                   | OK             |

**Call-site reconciliation (engine → adapters):**

Source: `spot-simulator/engine/decision.py` lines 106–131.

| Adapter                   | Invocation in `decide_action`                                                          | Matches contract? |
|---------------------------|----------------------------------------------------------------------------------------|-------------------|
| `region_create_affinity`  | `region_create_affinity(agent, agent.home_region_id, region_features)`                 | YES               |
| `category_match`          | `category_match(agent, best, persona_templates)`                                       | YES               |
| `budget_penalty`          | `budget_penalty(agent, best, persona_templates, region_features)`                      | YES (positional)  |
| `recent_host_penalty`     | `recent_host_penalty(agent, tick)`                                                     | YES               |

**Error-handling contract:**

| Situation                                       | Contract              | Source                          | Status |
|-------------------------------------------------|-----------------------|---------------------------------|--------|
| Unknown region_id                               | `0.0 + warnings.warn` | `warnings.warn(...); return 0.0`| OK     |
| Missing `spot.category`                         | `0.0`                 | returns `0.0`                   | OK     |
| Missing budget info (agent/spot/region)         | `0.0`                 | returns `0.0`                   | OK     |
| No hosted history / `last_action_tick == -1`    | `0.0`                 | returns `0.0`                   | OK     |

**Verdict:** no drift. Adapter signatures, ranges, and error paths are consistent across `adapter_contract.md`, `data/adapters.py`, and `engine/decision.py`.

---

## 3. Event schema — smoke `event_log.jsonl` vs plan §6.1

Plan §6.1 shows the expected JSONL format:

```json
{"tick": 14, "event": "CREATE_SPOT", "agent": "A_023", "spot": "S_001", "region": "emd_yeonmu"}
```

Actual row from `spot-simulator/output/event_log.jsonl`:

```json
{"agent_id": "A_38657", "event_id": 1, "event_type": "NO_ACTION", "payload": {}, "region_id": "emd_yeonmu", "spot_id": null, "tick": 0}
```

### Key diffs

| Plan §6.1 key | Actual key    | Notes                                                                                    |
|---------------|---------------|------------------------------------------------------------------------------------------|
| `event`       | `event_type`  | **Rename.** The plan's §6.1 example uses `event`; source-of-truth §2.3 `EventLog` uses `event_type`. |
| `agent`       | `agent_id`    | **Rename.** Contract uses `_id` suffix consistently.                                     |
| `spot`        | `spot_id`     | **Rename.** Same reason.                                                                 |
| `region`      | `region_id`   | **Rename.** Same reason.                                                                 |
| (absent)      | `event_id`    | **Addition.** Monotonic counter per `reset_event_counter`.                               |
| (absent)      | `payload`     | **Addition.** Free-form dict, plan §2.3 defines it.                                      |

**Interpretation:** the plan §6.1 snippet is illustrative, not normative. The actual schema follows the §2.3 `EventLog` dataclass, which is the contract both `sim_02_models/column_contract.md` and `make_event`'s implementation agree on. Downstream consumers (the bariral page at `/simulation` per plan §10) must read `event_type` / `agent_id` / `spot_id` / `region_id`, not the §6.1 example keys. **No bug** — but plan §6.1 should be amended in a follow-up to match the real schema so future readers don't write broken consumers.

### Append-only invariant (Phase 2/3 forward-compat)

Phase 1 `event_type` set observed in `event_log.jsonl`:
- `CREATE_SPOT`, `JOIN_SPOT`, `SPOT_MATCHED`, `NO_ACTION`

Plan §2.3 declares these exact four for Phase 1. Phase 2 will append `SPOT_TIMEOUT, SPOT_CONFIRMED, SPOT_STARTED, SPOT_COMPLETED, SPOT_DISPUTED`. Phase 3 adds `SPOT_SETTLED, FORCE_SETTLED, REVIEW_WRITTEN`. The `event_type` field is a free `str`, so append-only is mechanical.

**Verdict:** schema is stable. Plan §6.1 example keys are cosmetic drift, not a code bug.

---

## 4. Time-slot contract — `engine.time_utils` vs `column_contract.md` §schedule_weights

Contract declares the key format as `"{day_type}_{time_slot}"` with 14 total keys.

`engine/time_utils.py::schedule_key(tick)` returns exactly that. `persona_templates.yaml` populates all 14 keys for each persona (verified by manual read).

`engine/decision.py::decide_action` calls `agent.schedule_weights.get(schedule_key(tick), 0.1)` — the `0.1` default matches plan §2.6.

**Verdict:** time-slot contract is coherent end-to-end.

---

## 5. Summary

| Boundary                                                      | Status |
|---------------------------------------------------------------|--------|
| `AgentState` dataclass ↔ column_contract.md                   | OK     |
| `SpotStatus` / `Spot` dataclass ↔ column_contract.md          | OK     |
| `EventLog` dataclass ↔ column_contract.md                     | OK     |
| Adapter signatures ↔ adapter_contract.md                      | OK     |
| Engine call-sites ↔ adapter contract                          | OK     |
| Adapter error paths ↔ adapter contract                        | OK     |
| Event schema actual ↔ plan §2.3                               | OK     |
| Event schema actual ↔ plan §6.1 example                       | COSMETIC DRIFT |
| `schedule_weights` key format end-to-end                      | OK     |

**No code-level contract violations detected.** The only drift is in the plan's §6.1 illustrative snippet, which is purely documentation.

## 6. Hands-off notes for next phase

- When Phase 2 adds `duration`, `checked_in`, `disputed_at_tick` to `Spot`, append them at the END of the dataclass (before `participants` stays, fields with defaults must stay trailing).
- When Phase 2 adds `trust_score` / `prev_trust` to `AgentState`, default them to `0.5` / `None` so existing `tests/test_models.py::test_agent_state_column_contract_field_set` can be updated in one place.
- When Phase 2 starts emitting lifecycle events, update `analysis/validate.py::validate_phase2` (not yet written — stub in the same module).

---

# Phase 2 Boundary Audit (append)

Task: `sim_05_qa_phase2_complete`
Agent: `sim-analyst-qa`
Date: 2026-04-14

Phase 2 appended fields, enum values, and event types. This section
re-runs the four boundary checks from the agent contract against the
Phase 2 delta. Companion document: `phase2_report.md`.

## 7. `Spot` Phase 2 fields vs `process_lifecycle` actual mutations

Source of truth: `_workspace/sim_02_models/phase2_field_diff.md` §`Spot — appended fields`.
Actual mutations observed in `spot-simulator/engine/lifecycle.py`.

| Spot field           | Declared owner         | `process_lifecycle` write            | Status |
|----------------------|------------------------|--------------------------------------|--------|
| `duration`           | data-integrator/engine | READ only (completion gate)          | OK     |
| `confirmed_at_tick`  | engine/lifecycle       | SET on MATCHED → CONFIRMED           | OK     |
| `started_at_tick`    | engine/lifecycle       | SET on CONFIRMED → IN_PROGRESS       | OK     |
| `completed_at_tick`  | engine/lifecycle       | SET on IN_PROGRESS → COMPLETED       | OK     |
| `disputed_at_tick`   | engine/lifecycle       | SET on IN_PROGRESS → DISPUTED        | OK     |
| `canceled_at_tick`   | engine/lifecycle       | SET on OPEN → CANCELED (timeout)     | OK     |
| `checked_in: set[str]`| engine/executors      | WRITTEN by `execute_check_in`        | OK     |
| `noshow: set[str]`   | engine/executors       | WRITTEN by `execute_no_show`         | OK     |

**Cross-check:** `process_lifecycle` NEVER writes `checked_in` / `noshow`
directly — the check-in pass in `runner.py::run_simulation` owns those
writes, mediated by `execute_check_in` / `execute_no_show`. Lifecycle
reads them for the completion check (`noshow_ratio > 0.5`). This is the
split documented in `lifecycle.py`'s module docstring ("runner.py owns
the check-in pass so rng draws stay ordered by shuffled agent iteration,
not spot iteration").

**Verdict:** no drift. Every Phase 2 Spot field has exactly one writer,
and the writer lives where the contract said it would.

## 8. `AgentState` Phase 2 fields vs `column_contract.md`

Source of truth: `column_contract.md` §`Phase 2 additions — AgentState`.
Actual dataclass: `spot-simulator/models/agent.py`.

Verified by `tests/test_models.py::test_agent_state_phase2_field_set`
(asserts `PHASE2_AGENT_FIELDS ⊆ AgentState.__annotations__` AND
`AgentState.__annotations__ ⊆ PHASE1_AGENT_FIELDS ∪ PHASE2_AGENT_FIELDS`).

| Field             | Type         | Default | Writer                                    | Status |
|-------------------|--------------|---------|-------------------------------------------|--------|
| `trust_score`     | `float`      | `0.5`   | Phase 3 settlement; Phase 2 READ-ONLY      | OK     |
| `prev_trust`      | `float`      | `0.5`   | Phase 3 settlement snapshot                | OK     |
| `confirmed_spots` | `list[str]`  | `[]`    | lifecycle (CONFIRMED); executors (CHECK_IN / NO_SHOW / CANCEL_JOIN clear) | OK |
| `checked_in_spots`| `set[str]`   | `∅`     | `execute_check_in`                         | OK     |
| `noshow_spots`    | `set[str]`   | `∅`     | `execute_no_show`                          | OK     |

**Phase 2 Spot reached-match invariant:** we verified through the
runtime log that `execute_check_in` is called only when
`spot.status == IN_PROGRESS` (guarded inside the executor) and adds the
spot to `agent.checked_in_spots` atomically. `spot.checked_in` membership
and `agent.checked_in_spots` membership therefore always match —
`AgentState.checked_in_for(spot_id)` returns the same boolean as
`spot_id in spot.checked_in`. No divergence observed in 8,750 CHECK_IN
events across the Phase 2 run.

**Verdict:** no drift.

## 9. `calc_social_join_modifier` coefficients vs plan §3.5

Source of truth: plan §3.5 + `_workspace/sim_03_engine/probability_table.md`
§"Phase 2 `calc_social_join_modifier`".

| Component               | Plan §3.5 formula          | `engine/decision.py::calc_social_join_modifier` | Status |
|-------------------------|----------------------------|------------------------------------------------|--------|
| FOMO bonus              | `+0.15 if fill_rate >= 0.7`| `0.15 if fill_rate >= 0.7 else 0.0`            | OK     |
| Host trust modifier     | `+0.10 * (host.trust_score - 0.5)` | `0.10 * (host.trust_score - 0.5)`      | OK     |
| Interest affinity       | `+0.10 * avg_interest_overlap` | `0.10 * avg_interest_overlap(...)`         | OK     |
| Baseline (no participants) | `0.0`                   | returns `0.0` on empty participants            | OK     |

Weight application in `p_join`:

| term                        | plan §3.5 | `decision.py::decide_action` (phase 2 branch) |
|-----------------------------|-----------|------------------------------------------------|
| `join_score`                | `+0.25`   | `+0.25`                                        |
| `category_match`            | `+0.20`   | `+0.20`                                        |
| `social_need`               | `+0.15`   | `+0.15`                                        |
| `social_join_modifier`      | `+0.15`   | `+0.15`                                        |
| `region_create_affinity`    | `+0.10`   | `+0.10`                                        |
| `fatigue`                   | `-0.10`   | `-0.10`                                        |
| `budget_penalty`            | `-0.05`   | `-0.05`                                        |

**Verdict:** all coefficients match byte-for-byte. HOWEVER — the FOMO
bonus is structurally unreachable under the current `try_auto_match`
semantics (match fires at `min_participants` before fill_rate can reach
0.7). This is a design-level mismatch between plan §3.3 (auto-match at
min) and plan §3.5 (FOMO bonus threshold). Flagged as criterion 3 root
cause in `phase2_report.md` §9.

## 10. `pick_scheduled_tick` distributions vs plan §3.6

Source of truth: plan §3.6.

| Persona                                   | Plan §3.6 lead hours | `decision.py::pick_scheduled_tick`    | Status |
|-------------------------------------------|----------------------|---------------------------------------|--------|
| `spontaneous`, `night_social`             | `6..24`              | `rng.randint(6, 24)`                  | OK     |
| `planner`, `weekend_explorer`             | `24..72`             | `rng.randint(24, 72)`                 | OK     |
| default (everything else)                 | `12..48`             | `rng.randint(12, 48)`                 | OK     |
| snap to preferred schedule slot           | ±6 ticks window      | `_snap_to_preferred_time(agent, cand)`| OK     |

**Empirical check (Phase 2 run):** across 4212 MATCHED spots, lead time
stats are `min=0, p10=6, p50=24, p90=55, max=78, avg=28.3`. The range
(0–78) slightly exceeds the theoretical 6–72 because `_snap_to_preferred_time`
nudges ±6 into windows outside the initial randint bounds (e.g. 72+6=78).
The `min=0` lower outlier comes from `recent_host_penalty` NOT affecting
`scheduled_tick` — but `_snap_to_preferred_time` can pull candidates
backward into the past relative to `current_tick`. Phase 2 does not
clamp `scheduled_tick >= current_tick`, so a min=0 spot is technically
an immediate-start spot. **Minor concern**, not a criterion failure.

**Verdict:** distributions match spec. One minor edge case (min=0) is
worth a follow-up in Phase 3 tuning but is not blocking.

## 11. Check-in pass vs plan §3.4 CHECK_IN spec

Source of truth: plan §3.4 check-in spec + `probability_table.md`
"`p_checkin` formula".

```
p_checkin = 0.85 - 0.20 * agent.fatigue + 0.10 * (agent.trust_score - 0.5)
```

`runner.py::_p_checkin` implements this formula verbatim.

| Property                                          | Spec         | Runner code | Status |
|---------------------------------------------------|--------------|-------------|--------|
| Rolled once per host                              | yes          | roster includes host first | OK  |
| Rolled once per participant                       | yes          | roster extended with participants, dedup | OK |
| Only fires on spots whose `started_at_tick == tick`| yes         | guard in for-loop          | OK  |
| Already-resolved agents skipped                    | yes         | `if aid in checked_in/noshow: continue` | OK |
| CHECK_IN event on hit                              | yes         | `make_event(..., "CHECK_IN", ...)` | OK  |
| NO_SHOW event on miss                              | yes         | `make_event(..., "NO_SHOW", ...)` | OK   |

**Verdict:** formula and wiring match spec. The formula produces
p_checkin ≈ 0.75 under realistic fatigue (~0.5), which generates the
~25 % NO_SHOW floor that violates criterion 6. This is not a drift bug —
it's a tuning issue flagged as criterion 6 root cause.

## 12. Phase 2 boundary summary

| Boundary                                                 | Status |
|----------------------------------------------------------|--------|
| `Spot` Phase 2 fields ↔ `process_lifecycle` mutations    | OK     |
| `AgentState` Phase 2 fields ↔ column contract            | OK     |
| `calc_social_join_modifier` coefficients ↔ plan §3.5     | OK     |
| `p_join` Phase 2 weights ↔ plan §3.5                     | OK     |
| `pick_scheduled_tick` lead-time distributions ↔ plan §3.6| OK (one minor edge: min=0 from snap) |
| `_p_checkin` formula ↔ plan §3.4                         | OK     |
| `try_auto_match` timing ↔ plan §3.5 FOMO semantics       | **DESIGN MISMATCH** (criterion 3) |
| Phase 2 event_type set ↔ `PHASE2_EVENT_TYPES`            | OK (except `COMPLETE_SPOT` intentionally not wired) |

**No code-level contract violations.** All Phase 2 field owners, formula
coefficients, and event emissions match their contracts. The single
design-level mismatch (try_auto_match vs FOMO threshold) is documented
in `phase2_report.md` §9 with a recommended fix for
`sim-engine-engineer`.

## 13. Phase 3 hand-off notes

- When Phase 3 adds `avg_satisfaction`, `noshow_count` to `Spot`, append
  at the end of the dataclass. The Phase 2 sentinel
  (`test_spot_phase2_enum_values`) will catch any accidental removal of
  existing values.
- When Phase 3 adds `trust_threshold` to `AgentState`, mirror the
  Phase 2 split pattern: add a `PHASE3_AGENT_FIELDS` constant and a new
  `test_agent_state_phase3_field_set` test. Update the "known" union in
  `test_agent_state_phase2_field_set` so Phase 3 fields don't trip the
  "unexpected field" branch.
- When `validate_phase3` lands, mark criterion 4 (`host_trust` ratio) as
  non-NEUTRAL — by that point `trust_score` will have real variance
  from settlement.
- When `SETTLED` / `FORCE_SETTLED` enum values are added, update
  `PHASE2_EVENT_TYPES` reference in `phase2_field_diff.md` AND the
  `_SPOT_BOUND_EVENTS` tuple in `analysis/visualize.py` so spot
  timelines include the settlement lines.
