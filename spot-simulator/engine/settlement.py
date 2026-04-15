"""Phase 3 settlement, satisfaction, and dispute resolution — plan §4.3-§4.5.

This module is wired into the tick loop (`engine/runner.py`) only when
`phase >= 3`. Phase 1 and Phase 2 runs never import from here, so adding
it has zero effect on the byte-identical Phase 1 / Phase 2 logs.

All randomness flows through an injected `rng: random.Random` so a fixed
seed produces byte-identical Phase 3 event logs across runs.

Public functions:
    calculate_satisfaction(agent, spot, agents_by_id, *, rng) -> float
    process_settlement(spot, agents_by_id, tick, event_log, *, rng) -> SettlementResult | None
    resolve_disputes(spots, agents_by_id, tick, event_log, *, rng) -> None

Helpers:
    generate_review(agent, spot, satisfaction, tick) -> Review
    make_review_event(tick, agent, spot, review) -> EventLog

Constants are exposed at module level so sim-analyst-qa can retune from
the QA harness without touching the body of these functions.
"""

from __future__ import annotations

import random
from statistics import mean

from engine._math import clamp
from models import (
    AgentState,
    EventLog,
    Review,
    SettlementResult,
    Spot,
    SpotStatus,
    make_event,
)

# ---------------------------------------------------------------------------
# Tunable constants — plan §4.3 / §4.4 / §4.5
# ---------------------------------------------------------------------------

# Satisfaction (plan §4.4)
SATISFACTION_BASE: float = 0.5
CATEGORY_MATCH_BONUS: float = 0.15
FILL_SWEET_SPOT_LO: float = 0.6
FILL_SWEET_SPOT_HI: float = 0.9
FILL_SWEET_BONUS: float = 0.10
FILL_LOW_THRESHOLD: float = 0.4
FILL_LOW_PENALTY: float = 0.10
NOSHOW_DISSAT_COEFF: float = 0.15
TRUST_GAP_PENALTY: float = 0.10
SATISFACTION_NOISE_LO: float = -0.08
SATISFACTION_NOISE_HI: float = 0.08

# Trust updates (plan §4.3 step 3 + 4)
HOST_TRUST_UP: float = 0.05
HOST_TRUST_DOWN: float = 0.08
HIGH_SAT_THRESHOLD: float = 0.7
LOW_SAT_THRESHOLD: float = 0.3
NOSHOW_TRUST_PENALTY: float = 0.15

# Review generation (plan §4.3 step 2)
REVIEW_BASE_PROB: float = 0.35
REVIEW_INTENSITY_COEFF: float = 0.4

# Dispute resolution (plan §4.5)
DISPUTE_RESOLVE_TICKS: int = 3
DISPUTE_TIMEOUT_TICKS: int = 72
FORCE_SETTLE_TRUST_PENALTY: float = 0.12


# ---------------------------------------------------------------------------
# Satisfaction (plan §4.4)
# ---------------------------------------------------------------------------


def calculate_satisfaction(
    agent: AgentState,
    spot: Spot,
    agents_by_id: dict[str, AgentState],
    *,
    rng: random.Random,
) -> float:
    """Plan §4.4 satisfaction in [0, 1].

    Components, all additive on `SATISFACTION_BASE = 0.5`:
      * +CATEGORY_MATCH_BONUS if `spot.category` is in `agent.interest_categories`
      * +FILL_SWEET_BONUS if `len(checked_in)/capacity` is in [0.6, 0.9]
        else -FILL_LOW_PENALTY if it is below 0.4
      * -NOSHOW_DISSAT_COEFF * `noshow_count / max(1, len(participants))`
      * -TRUST_GAP_PENALTY * `|agent.trust_threshold - host.trust_score|`
      * Uniform noise in [SATISFACTION_NOISE_LO, SATISFACTION_NOISE_HI]
    """
    base = SATISFACTION_BASE

    if spot.category in agent.interest_categories:
        base += CATEGORY_MATCH_BONUS

    ideal_ratio = len(spot.checked_in) / max(1, spot.capacity)
    if FILL_SWEET_SPOT_LO <= ideal_ratio <= FILL_SWEET_SPOT_HI:
        base += FILL_SWEET_BONUS
    elif ideal_ratio < FILL_LOW_THRESHOLD:
        base -= FILL_LOW_PENALTY

    noshow_ratio = spot.noshow_count / max(1, len(spot.participants))
    base -= NOSHOW_DISSAT_COEFF * noshow_ratio

    host = agents_by_id.get(spot.host_agent_id)
    if host is not None:
        host_trust_gap = abs(agent.trust_threshold - host.trust_score)
        base -= TRUST_GAP_PENALTY * host_trust_gap

    noise = rng.uniform(SATISFACTION_NOISE_LO, SATISFACTION_NOISE_HI)
    return clamp(base + noise, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Review helpers (plan §4.3 step 2)
# ---------------------------------------------------------------------------


def generate_review(
    agent: AgentState, spot: Spot, satisfaction: float, tick: int
) -> Review:
    """Build a `Review` row for one checked-in agent."""
    return Review(
        reviewer_agent_id=agent.agent_id,
        spot_id=spot.spot_id,
        satisfaction=satisfaction,
        tick=tick,
    )


def make_review_event(
    tick: int, agent: AgentState, spot: Spot, review: Review
) -> EventLog:
    """Lift a `Review` into a `WRITE_REVIEW` EventLog row."""
    return make_event(
        tick,
        "WRITE_REVIEW",
        agent=agent,
        spot=spot,
        payload={"satisfaction": round(review.satisfaction, 3)},
    )


# ---------------------------------------------------------------------------
# process_settlement (plan §4.3)
# ---------------------------------------------------------------------------


def process_settlement(
    spot: Spot,
    agents_by_id: dict[str, AgentState],
    tick: int,
    event_log: list[EventLog],
    *,
    rng: random.Random,
) -> SettlementResult | None:
    """Plan §4.3 settlement processor.

    Runs on COMPLETED or DISPUTED spots and is idempotent: a spot whose
    `settled_at_tick` is already populated is skipped (returns `None`).
    On the COMPLETED branch the spot transitions to SETTLED and the
    function emits, in order:
      1. one `WRITE_REVIEW` per checked-in agent that rolls the review prob
      2. `SPOT_SETTLED` (status payload)
      3. `SETTLE` action event for the host

    On the DISPUTED branch the same review/satisfaction math runs but the
    status flip to SETTLED is left to `resolve_disputes` (it only happens
    when `avg_satisfaction >= 0.5`). `SPOT_SETTLED` is therefore NOT
    emitted on the DISPUTED branch — `resolve_disputes` emits
    `DISPUTE_RESOLVED` instead.

    Returns the populated `SettlementResult` (or `None` if the spot was
    already settled / not in a settlable state).
    """
    if spot.status not in (SpotStatus.COMPLETED, SpotStatus.DISPUTED):
        return None
    # Idempotency guard — a spot only gets processed once.
    if spot.settled_at_tick is not None:
        return None

    was_completed = spot.status == SpotStatus.COMPLETED

    participants = [
        agents_by_id[pid]
        for pid in spot.participants
        if pid in agents_by_id
    ]
    checked_in_agents = [
        a for a in participants if a.checked_in_for(spot.spot_id)
    ]

    # Mirror noshow snapshot before satisfaction math reads it.
    spot.noshow_count = len(spot.noshow)

    # 1. Satisfaction per checked-in agent.
    sats: list[float] = []
    for agent in checked_in_agents:
        sat = calculate_satisfaction(agent, spot, agents_by_id, rng=rng)
        sats.append(sat)
        agent.satisfaction_history.append(sat)

    avg_sat = mean(sats) if sats else 0.0
    spot.avg_satisfaction = avg_sat

    # 2. Review generation — review prob proportional to satisfaction
    #    intensity (|sat - 0.5|). Iteration order matches checked_in_agents
    #    so rng draw order is deterministic.
    for agent, sat in zip(checked_in_agents, sats):
        p_review = REVIEW_BASE_PROB + REVIEW_INTENSITY_COEFF * abs(sat - 0.5)
        if rng.random() < p_review:
            review = generate_review(agent, spot, sat, tick)
            event_log.append(make_review_event(tick, agent, spot, review))
            agent.review_spots.append(spot.spot_id)
            spot.review_count += 1

    # 3. Host trust update.
    host = agents_by_id.get(spot.host_agent_id)
    if host is not None:
        host.prev_trust = host.trust_score
        if avg_sat >= HIGH_SAT_THRESHOLD:
            host.trust_score = clamp(host.trust_score + HOST_TRUST_UP)
        elif avg_sat < LOW_SAT_THRESHOLD:
            host.trust_score = clamp(host.trust_score - HOST_TRUST_DOWN)
        # else: trust unchanged
        host_trust_delta = host.trust_score - host.prev_trust
    else:
        host_trust_delta = 0.0

    # 4. Participant no-show trust penalty.
    for agent in participants:
        if agent.agent_id in spot.noshow:
            agent.trust_score = clamp(agent.trust_score - NOSHOW_TRUST_PENALTY)

    # 5. Spot lifecycle.
    settled_at_tick = tick
    spot.settled_at_tick = settled_at_tick
    if was_completed:
        spot.status = SpotStatus.SETTLED
        status_str = SpotStatus.SETTLED.value
    else:
        # DISPUTED branch — leave status flip to `resolve_disputes`.
        # Whatever status string we report depends on whether the dispute
        # resolves; for now keep it as DISPUTED so SettlementResult is
        # accurate at this call site.
        status_str = SpotStatus.DISPUTED.value

    # 6. Emit SPOT_SETTLED only for the COMPLETED branch — DISPUTED branch
    # defers the SETTLED flip to resolve_disputes.
    if was_completed:
        event_log.append(
            make_event(
                tick,
                "SPOT_SETTLED",
                agent=None,
                spot=spot,
                payload={
                    "avg_sat": round(avg_sat, 3),
                    "host_trust_delta": round(host_trust_delta, 3),
                    "completed": len(checked_in_agents),
                    "noshow": spot.noshow_count,
                },
            )
        )
        # 7. Emit SETTLE action event for the host (one per spot).
        if host is not None:
            event_log.append(
                make_event(tick, "SETTLE", agent=host, spot=spot)
            )

    return SettlementResult(
        spot_id=spot.spot_id,
        completed_count=len(checked_in_agents),
        noshow_count=len(participants) - len(checked_in_agents),
        avg_satisfaction=avg_sat,
        host_trust_delta=host_trust_delta,
        status=status_str,
        settled_at_tick=settled_at_tick,
    )


# ---------------------------------------------------------------------------
# resolve_disputes (plan §4.5)
# ---------------------------------------------------------------------------


def resolve_disputes(
    spots: list[Spot],
    agents_by_id: dict[str, AgentState],
    tick: int,
    event_log: list[EventLog],
    *,
    rng: random.Random,
) -> None:
    """Plan §4.5 dispute resolver.

    Two windows:
      * `dispute_age > DISPUTE_TIMEOUT_TICKS` (24h): force-settle. Host
        trust penalized by `FORCE_SETTLE_TRUST_PENALTY`. Emits
        `FORCE_SETTLED` with `payload={"reason": "dispute_timeout"}`.
      * `dispute_age > DISPUTE_RESOLVE_TICKS` (6h): run `process_settlement`
        on the spot to populate `avg_satisfaction`. If the spot ends up
        with `avg_satisfaction >= 0.5`, flip status to SETTLED and emit
        `DISPUTE_RESOLVED` followed by `SPOT_SETTLED`.
    """
    for spot in spots:
        if spot.status != SpotStatus.DISPUTED:
            continue
        if spot.disputed_at_tick is None:
            continue

        dispute_age = tick - spot.disputed_at_tick

        # 24h timeout — force settle, no satisfaction calc.
        if dispute_age > DISPUTE_TIMEOUT_TICKS:
            host = agents_by_id.get(spot.host_agent_id)
            if host is not None:
                host.prev_trust = host.trust_score
                host.trust_score = clamp(
                    host.trust_score - FORCE_SETTLE_TRUST_PENALTY
                )
            spot.status = SpotStatus.FORCE_SETTLED
            spot.force_settled = True
            spot.settled_at_tick = tick
            event_log.append(
                make_event(
                    tick,
                    "FORCE_SETTLED",
                    agent=None,
                    spot=spot,
                    payload={"reason": "dispute_timeout"},
                )
            )
            continue

        # 6h rule — settle and emit DISPUTE_RESOLVED if avg_sat >= 0.5.
        if dispute_age > DISPUTE_RESOLVE_TICKS:
            result = process_settlement(
                spot, agents_by_id, tick, event_log, rng=rng
            )
            if result is None:
                continue
            if (
                spot.avg_satisfaction is not None
                and spot.avg_satisfaction >= 0.5
            ):
                spot.status = SpotStatus.SETTLED
                event_log.append(
                    make_event(
                        tick, "DISPUTE_RESOLVED", agent=None, spot=spot
                    )
                )
                event_log.append(
                    make_event(
                        tick,
                        "SPOT_SETTLED",
                        agent=None,
                        spot=spot,
                        payload={
                            "avg_sat": round(spot.avg_satisfaction, 3),
                            "completed": result.completed_count,
                            "noshow": spot.noshow_count,
                            "from_dispute": True,
                        },
                    )
                )
