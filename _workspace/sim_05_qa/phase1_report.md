# Phase 1 Validation Report

Task: `sim_05_qa_phase1_complete`
Agent: `sim-analyst-qa`
Date: 2026-04-14
Run source: `python -m analysis.run_validate --phase 1` (in-memory re-run with seed=42)

## GATE VERDICT: **FAIL**

4 of 7 plan §2.8 criteria pass. 3 fail. **Phase 2 MUST NOT proceed** until the root causes below are addressed.

---

## 1. Criteria Table (plan §2.8)

| # | Criterion                                     | Target        | Actual                         | Status |
|---|-----------------------------------------------|---------------|--------------------------------|--------|
| 1 | total events in 48 ticks                      | `>= 30`       | `302`                          | PASS   |
| 2 | `CREATE_SPOT` events                          | `>= 5`        | `50`                           | PASS   |
| 3 | `JOIN_SPOT` events                            | `>= 10`       | `100`                          | PASS   |
| 4 | `SPOT_MATCHED` events                         | `>= 2`        | `50`                           | PASS   |
| 5 | dawn (hour 0–6) event ratio                   | `< 10 %`      | `11.92 %` (36/302)             | FAIL   |
| 6 | fatigue variance (active/idle bipartition)    | both non-empty| `50 active / 0 idle`           | FAIL   |
| 7 | host_score top-half / bottom-half CREATE ratio| `>= 1.3x`     | `1.00x` (25/25 on stable sort) | FAIL   |

Overall: **FAIL**

---

## 2. Event Type Breakdown

```
NO_ACTION    ########################################  102
JOIN_SPOT    #######################################   100
CREATE_SPOT  ####################                       50
SPOT_MATCHED ####################                       50
```

Totals: 302 events across 50 agents × 48 ticks = 2400 decisions. Non-NO_ACTION events = 200 (8.3 % of decisions).

---

## 3. Persona → CREATE_SPOT Count

| Persona           | host_score | Agents | CREATE_SPOT | JOIN_SPOT |
|-------------------|------------|--------|-------------|-----------|
| `homebody`        | 0.15       | 10     | 10          | 8         |
| `weekend_explorer`| 0.50       | 10     | 10          | 30        |
| `planner`         | 0.60       | 10     | 10          | 22        |
| `night_social`    | 0.70       | 10     | 10          | 15        |
| `spontaneous`     | 0.75       | 10     | 10          | 25        |

**Observation:** `CREATE_SPOT` is a flat 10-per-persona regardless of `host_score`.
This is the symptom that drives criterion 7 (host_score correlation) to fail.

---

## 4. host_score Quartile Analysis

| Quartile | n   | avg host_score | CREATE_SPOT | JOIN_SPOT |
|----------|-----|----------------|-------------|-----------|
| Q1 (low) | 12  | 0.21           | 12          | 13        |
| Q2       | 13  | 0.54           | 13          | 38        |
| Q3       | 12  | 0.66           | 12          | 18        |
| Q4 (high)| 13  | 0.74           | 13          | 31        |

Across 48 ticks every agent eventually crosses the roll threshold and hosts **exactly one** spot, then locks into `current_state="hosting"` and cannot host again. The correlation signal is flattened by the lifecycle ceiling, not by a probability bug.

`join_score` by contrast produces a visible gradient (`homebody=0.25 → 8 joins` vs `weekend_explorer=0.80 → 30 joins`), confirming the probability math itself is sound.

---

## 5. Dawn-hour Breakdown (per hour of day)

```
h00  ################  8
h01  ##                1
h02  ##########        5
h03  ##########        5
h04  ########          4
h05  ############      6
h06  ##############    7    <- dawn end
h07  ######################  11
h08  ################  8
h09  ################  8
h10  ######################################  19
h11  ##############################  15
h12  ####################################################  26
h13  ############################################  22
h14  ##############################################  23
h15  ########################################  20
h16  ##########################################  21
h17  ##########################  13
h18  ##########################################################  29
h19  ####################################  18
h20  ########################  12
h21  ##################  9
h22  ##################  9
h23  ######            3
```

Dawn events by type: `NO_ACTION=31, CREATE_SPOT=2, JOIN_SPOT=2, SPOT_MATCHED=1`.

**Real-action dawn ratio:** 4 of 200 action events = **2.0 %** — the decision-time gate works as intended.

**Logged dawn ratio:** 36 of 302 all-events = **11.92 %** — fails because `engine.runner.NO_ACTION_LOG_PROB=0.05` uniformly samples NO_ACTION rows across all 48 ticks, so the 7 dawn hours contribute `~7/24 ≈ 29%` of the NO_ACTION log, overwhelming the real-action gate.

---

## 6. Pytest Summary

```
tests/test_decision.py ..........
tests/test_models.py   ...............
=== 25 passed in 0.04s ===
```

Test counts: `test_decision.py` = 10 (1 test verifies find_matchable_sort, 1 verifies capacity filter, 1 verifies self-host filter), `test_models.py` = 15 (includes the column-contract drift test that asserts AgentState.__annotations__ matches `sim_02_models/column_contract.md` verbatim).

No test failures, no errors, total runtime ~0.04s (well under Phase 1 budget of <10s).

---

## 7. Root Cause Hypotheses & Ownership

### Criterion 5 — Dawn ratio (11.92% > 10%)
- **Signal:** false-positive — the real-action dawn filter works (2.0 %).
- **Root cause:** `engine.runner.NO_ACTION_LOG_PROB = 0.05` samples uniformly across all ticks, so the dawn window (7 of 24 hours) bleeds sampled NO_ACTION rows into the log regardless of time weight.
- **Owner:** `sim-engine-engineer`.
- **Recommended fix (document-only, do not patch):** either
  - (a) drop `NO_ACTION_LOG_PROB` to `0.02` so the dawn residue stays under 10 %, or
  - (b) exclude NO_ACTION rows from the `dawn_filter` denominator in `validate.py` (validator-side softening, not engine-side).
  The QA team prefers option (a) because it keeps the criterion literal and does not silently drop signal.

### Criterion 6 — Fatigue variance proxy
- **Signal:** false-positive — engagement is in fact high (50/50 agents acted).
- **Root cause:** the proxy check `active>0 AND idle>0` is the wrong bipartition. With 48 ticks every agent in Phase 1 eventually fires at least one action, so the `idle_agents==0` branch always fails.
- **Owner:** `sim-analyst-qa` (this agent). The validator itself is mis-specified.
- **Recommended fix:** Phase 2 will write per-tick fatigue snapshots to the event log (plan §3 lifecycle). Once snapshots exist, `check_fatigue_variance` should compute `stdev(fatigue across agents at tick T) > epsilon` over a sampled tick set. Until then, the criterion cannot be evaluated with the current log format and should be recorded as *unevaluable* rather than hard-fail. **For this gate, we treat criterion 6 as FAIL until Phase 2 adds snapshots.**

### Criterion 7 — host_score CREATE ratio (1.00x < 1.3x)
- **Signal:** **true FAIL** — the correlation the plan requires is not observable in the current log.
- **Root cause (engine lifecycle ceiling):** `execute_create_spot` sets `agent.current_state = "hosting"` but Phase 1 has no transition back to `"idle"`. Combined with the `roll < p_create and agent.current_state == "idle"` guard in `decide_action`, every agent can host **at most one** spot in 48 ticks. Given 48 ticks × 50 agents × realistic `p_create ∈ [0.1, 0.35]`, even the lowest-score agent (`homebody` host_score=0.15) reaches the first successful roll well inside the window, producing flat 1-per-agent CREATE counts.
- **Owner:** `sim-engine-engineer` (primary), `sim-model-designer` (secondary — decay parameters do not buy back enough probability variance to matter here either).
- **Recommended fixes (pick one):**
  1. **Host cooldown instead of permanent lock** — on `after_create_spot`, set `current_state = "idle"` but let `recent_host_penalty` (already implemented as a 12-tick flat 0.5 penalty) do the rate-limiting. This is consistent with the plan §2.6 text, which describes the penalty as a cooldown, and it unblocks multi-hosting by high-host_score personas. This is the lowest-risk change.
  2. **Run Phase 1 over 168 ticks but with 50 agents to amplify the gradient** — not recommended because it diverges from the plan's scale table.
  3. **Raise the host-gate probability floor** to `0.5 * host_score` so low-score agents don't eventually hit the roll ceiling — but this still caps at 1 per agent and doesn't produce a >1.3x gradient.

  **We recommend fix #1.** It is a two-line edit in `engine/runner.py` (or in `engine/executors.py::execute_create_spot`) and does not touch the probability math or the data contract.

---

## 8. Actions Taken

- Wrote `analysis/validate.py`, `analysis/visualize.py`, `analysis/run_validate.py`.
- Wrote `tests/test_decision.py` (10 scenarios), `tests/test_models.py` (15 scenarios).
- Ran `pytest tests/ -q` → 25 passed.
- Ran `python -m analysis.run_validate --phase 1` → 4/7 pass, 3/7 fail.
- Produced this report + `boundary_audit.md`.

## 9. Gate Decision

**Phase 2 is BLOCKED.** The CREATE_SPOT ceiling (criterion 7) is the critical blocker — it indicates the engine cannot produce the behavioural variance the plan expects even after the probability math is correct. The other two failures are noise-level and can be resolved inside QA.

Next step: `sim-engine-engineer` applies recommended fix #1 (release the hosting lock on `after_create_spot`), then re-runs this validator. If the resulting log shows `host_score top/bottom >= 1.3x` and dawn ratio is reduced, Phase 1 passes and Phase 2 unblocks.
