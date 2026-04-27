"""POI-anchored pipeline demo — 단일 generator 만 codex 로 호출.

CLI:
    py scripts/demo_poi_anchored_one.py [content_type]

content_type ∈ {feed, detail, plan, price, preparation}. 기본 detail.

SCP_LLM_MODE=live 로 실행해야 codex 호출. stub 이면 fixture.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from demo_poi_anchored_generate import build_demo_spec  # noqa: E402


def main() -> int:
    ct = sys.argv[1] if len(sys.argv) > 1 else "detail"
    spec = build_demo_spec()

    print(f"[demo] content_type={ct}, mode={os.environ.get('SCP_LLM_MODE','stub')}")
    print(f"[demo] primary_pin={spec.primary_pin.name if spec.primary_pin else 'None'}")
    print(f"[demo] anchors={[(a.role, a.name) for a in spec.venue_anchors]}")

    factory = {
        "feed": "pipeline.generators.feed.FeedGenerator",
        "detail": "pipeline.generators.detail.SpotDetailGenerator",
        "plan": "pipeline.generators.plan.SpotPlanGenerator",
        "price": "pipeline.generators.price.SpotPriceGenerator",
        "preparation": "pipeline.generators.preparation.SpotPreparationGenerator",
    }[ct]
    mod_path, cls_name = factory.rsplit(".", 1)
    import importlib
    cls = getattr(importlib.import_module(mod_path), cls_name)

    gen = cls()
    t0 = time.time()
    cands = gen.generate(spec)
    elapsed = time.time() - t0

    out_dir = ROOT / "_workspace"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for c in cands:
        rows.append(
            {
                "content_type": ct,
                "variant": c.variant,
                "template_id": c.template_id,
                "stub": c.meta.get("stub", False),
                "retry_count": c.meta.get("retry_count", 0),
                "retry_exhausted": c.meta.get("retry_exhausted", False),
                "rejection_history": c.meta.get("rejection_history", []),
                "payload": c.payload,
            }
        )
    out_path = out_dir / f"demo_{ct}_output.json"
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[demo] elapsed={elapsed:.1f}s, saved={out_path}")
    print(json.dumps(rows[0]["payload"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
