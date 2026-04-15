"""Public surface of the `models` package.

Phase 1 + Phase 2 + Phase 3 + Phase Peer-A (peer-pivot §2) append-only.

Re-exports the dataclasses and helpers sim-engine-engineer / sim-analyst-qa
consume. Keeping this list tight prevents accidental coupling to internals.
"""

from models.agent import AgentState
from models.event import (
    PHASE2_EVENT_TYPES,
    PHASE3_EVENT_TYPES,
    PHASE_PEER_EVENT_TYPES,
    EventLog,
    make_event,
    reset_event_counter,
    serialize_event,
)
from models.settlement import Review, SettlementResult
from models.skills import (
    HARD_CAP_PER_PARTNER,
    LABOR_CAP_PER_PARTNER,
    SOFT_CAP_PER_PARTNER,
    Assets,
    FeeBreakdown,
    Relationship,
    SkillProfile,
    SkillRequest,
    SkillTopic,
)
from models.spot import Spot, SpotStatus

__all__ = [
    "AgentState",
    "Spot",
    "SpotStatus",
    "EventLog",
    "make_event",
    "serialize_event",
    "reset_event_counter",
    "PHASE2_EVENT_TYPES",
    # Phase 3 (plan §4) additions:
    "PHASE3_EVENT_TYPES",
    "SettlementResult",
    "Review",
    # Phase Peer-A (peer-pivot §2) additions:
    "PHASE_PEER_EVENT_TYPES",
    "SkillTopic",
    "SkillProfile",
    "Assets",
    "Relationship",
    "FeeBreakdown",
    "LABOR_CAP_PER_PARTNER",
    "SOFT_CAP_PER_PARTNER",
    "HARD_CAP_PER_PARTNER",
    # Phase Peer-A+ (peer-pivot §3-counter / §3-request) additions:
    "SkillRequest",
]
