"""Phase 3 settlement and review dataclasses — plan §4.3.

Pure data containers. No logic lives here: `process_settlement`,
`calculate_satisfaction`, `resolve_disputes`, `generate_review`, and
`make_review_event` are sim-engine-engineer's territory (engine/settlement.py).
This module only declares the shapes those functions return / construct so
sim-engine-engineer and sim-analyst-qa share a single import point.

Both dataclasses are immutable-friendly (no mutation methods) and contain
only primitive fields — they serialize cleanly via `dataclasses.asdict` if
ever needed for analysis sidecar files.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SettlementResult:
    """Return value of `engine.settlement.process_settlement` (plan §4.3).

    Fields:
      - spot_id          : the settled spot's id
      - completed_count  : number of CHECKED_IN participants
      - noshow_count     : `len(participants) - completed_count`. Mirrored
                           into `Spot.noshow_count` at settlement time.
      - avg_satisfaction : mean of `calculate_satisfaction` over CHECKED_IN
                           participants. `0.0` when there are no
                           checked_in agents (plan §4.3 step 1).
      - host_trust_delta : `host.trust_score - host.prev_trust` AFTER the
                           Phase 3 trust update step. Positive when host's
                           trust grew; negative on noshow/penalty.
      - status           : "SETTLED" or "FORCE_SETTLED". A string (not the
                           SpotStatus enum) so `dataclasses.asdict` produces
                           a JSON-safe payload without registering a custom
                           encoder. Mirrors `Spot.status.value`.
      - settled_at_tick  : tick at which settlement ran. Mirrors
                           `Spot.settled_at_tick`.
    """

    spot_id: str
    completed_count: int
    noshow_count: int
    avg_satisfaction: float
    host_trust_delta: float
    status: str  # "SETTLED" | "FORCE_SETTLED"
    settled_at_tick: int


@dataclass
class Review:
    """Output of `engine.settlement.generate_review` (plan §4.3 step 2).

    A single review row, written by a checked-in participant about a
    settled spot. `make_review_event(tick, agent, spot, review)` lifts this
    into a `WRITE_REVIEW` EventLog row in `engine/settlement.py`.

    Fields:
      - reviewer_agent_id : agent_id of the writer
      - spot_id           : the spot being reviewed
      - satisfaction      : 0..1, the value emitted by
                            `calculate_satisfaction(agent, spot)` (plan §4.4)
      - tick              : tick at which the review was written
                            (== the settlement tick in plan §4.3)
    """

    reviewer_agent_id: str
    spot_id: str
    satisfaction: float
    tick: int
