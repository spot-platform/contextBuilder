from __future__ import annotations

from engine.hotspot_signal import build_map_anchor_payload
from models.agent import AgentState
from models.spot import Spot


def _agent() -> AgentState:
    return AgentState(
        agent_id="A_001",
        persona_type="social",
        home_region_id="emd_yeonmu",
        active_regions=["emd_yeonmu"],
        interest_categories=["teach"],
        host_score=0.8,
        join_score=0.6,
        fatigue=0.1,
        social_need=0.7,
        current_state="idle",
        schedule_weights={},
        budget_level=2,
    )


def _spot(spot_id: str = "S_001", venue_type: str = "cafe") -> Spot:
    return Spot(
        spot_id=spot_id,
        host_agent_id="A_001",
        region_id="emd_yeonmu",
        category="teach",
        capacity=4,
        min_participants=2,
        scheduled_tick=12,
        created_at_tick=0,
        skill_topic="일본어 회화",
        venue_type=venue_type,
    )


REGION_FEATURES = {
    "emd_yeonmu": {
        "region_name": "연무동",
        "center_lat": 37.2942,
        "center_lng": 127.0276,
    }
}


def test_map_anchor_prefers_lcb_poi_when_region_dump_matches() -> None:
    anchor = build_map_anchor_payload(
        spot=_spot(),
        host=_agent(),
        region_features=REGION_FEATURES,
    )

    assert anchor["type"] == "poi_public_anchor"
    assert anchor["match_reason"] == "lcb_poi_match"
    assert anchor["poi_id"]
    assert anchor["poi_name"]
    assert anchor["region_id"] == "emd_yeonmu"


def test_map_anchor_falls_back_to_region_jitter_without_region_name() -> None:
    anchor = build_map_anchor_payload(
        spot=_spot(),
        host=_agent(),
        region_features={"emd_yeonmu": {"center_lat": 37.2942, "center_lng": 127.0276}},
    )

    assert anchor["type"] == "region_public_jitter"
    assert anchor["match_reason"] == "region_center_public_jitter"
