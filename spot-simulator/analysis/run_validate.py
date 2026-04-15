"""CLI: run a Phase 1 simulation in-memory and validate against §2.8.

Why not reuse `engine.runner.run_phase`? `run_phase` writes the event log to
disk and discards the in-memory agent/spot objects, so we can't run the
post-hoc correlation checks (which need live AgentState host_score values).
Instead, this script reproduces `run_phase`'s data-loading prologue and
calls `run_simulation` directly.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

# Make `spot-simulator/` importable when invoked via `python -m analysis.run_validate`
# from the spot-simulator directory. This mirrors tests/conftest.py.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analysis.validate import (  # noqa: E402
    validate_phase1,
    validate_phase2,
    validate_phase3,
)
from analysis.visualize import (  # noqa: E402
    aggregated_metrics_report,
    print_phase1_report,
    print_phase2_report,
    print_phase3_report,
    sample_phase3_spot_timelines,
    sample_spot_timelines,
    satisfaction_histogram,
    trust_distribution,
)
from data.agent_factory import build_agent_population  # noqa: E402
from data.loader import (  # noqa: E402
    load_persona_region_affinity,
    load_persona_templates,
    load_region_features,
    load_simulation_config,
)
from engine.runner import run_simulation  # noqa: E402


def _resolve_config_path(arg: str | None) -> Path:
    if arg is not None:
        return Path(arg).resolve()
    return (_ROOT / "config" / "simulation_config.yaml").resolve()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run spot-simulator Phase N and validate in-memory."
    )
    parser.add_argument(
        "--phase",
        type=int,
        default=1,
        help="Phase to validate (1, 2, or 3).",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to simulation_config.yaml (default: spot-simulator/config/...)",
    )
    args = parser.parse_args()

    if args.phase not in (1, 2, 3):
        print(
            f"phase {args.phase} validation is not implemented yet",
            file=sys.stderr,
        )
        return 2

    config_path = _resolve_config_path(args.config)
    sim_cfg = load_simulation_config(config_path)
    phase_key = f"phase_{args.phase}"
    phase_cfg = sim_cfg[phase_key]

    project_root = config_path.parent.parent
    persona_templates = load_persona_templates(
        project_root / "config" / "persona_templates.yaml"
    )
    region_features = load_region_features(
        project_root / "data" / "region_features.json"
    )
    persona_affinity = load_persona_region_affinity(
        project_root / "data" / "persona_region_affinity.json"
    )

    seed = int(phase_cfg.get("seed", 42))
    rng = random.Random(seed)
    agents = build_agent_population(
        total=int(phase_cfg.get("agents", 50)),
        persona_templates=persona_templates,
        region_features=region_features,
        affinity=persona_affinity,
        rng=rng,
    )

    event_log, spots = run_simulation(
        agents,
        phase_cfg,
        region_features=region_features,
        persona_templates=persona_templates,
        persona_affinity=persona_affinity,
        seed=seed,
        phase=args.phase,
    )

    if args.phase == 1:
        report = validate_phase1(event_log, agents, spots)
        print_phase1_report(report)
    elif args.phase == 2:
        report = validate_phase2(event_log, agents, spots)
        print_phase2_report(report)
        print()
        print("Sample spot timelines (plan §6.2):")
        print("-" * 78)
        print(sample_spot_timelines(spots, event_log, n=3))
    else:
        report = validate_phase3(event_log, agents, spots)
        print_phase3_report(report)
        print()
        print("Aggregated metrics (plan §6.3):")
        print("-" * 78)
        print(aggregated_metrics_report(event_log, agents, spots))
        print()
        print("Trust distribution:")
        print("-" * 78)
        print(trust_distribution(agents))
        print()
        print("Satisfaction distribution:")
        print("-" * 78)
        print(satisfaction_histogram(spots))
        print()
        print("Sample spot timelines (plan §6.2):")
        print("-" * 78)
        print(sample_phase3_spot_timelines(spots, event_log))

    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
