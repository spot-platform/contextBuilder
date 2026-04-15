"""scp_03 sample_outputs.jsonl 생성 — feed generator stub 결과 5건 dump."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("SCP_LLM_MODE", "stub")

from pipeline.spec.builder import build_content_spec  # noqa: E402
from pipeline.generators.feed import FeedGenerator  # noqa: E402

EVENT_LOG = Path(
    "/home/seojingyu/project/spotContextBuilder/spot-simulator/output/event_log.jsonl"
)
SPOT_IDS = ["S_0001", "S_0002", "S_0003", "S_0004", "S_0005"]
OUT = ROOT / "_workspace" / "scp_03_gen" / "sample_outputs.jsonl"
OUT.parent.mkdir(parents=True, exist_ok=True)

gen = FeedGenerator()
lines = []
for sid in SPOT_IDS:
    try:
        spec = build_content_spec(EVENT_LOG, sid)
    except Exception as exc:  # noqa: BLE001
        print(f"skip {sid}: {exc}")
        continue
    cands = gen.generate(spec)
    for cand in cands:
        record = {
            "spot_id": sid,
            "content_type": cand.content_type,
            "variant": cand.variant,
            "template_id": cand.template_id,
            "meta": cand.meta,
            "payload": cand.payload,
            "spec_summary": {
                "region": spec.region,
                "category": spec.category,
                "host_persona_type": spec.host_persona.type,
                "expected_count": spec.participants.expected_count,
                "schedule_date": spec.schedule.date,
                "schedule_time": spec.schedule.start_time,
            },
        }
        lines.append(json.dumps(record, ensure_ascii=False))

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"wrote {len(lines)} samples → {OUT}")
