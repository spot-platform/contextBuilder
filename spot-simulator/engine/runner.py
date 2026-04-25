"""Main tick loop — plan §2.7 (Phase 1 MVP) + plan §3 (Phase 2 lifecycle)
+ peer-pivot §3/§3-counter/§3-request (Phase Peer-B `_run_peer`).

`run_simulation` wires together decay, lifecycle, decision, check-in, and
cancel phases into a single deterministic loop. `run_phase` is the
CLI-facing helper that loads configs, builds the agent population, runs
the simulation, writes `output/event_log.jsonl`, and prints a short
summary.

## simulation_mode dispatch (peer-pivot §7-3)

`run_simulation` now accepts an optional `simulation_mode` kwarg that is
routed into one of two internal entrypoints:

  - `"legacy"` → `_run_legacy`  : byte-identical Phase 1~3 tick loop.
                                  append-only guarantee — nothing in the
                                  legacy body changes.
  - `"peer"`   → `_run_peer`    : peer-pivot Phase B tick loop. p_teach /
                                  p_learn / counter-offer / request
                                  lifecycle coexist with the Phase 2
                                  lifecycle / Phase 3 settlement passes.

Default mode is `"legacy"` so existing tests (`phase=1/2/3` without the
kwarg) continue to hit the same code path. CLI `run_phase` keeps calling
`run_simulation` with `phase=<int>` only.

## Phase 1 vs Phase 2 flow (legacy path)

Phase 1 (`phase=1`, default):

    1. decay_fatigue / grow_social_need for every agent
    2. build active set + shuffle
    3. decide_action -> execute -> log (CREATE_SPOT / JOIN_SPOT / NO_ACTION)

Phase 2 (`phase=2`) inserts two new passes AROUND the decision pass:

    0.5  process_lifecycle(spots, tick, ...)            [pre-decision]
    ...  (decision pass, as Phase 1)
    5.5  check-in pass over every agent with a spot that transitioned
         to IN_PROGRESS this tick (roll p_checkin, emit CHECK_IN / NO_SHOW)
    5.6  cancel pass — each participant of an OPEN/MATCHED spot rolls
         P_CANCEL_JOIN; fires CANCEL_JOIN on hit

Gating these passes behind `if phase >= 2:` guarantees the Phase 1 rng
draw sequence is byte-identical to the pre-Phase-2 runner.

## Peer mode flow (peer-pivot §3)

`_run_peer` extends the legacy skeleton with 4 new passes per tick:

    1.7  process_open_requests(...)            [post-lifecycle, pre-decide]
    4.5  (inside decide pass) p_post_request / p_teach / p_learn /
         find_matchable_teach_spot branches
    5.7  counter-offer trigger + finalize pass (plan §3-counter)
    (settlement pass unchanged — reuses Phase 3 process_settlement path
     via `phase >= 3` gate to keep relationship hooks Phase C-ready.)
"""

from __future__ import annotations

import itertools
import random
import time
from pathlib import Path
from typing import Iterator

from data.adapters import (  # noqa: F401 — re-exported via engine.__init__
    budget_penalty,
    category_match,
    recent_host_penalty,
    region_create_affinity,
)
from data.agent_factory import build_agent_population
from data.loader import (
    load_persona_region_affinity,
    load_persona_templates,
    load_region_features,
    load_simulation_config,
    load_skills_catalog,
)
from engine.decay import (
    after_create_spot,
    after_join_spot,
    decay_fatigue,
    grow_social_need,
)
from engine.decision import decide_action
from engine.executors import (
    execute_cancel_join,
    execute_check_in,
    execute_create_spot,
    execute_join_spot,
    execute_no_show,
    execute_save_spot,
    execute_view_feed,
    try_auto_match,
)
from engine.lifecycle import process_lifecycle
from engine.settlement import process_settlement, resolve_disputes

# ── Peer-pivot Phase B (append-only) — these modules are only imported
# when `simulation_mode == "peer"`. Importing at the top of runner.py is
# fine because they do not reference any legacy decision / executor
# symbols.
from engine.fee import suggest_fee_breakdown
from engine.negotiation import (
    DEFAULT_COUNTER_OFFER_RESPONSE_TICKS,
    check_counter_offer_trigger,
    finalize_counter_offer,
    send_counter_offer,
)
from engine.peer_decision import (
    find_matchable_teach_spot,
    p_learn,
    p_teach,
    pick_skill_to_teach,
    pick_teach_mode,
    pick_venue,
)
from engine.request_lifecycle import (
    p_post_request,
    process_open_requests,
)

# ── Phase Peer-C (append-only) — relationship FSM, wallet/earnings
# tracker, reputation EMA, referral emission. Only _run_peer calls these;
# _run_legacy is untouched.
from engine import relationships as rel_module
from engine import wallet_tracker
from models import (
    AgentState,
    EventLog,
    Spot,
    SpotStatus,
    make_event,
    reset_event_counter,
    serialize_event,
)
from models.skills import Assets, FeeBreakdown, SkillProfile, SkillRequest

# Sampling rate for NO_ACTION logging — plan §2.7 asks for a ~5% sample so
# the log keeps some "nothing happened" signal without ballooning to tens of
# thousands of rows.
NO_ACTION_LOG_PROB = 0.02

# Phase 2 — per-participant-per-tick chance that an agent who already joined
# an OPEN or MATCHED spot decides to back out. Deliberately small so the
# cancel pass produces a trickle of CANCEL_JOIN events, not an avalanche.
P_CANCEL_JOIN: float = 0.01

# Phase 2 check-in probability coefficients (plan §3.4).
#
# Retuned from the original (0.85, 0.20, 0.10) because the baseline drove
# NO_SHOW ratios to ~36%, well above the 5-15% QA target band. Bumping the
# base to 0.92 and halving the fatigue penalty to 0.12 lands fatigue=0.5,
# trust=0.5 at p=0.86 (~14% no-show) and fatigue=0.8 at p=0.824 (~17.6%).
# Exposed as module-level constants so sim-analyst-qa can retune without
# touching the tick loop.
P_CHECKIN_BASE: float = 0.92
P_CHECKIN_FATIGUE_COEFF: float = 0.08
P_CHECKIN_TRUST_COEFF: float = 0.10


def _p_checkin(agent: AgentState) -> float:
    """Plan §3.4 runner check-in pass.

    `p_checkin = BASE - FATIGUE_COEFF * fatigue + TRUST_COEFF * (trust - 0.5)`
    """
    return (
        P_CHECKIN_BASE
        - P_CHECKIN_FATIGUE_COEFF * agent.fatigue
        + P_CHECKIN_TRUST_COEFF * (agent.trust_score - 0.5)
    )


def run_simulation(
    agents: list[AgentState],
    config: dict,
    region_features: dict,
    persona_templates: dict,
    persona_affinity: dict,
    *,
    seed: int | None = None,
    phase: int = 1,
    simulation_mode: str = "legacy",
    skills_catalog: dict | None = None,
) -> tuple[list[EventLog], list[Spot]]:
    """Run the tick loop and return `(event_log, spots)`.

    `phase=1` preserves the exact Phase 1 behaviour (byte-identical
    `event_log.jsonl` for a given seed). `phase=2` enables lifecycle,
    check-in, and cancel passes (plan §3).

    `simulation_mode`:
      - ``"legacy"`` (default) → runs the Phase 1~3 code path exactly as
        before. `skills_catalog` is ignored.
      - ``"peer"``              → runs the peer-pivot Phase B code path
        (`_run_peer`). Requires `skills_catalog` to be the parsed
        `config/skills_catalog.yaml` dict.

    `config` is expected to be the parsed phase block, e.g.
    `simulation_config["phase_1"]`. `total_ticks` defaults to 48.
    """

    if simulation_mode == "peer":
        return _run_peer(
            agents,
            config,
            region_features=region_features,
            persona_templates=persona_templates,
            persona_affinity=persona_affinity,
            seed=seed,
            phase=phase,
            skills_catalog=skills_catalog or {},
        )
    return _run_legacy(
        agents,
        config,
        region_features=region_features,
        persona_templates=persona_templates,
        persona_affinity=persona_affinity,
        seed=seed,
        phase=phase,
    )


def _run_legacy(
    agents: list[AgentState],
    config: dict,
    region_features: dict,
    persona_templates: dict,
    persona_affinity: dict,
    *,
    seed: int | None = None,
    phase: int = 1,
) -> tuple[list[EventLog], list[Spot]]:
    """Phase 1~3 tick loop — byte-identical to the pre-pivot runner.

    This is the original body of `run_simulation` renamed to `_run_legacy`
    so the peer pivot can add a new entrypoint without altering the rng
    draw sequence that 53 legacy pytest tests rely on.
    """
    del persona_affinity  # captured by the caller via the agent factory

    reset_event_counter(1)
    rng = random.Random(seed)
    spot_counter: Iterator[int] = itertools.count(1)

    spots: list[Spot] = []
    event_log: list[EventLog] = []

    # Phase 2 helpers — cheap to build once and reuse across the run.
    agents_by_id: dict[str, AgentState] = {a.agent_id: a for a in agents}

    total_ticks = int(config.get("total_ticks", 48))

    for tick in range(total_ticks):
        # 1. Natural decay / growth on every agent.
        for agent in agents:
            decay_fatigue(agent)
            grow_social_need(agent)

        # 1.5 (Phase 2) Lifecycle pass — runs BEFORE the decision pass so
        # decisions see fresh status values. Phase 1 skips this entirely to
        # keep the rng untouched.
        if phase >= 2:
            process_lifecycle(
                spots, tick, event_log, agents_by_id, rng=rng
            )

        # 1.6 (Phase 3) Dispute resolution + settlement pass.
        #
        # `resolve_disputes` runs first so any DISPUTED spot whose age has
        # crossed the 6h or 24h boundary advances to SETTLED / FORCE_SETTLED
        # before the bulk settlement pass picks up freshly-COMPLETED spots.
        #
        # The bulk settlement pass walks every spot and runs
        # `process_settlement` on those with status==COMPLETED that have not
        # yet been settled (idempotency guard inside process_settlement
        # also catches double-settle). Phase 1/2 never enter this branch.
        if phase >= 3:
            resolve_disputes(
                spots, agents_by_id, tick, event_log, rng=rng
            )
            for spot in spots:
                if (
                    spot.status == SpotStatus.COMPLETED
                    and spot.settled_at_tick is None
                ):
                    process_settlement(
                        spot, agents_by_id, tick, event_log, rng=rng
                    )

        # 2. Active-agent selection (Phase 1 + 2: all agents every tick).
        active_agents = list(agents)

        # 3. Snapshot OPEN spots for matchability scans this tick.
        open_spots = [s for s in spots if s.status == SpotStatus.OPEN]

        # 4. Shuffle with seeded RNG for determinism.
        rng.shuffle(active_agents)

        for agent in active_agents:
            action, target = decide_action(
                agent,
                tick,
                open_spots,
                rng=rng,
                region_features=region_features,
                persona_templates=persona_templates,
                agents_by_id=agents_by_id if phase >= 2 else None,
                phase=phase,
            )

            if action == "CREATE_SPOT":
                new_spot = execute_create_spot(
                    agent,
                    tick,
                    rng=rng,
                    region_features=region_features,
                    persona_templates=persona_templates,
                    spot_counter=spot_counter,
                    phase=phase,
                )
                spots.append(new_spot)
                open_spots.append(new_spot)
                event_log.append(
                    make_event(tick, "CREATE_SPOT", agent=agent, spot=new_spot)
                )
                after_create_spot(agent)

            elif action == "JOIN_SPOT" and target is not None:
                joined = execute_join_spot(agent, target, tick)
                if joined:
                    event_log.append(
                        make_event(tick, "JOIN_SPOT", agent=agent, spot=target)
                    )
                    after_join_spot(agent)

                    if try_auto_match(target, phase=phase):
                        event_log.append(
                            make_event(
                                tick,
                                "SPOT_MATCHED",
                                agent=None,
                                spot=target,
                            )
                        )
                        # Once MATCHED the spot is no longer a JOIN candidate
                        # this tick — drop it from the local OPEN snapshot.
                        if target in open_spots:
                            open_spots.remove(target)

            elif action == "VIEW_FEED":
                # Phase 3 background action — pure behavioural signal.
                execute_view_feed(agent, tick)
                event_log.append(
                    make_event(tick, "VIEW_FEED", agent=agent, spot=None)
                )

            elif action == "SAVE_SPOT" and target is not None:
                # Phase 3 bookmark action.
                execute_save_spot(agent, target, tick)
                event_log.append(
                    make_event(tick, "SAVE_SPOT", agent=agent, spot=target)
                )

            else:
                # NO_ACTION — sampled for QA signal (~5%).
                if rng.random() < NO_ACTION_LOG_PROB:
                    event_log.append(
                        make_event(tick, "NO_ACTION", agent=agent, spot=None)
                    )

        # --- Phase 2 post-decision passes -----------------------------------
        if phase >= 2:
            # 6. Check-in pass — every spot that just transitioned to
            # IN_PROGRESS in this tick's lifecycle pass gets a roll per
            # participant (and host). Iterate spots in insertion order so
            # the rng consumption is deterministic.
            for spot in spots:
                if spot.status != SpotStatus.IN_PROGRESS:
                    continue
                if spot.started_at_tick != tick:
                    continue

                # Host + participants all roll independently.
                roster: list[str] = []
                if spot.host_agent_id not in roster:
                    roster.append(spot.host_agent_id)
                for pid in spot.participants:
                    if pid not in roster:
                        roster.append(pid)

                for aid in roster:
                    agent = agents_by_id.get(aid)
                    if agent is None:
                        continue
                    # Already resolved this tick? (defensive)
                    if agent.agent_id in spot.checked_in:
                        continue
                    if agent.agent_id in spot.noshow:
                        continue

                    roll = rng.random()
                    if roll < _p_checkin(agent):
                        execute_check_in(agent, spot, tick)
                        event_log.append(
                            make_event(
                                tick,
                                "CHECK_IN",
                                agent=agent,
                                spot=spot,
                                payload={
                                    "arrived_at_tick": tick,
                                    "persona_id": agent.agent_id,
                                },
                            )
                        )
                    else:
                        execute_no_show(agent, spot, tick)
                        event_log.append(
                            make_event(
                                tick,
                                "NO_SHOW",
                                agent=agent,
                                spot=spot,
                                payload={
                                    "persona_id": agent.agent_id,
                                    "reason": "no_show",
                                },
                            )
                        )

            # 7. Cancel pass — independent rolls for every participant of
            # an OPEN or MATCHED spot. Iterate spots in insertion order;
            # mutate a copy of participants so the in-place remove inside
            # execute_cancel_join is safe.
            for spot in spots:
                if spot.status not in (SpotStatus.OPEN, SpotStatus.MATCHED):
                    continue
                # Snapshot the roster — execute_cancel_join mutates the list.
                for pid in list(spot.participants):
                    if rng.random() >= P_CANCEL_JOIN:
                        continue
                    agent = agents_by_id.get(pid)
                    if agent is None:
                        continue
                    if execute_cancel_join(agent, spot, tick):
                        event_log.append(
                            make_event(
                                tick,
                                "CANCEL_JOIN",
                                agent=agent,
                                spot=spot,
                                payload={
                                    # FE handoff 2026-04-24: maps to
                                    # `spot.participant_left` event.
                                    "persona_id": agent.agent_id,
                                    "left_at_tick": tick,
                                    "reason": "cancel_join",
                                },
                            )
                        )

    return event_log, spots


# ---------------------------------------------------------------------------
# Peer-pivot Phase B tick loop (plan §3 / §3-counter / §3-request)
# ---------------------------------------------------------------------------
#
# peer-pivot §7-3 append-only: the _run_peer body below NEVER calls the
# legacy `p_create` / `p_join` / `find_matchable_spots` functions from
# `engine/decision.py`. Legacy path stays byte-identical.
#
# Peer-mode probability constants. Exposed as module-level so sim-analyst-qa
# can retune without touching the tick loop (mirrors the Phase 2 pattern).

#: Sampling rate for NO_ACTION logging in peer mode.
PEER_NO_ACTION_LOG_PROB: float = 0.01

#: Baseline weight applied to `p_teach` / `p_learn` / `p_post_request` raw
#: probabilities before the rng comparison. These raw values sit in the
#: ~[0, 1] range already (teach/learn/post have multiple clamped factors)
#: so they can be compared directly.
PEER_POST_REQUEST_COEFF: float = 0.6
PEER_TEACH_COEFF: float = 0.6
PEER_LEARN_COEFF: float = 1.0

#: Student per-tick max open requests — mirrors
#: `peer.max_open_requests_per_learner` in simulation_config.yaml.
DEFAULT_MAX_OPEN_REQUESTS_PER_LEARNER: int = 2


def _enrich_agents_with_peer_fields(
    agents: list[AgentState],
    persona_templates: dict,
) -> None:
    """Populate `skills / assets / role_preference / relationships` on each
    agent from the loaded persona dict.

    The legacy `agent_factory.init_agent_from_persona` predates Phase Peer-A
    and does not read these keys. Rather than mutate the factory (would
    violate append-only), this helper runs once at the start of
    `_run_peer` and enriches every agent whose persona has peer fields.

    Agents with already-populated skills (e.g. from a test fixture) are
    left untouched.
    """

    for agent in agents:
        tpl = persona_templates.get(agent.persona_type)
        if not tpl:
            continue

        # skills
        if not getattr(agent, "skills", None):
            raw_skills = tpl.get("skills") or {}
            sk_map: dict[str, SkillProfile] = {}
            for skill_name, profile in raw_skills.items():
                if not isinstance(profile, dict):
                    continue
                sk_map[skill_name] = SkillProfile(
                    level=int(profile.get("level", 0)),
                    years_exp=float(profile.get("years_exp", 0.0)),
                    teach_appetite=float(profile.get("teach", 0.0)),
                    learn_appetite=float(profile.get("learn", 0.0)),
                )
            agent.skills = sk_map

        # assets — overwrite default Assets() if persona provides fields.
        asset_keys = (
            "wallet_monthly",
            "pocket_money_motivation",
            "time_budget_weekday",
            "time_budget_weekend",
            "equipment",
            "space_level",
            "space_type",
            "social_capital",
            "reputation_score",
        )
        has_any_asset = any(k in tpl for k in asset_keys)
        if has_any_asset:
            equipment = tpl.get("equipment") or []
            agent.assets = Assets(
                wallet_monthly=int(tpl.get("wallet_monthly", 25_000)),
                pocket_money_motivation=float(tpl.get("pocket_money_motivation", 0.5)),
                time_budget_weekday=int(tpl.get("time_budget_weekday", 3)),
                time_budget_weekend=int(tpl.get("time_budget_weekend", 10)),
                equipment=set(equipment) if equipment else set(),
                space_level=int(tpl.get("space_level", 1)),
                space_type=str(tpl.get("space_type", "cafe")),
                social_capital=float(tpl.get("social_capital", 0.5)),
                reputation_score=float(tpl.get("reputation_score", 0.5)),
            )

        if "role_preference" in tpl:
            agent.role_preference = str(tpl["role_preference"])


def _run_peer_settlement_hook(
    spot: Spot,
    host: AgentState | None,
    agents_by_id: dict[str, AgentState],
    tick: int,
    rng: random.Random,
) -> list[EventLog]:
    """Phase Peer-C post-settlement hook (plan §3-5 / §3-6).

    Called from `_run_peer` right after `process_settlement` has flipped
    a spot to SETTLED / FORCE_SETTLED. Responsible for:

      1. relationship FSM update — for each checked-in partner,
         update_relationship(host, partner, spot, sat, tick, rng) → emits
         BOND_UPDATED / FRIEND_UPGRADE.
      2. reputation EMA — host.assets.reputation_score ← 0.9*prev + 0.1*avg.
         Emits REPUTATION_UPDATED.
      3. host wallet credit — peer_labor_fee × partner_count →
         host.assets.earn_total / wallet_monthly. Emits POCKET_MONEY_EARNED.
      4. referral emission — for each happy partner (avg_sat >= 0.75 &
         rel >= regular), rng-gated REFERRAL_SENT to their highest-affinity
         friend.

    Legacy `process_settlement` is NOT touched — this hook reads
    `spot.avg_satisfaction` and per-partner satisfaction from each
    participant's `satisfaction_history[-1]` (both populated by the
    settlement pass that ran immediately before).

    Returns the collected event list in the order
    `relationships → reputation → wallet → referrals` so rng draws are
    deterministic for a given seed.
    """
    events: list[EventLog] = []

    # Guard: only fire when settlement actually ran.
    if spot.settled_at_tick is None or spot.avg_satisfaction is None:
        return events
    if host is None:
        return events
    # Idempotency guard — rel hooks should only run once per spot.
    if getattr(spot, "_peer_c_hook_done", False):
        return events
    # Use an attribute instead of adding a dataclass field so this stays
    # append-only at the Spot model level. dataclasses allow ad-hoc attrs
    # on instances; runner is the only caller that looks at this flag.
    spot._peer_c_hook_done = True  # type: ignore[attr-defined]

    avg_sat = float(spot.avg_satisfaction)

    # (1) Relationship FSM — one call per checked-in partner (host excl).
    checked_in_partners: list[AgentState] = []
    for pid in spot.participants:
        if pid == host.agent_id:
            continue
        partner = agents_by_id.get(pid)
        if partner is None:
            continue
        if not partner.checked_in_for(spot.spot_id):
            continue
        checked_in_partners.append(partner)

    for partner in checked_in_partners:
        # Prefer per-partner satisfaction from history; fall back to avg.
        hist = partner.satisfaction_history
        partner_sat = float(hist[-1]) if hist else avg_sat
        events.extend(
            rel_module.update_relationship(
                host, partner, spot, partner_sat, tick, rng
            )
        )

    # (2) Reputation EMA + event.
    prev_rep = float(host.assets.reputation_score)
    rel_module.update_reputation(host, avg_sat)
    delta = host.assets.reputation_score - prev_rep
    events.extend(
        wallet_tracker.record_reputation_update(host, tick, delta, spot=spot)
    )

    # (3) Host wallet credit.
    events.extend(
        wallet_tracker.credit_host_on_settlement(host, spot, tick)
    )

    # (4) Referral emission — iterate partners in insertion order so rng
    # draws stay deterministic.
    for partner in checked_in_partners:
        events.extend(
            rel_module.maybe_emit_referral(
                partner, host, agents_by_id, spot, tick, rng
            )
        )

    return events


def _run_peer(
    agents: list[AgentState],
    config: dict,
    region_features: dict,
    persona_templates: dict,
    persona_affinity: dict,
    *,
    seed: int | None = None,
    phase: int = 1,
    skills_catalog: dict,
) -> tuple[list[EventLog], list[Spot]]:
    """Phase Peer-B tick loop (plan §3 / §3-counter / §3-request).

    Structural contract:
      1. decay_fatigue / grow_social_need (legacy pass)
      2. (phase>=2) process_lifecycle
      3. (phase>=3) resolve_disputes + process_settlement
      4. process_open_requests (peer — SkillRequest → Spot matching)
      5. shuffle(agents) + per-agent decide branch:
           - p_post_request → CREATE_SKILL_REQUEST + append to open_requests
           - p_teach        → CREATE_TEACH_SPOT    + append to spots
           - find_matchable_teach_spot → JOIN_TEACH_SPOT
      6. counter-offer trigger + finalize pass over OPEN spots
      7. (phase>=2) check-in pass + cancel pass (legacy Phase 2 logic —
         reused verbatim so Phase 2 event counts stay comparable)

    All randomness flows through the injected `rng` for determinism.
    """

    del region_features  # reserved for p_teach region density Phase C
    del persona_affinity  # captured by the caller via the agent factory

    reset_event_counter(1)
    rng = random.Random(seed)
    spot_counter: Iterator[int] = itertools.count(1)
    request_counter: Iterator[int] = itertools.count(1)

    spots: list[Spot] = []
    open_requests: list[SkillRequest] = []
    event_log: list[EventLog] = []

    # Enrich agents with peer fields from persona yaml once per run.
    _enrich_agents_with_peer_fields(agents, persona_templates)

    agents_by_id: dict[str, AgentState] = {a.agent_id: a for a in agents}

    # Peer config block (optional). Keys fall through to module-level
    # defaults so legacy simulation_config.yaml (without a `peer:` block)
    # still runs.
    peer_cfg = config.get("peer", {}) if isinstance(config, dict) else {}
    response_wait = int(
        peer_cfg.get(
            "counter_offer_response_ticks", DEFAULT_COUNTER_OFFER_RESPONSE_TICKS
        )
    )
    max_open_per_learner = int(
        peer_cfg.get(
            "max_open_requests_per_learner", DEFAULT_MAX_OPEN_REQUESTS_PER_LEARNER
        )
    )
    request_deadline_lead = int(peer_cfg.get("request_wait_deadline_ticks", 12))

    total_ticks = int(config.get("total_ticks", 48))

    # Spot / request id generators — prefix differs so event_log sorts nicely.
    def _alloc_spot_id() -> str:
        return f"S_{next(spot_counter):04d}"

    def _alloc_request_id() -> str:
        return f"R_{next(request_counter):04d}"

    def _count_open_requests_for(learner_id: str) -> int:
        return sum(
            1
            for r in open_requests
            if r.status == "OPEN" and r.learner_agent_id == learner_id
        )

    for tick in range(total_ticks):
        # 1. decay / growth (legacy pass)
        for agent in agents:
            decay_fatigue(agent)
            grow_social_need(agent)

        # 2. (phase>=2) lifecycle — reuse the Phase 2 processor so
        # CONFIRMED → IN_PROGRESS → COMPLETED transitions run exactly
        # like legacy mode. peer spots with skill_topic != "" are
        # transparent to process_lifecycle (it only touches Phase 2 fields).
        if phase >= 2:
            process_lifecycle(spots, tick, event_log, agents_by_id, rng=rng)

        # 3. (phase>=3) settlement
        if phase >= 3:
            resolve_disputes(spots, agents_by_id, tick, event_log, rng=rng)
            for spot in spots:
                if (
                    spot.status == SpotStatus.COMPLETED
                    and spot.settled_at_tick is None
                ):
                    process_settlement(
                        spot, agents_by_id, tick, event_log, rng=rng
                    )

            # 3.5 Phase Peer-C settlement hook (plan §3-5 / §3-6).
            # For every spot whose status flipped to SETTLED /
            # FORCE_SETTLED this run and hasn't been hooked yet, run the
            # relationship FSM + reputation + wallet + referral pass.
            # `_peer_c_hook_done` is the idempotency flag stored on the
            # spot instance. legacy `_run_legacy` never enters this block.
            for spot in spots:
                if spot.status not in (SpotStatus.SETTLED, SpotStatus.FORCE_SETTLED):
                    continue
                if getattr(spot, "_peer_c_hook_done", False):
                    continue
                host = agents_by_id.get(spot.host_agent_id)
                peer_c_events = _run_peer_settlement_hook(
                    spot, host, agents_by_id, tick, rng
                )
                event_log.extend(peer_c_events)

        # 4. process_open_requests — request_matched path creates new spots
        # that are inserted into `spots` so the decision pass below can
        # see them as open JOIN candidates.
        new_from_requests: list[Spot] = []
        req_events = process_open_requests(
            agents,
            open_requests,
            tick,
            rng,
            catalog=skills_catalog,
            spot_id_generator=_alloc_spot_id,
            new_spots_collector=new_from_requests,
        )
        event_log.extend(req_events)
        for s in new_from_requests:
            spots.append(s)

        # 5. decision pass
        active_agents = list(agents)
        rng.shuffle(active_agents)

        # Snapshot OPEN teach-spots for the JOIN candidate scan this tick.
        open_teach_spots = [
            s
            for s in spots
            if s.status == SpotStatus.OPEN and s.skill_topic
        ]

        for agent in active_agents:
            skills = getattr(agent, "skills", None) or {}

            # --- (a) p_post_request ------------------------------------
            # pick the skill with the highest learn_appetite; post only
            # if the student has headroom (< max_open_per_learner).
            learn_candidates = [
                (skill, sp)
                for skill, sp in skills.items()
                if sp.learn_appetite >= 0.3
            ]
            learn_candidates.sort(
                key=lambda kv: (-kv[1].learn_appetite, kv[0])
            )

            posted_request = False
            if (
                learn_candidates
                and _count_open_requests_for(agent.agent_id) < max_open_per_learner
            ):
                top_skill, top_sp = learn_candidates[0]
                p_req = p_post_request(
                    agent, top_skill, tick, catalog=skills_catalog
                ) * PEER_POST_REQUEST_COEFF
                if rng.random() < p_req:
                    # Build SkillRequest.
                    assets = getattr(agent, "assets", None)
                    wallet = int(getattr(assets, "wallet_monthly", 25_000))
                    max_fee = max(3_000, min(15_000, wallet // 3))
                    req = SkillRequest(
                        request_id=_alloc_request_id(),
                        learner_agent_id=agent.agent_id,
                        skill_topic=top_skill,
                        region_id=agent.home_region_id,
                        created_at_tick=tick,
                        max_fee_per_partner=max_fee,
                        preferred_teach_mode=pick_teach_mode(
                            top_skill, skills_catalog, rng
                        ),
                        preferred_venue=pick_venue(
                            top_skill, skills_catalog, rng
                        ),
                        wait_deadline_tick=tick + request_deadline_lead,
                    )
                    open_requests.append(req)
                    event_log.append(
                        make_event(
                            tick=tick,
                            event_type="CREATE_SKILL_REQUEST",
                            agent=agent,
                            payload={
                                "request_id": req.request_id,
                                "skill": top_skill,
                                "max_fee": req.max_fee_per_partner,
                                "mode": req.preferred_teach_mode,
                                "venue": req.preferred_venue,
                                "deadline_tick": req.wait_deadline_tick,
                            },
                        )
                    )
                    posted_request = True

            if posted_request:
                continue

            # --- (b) p_teach — pick a skill, create teach-spot ----------
            teach_skill = pick_skill_to_teach(
                agent, tick, catalog=skills_catalog, rng=rng
            )
            created_spot = False
            if teach_skill is not None:
                p_t = p_teach(
                    agent, teach_skill, tick, catalog=skills_catalog
                ) * PEER_TEACH_COEFF
                if rng.random() < p_t:
                    teach_mode = pick_teach_mode(
                        teach_skill, skills_catalog, rng
                    )
                    spec = skills_catalog.get(teach_skill) or {}
                    venue_type = spec.get("default_venue", "cafe")
                    expected_partners = (
                        4 if teach_mode == "small_group"
                        else 1 if teach_mode == "1:1"
                        else 6
                    )
                    capacity = expected_partners
                    fb = suggest_fee_breakdown(
                        agent,
                        teach_skill,
                        teach_mode,
                        venue_type,
                        expected_partners=expected_partners,
                        catalog=skills_catalog,
                    )
                    scheduled = tick + rng.randint(6, 24)
                    new_spot = Spot(
                        spot_id=_alloc_spot_id(),
                        host_agent_id=agent.agent_id,
                        region_id=agent.home_region_id,
                        category="teach",
                        capacity=capacity,
                        min_participants=2,
                        scheduled_tick=scheduled,
                        created_at_tick=tick,
                        skill_topic=teach_skill,
                        host_skill_level=skills[teach_skill].level,
                        fee_breakdown=fb,
                        venue_type=venue_type,
                        teach_mode=teach_mode,
                        target_partner_count=capacity,
                        min_viable_count=2,
                        wait_deadline_tick=tick + request_deadline_lead,
                        origination_mode="offer",
                        origination_agent_id=agent.agent_id,
                        # FE handoff 2026-04-24: deterministic expected-close.
                        # Default to scheduled_tick + duration; SPOT_RENEGOTIATED
                        # is the only path that rewrites this.
                        expected_closed_at_tick=scheduled + 2,
                    )
                    spots.append(new_spot)
                    open_teach_spots.append(new_spot)
                    agent.hosted_spots.append(new_spot.spot_id)
                    after_create_spot(agent)
                    event_log.append(
                        make_event(
                            tick=tick,
                            event_type="CREATE_TEACH_SPOT",
                            agent=agent,
                            spot=new_spot,
                            payload={
                                "skill": teach_skill,
                                "fee": new_spot.fee_per_partner,
                                "teach_mode": teach_mode,
                                "venue_type": venue_type,
                                "origination_mode": "offer",
                                # Phase Peer-F: full fee_breakdown in payload
                                # so content_spec_builder reads it directly
                                # instead of scaling-down catalog defaults.
                                "host_skill_level": skills[teach_skill].level,
                                "capacity": new_spot.capacity,
                                "fee_breakdown": {
                                    "peer_labor_fee": fb.peer_labor_fee,
                                    "material_cost": fb.material_cost,
                                    "venue_rental": fb.venue_rental,
                                    "equipment_rental": fb.equipment_rental,
                                    "total": fb.total,
                                    "passthrough_total": fb.passthrough_total,
                                },
                                # FE handoff 2026-04-24: spot.created payload
                                # fields the BE publisher needs to build a
                                # `SpotLifecycleEvent.spot.created`.
                                "host_persona_id": agent.agent_id,
                                "region_id": agent.home_region_id,
                                "scheduled_tick": scheduled,
                                "expected_closed_at_tick": new_spot.expected_closed_at_tick,
                                "intent": "offer",
                            },
                        )
                    )
                    created_spot = True

            if created_spot:
                continue

            # --- (c) find_matchable_teach_spot — JOIN ------------------
            target = find_matchable_teach_spot(
                agent, open_teach_spots, tick, catalog=skills_catalog
            )
            joined = False
            if target is not None:
                if execute_join_spot(agent, target, tick):
                    after_join_spot(agent)
                    # Phase Peer-C: soft wallet deduction at JOIN time
                    # (plan §3-6 / §8-3). Partner spent_total +=
                    # fee_per_partner, wallet_monthly drops with 0 floor.
                    wallet_tracker.charge_partner_on_join(agent, target)
                    event_log.append(
                        make_event(
                            tick=tick,
                            event_type="JOIN_TEACH_SPOT",
                            agent=agent,
                            spot=target,
                            payload={
                                "skill": target.skill_topic,
                                "is_follower": False,
                                "fee_charged": target.fee_per_partner,
                                "wallet_after": agent.assets.wallet_monthly,
                                # FE handoff 2026-04-24:
                                # `spot.participant_joined` requires tick so
                                # the BE publisher can convert to ms.
                                "joined_at_tick": tick,
                                "persona_id": agent.agent_id,
                            },
                        )
                    )
                    if try_auto_match(target, phase=max(phase, 2)):
                        event_log.append(
                            make_event(
                                tick,
                                "SPOT_MATCHED",
                                agent=None,
                                spot=target,
                                payload={
                                    # FE handoff 2026-04-24: `spot.matched`
                                    # carries BE-adjudicated arrival state.
                                    # `arrived_count` == len(participants) at
                                    # match time; FE must not infer from
                                    # coordinate thresholds.
                                    "matched_at_tick": tick,
                                    "arrived_count": len(target.participants),
                                    "participants": [
                                        {"persona_id": pid}
                                        for pid in target.participants
                                    ],
                                },
                            )
                        )
                        if target in open_teach_spots:
                            open_teach_spots.remove(target)
                    joined = True

            if joined:
                continue

            if rng.random() < PEER_NO_ACTION_LOG_PROB:
                event_log.append(
                    make_event(tick, "NO_ACTION", agent=agent, spot=None)
                )

        # 6. counter-offer pass — iterate spots in insertion order.
        for spot in list(spots):
            if check_counter_offer_trigger(spot, tick):
                event_log.extend(send_counter_offer(spot, agents_by_id.get(spot.host_agent_id), tick))
            if spot.counter_offer_sent:
                event_log.extend(
                    finalize_counter_offer(
                        spot,
                        agents_by_id.get(spot.host_agent_id),
                        agents_by_id,
                        tick,
                        rng,
                        response_wait_ticks=response_wait,
                    )
                )

        # 7. (phase>=2) check-in + cancel passes — verbatim reuse of the
        # legacy Phase 2 logic so peer runs still produce CHECK_IN /
        # NO_SHOW / CANCEL_JOIN events comparable to Phase 2 baselines.
        if phase >= 2:
            for spot in spots:
                if spot.status != SpotStatus.IN_PROGRESS:
                    continue
                if spot.started_at_tick != tick:
                    continue
                roster: list[str] = []
                if spot.host_agent_id not in roster:
                    roster.append(spot.host_agent_id)
                for pid in spot.participants:
                    if pid not in roster:
                        roster.append(pid)
                for aid in roster:
                    agent = agents_by_id.get(aid)
                    if agent is None:
                        continue
                    if agent.agent_id in spot.checked_in:
                        continue
                    if agent.agent_id in spot.noshow:
                        continue
                    roll = rng.random()
                    if roll < _p_checkin(agent):
                        execute_check_in(agent, spot, tick)
                        event_log.append(
                            make_event(
                                tick,
                                "CHECK_IN",
                                agent=agent,
                                spot=spot,
                                # FE handoff 2026-04-24: CHECK_IN carries the
                                # arrival timestamp the BE publisher uses to
                                # populate `participants[].arrived_at_ms` on
                                # spot.matched events. Previously FE inferred
                                # arrival from a 55m coordinate threshold —
                                # now BE owns the judgement.
                                payload={
                                    "arrived_at_tick": tick,
                                    "persona_id": agent.agent_id,
                                },
                            )
                        )
                    else:
                        execute_no_show(agent, spot, tick)
                        event_log.append(
                            make_event(
                                tick,
                                "NO_SHOW",
                                agent=agent,
                                spot=spot,
                                payload={
                                    "persona_id": agent.agent_id,
                                    "reason": "no_show",
                                },
                            )
                        )

            for spot in spots:
                if spot.status not in (SpotStatus.OPEN, SpotStatus.MATCHED):
                    continue
                for pid in list(spot.participants):
                    if rng.random() >= P_CANCEL_JOIN:
                        continue
                    agent = agents_by_id.get(pid)
                    if agent is None:
                        continue
                    if execute_cancel_join(agent, spot, tick):
                        event_log.append(
                            make_event(
                                tick,
                                "CANCEL_JOIN",
                                agent=agent,
                                spot=spot,
                                payload={
                                    "persona_id": agent.agent_id,
                                    "left_at_tick": tick,
                                    "reason": "cancel_join",
                                },
                            )
                        )

    return event_log, spots


def _summary_counts(event_log: list[EventLog]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in event_log:
        counts[e.event_type] = counts.get(e.event_type, 0) + 1
    return counts


def run_phase(phase: int, config_path: Path) -> None:
    """CLI-facing entry: load config + data, run the simulation, persist log.

    Paths for persona templates / region features / affinity are resolved
    relative to the config file's parent so the default invocation from the
    project root (`python3 main.py --phase 1`) works without extra flags.
    """
    config_path = Path(config_path)
    sim_cfg = load_simulation_config(config_path)
    phase_key = f"phase_{phase}"
    if phase_key not in sim_cfg:
        raise KeyError(
            f"simulation_config at {config_path} has no '{phase_key}' block"
        )
    phase_cfg = sim_cfg[phase_key]

    project_root = config_path.parent.parent
    persona_templates_path = project_root / "config" / "persona_templates.yaml"
    region_features_path = project_root / "data" / "region_features.json"
    persona_affinity_path = project_root / "data" / "persona_region_affinity.json"
    skills_catalog_path = project_root / "config" / "skills_catalog.yaml"

    persona_templates = load_persona_templates(persona_templates_path)
    region_features = load_region_features(region_features_path)
    persona_affinity = load_persona_region_affinity(persona_affinity_path)

    simulation_mode = str(sim_cfg.get("simulation_mode", "legacy"))
    skills_catalog: dict = {}
    if simulation_mode == "peer":
        skills_catalog = load_skills_catalog(skills_catalog_path)

    seed = int(phase_cfg.get("seed", 42))
    rng = random.Random(seed)

    agents = build_agent_population(
        total=int(phase_cfg.get("agents", 50)),
        persona_templates=persona_templates,
        region_features=region_features,
        affinity=persona_affinity,
        rng=rng,
    )

    started = time.perf_counter()
    event_log, spots = run_simulation(
        agents,
        phase_cfg,
        region_features=region_features,
        persona_templates=persona_templates,
        persona_affinity=persona_affinity,
        seed=seed,
        phase=phase,
        simulation_mode=simulation_mode,
        skills_catalog=skills_catalog,
    )
    elapsed = time.perf_counter() - started

    output_dir = project_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "event_log.jsonl"
    with log_path.open("w", encoding="utf-8") as f:
        for e in event_log:
            f.write(serialize_event(e))
            f.write("\n")

    counts = _summary_counts(event_log)
    print(f"total events: {len(event_log)}")
    print(f"CREATE_SPOT:  {counts.get('CREATE_SPOT', 0)}")
    print(f"JOIN_SPOT:    {counts.get('JOIN_SPOT', 0)}")
    print(f"SPOT_MATCHED: {counts.get('SPOT_MATCHED', 0)}")
    if phase >= 2:
        print(f"SPOT_CONFIRMED: {counts.get('SPOT_CONFIRMED', 0)}")
        print(f"SPOT_STARTED:   {counts.get('SPOT_STARTED', 0)}")
        print(f"SPOT_COMPLETED: {counts.get('SPOT_COMPLETED', 0)}")
        print(f"SPOT_TIMEOUT:   {counts.get('SPOT_TIMEOUT', 0)}")
        print(f"SPOT_DISPUTED:  {counts.get('SPOT_DISPUTED', 0)}")
        print(f"CHECK_IN:     {counts.get('CHECK_IN', 0)}")
        print(f"NO_SHOW:      {counts.get('NO_SHOW', 0)}")
        print(f"CANCEL_JOIN:  {counts.get('CANCEL_JOIN', 0)}")
    if phase >= 3:
        print(f"WRITE_REVIEW:    {counts.get('WRITE_REVIEW', 0)}")
        print(f"SETTLE:          {counts.get('SETTLE', 0)}")
        print(f"SPOT_SETTLED:    {counts.get('SPOT_SETTLED', 0)}")
        print(f"FORCE_SETTLED:   {counts.get('FORCE_SETTLED', 0)}")
        print(f"DISPUTE_RESOLVED:{counts.get('DISPUTE_RESOLVED', 0)}")
        print(f"VIEW_FEED:       {counts.get('VIEW_FEED', 0)}")
        print(f"SAVE_SPOT:       {counts.get('SAVE_SPOT', 0)}")
        # Satisfaction distribution across SETTLED/FORCE_SETTLED spots.
        settled_sats = [
            s.avg_satisfaction
            for s in spots
            if s.avg_satisfaction is not None
        ]
        if settled_sats:
            mean_sat = sum(settled_sats) / len(settled_sats)
            lo = min(settled_sats)
            hi = max(settled_sats)
            print(
                f"avg_satisfaction: mean={mean_sat:.3f} "
                f"min={lo:.3f} max={hi:.3f} (n={len(settled_sats)})"
            )
        # Trust distribution across the agent population.
        trusts = [a.trust_score for a in agents]
        if trusts:
            mean_trust = sum(trusts) / len(trusts)
            t_lo = min(trusts)
            t_hi = max(trusts)
            below_03 = sum(1 for t in trusts if t < 0.3)
            above_07 = sum(1 for t in trusts if t > 0.7)
            print(
                f"trust_score:      mean={mean_trust:.3f} "
                f"min={t_lo:.3f} max={t_hi:.3f} "
                f"<0.3={below_03} >0.7={above_07}"
            )
    print(f"elapsed:      {elapsed:.3f}s (spots={len(spots)})")
