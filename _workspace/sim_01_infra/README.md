# sim_01_infra_complete — scaffolding report

Task: `sim_01_infra_complete`
Agent: `sim-infra-architect`
Date: 2026-04-14

## Files created

```
spot-simulator/
├── pyproject.toml
├── main.py
├── README.md
├── config/
│   ├── simulation_config.yaml
│   └── persona_templates.yaml
├── models/
│   └── __init__.py
├── engine/
│   └── __init__.py
├── analysis/
│   └── __init__.py
├── data/
│   └── .gitkeep
├── output/
│   └── .gitkeep
└── tests/
    ├── __init__.py
    └── conftest.py
```

Absolute paths:

- `/home/seojingyu/project/spotContextBuilder/spot-simulator/pyproject.toml`
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/main.py`
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/README.md`
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/config/simulation_config.yaml`
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/config/persona_templates.yaml`
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/models/__init__.py`
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/engine/__init__.py`
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/analysis/__init__.py`
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/data/.gitkeep`
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/output/.gitkeep`
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/tests/__init__.py`
- `/home/seojingyu/project/spotContextBuilder/spot-simulator/tests/conftest.py`

## pyproject dependency versions

- Build backend: `hatchling` (chosen for a minimal, modern PEP 517 setup; no `src/` layout required, packages are declared explicitly under `[tool.hatch.build.targets.wheel]`).
- Python: `>=3.12`
- Runtime dependencies:
  - `pyyaml>=6.0`
  - `pydantic>=2.5`
  - `numpy>=1.26`
- Dev dependencies:
  - `pytest>=8.0`
- Package name: `spot-simulator`, version `0.1.0`.
- `[tool.pytest.ini_options].testpaths = ["tests"]` so `pytest` run from the project root picks up `tests/` automatically; `tests/conftest.py` also injects the spot-simulator root onto `sys.path` so `from models.agent import ...` works before the package is installed.

## Logic-free confirmation

No `.py` file contains any simulation logic:

- `models/__init__.py`, `engine/__init__.py`, `analysis/__init__.py`, `tests/__init__.py` — all empty (0 bytes).
- `tests/conftest.py` — only pytest/sys.path bootstrap; no fixtures, no logic.
- `main.py` — argparse plumbing and a `run_phase(...)` placeholder that immediately raises `NotImplementedError("wired by sim-engine-engineer")`. No decision functions, no state, no dataclasses.

Smoke check:

```
$ python3 main.py --phase 1
Traceback (most recent call last):
  ...
  File ".../main.py", line 11, in run_phase
    raise NotImplementedError("wired by sim-engine-engineer")
NotImplementedError: wired by sim-engine-engineer
```

CLI is runnable, argparse accepts `--phase {1,2,3}` and `--config PATH`, and the placeholder fires cleanly. The host only has `python3` (no `python` shim), but the entrypoint has no interpreter-path hard-coding, so `python3 main.py --phase N` and `python main.py --phase N` (where available) both work.

## Config schemas delivered

- `config/simulation_config.yaml` — three top-level keys `phase_1`, `phase_2`, `phase_3`, each with the plan §1 row (`agents`, `total_ticks`, `seed`, `time_resolution_hours`, `action_count`, `target_runtime_seconds`). Values match the plan table exactly.
- `config/persona_templates.yaml` — five persona entries (`night_social`, `weekend_explorer`, `planner`, `spontaneous`, `homebody`). Each has empty-but-typed fields: `host_score`, `join_score`, `home_region`, `preferred_categories`, `time_preferences`, `budget_level`. Top-of-file comment: `# Schema only. Values filled by sim-data-integrator.`

## TODOs for downstream agents

- **sim-model-designer**
  - Add `models/agent.py` (`AgentState`), `models/spot.py` (`Spot`, `SpotStatus`), `models/event.py` (`EventLog`) per plan §2.3.
  - Decide the public surface of `models/__init__.py` — the current file is intentionally empty and awaits exports such as `from models.agent import AgentState`.
- **sim-engine-engineer**
  - Implement `engine/runner.py` (tick loop), `engine/decision.py`, `engine/lifecycle.py` (Phase 2), `engine/settlement.py` (Phase 3).
  - Replace the `run_phase(phase, config_path)` placeholder in `main.py` with the actual dispatch into `engine.runner`. The signature `run_phase(phase: int, config_path: Path) -> None` is the contract; if you need a richer return type, update `main.py` accordingly.
- **sim-data-integrator**
  - Fill `config/persona_templates.yaml` with real `host_score` / `join_score` / `time_preferences` / `preferred_categories` / `home_region` / `budget_level` per persona.
  - Populate `data/` with `region_features.json` and `persona_region_affinity.json` (plan §7). Directory currently holds only `.gitkeep`.
- **sim-analyst-qa**
  - Add `analysis/validate.py` and `analysis/visualize.py` (plan §7) plus Phase-1/2/3 acceptance checks (plan §2.8, §3.7, §4.6).
  - Add `tests/test_decision.py`, `tests/test_lifecycle.py`, `tests/test_settlement.py`. `tests/conftest.py` already puts the project root on `sys.path`, so test imports like `from models.agent import AgentState` will work once the model files land.

## Notes / decisions

- Chose **hatchling** over setuptools: zero boilerplate, explicit package list, matches the Python 3.12 era. If the downstream team prefers setuptools, the switch is a 5-line change in `pyproject.toml`.
- `data/` and `output/` intentionally contain only `.gitkeep`; sim-data-integrator and the engine's log writer fill them.
- Nothing touched outside `spot-simulator/` and `_workspace/sim_01_infra/`. `local-context-builder/` and `.claude/` are unmodified.
