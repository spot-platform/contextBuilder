"""spot-simulator CLI entrypoint (skeleton).

Real phase execution is wired by sim-engine-engineer.
"""

import argparse
from pathlib import Path


def run_phase(phase: int, config_path: Path) -> None:
    # Lazy import so `python main.py --help` still works even if engine
    # modules fail to import (sim-analyst-qa can print the parser without
    # dragging in the whole tick loop).
    from engine.runner import run_phase as _run_phase

    _run_phase(phase, config_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spot-simulator",
        description="Run the Spot agent-based simulation for a given phase.",
    )
    parser.add_argument(
        "--phase",
        type=int,
        choices=[1, 2, 3],
        default=1,
        help="Simulation phase to run (1=MVP loop, 2=lifecycle, 3=settlement).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/simulation_config.yaml"),
        help="Path to simulation_config.yaml.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    run_phase(args.phase, args.config)


if __name__ == "__main__":
    main()
