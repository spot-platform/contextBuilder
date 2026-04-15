"""Agent factory — turns persona templates + reference data into AgentState.

Implements plan §5 "기존 모델 연결 지점". All randomness is injected via
``rng: random.Random`` so runs are reproducible for a given ``seed``.
"""

from __future__ import annotations

import random
from typing import Any

from models.agent import AgentState  # sim-model-designer supplies this


def _top_active_regions(
    persona_type: str,
    home_region_id: str,
    affinity: dict[str, dict[str, dict[str, float]]],
    region_features: dict[str, dict[str, Any]],
    rng: random.Random,
    k: int = 3,
) -> list[str]:
    """Pick the top-k active regions for a persona by create_mult.

    Falls back to ``[home_region_id]`` + (k-1) random regions when the
    persona or home region is missing from the affinity table.
    """
    persona_affinity = affinity.get(persona_type)
    if not persona_affinity:
        pool = [r for r in region_features.keys() if r != home_region_id]
        rng.shuffle(pool)
        fallback = [home_region_id] + pool[: max(0, k - 1)]
        return fallback[:k]

    ranked = sorted(
        persona_affinity.items(),
        key=lambda item: float(item[1].get("create_mult", 0.0)),
        reverse=True,
    )
    picked: list[str] = []
    # Ensure home_region comes first if it exists in the affinity table.
    if home_region_id in persona_affinity:
        picked.append(home_region_id)
    for region_id, _ in ranked:
        if region_id in picked:
            continue
        picked.append(region_id)
        if len(picked) >= k:
            break
    if not picked:
        picked = [home_region_id]
    return picked[:k]


def init_agent_from_persona(
    persona_type: str,
    persona_data: dict[str, Any],
    region_features: dict[str, dict[str, Any]],
    affinity: dict[str, dict[str, dict[str, float]]],
    rng: random.Random,
) -> AgentState:
    """Create a single AgentState from a persona template.

    Mirrors plan §5 exactly. Random draws use the injected ``rng`` so seeds
    produce deterministic populations.
    """
    home_region_id = persona_data["home_region"]
    active_regions = _top_active_regions(
        persona_type=persona_type,
        home_region_id=home_region_id,
        affinity=affinity,
        region_features=region_features,
        rng=rng,
        k=3,
    )

    agent_num = rng.randint(10000, 99999)
    agent_id = f"A_{agent_num}"

    return AgentState(
        agent_id=agent_id,
        persona_type=persona_type,
        home_region_id=home_region_id,
        active_regions=active_regions,
        interest_categories=list(persona_data["preferred_categories"]),
        host_score=float(persona_data["host_score"]),
        join_score=float(persona_data["join_score"]),
        fatigue=rng.uniform(0.05, 0.25),
        social_need=rng.uniform(0.3, 0.7),
        current_state="idle",
        schedule_weights=dict(persona_data["time_preferences"]),
        last_action_tick=-1,
        hosted_spots=[],
        joined_spots=[],
        budget_level=int(persona_data["budget_level"]),
    )


def build_agent_population(
    total: int,
    persona_templates: dict[str, dict[str, Any]],
    region_features: dict[str, dict[str, Any]],
    affinity: dict[str, dict[str, dict[str, float]]],
    rng: random.Random,
) -> list[AgentState]:
    """Build ``total`` agents, evenly distributed across persona types.

    For 50 agents × 5 personas this yields 10 of each. Remainder agents are
    distributed round-robin across persona types in declaration order so the
    distribution is deterministic for a given seed.
    """
    if total <= 0:
        return []
    persona_types = list(persona_templates.keys())
    if not persona_types:
        raise ValueError("persona_templates is empty — cannot build population")

    base = total // len(persona_types)
    remainder = total % len(persona_types)
    counts = {pt: base for pt in persona_types}
    for i in range(remainder):
        counts[persona_types[i]] += 1

    population: list[AgentState] = []
    for pt in persona_types:
        tpl = persona_templates[pt]
        for _ in range(counts[pt]):
            population.append(
                init_agent_from_persona(
                    persona_type=pt,
                    persona_data=tpl,
                    region_features=region_features,
                    affinity=affinity,
                    rng=rng,
                )
            )
    return population
