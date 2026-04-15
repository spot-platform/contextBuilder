"""One-shot verification harness for the Phase Peer-A drift sentinels.

Mirrors the sets in `tests/test_models.py` so we can exercise the same
contract without invoking `pytest` (the sandbox restricts `pytest` calls).
"""

from __future__ import annotations

import models


def main() -> None:
    a = set(models.AgentState.__annotations__.keys())
    s = set(models.Spot.__annotations__.keys())

    P1 = {
        "agent_id",
        "persona_type",
        "home_region_id",
        "active_regions",
        "interest_categories",
        "host_score",
        "join_score",
        "fatigue",
        "social_need",
        "current_state",
        "schedule_weights",
        "budget_level",
        "last_action_tick",
        "hosted_spots",
        "joined_spots",
    }
    P2 = {
        "trust_score",
        "prev_trust",
        "confirmed_spots",
        "checked_in_spots",
        "noshow_spots",
    }
    P3 = {
        "trust_threshold",
        "review_spots",
        "saved_spots",
        "satisfaction_history",
    }
    PP = {"skills", "assets", "relationships", "role_preference"}
    known = P1 | P2 | P3 | PP

    print("AgentState total fields :", len(a))
    print("  phase1 missing         :", sorted(P1 - a))
    print("  phase2 missing         :", sorted(P2 - a))
    print("  phase3 missing         :", sorted(P3 - a))
    print("  peer   missing         :", sorted(PP - a))
    print("  unexpected drift       :", sorted(a - known))

    peer_spot = {
        "skill_topic",
        "host_skill_level",
        "fee_breakdown",
        "required_equipment",
        "venue_type",
        "is_followup_session",
        "bonded_partner_ids",
        "teach_mode",
    }
    print("Spot total fields       :", len(s))
    print("  peer spot subset ok    :", peer_spot.issubset(s))
    print("  peer spot missing      :", sorted(peer_spot - s))

    # Default instantiation for legacy constructors
    legacy_agent = models.AgentState(
        agent_id="A_test",
        persona_type="night_social",
        home_region_id="emd_yeonmu",
        active_regions=["emd_yeonmu"],
        interest_categories=["food"],
        host_score=0.5,
        join_score=0.5,
        fatigue=0.2,
        social_need=0.5,
        current_state="idle",
        schedule_weights={"weekday_evening": 1.0},
        budget_level=2,
    )
    print("legacy agent wallet     :", legacy_agent.assets.wallet_monthly)
    print("legacy agent skills     :", len(legacy_agent.skills))
    print("legacy agent rel count  :", len(legacy_agent.relationships))
    print("legacy agent role       :", legacy_agent.role_preference)

    legacy_spot = models.Spot(
        spot_id="S_0001",
        host_agent_id="A_host",
        region_id="emd_yeonmu",
        category="food",
        capacity=4,
        min_participants=2,
        scheduled_tick=20,
        created_at_tick=10,
    )
    print("legacy spot skill_topic :", repr(legacy_spot.skill_topic))
    print("legacy spot teach_mode  :", legacy_spot.teach_mode)
    print("legacy spot venue_type  :", legacy_spot.venue_type)
    print("legacy spot fee_per     :", legacy_spot.fee_per_partner)
    print("legacy spot fee_total   :", legacy_spot.fee_breakdown.total)
    print("legacy spot status      :", legacy_spot.status)


if __name__ == "__main__":
    main()
