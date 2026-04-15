"""scp_03 feed generator end-to-end check (stub mode)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pipeline.spec.builder import build_content_spec  # noqa: E402
from pipeline.generators.feed import FeedGenerator  # noqa: E402
from pipeline.generators.base import COMMON_VARIABLE_KEYS  # noqa: E402

EVENT_LOG = Path(
    "/home/seojingyu/project/spotContextBuilder/spot-simulator/output/event_log.jsonl"
)

spec = build_content_spec(EVENT_LOG, "S_0001")
gen = FeedGenerator()
cands = gen.generate(spec)
print("CANDIDATE_COUNT:", len(cands))
print("VARIANTS:", cands[0].variant, cands[1].variant)
print("PAYLOAD0:")
print(json.dumps(cands[0].payload, ensure_ascii=False, indent=2))
print("PAYLOAD1:")
print(json.dumps(cands[1].payload, ensure_ascii=False, indent=2))
print("META0:", cands[0].meta)
print("META1:", cands[1].meta)

vars_dump = gen.spec_to_variables(spec, variant="primary", length_bucket="medium")
print("VAR_KEYS:", sorted(vars_dump.keys()))
print(
    "COMMON_VARIABLE_KEYS subset of var_keys?:",
    COMMON_VARIABLE_KEYS.issubset(vars_dump.keys()),
)

# Jinja2 template syntax check
from jinja2 import Environment, FileSystemLoader, StrictUndefined  # noqa: E402

env = Environment(
    loader=FileSystemLoader(str(ROOT / "config" / "prompts")),
    undefined=StrictUndefined,
)
tmpl = env.get_template("feed/v1.j2")
print("JINJA_OK:", tmpl.filename)

# Render once with primary variant variables to ensure no missing var.
rendered = tmpl.render(**vars_dump, previous_rejections=[])
print("RENDERED_LEN:", len(rendered))
print("RENDERED_HEAD:")
print(rendered[:600])
