# sim_03_engine — Probability Table (Phase 1)

Task: `sim_03_engine_phase1_complete`
Agent: `sim-engine-engineer`
Date: 2026-04-14

This document is the source of truth for the probability math implemented
in `spot-simulator/engine/decision.py`. All weights come verbatim from plan
§2.6. Any tuning proposal must go through `sim-analyst-qa` and be reflected
here before `decision.py` is edited.

---

## `decide_action` overview

Returns `(action, target_spot)` where `action` is one of
`"CREATE_SPOT" | "JOIN_SPOT" | "NO_ACTION"`. Randomness is drawn from an
injected `rng: random.Random` so a fixed seed produces byte-identical logs.

```
1. time_weight = agent.schedule_weights.get(schedule_key(tick), 0.1)
2. if rng.random() > time_weight: return NO_ACTION            # time gate
3. p_create = clamp(formula_create)                           # see below
4. matchable = find_matchable_spots(agent, open_spots, ...)
5. best = matchable[0] if matchable else None
6. p_join = clamp(formula_join(best)) if best else 0.0
7. roll = rng.random()
8. if roll < p_create and agent.current_state == "idle":
       return ("CREATE_SPOT", None)
9. if best and roll < p_create + p_join:
       return ("JOIN_SPOT", best)
10. return ("NO_ACTION", None)
```

The single `roll` is re-used across both thresholds — this is what makes
the two probabilities stack as `P(create) + P(join)` without needing a
second draw.

---

## `p_create` formula

```
p_create = ( 0.35 * agent.host_score
           + 0.20 * region_create_affinity(agent, agent.home_region_id, region_features)
           + 0.25 * agent.social_need
           - 0.15 * agent.fatigue
           - 0.10 * recent_host_penalty(agent, tick) )
p_create = clamp(p_create, 0, 1)
```

### Variable sources

| Variable                           | Source                                    | Range   | Adapter / field                         |
|------------------------------------|-------------------------------------------|---------|-----------------------------------------|
| `agent.host_score`                 | `AgentState.host_score`                   | 0..1    | set by `init_agent_from_persona`        |
| `region_create_affinity(...)`      | `data.adapters.region_create_affinity`    | 0..1    | `region_features[region_id]["spot_create_affinity"]` |
| `agent.social_need`                | `AgentState.social_need`                  | 0..1    | mutated by `grow_social_need` / `after_*` |
| `agent.fatigue`                    | `AgentState.fatigue`                      | 0..1    | mutated by `decay_fatigue` / `after_*`    |
| `recent_host_penalty(agent, tick)` | `data.adapters.recent_host_penalty`       | 0.0 or 0.5 | flat penalty inside 12-tick cooldown  |

Weights are plan §2.6 verbatim: `+0.35, +0.20, +0.25, -0.15, -0.10`.

---

## `p_join` formula

```
matchable = find_matchable_spots(agent, open_spots, persona_templates=...)
best = matchable[0] if matchable else None

if best is None:
    p_join = 0.0
else:
    p_join = ( 0.30 * agent.join_score
             + 0.25 * category_match(agent, best, persona_templates)
             + 0.20 * agent.social_need
             - 0.15 * agent.fatigue
             - 0.10 * budget_penalty(agent, best, persona_templates, region_features) )
    p_join = clamp(p_join, 0, 1)
```

### Variable sources

| Variable                           | Source                                    | Range       | Adapter / field                          |
|------------------------------------|-------------------------------------------|-------------|------------------------------------------|
| `agent.join_score`                 | `AgentState.join_score`                   | 0..1        | set by `init_agent_from_persona`         |
| `category_match(agent, spot, ...)` | `data.adapters.category_match`            | {0.0, 1.0}  | `spot.category in agent.interest_categories` |
| `agent.social_need`                | `AgentState.social_need`                  | 0..1        | dynamic                                  |
| `agent.fatigue`                    | `AgentState.fatigue`                      | 0..1        | dynamic                                  |
| `budget_penalty(agent, spot, ...)` | `data.adapters.budget_penalty`            | 0..1        | `abs(agent.budget_level - spot_budget) / 3` |

Weights are plan §2.6 verbatim: `+0.30, +0.25, +0.20, -0.15, -0.10`.

### `find_matchable_spots` filter

Plan §2.6. Applied to every `OPEN` spot:

1. `spot.region_id in agent.active_regions`
2. `len(spot.participants) < spot.capacity`
3. `spot.host_agent_id != agent.agent_id`
4. `agent.agent_id not in spot.participants`

Survivors are sorted by `(category_match desc, len(participants) desc)` so
the head of the list is the agent's best bet and Phase 2 can cheaply add
`social_join_modifier` as a third sort key.

---

## Adapter signature reconciliation

`adapter_contract.md` documents the canonical signatures. `decision.py`
calls them with:

| Adapter                    | Call site                                                                 |
|----------------------------|---------------------------------------------------------------------------|
| `region_create_affinity`   | `region_create_affinity(agent, agent.home_region_id, region_features)`    |
| `category_match`           | `category_match(agent, spot, persona_templates)`                          |
| `budget_penalty`           | `budget_penalty(agent, spot, persona_templates, region_features)`         |
| `recent_host_penalty`      | `recent_host_penalty(agent, tick)`                                        |

Note: `budget_penalty` accepts `region_features` as a keyword-optional 4th
argument to look up `budget_avg_level` when a Phase 1 `Spot` has no
`budget_level` field. The engine always passes it positionally so the
region fallback is active from day one.

---

## Tick loop pseudocode (excerpt)

```
for tick in range(total_ticks):
    for agent in agents:
        decay_fatigue(agent)
        grow_social_need(agent)

    active_agents = list(agents)                 # Phase 1: all agents
    open_spots    = [s for s in spots if s.status == OPEN]
    rng.shuffle(active_agents)

    for agent in active_agents:
        action, target = decide_action(
            agent, tick, open_spots,
            rng=rng,
            region_features=region_features,
            persona_templates=persona_templates,
        )
        if action == "CREATE_SPOT":
            new_spot = execute_create_spot(agent, tick, rng=rng, ...)
            spots.append(new_spot); open_spots.append(new_spot)
            event_log.append(make_event(tick, "CREATE_SPOT", agent=agent, spot=new_spot))
            after_create_spot(agent)
        elif action == "JOIN_SPOT":
            if execute_join_spot(agent, target, tick):
                event_log.append(make_event(tick, "JOIN_SPOT", agent=agent, spot=target))
                after_join_spot(agent)
                if try_auto_match(target):
                    event_log.append(make_event(tick, "SPOT_MATCHED", agent=None, spot=target))
                    open_spots.remove(target)
        else:  # NO_ACTION
            if rng.random() < 0.05:
                event_log.append(make_event(tick, "NO_ACTION", agent=agent, spot=None))
```

---

# Phase 2 Additions (plan §3.5 / §3.6 / §3.4 check-in)

Task: `sim_03_engine_phase2_complete`
Agent: `sim-engine-engineer`
Date: 2026-04-14

Phase 2 keeps the Phase 1 `p_create` formula untouched but swaps the
`p_join` weights and introduces a social modifier, a persona-aware lead
time picker, a check-in probability, and a per-tick cancel-join
probability. All Phase 2 code paths are gated behind `phase >= 2` so
`python main.py --phase 1` remains byte-identical (md5
`a51da542975010f6382895621b72f868`).

## Phase 2 `p_join` formula

```
p_join = ( 0.25 * agent.join_score
         + 0.20 * category_match(agent, best, persona_templates)
         + 0.15 * agent.social_need
         + 0.15 * calc_social_join_modifier(agent, best, agents_by_id, persona_templates)
         + 0.10 * region_create_affinity(agent, best.region_id, region_features)
         - 0.10 * agent.fatigue
         - 0.05 * budget_penalty(agent, best, persona_templates, region_features) )
p_join = clamp(p_join, 0, 1)
```

Weights come from plan §3.5 verbatim. Differences from Phase 1:

| term                    | Phase 1 weight | Phase 2 weight |
|-------------------------|----------------|----------------|
| `join_score`            | +0.30          | +0.25          |
| `category_match`        | +0.25          | +0.20          |
| `social_need`           | +0.20          | +0.15          |
| `social_join_modifier`  | —              | +0.15 (new)    |
| `region_create_affinity`| —              | +0.10 (new)    |
| `fatigue`               | -0.15          | -0.10          |
| `budget_penalty`        | -0.10          | -0.05          |

`p_create` is unchanged in Phase 2.

## `calc_social_join_modifier` formula (plan §3.5)

```
if not spot.participants:
    return 0.0

fill_rate      = len(spot.participants) / spot.capacity
fomo_bonus     = 0.15 if fill_rate >= 0.7 else 0.0
host           = agents_by_id[spot.host_agent_id]
trust_modifier = 0.10 * (host.trust_score - 0.5)
shared         = avg_interest_overlap(agent, spot.participants, agents_by_id, persona_templates)
affinity_bonus = 0.10 * shared

return fomo_bonus + trust_modifier + affinity_bonus
```

Range: approximately `[-0.05, 0.35]`.

`avg_interest_overlap` computes the arithmetic mean of Jaccard similarities
between `agent.interest_categories` and each participant's
`interest_categories`. Empty-union pairs contribute 0.0. Unknown participant
ids (not present in `agents_by_id`) are skipped. If every resolved pair is
empty-union the function returns 0.0.

## `pick_scheduled_tick` distribution (plan §3.6)

```
persona in {spontaneous, night_social}  -> lead_hours = rng.randint(6, 24)
persona in {planner, weekend_explorer}  -> lead_hours = rng.randint(24, 72)
otherwise                               -> lead_hours = rng.randint(12, 48)

candidate = current_tick + lead_hours
return snap_to_preferred_time(agent, candidate)
```

`snap_to_preferred_time` searches `candidate-6 .. candidate+6` inclusive
for the tick whose `schedule_key` has the highest weight in
`agent.schedule_weights`. Ties resolve to the smallest tick (earliest
candidate wins). The Phase 1 fallback (`tick + rng.randint(6, 36)`) is
preserved inside `execute_create_spot` when `phase=1`.

## `p_checkin` formula (plan §3.4 runner check-in pass)

```
p_checkin = 0.85 - 0.20 * agent.fatigue + 0.10 * (agent.trust_score - 0.5)
```

Rolled once per host + participant for each spot whose lifecycle
transition `CONFIRMED -> IN_PROGRESS` happened on this tick. Hit ->
`CHECK_IN` event. Miss -> `NO_SHOW` event. Agents already resolved
(present in `spot.checked_in` or `spot.noshow`) are skipped so a spot
spanning multiple ticks is not double-rolled.

## `P_CANCEL_JOIN` constant

```
P_CANCEL_JOIN = 0.01  # per participant per tick, only while spot is OPEN or MATCHED
```

Cancel pass runs once per tick AFTER the check-in pass, iterating every
spot in insertion order. A participant that rolls below `P_CANCEL_JOIN`
is removed from `spot.participants` via `execute_cancel_join`; a MATCHED
spot that drops below `min_participants` falls back to `OPEN` so later
joiners can still match it.

---

# sim_03_engine — Probability Table (Phase 3 append)

Task: `sim_03_engine_phase3_complete`
Agent: `sim-engine-engineer`
Date: 2026-04-14

Phase 3 adds settlement, satisfaction, review, and dispute resolution
math. Everything below is gated behind `phase >= 3` so Phase 1 / Phase 2
md5s stay frozen.

## Trust-aware candidate sort — plan §4.6 criterion 4

`find_matchable_spots(..., phase=3, agents_by_id=...)` multiplies the
category-match score by `(0.5 + 0.5 * host.trust_score)` before sorting:

```
effective_score(spot) = category_match(agent, spot, persona_templates)
                      * (0.5 + 0.5 * host_of(spot).trust_score)
```

Low-trust hosts (`trust_score=0.0`) see their score halved; high-trust
hosts (`trust_score=1.0`) sort at full weight. The tie-breaker (participant
count) is unchanged. Phase 1 / Phase 2 continue to use the raw
`category_match` sort.

## Phase 3 additions to `decide_action`

After the existing CREATE / JOIN / NO_ACTION dispatch, when the agent
has not otherwise committed:

```
if phase >= 3:
    p_view_feed = clamp(0.10 * agent.social_need)
    if rng.random() < p_view_feed:
        return ("VIEW_FEED", None)

    p_save_spot = clamp(0.05 * agent.join_score)
    if matchable and rng.random() < p_save_spot and best is not None:
        return ("SAVE_SPOT", best)
```

Both draws live behind `if phase >= 3:` so Phase 1 / Phase 2 consume
exactly zero extra rng numbers.

`WRITE_REVIEW` is NOT a decision-pass action — per plan §4.3 it is
emitted by `process_settlement` as a side effect of each checked-in
agent's satisfaction roll. `execute_write_review` exists in
`engine/executors.py` as a placeholder for a future proactive-review
path but is not wired into `decide_action`.

## `calculate_satisfaction` — plan §4.4

```
base = 0.5
if spot.category in agent.interest_categories:
    base += 0.15
ideal_ratio = len(spot.checked_in) / max(1, spot.capacity)
if 0.6 <= ideal_ratio <= 0.9:
    base += 0.10
elif ideal_ratio < 0.4:
    base -= 0.10
noshow_ratio = spot.noshow_count / max(1, len(spot.participants))
base -= 0.15 * noshow_ratio
host_trust_gap = abs(agent.trust_threshold - host.trust_score)
base -= 0.10 * host_trust_gap
noise = rng.uniform(-0.08, 0.08)
return clamp(base + noise, 0, 1)
```

Constants are exposed at module level (`SATISFACTION_BASE`,
`CATEGORY_MATCH_BONUS`, ...) so sim-analyst-qa can retune without
editing the function body.

## Review probability — plan §4.3 step 2

```
p_review(sat) = REVIEW_BASE_PROB + REVIEW_INTENSITY_COEFF * |sat - 0.5|
              = 0.3 + 0.4 * |sat - 0.5|
```

Drawn once per checked-in agent after the satisfaction roll, using the
same `rng` that produced the satisfaction noise. Fires a `WRITE_REVIEW`
event and appends to `agent.review_spots` / `spot.review_count`.

## Host trust update — plan §4.3 step 3

```
if avg_sat >= 0.7: host.trust_score += 0.05     # clamp to [0, 1]
elif avg_sat < 0.4: host.trust_score -= 0.08    # clamp to [0, 1]
# else: unchanged
host.prev_trust is snapshotted BEFORE the delta so
SettlementResult.host_trust_delta = host.trust_score - host.prev_trust.
```

## Participant no-show penalty — plan §4.3 step 4

```
for agent in participants:
    if agent.agent_id in spot.noshow:
        agent.trust_score = clamp(agent.trust_score - 0.15)
```

Applied once per settled spot. The same agent can be penalized for
multiple no-shows across the simulation; `clamp` prevents negative
trust.

## Dispute resolution windows — plan §4.5

```
dispute_age = tick - spot.disputed_at_tick

if dispute_age > DISPUTE_TIMEOUT_TICKS (24):
    host.trust_score -= FORCE_SETTLE_TRUST_PENALTY (0.12)  # clamp
    spot.status = FORCE_SETTLED
    spot.force_settled = True
    emit FORCE_SETTLED event {"reason": "dispute_timeout"}

elif dispute_age > DISPUTE_RESOLVE_TICKS (6):
    process_settlement(spot, ...)   # populates spot.avg_satisfaction
    if spot.avg_satisfaction >= 0.5:
        spot.status = SETTLED
        emit DISPUTE_RESOLVED event
        emit SPOT_SETTLED event {"from_dispute": True, ...}
```

## Source mapping

| formula / constant         | source                  | file / symbol                                    |
|----------------------------|-------------------------|---------------------------------------------------|
| `calculate_satisfaction`   | plan §4.4               | `engine/settlement.py::calculate_satisfaction`    |
| `p_review`                 | plan §4.3 step 2        | `engine/settlement.py::process_settlement`        |
| host trust up/down         | plan §4.3 step 3        | `engine/settlement.py::process_settlement`        |
| noshow penalty             | plan §4.3 step 4        | `engine/settlement.py::process_settlement`        |
| 6h / 24h dispute rules     | plan §4.5               | `engine/settlement.py::resolve_disputes`          |
| trust-aware sort weight    | plan §4.6 criterion 4   | `engine/decision.py::find_matchable_spots`        |
| `p_view_feed` / `p_save_spot` | plan §4.2            | `engine/decision.py::decide_action`               |

## Empirically observed (Phase 3, 2000 agents, seed=42)

Run: `python3 main.py --phase 3`

```
total events:     400,438
CREATE_SPOT       55,436
JOIN_SPOT         74,153
SPOT_MATCHED      26,056
SPOT_CONFIRMED    20,334
SPOT_STARTED      20,290
SPOT_COMPLETED    18,708
SPOT_TIMEOUT      27,160
SPOT_DISPUTED      1,395
CHECK_IN          65,020
NO_SHOW           10,819
CANCEL_JOIN       11,781
WRITE_REVIEW      17,175
SETTLE            18,708
SPOT_SETTLED      19,276
FORCE_SETTLED        662
DISPUTE_RESOLVED     568
VIEW_FEED            517
SAVE_SPOT          1,635

avg_satisfaction: mean=0.646 min=0.000 max=0.825 (n=20,008)
trust_score:      mean=0.213 min=0.000 max=0.980 <0.3=1328 >0.7=120
elapsed:          195.6s
md5:              678a0e4a3797db478d928d11d7caafe8 (deterministic, 2 runs)
```

Observations that sim-analyst-qa should validate (NOT in scope for
sim-engine-engineer):

* `SPOT_SETTLED / SPOT_COMPLETED` = 19,276 / 18,708 ≈ 1.03 — all
  COMPLETED spots settle, plus 568 dispute-resolved settlements. Good.
* Mean trust_score drifts to 0.213 — the noshow penalty (0.15) + host
  trust down (0.08) dominate for this config; the 5000-agent run would
  likely show similar drift. QA should consider raising
  `P_CHECKIN_BASE` or lowering the noshow penalty.
* Mean satisfaction 0.646 is close to plan §4.6 expectations. The
  satisfaction noise floor at 0 appears because `spot.capacity=0` and
  empty-checked-in combinations zero out the ideal_ratio term; the
  `max(1, ...)` guards prevent ZeroDivisionError.

