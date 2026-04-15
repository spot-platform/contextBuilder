"""Pure loading functions for spot-simulator config and reference data.

All loaders accept ``str`` or ``pathlib.Path``. No caching, no module-level
state — callers hold the loaded dicts and pass them into adapters / the agent
factory explicitly so tests can swap fixtures easily.

External data mapping (local-context-builder -> simulator JSON keys)
--------------------------------------------------------------------

region_features table (local-context-builder) -> data/region_features.json:

    emd_cd                -> region_id
    emd_nm                -> region_name
    target_city           -> target_city
    density_food_norm     -> density_food
    density_cafe_norm     -> density_cafe
    density_bar_norm      -> density_bar
    density_exercise_norm -> density_exercise
    density_nature_norm   -> density_nature
    night_friendliness    -> night_friendliness
    group_friendliness    -> group_friendliness
    spot_create_affinity  -> spot_create_affinity   (key driver of CREATE_SPOT)
    budget_avg_level      -> budget_avg_level       (used by budget_penalty())

persona_region_weights table -> data/persona_region_affinity.json:

    persona_type          -> top-level key
    emd_cd                -> second-level key (region_id)
    create_score_weight   -> create_mult
    join_score_weight     -> join_mult

persona_templates (curated, not from DB) -> config/persona_templates.yaml:

    persona_type          -> top-level key
    host_tendency         -> host_score
    join_tendency         -> join_score
    home_emd_cd           -> home_region
    preferred_categories  -> preferred_categories
    time_pref_matrix      -> time_preferences (flattened into "{day}_{slot}")
    budget_level          -> budget_level

Phase Peer-A additions
----------------------

* ``load_skills_catalog(path)`` — loads ``config/skills_catalog.yaml`` and
  validates that every ``teach_mode_distribution`` sums to 1.0 (plan §3-4).
* ``load_personas(...)`` — Phase Peer-A multi-file persona loader. Reads
  ``config/persona_templates.yaml`` first (YAML anchor / merge-key aware,
  strips the ``_base_persona`` anchor entry), then glob-scans
  ``config/personas/*.yaml`` as drop-in overrides (plan §4-2 방법 B).
  Each loaded persona is run through ``_validate_persona`` which enforces
  the 8 plan-§4-3 invariants; violators are skipped with a warning so the
  engine can keep running on the remaining personas.
* ``load_persona_templates(path)`` — legacy loader kept unchanged for Phase
  1~3 call sites (``engine/runner.py``, ``analysis/run_validate.py``, tests).
  It still fails fast on missing legacy keys.

The full mapping table lives at
``_workspace/sim_04_data/external_data_mapping.md``.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import yaml

PathLike = str | Path

# Project root is two levels above this file (spot-simulator/data/loader.py
# -> spot-simulator/). Everything under config/ is resolved relative to it
# so drop-in glob works regardless of the caller's cwd.
_THIS_FILE = Path(__file__).resolve()
_PROJECT_ROOT = _THIS_FILE.parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "config"
_DEFAULT_PERSONA_TEMPLATES = _CONFIG_DIR / "persona_templates.yaml"
_DEFAULT_PERSONAS_DIR = _CONFIG_DIR / "personas"
_DEFAULT_SKILLS_CATALOG = _CONFIG_DIR / "skills_catalog.yaml"
_DEFAULT_REGION_FEATURES = _PROJECT_ROOT / "data" / "region_features.json"


def _as_path(path: PathLike) -> Path:
    return path if isinstance(path, Path) else Path(path)


def load_simulation_config(path: PathLike) -> dict[str, Any]:
    """Load ``simulation_config.yaml`` and return the parsed dict.

    The file contains phase_1 / phase_2 / phase_3 blocks with scale
    parameters (agents, total_ticks, seed, ...).
    """
    p = _as_path(path)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"simulation_config at {p} must be a mapping, got {type(data)}")
    return data


def load_persona_templates(path: PathLike) -> dict[str, dict[str, Any]]:
    """Load persona templates yaml and return ``{persona_type: template_dict}``.

    Fails fast if a required key is missing for any persona so the startup
    crashes loudly rather than producing silently broken agents.

    Phase Peer-A note: top-level keys starting with ``_`` (e.g.
    ``_base_persona`` YAML anchor) are skipped — they are template scaffolding,
    not real personas. This keeps the legacy Phase 1~3 callers
    (``engine/runner.py``, ``analysis/run_validate.py``) working on the new
    anchor-based yaml without any change.
    """
    required_keys = {
        "host_score",
        "join_score",
        "home_region",
        "preferred_categories",
        "time_preferences",
        "budget_level",
    }
    p = _as_path(path)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"persona_templates at {p} must be a mapping, got {type(data)}")

    real_personas: dict[str, dict[str, Any]] = {}
    for persona_type, tpl in data.items():
        if persona_type.startswith("_"):
            # YAML anchor scaffolding (e.g. _base_persona) — not a persona.
            continue
        if not isinstance(tpl, dict):
            raise ValueError(f"persona '{persona_type}' must map to a dict, got {type(tpl)}")
        missing = required_keys - tpl.keys()
        if missing:
            raise KeyError(
                f"persona '{persona_type}' missing required keys: {sorted(missing)}"
            )
        real_personas[persona_type] = tpl
    return real_personas


def load_region_features(path: PathLike) -> dict[str, dict[str, Any]]:
    """Load region features JSON. Returns dict keyed by ``region_id``.

    The file itself is already keyed by region_id; this function just
    validates the structure and guarantees the key invariant.
    """
    p = _as_path(path)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"region_features at {p} must be a mapping, got {type(data)}")

    normalized: dict[str, dict[str, Any]] = {}
    for key, entry in data.items():
        if not isinstance(entry, dict):
            raise ValueError(f"region_features['{key}'] must be a dict, got {type(entry)}")
        region_id = entry.get("region_id", key)
        normalized[region_id] = entry
    return normalized


def load_persona_region_affinity(
    path: PathLike,
) -> dict[str, dict[str, dict[str, float]]]:
    """Load persona-region affinity JSON.

    Shape: ``{persona_type: {region_id: {"create_mult": f, "join_mult": f}}}``.
    """
    p = _as_path(path)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"persona_region_affinity at {p} must be a mapping, got {type(data)}")
    for persona_type, region_map in data.items():
        if not isinstance(region_map, dict):
            raise ValueError(
                f"persona_region_affinity['{persona_type}'] must be a dict, got {type(region_map)}"
            )
    return data


# ---------------------------------------------------------------------------
# Phase Peer-A — skills catalog + multi-file persona loader (plan §3-4 / §4)
# ---------------------------------------------------------------------------


#: Required key per skill entry in ``skills_catalog.yaml``.
_SKILLS_CATALOG_REQUIRED = {"material_cost_per_partner", "teach_mode_distribution"}


def load_skills_catalog(path: PathLike | None = None) -> dict[str, dict[str, Any]]:
    """Load ``config/skills_catalog.yaml`` and validate mode-dist sums.

    Returns ``{skill_name: spec_dict}``. Raises ``ValueError`` if any
    ``teach_mode_distribution`` sums to anything other than 1.0 (±0.01) —
    this is a data-integrity check, not a runtime warning, because every
    fee suggester downstream assumes the distribution is a proper PMF.
    """

    p = _as_path(path) if path is not None else _DEFAULT_SKILLS_CATALOG
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"skills_catalog at {p} must be a mapping, got {type(data)}")

    for skill, spec in data.items():
        if not isinstance(spec, dict):
            raise ValueError(
                f"skills_catalog['{skill}'] must be a dict, got {type(spec)}"
            )
        missing = _SKILLS_CATALOG_REQUIRED - spec.keys()
        if missing:
            raise KeyError(
                f"skills_catalog['{skill}'] missing required keys: {sorted(missing)}"
            )
        dist = spec.get("teach_mode_distribution") or {}
        if not isinstance(dist, dict) or not dist:
            raise ValueError(
                f"skills_catalog['{skill}'].teach_mode_distribution must be a non-empty dict"
            )
        total = sum(float(v) for v in dist.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"skills_catalog['{skill}'].teach_mode_distribution sum = {total:.4f}"
                " (expected 1.0 ±0.01)"
            )
    return data


# Legacy keys expected by every persona that will be consumed by the Phase
# 1~3 decision path (`engine/runner.py` / `agent_factory.py`).
_PERSONA_LEGACY_REQUIRED = {
    "host_score",
    "join_score",
    "home_region",
    "preferred_categories",
    "budget_level",
    "time_preferences",
}


def _validate_persona(
    name: str,
    spec: dict[str, Any],
    skills_catalog: dict[str, dict[str, Any]],
    region_ids: set[str],
) -> bool:
    """Run the 8 plan-§4-3 invariants + legacy-key check on one persona.

    Returns True if the persona is usable. On any violation emits a
    ``UserWarning`` and returns False — the caller drops the persona from
    the merged dict so the engine keeps running on what remains.
    """

    # Legacy keys (required by Phase 1~3 engine code path).
    missing_legacy = _PERSONA_LEGACY_REQUIRED - spec.keys()
    if missing_legacy:
        warnings.warn(
            f"persona '{name}' missing legacy keys {sorted(missing_legacy)} — skipped",
            stacklevel=2,
        )
        return False

    # Invariant 4: home_region exists in region_features.
    home = spec.get("home_region")
    if region_ids and home not in region_ids:
        warnings.warn(
            f"persona '{name}' home_region '{home}' not in region_features — skipped",
            stacklevel=2,
        )
        return False

    # Invariant 7: wallet_monthly in [10_000, 60_000]. Tolerate legacy
    # personas without peer fields yet by using the base default (25_000).
    wallet = spec.get("wallet_monthly", 25_000)
    try:
        wallet_i = int(wallet)
    except (TypeError, ValueError):
        warnings.warn(
            f"persona '{name}' wallet_monthly not numeric ({wallet!r}) — skipped",
            stacklevel=2,
        )
        return False
    if not (10_000 <= wallet_i <= 60_000):
        warnings.warn(
            f"persona '{name}' wallet_monthly={wallet_i} outside [10000, 60000] — skipped",
            stacklevel=2,
        )
        return False

    # Invariant 8: pocket_money_motivation in [0, 1].
    pmm = float(spec.get("pocket_money_motivation", 0.5))
    if not (0.0 <= pmm <= 1.0):
        warnings.warn(
            f"persona '{name}' pocket_money_motivation={pmm} outside [0, 1] — skipped",
            stacklevel=2,
        )
        return False

    # Invariants 2, 3, 5, 6: skills / equipment against catalog.
    catalog_keys = set(skills_catalog.keys())
    skills = spec.get("skills") or {}
    if not isinstance(skills, dict):
        warnings.warn(
            f"persona '{name}' skills must be a dict — skipped",
            stacklevel=2,
        )
        return False

    unknown_skills = [k for k in skills.keys() if k not in catalog_keys]
    if unknown_skills:
        warnings.warn(
            f"persona '{name}' skills {unknown_skills} not in skills_catalog — skipped",
            stacklevel=2,
        )
        return False

    equipment = spec.get("equipment") or []
    unknown_equipment = [e for e in equipment if e not in catalog_keys]
    if unknown_equipment:
        warnings.warn(
            f"persona '{name}' equipment {unknown_equipment} not in skills_catalog — skipped",
            stacklevel=2,
        )
        return False

    # Invariant 5: each skill teach / learn in [0, 1].
    nonzero = 0
    for sk, profile in skills.items():
        if not isinstance(profile, dict):
            warnings.warn(
                f"persona '{name}' skill '{sk}' must be a dict — skipped",
                stacklevel=2,
            )
            return False
        teach = float(profile.get("teach", 0.0))
        learn = float(profile.get("learn", 0.0))
        level = int(profile.get("level", 0))
        if not (0.0 <= teach <= 1.0) or not (0.0 <= learn <= 1.0):
            warnings.warn(
                f"persona '{name}' skill '{sk}' teach/learn outside [0,1] — skipped",
                stacklevel=2,
            )
            return False
        if level > 0 or teach > 0.0 or learn > 0.0:
            nonzero += 1

    # Invariant 6: non-zero skill entries in [3, 6]. Skills dict can be
    # empty for personas that have not yet been ported to peer pivot
    # (we warn but do NOT skip in that case — legacy-only personas must
    # still load during the transition). The bound only fires when there
    # is at least one peer-A skill entry.
    if skills and not (3 <= nonzero <= 6):
        warnings.warn(
            f"persona '{name}' non-zero skill count = {nonzero} outside [3, 6]",
            stacklevel=2,
        )
        # Non-fatal — keep the persona but surface the drift.

    return True


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a yaml mapping at the top level")
    return data


def load_personas(
    main_path: PathLike | None = None,
    drop_in_dir: PathLike | None = None,
    skills_catalog: dict[str, dict[str, Any]] | None = None,
    region_features: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Phase Peer-A persona loader (plan §4-1 / §4-2 / §4-3).

    Pipeline:
      1. Load ``config/persona_templates.yaml`` with PyYAML (anchor-aware).
      2. Strip any top-level key starting with ``_`` — the ``_base_persona``
         anchor entry is NOT a real persona.
      3. Glob ``config/personas/*.yaml`` and merge each file on top (drop-in
         files take precedence on key collision, plan §4-2 방법 B).
      4. Run ``_validate_persona`` on every merged entry; skip failures with
         a warning so the engine can continue on what remains.

    Returns ``{persona_name: merged_spec_dict}``. Legacy fields
    (host_score, join_score, home_region, preferred_categories, budget_level,
    time_preferences) are preserved verbatim — `load_persona_templates(path)`
    continues to work for Phase 1~3 call sites.
    """

    main = _as_path(main_path) if main_path is not None else _DEFAULT_PERSONA_TEMPLATES
    drop_dir = (
        _as_path(drop_in_dir) if drop_in_dir is not None else _DEFAULT_PERSONAS_DIR
    )

    # 1. Main file with anchor support.
    raw_main = _load_yaml_mapping(main)

    # 2. Strip anchor entries (keys starting with "_").
    merged: dict[str, dict[str, Any]] = {
        k: v for k, v in raw_main.items() if not k.startswith("_") and isinstance(v, dict)
    }

    # 3. Drop-in glob merge — individual files take precedence on collision.
    if drop_dir.exists() and drop_dir.is_dir():
        for extra_path in sorted(drop_dir.glob("*.yaml")):
            extra = _load_yaml_mapping(extra_path)
            for k, v in extra.items():
                if k.startswith("_"):
                    continue
                if not isinstance(v, dict):
                    continue
                merged[k] = v

    # 4. Invariant validation.
    catalog = skills_catalog if skills_catalog is not None else load_skills_catalog()
    if region_features is None:
        try:
            regions = load_region_features(_DEFAULT_REGION_FEATURES)
            region_ids = set(regions.keys())
        except FileNotFoundError:
            region_ids = set()
    else:
        region_ids = set(region_features.keys())

    validated: dict[str, dict[str, Any]] = {}
    for name, spec in merged.items():
        if _validate_persona(name, spec, catalog, region_ids):
            validated[name] = spec
    return validated
