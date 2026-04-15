"""Public surface of the `engine` package (Phase 1 + Phase 2 utilities).

Tick-loop glue (`runner.py`, `decision.py`, `lifecycle.py`, `settlement.py`)
is owned by sim-engine-engineer. Phase 2 adds `lifecycle.py` and several
helpers in `decision.py` / `executors.py`; Phase 3 will add `settlement.py`.
"""

from engine._math import clamp
from engine.decay import (
    COMPLETE_FATIGUE_DELTA,
    COMPLETE_SOCIAL_DELTA,
    CREATE_FATIGUE_DELTA,
    CREATE_SOCIAL_DELTA,
    FATIGUE_DECAY_MULT,
    FATIGUE_DECAY_SUB,
    JOIN_FATIGUE_DELTA,
    JOIN_SOCIAL_DELTA,
    SOCIAL_NEED_GROW,
    after_complete_spot,
    after_create_spot,
    after_join_spot,
    decay_fatigue,
    grow_social_need,
)
from engine.decision import (
    avg_interest_overlap,
    calc_social_join_modifier,
    decide_action,
    find_matchable_spots,
    pick_scheduled_tick,
)
from engine.executors import (
    execute_cancel_join,
    execute_check_in,
    execute_create_spot,
    execute_join_spot,
    execute_no_show,
    execute_save_spot,
    execute_view_feed,
    execute_write_review,
    try_auto_match,
)
from engine.lifecycle import (
    CONFIRM_LEAD_TICKS,
    NOSHOW_DISPUTE_THRESHOLD,
    OPEN_TIMEOUT_TICKS,
    process_lifecycle,
)
from engine.runner import P_CANCEL_JOIN, run_phase, run_simulation
from engine.settlement import (
    DISPUTE_RESOLVE_TICKS,
    DISPUTE_TIMEOUT_TICKS,
    FORCE_SETTLE_TRUST_PENALTY,
    HOST_TRUST_DOWN,
    HOST_TRUST_UP,
    NOSHOW_TRUST_PENALTY,
    REVIEW_BASE_PROB,
    REVIEW_INTENSITY_COEFF,
    calculate_satisfaction,
    generate_review,
    make_review_event,
    process_settlement,
    resolve_disputes,
)
from engine.time_utils import (
    TIME_SLOTS,
    get_day_type,
    get_time_slot,
    schedule_key,
)

__all__ = [
    # time_utils
    "TIME_SLOTS",
    "get_time_slot",
    "get_day_type",
    "schedule_key",
    # decay — functions
    "decay_fatigue",
    "grow_social_need",
    "after_create_spot",
    "after_join_spot",
    "after_complete_spot",
    # decay — tunable constants
    "FATIGUE_DECAY_MULT",
    "FATIGUE_DECAY_SUB",
    "SOCIAL_NEED_GROW",
    "CREATE_FATIGUE_DELTA",
    "CREATE_SOCIAL_DELTA",
    "JOIN_FATIGUE_DELTA",
    "JOIN_SOCIAL_DELTA",
    "COMPLETE_FATIGUE_DELTA",
    "COMPLETE_SOCIAL_DELTA",
    # decision / executors / runner
    "clamp",
    "decide_action",
    "find_matchable_spots",
    "execute_create_spot",
    "execute_join_spot",
    "try_auto_match",
    "run_simulation",
    "run_phase",
    # Phase 2 additions
    "process_lifecycle",
    "OPEN_TIMEOUT_TICKS",
    "CONFIRM_LEAD_TICKS",
    "NOSHOW_DISPUTE_THRESHOLD",
    "calc_social_join_modifier",
    "avg_interest_overlap",
    "pick_scheduled_tick",
    "execute_cancel_join",
    "execute_check_in",
    "execute_no_show",
    "P_CANCEL_JOIN",
    # Phase 3 additions
    "process_settlement",
    "calculate_satisfaction",
    "resolve_disputes",
    "generate_review",
    "make_review_event",
    "execute_write_review",
    "execute_save_spot",
    "execute_view_feed",
    "DISPUTE_RESOLVE_TICKS",
    "DISPUTE_TIMEOUT_TICKS",
    "FORCE_SETTLE_TRUST_PENALTY",
    "HOST_TRUST_UP",
    "HOST_TRUST_DOWN",
    "NOSHOW_TRUST_PENALTY",
    "REVIEW_BASE_PROB",
    "REVIEW_INTENSITY_COEFF",
]
