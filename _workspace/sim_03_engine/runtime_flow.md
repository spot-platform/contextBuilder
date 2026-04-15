# sim_03_engine — Runtime Flow (Phase 1)

Task: `sim_03_engine_phase1_complete`
Agent: `sim-engine-engineer`
Date: 2026-04-14

ASCII flow of ONE tick inside `engine.runner.run_simulation`. Downstream
phases will hook in after step 4 (Phase 2 lifecycle) and step 5 (Phase 3
settlement) without reshaping this diagram.

---

## One tick, top to bottom

```
                       ┌────────────────────────────────┐
                       │ tick = N  (0 <= N < total_ticks)│
                       └─────────────┬──────────────────┘
                                     │
                 ┌───────────────────▼────────────────────┐
                 │ 1. Natural state update (every agent)  │
                 │    decay_fatigue(agent)                │
                 │    grow_social_need(agent)             │
                 └───────────────────┬────────────────────┘
                                     │
                 ┌───────────────────▼────────────────────┐
                 │ 2. Build active set                    │
                 │    Phase 1: active_agents = list(agents) │
                 │    (Phase 3 will swap in sampling)     │
                 └───────────────────┬────────────────────┘
                                     │
                 ┌───────────────────▼────────────────────┐
                 │ 3. Snapshot OPEN spots                 │
                 │    open_spots = [s for s in spots      │
                 │                  if s.status == OPEN]  │
                 └───────────────────┬────────────────────┘
                                     │
                 ┌───────────────────▼────────────────────┐
                 │ 4. rng.shuffle(active_agents)          │
                 │    (deterministic via seeded RNG)      │
                 └───────────────────┬────────────────────┘
                                     │
         ┌───────────────────────────▼────────────────────────────┐
         │ 5. For each agent in shuffle order:                    │
         │                                                        │
         │     action, target = decide_action(                    │
         │         agent, tick, open_spots,                       │
         │         rng=rng,                                       │
         │         region_features=region_features,               │
         │         persona_templates=persona_templates,           │
         │     )                                                  │
         │                                                        │
         │          ┌─────────────┬─────────────────┬──────────┐  │
         │          │             │                 │          │  │
         │          ▼             ▼                 ▼          │  │
         │   CREATE_SPOT     JOIN_SPOT          NO_ACTION      │  │
         │      │              │                    │         │  │
         │      ▼              ▼                    ▼         │  │
         │  execute_       execute_join_spot  if rng.random() │  │
         │  create_spot    (mutates target)       < 0.05:     │  │
         │  (mutates host) ↓                    log NO_ACTION │  │
         │  spots.append() on success:                        │  │
         │  open_spots.++   log JOIN_SPOT                     │  │
         │  log CREATE      after_join_spot()                 │  │
         │  after_create    try_auto_match(target):           │  │
         │                     if MATCHED:                    │  │
         │                       log SPOT_MATCHED             │  │
         │                       open_spots.remove(target)    │  │
         └─────────────────────────┬──────────────────────────┘
                                   │
                                   ▼
                           next agent / next tick
```

---

## Determinism guarantees

- `reset_event_counter(1)` called once at the start of `run_simulation`
  gives `event_id` the same sequence for a given seed.
- The only RNG is `rng = random.Random(seed)`. Every random draw in
  `decision.py`, `executors.py`, and `runner.py` goes through it:
  - `rng.random()` for the time gate, the action roll, and the NO_ACTION
    sampler.
  - `rng.shuffle(active_agents)` for agent ordering.
  - `rng.choice(...)` / `rng.randint(...)` inside `execute_create_spot`
    for region, category, capacity, and lead time.
- `make_event` never calls random; event ids are pulled from the reset
  counter.

Two runs with the same seed produce byte-identical `event_log.jsonl`
(enforced by `sort_keys=True` in `serialize_event`).

---

## Phase extension points (reserved, not implemented in Phase 1)

- **Between steps 4 and 5** — Phase 2 `process_lifecycle(spots, tick, event_log)`
  will handle OPEN→CANCELED, MATCHED→CONFIRMED, etc.
- **After step 5** — Phase 3 `process_settlement(spot, agents, tick)` runs
  for each freshly COMPLETED spot.
- **Active-agent selection in step 2** — Phase 3 replaces the `list(agents)`
  enumeration with a sampled subset to keep the 5000-agent loop under
  3 minutes.

None of these hooks exist in Phase 1. Adding them must not require
reshaping the tick loop — just inserting a new block at the marked point.

---

# Phase 2 — Runtime Flow Update

Task: `sim_03_engine_phase2_complete`
Agent: `sim-engine-engineer`
Date: 2026-04-14

Phase 2 inserts a lifecycle pass BEFORE the decision pass and two new
passes (check-in + cancel) AFTER the decision pass. Phase 1 code path is
preserved byte-identically — every Phase 2 block is gated behind
`if phase >= 2:` so the Phase 1 rng draw sequence is untouched.

## One Phase 2 tick, top to bottom

```
tick = N  (0 <= N < total_ticks)
 |
 v
1. Natural decay / growth
     for agent in agents:
         decay_fatigue(agent)
         grow_social_need(agent)
 |
 v
1.5 process_lifecycle(spots, tick, event_log, agents_by_id, rng=rng)
     for spot in spots:          # single-pass, one transition per spot
         OPEN        -> CANCELED     (SPOT_TIMEOUT)        if age > 48
         MATCHED     -> CONFIRMED    (SPOT_CONFIRMED)      if scheduled-tick <= 2
                        |
                        +-> host.confirmed_spots += spot
                        +-> each participant.confirmed_spots += spot
         CONFIRMED   -> IN_PROGRESS  (SPOT_STARTED)        if tick >= scheduled
         IN_PROGRESS -> DISPUTED     (SPOT_DISPUTED)       if noshow_ratio > 0.5
                     -> COMPLETED    (SPOT_COMPLETED)      otherwise
                        |
                        +-> for pid in spot.checked_in:
                                after_complete_spot(agents_by_id[pid])
 |
 v
2. active_agents = list(agents)
3. open_spots    = [s for s in spots if s.status == OPEN]
4. rng.shuffle(active_agents)
 |
 v
5. For each agent in shuffle order:
     action, target = decide_action(
         agent, tick, open_spots,
         rng=rng,
         region_features=..., persona_templates=...,
         agents_by_id=agents_by_id,   # Phase 2: enables social modifier
         phase=2,
     )
     |
     +-- CREATE_SPOT -> execute_create_spot(..., phase=2)
     |                   -> pick_scheduled_tick (plan §3.6)
     |                   -> log CREATE_SPOT
     |                   -> after_create_spot(agent)
     |
     +-- JOIN_SPOT  -> execute_join_spot
     |                 -> log JOIN_SPOT
     |                 -> after_join_spot(agent)
     |                 -> try_auto_match -> log SPOT_MATCHED
     |
     +-- NO_ACTION  -> ~2% sampled NO_ACTION log
 |
 v
6. Check-in pass
     for spot in spots:
         if spot.status != IN_PROGRESS: continue
         if spot.started_at_tick != tick: continue      # only freshly started
         roster = [host] + participants
         for aid in roster:
             if aid in spot.checked_in or aid in spot.noshow: continue
             roll = rng.random()
             p = 0.85 - 0.20*fatigue + 0.10*(trust - 0.5)
             if roll < p:
                 execute_check_in(agent, spot, tick);  log CHECK_IN
             else:
                 execute_no_show(agent, spot, tick);   log NO_SHOW
 |
 v
7. Cancel pass
     for spot in spots:
         if spot.status not in (OPEN, MATCHED): continue
         for pid in list(spot.participants):
             if rng.random() >= P_CANCEL_JOIN (=0.01): continue
             if execute_cancel_join(agent, spot, tick):
                 log CANCEL_JOIN
         # MATCHED spots that drop below min_participants revert to OPEN
         # so later joiners can still match them.
 |
 v
next tick
```

## Rng draw order (Phase 2)

Per tick, in order:
  1. `process_lifecycle` — zero rng draws (pure deterministic transitions)
  2. `rng.shuffle(active_agents)`
  3. per-agent decision pass: time-gate draw, action roll, NO_ACTION sample draw
     - inside `execute_create_spot(phase=2)`: region/category/capacity/lead
       draws via `rng.choice` / `pick_scheduled_tick` (which calls
       `rng.randint` inside)
  4. check-in pass: `rng.random()` per pending-host+participant
  5. cancel pass: `rng.random()` per participant of every OPEN/MATCHED spot

Because the Phase 1 path short-circuits ALL four Phase 2 additions with
`if phase >= 2:`, the Phase 1 draw sequence is byte-identical. Verified:
`python3 main.py --phase 1` still produces md5
`a51da542975010f6382895621b72f868`.

## Determinism guarantees (Phase 2)

Two runs of `python3 main.py --phase 2` with the same seed produce
byte-identical `event_log.jsonl`. `agents_by_id` is built once at the
start of `run_simulation` and is read-only w.r.t. ordering — all mutation
goes through agent refs, not the dict, so dict iteration order is never
visible to the rng.

---

## One-tick pseudocode (Phase 3)

Phase 3 inserts a dispute-resolution + settlement pass between
`process_lifecycle` and the decision pass and wires three new action
branches into the decision dispatcher.

```
for tick in range(total_ticks):

    # 1. Decay + social-need growth (unchanged across phases)
    for agent in agents:
        decay_fatigue(agent)
        grow_social_need(agent)

    # 1.5 Lifecycle pass (phase>=2) — unchanged
    if phase >= 2:
        process_lifecycle(spots, tick, event_log, agents_by_id, rng=rng)

    # 1.6 Phase 3 — dispute resolution + bulk settlement
    if phase >= 3:
        resolve_disputes(spots, agents_by_id, tick, event_log, rng=rng)
        for spot in spots:
            if (spot.status == SpotStatus.COMPLETED
                and spot.settled_at_tick is None):
                process_settlement(spot, agents_by_id, tick, event_log,
                                   rng=rng)

    # 2. Active-agent snapshot + shuffle (unchanged)
    active_agents = list(agents)
    open_spots = [s for s in spots if s.status == SpotStatus.OPEN]
    rng.shuffle(active_agents)

    # 3. Per-agent decision pass (phase-gated Phase 3 additions inside
    #    decide_action — see probability_table.md)
    for agent in active_agents:
        action, target = decide_action(agent, tick, open_spots, ...)
        if action == "CREATE_SPOT":
            spot = execute_create_spot(...); after_create_spot(agent)
            event_log.append(CREATE_SPOT event)
        elif action == "JOIN_SPOT":
            execute_join_spot(...); after_join_spot(agent)
            event_log.append(JOIN_SPOT event)
            if try_auto_match(target): event_log.append(SPOT_MATCHED)
        elif action == "VIEW_FEED":       # Phase 3
            execute_view_feed(agent, tick)
            event_log.append(VIEW_FEED event)
        elif action == "SAVE_SPOT":       # Phase 3
            execute_save_spot(agent, target, tick)
            event_log.append(SAVE_SPOT event)
        else:
            if rng.random() < NO_ACTION_LOG_PROB:
                event_log.append(NO_ACTION event)  # ~2% sample

    # 4. Phase 2 post-decision passes — check-in + cancel (unchanged)
    if phase >= 2:
        # check-in pass
        for spot in spots:
            if spot.status == IN_PROGRESS and spot.started_at_tick == tick:
                for aid in host + participants:
                    if rng.random() < p_checkin(agent):
                        execute_check_in(...)
                    else:
                        execute_no_show(...)
        # cancel pass
        for spot in spots:
            if spot.status in (OPEN, MATCHED):
                for pid in list(spot.participants):
                    if rng.random() < P_CANCEL_JOIN:
                        execute_cancel_join(...)
```

## Phase 3 settlement — one spot, step by step

`process_settlement(spot, agents_by_id, tick, event_log, rng)`:

```
if spot.status not in (COMPLETED, DISPUTED):    return None
if spot.settled_at_tick is not None:            return None   # idempotent

participants       = [agents_by_id[pid] for pid in spot.participants]
checked_in_agents  = [a for a in participants if a.checked_in_for(spot)]
spot.noshow_count  = len(spot.noshow)

# 1. Per-agent satisfaction (rng.uniform for noise, plan §4.4)
sats = []
for agent in checked_in_agents:
    sat = calculate_satisfaction(agent, spot, agents_by_id, rng=rng)
    sats.append(sat)
    agent.satisfaction_history.append(sat)
avg_sat              = mean(sats) if sats else 0.0
spot.avg_satisfaction = avg_sat

# 2. Review generation — p_review = 0.3 + 0.4 * |sat - 0.5|
for agent, sat in zip(checked_in_agents, sats):
    if rng.random() < 0.3 + 0.4 * abs(sat - 0.5):
        event_log.append(WRITE_REVIEW event {"satisfaction": sat})
        agent.review_spots.append(spot.spot_id)
        spot.review_count += 1

# 3. Host trust delta
host.prev_trust = host.trust_score
if   avg_sat >= 0.7: host.trust_score = clamp(host.trust_score + 0.05)
elif avg_sat <  0.4: host.trust_score = clamp(host.trust_score - 0.08)

# 4. Participant no-show penalty (0.15 per noshow)
for agent in participants:
    if agent.agent_id in spot.noshow:
        agent.trust_score = clamp(agent.trust_score - 0.15)

# 5. Status flip + events
spot.settled_at_tick = tick
if was COMPLETED:
    spot.status = SETTLED
    event_log.append(SPOT_SETTLED event {"avg_sat", "host_trust_delta",
                                          "completed", "noshow"})
    event_log.append(SETTLE event for host)
# (DISPUTED branch defers the SETTLED flip to resolve_disputes)

return SettlementResult(...)
```

## Phase 3 dispute resolution — `resolve_disputes`

```
for spot in spots:
    if spot.status != DISPUTED:  continue
    dispute_age = tick - spot.disputed_at_tick

    if dispute_age > 24:
        host.trust_score -= 0.12
        spot.status = FORCE_SETTLED
        spot.force_settled = True
        spot.settled_at_tick = tick
        event_log.append(FORCE_SETTLED event {"reason":"dispute_timeout"})
        continue

    if dispute_age > 6:
        result = process_settlement(spot, ..., rng=rng)
        if spot.avg_satisfaction >= 0.5:
            spot.status = SETTLED
            event_log.append(DISPUTE_RESOLVED event)
            event_log.append(SPOT_SETTLED event {"from_dispute": True, ...})
```

## Rng draw order (Phase 3)

Per tick, in order:
  1. `process_lifecycle` — zero rng draws (same as Phase 2)
  2. `resolve_disputes` — no rng of its own except through `process_settlement`
     (`rng.uniform` for satisfaction noise + `rng.random()` for review roll)
  3. bulk `process_settlement` over freshly-COMPLETED spots — same draw
     order: per-agent `rng.uniform` (sat noise) then per-agent `rng.random()`
     (review prob)
  4. `rng.shuffle(active_agents)`
  5. per-agent decision pass:
     - time-gate `rng.random()`
     - single stacked `rng.random()` vs (p_create + p_join)
     - execute_create_spot draws (`rng.choice` / `pick_scheduled_tick`)
     - Phase 3 only: two extra `rng.random()` draws for VIEW_FEED / SAVE_SPOT
       gated on the agent NOT having already picked CREATE_SPOT / JOIN_SPOT
     - NO_ACTION sample `rng.random()` if no action fired
  6. check-in pass — `rng.random()` per pending host+participant
  7. cancel pass — `rng.random()` per participant of each OPEN/MATCHED spot

Because ALL Phase 3 additions are gated behind `if phase >= 3:` (including
the extra VIEW_FEED / SAVE_SPOT draws inside `decide_action` and the
trust-aware candidate sort in `find_matchable_spots`), the Phase 1 and
Phase 2 draw sequences are byte-identical. Verified with the three-phase
md5 gate:

  * Phase 1: `a51da542975010f6382895621b72f868`
  * Phase 2: `ea8c17ec0030e06f05e375f466bcbee3`
  * Phase 3 (2000 agents, 336 ticks, seed=42):
    `678a0e4a3797db478d928d11d7caafe8` (reproducible across runs)

## Phase 3 determinism guarantees

Two runs of `python3 main.py --phase 3` with the same seed produce
byte-identical `event_log.jsonl`. Settlement iteration order is
`for spot in spots` (insertion order), dispute iteration order is
identical, and every `rng` draw is on the injected instance. No dict
iteration is observable by the rng (same invariant as Phase 2).

## Performance note (Phase 3)

Plan §1 targets 5000 agents × 336 ticks in <180s. The current Python
implementation hits ~10+ minutes for that size (spot count balloons to
~140k with the current `p_create` weights, which drives the lifecycle
and settlement scans O(ticks × spots)). For initial validation the
`phase_3.agents` config is temporarily lowered to **2000** agents so the
run finishes in ~3m15s. Numpy vectorization is a Phase 3 stretch goal
(plan §4.6 allows it); sim-engine-engineer has flagged the gap for
sim-analyst-qa to re-tune `p_create` / lifecycle cleanup instead of
optimizing the Python hot loop.
