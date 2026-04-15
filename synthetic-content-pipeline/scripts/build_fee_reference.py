"""Fee Reference Guide 생성 — cold start writing helper.

event_log.jsonl 의 CREATE_TEACH_SPOT payload 를 (skill, teach_mode, venue_type)
조합으로 그룹화하고, 각 그룹의 fee 분포 (p25/median/p75 등) 를 계산해 YAML 로
출력한다. 앱이 사용자 입력 flow 에서 "이 스킬은 보통 얼마인가요?" 답변 자료로
쓴다.

입력:  ../spot-simulator/output/event_log.jsonl
출력:  config/templates/fee_reference.yaml

이 스크립트는 read-only consumer 다 — pipeline 의 builder/generator/validator
어느 것도 import/수정하지 않는다. 순수 집계 + YAML 덤프.

재실행 idempotent: 동일 event_log 기준 동일 출력.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

# ────────────────────────────────────────────────────────────────────────────
# 설정
# ────────────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
EVENT_LOG_PATH = (REPO_ROOT / ".." / "spot-simulator" / "output" / "event_log.jsonl").resolve()
OUTPUT_PATH = REPO_ROOT / "config" / "templates" / "fee_reference.yaml"

# 정렬 순서 (결정론)
TEACH_MODE_ORDER = {"1:1": 0, "small_group": 1, "workshop": 2}
VENUE_ORDER = {"home": 0, "cafe": 1, "park": 2, "studio": 3, "gym": 4}

LOW_CONFIDENCE_THRESHOLD = 3


# ────────────────────────────────────────────────────────────────────────────
# 집계
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class GroupSamples:
    skill: str
    teach_mode: str
    venue_type: str
    total_fees: list[int] = field(default_factory=list)
    peer_labor_fees: list[int] = field(default_factory=list)
    material_costs: list[int] = field(default_factory=list)
    venue_rentals: list[int] = field(default_factory=list)
    equipment_rentals: list[int] = field(default_factory=list)
    passthroughs: list[int] = field(default_factory=list)
    by_level: dict[int, list[int]] = field(default_factory=lambda: defaultdict(list))


def _percentile(values: list[int], pct: float) -> int:
    """간단한 nearest-rank percentile — 소량 샘플 대응."""
    if not values:
        return 0
    sorted_v = sorted(values)
    k = max(0, min(len(sorted_v) - 1, int(round((pct / 100.0) * (len(sorted_v) - 1)))))
    return int(sorted_v[k])


def _round_nearest(value: int, step: int = 1000) -> int:
    return int(round(value / step)) * step


def _format_won(value: int) -> str:
    """1,000 단위 반올림 후 사람 친화 라벨 — '1.7만원' 형태."""
    rounded = _round_nearest(value, 1000)
    man = rounded / 10000
    if man >= 1:
        # 0.1 단위까지 표기, 정수면 소수점 제거
        label = f"{man:.1f}".rstrip("0").rstrip(".")
        return f"{label}만원"
    return f"{rounded:,}원"


def _collect_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        raise FileNotFoundError(f"event_log not found: {path}")
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event_type") != "CREATE_TEACH_SPOT":
                continue
            events.append(obj)
    return events


def _build_groups(events: list[dict[str, Any]]) -> dict[tuple[str, str, str], GroupSamples]:
    groups: dict[tuple[str, str, str], GroupSamples] = {}
    for ev in events:
        payload = ev.get("payload", {}) or {}
        skill = payload.get("skill")
        teach_mode = payload.get("teach_mode")
        venue_type = payload.get("venue_type")
        if not (skill and teach_mode and venue_type):
            continue

        key = (skill, teach_mode, venue_type)
        grp = groups.get(key)
        if grp is None:
            grp = GroupSamples(skill=skill, teach_mode=teach_mode, venue_type=venue_type)
            groups[key] = grp

        breakdown = payload.get("fee_breakdown")
        if breakdown and isinstance(breakdown, dict):
            total = int(breakdown.get("total", 0) or 0)
            grp.total_fees.append(total)
            grp.peer_labor_fees.append(int(breakdown.get("peer_labor_fee", 0) or 0))
            grp.material_costs.append(int(breakdown.get("material_cost", 0) or 0))
            grp.venue_rentals.append(int(breakdown.get("venue_rental", 0) or 0))
            grp.equipment_rentals.append(int(breakdown.get("equipment_rental", 0) or 0))
            grp.passthroughs.append(int(breakdown.get("passthrough_total", 0) or 0))
            level = payload.get("host_skill_level")
            if isinstance(level, int):
                grp.by_level[level].append(total)
        else:
            # request_matched 경로: breakdown 없지만 aggregate fee 는 있음
            fee = payload.get("fee")
            if isinstance(fee, (int, float)):
                grp.total_fees.append(int(fee))
    return groups


# ────────────────────────────────────────────────────────────────────────────
# 엔트리 빌드
# ────────────────────────────────────────────────────────────────────────────


def _build_entry(grp: GroupSamples) -> dict[str, Any]:
    sample_count = len(grp.total_fees)
    entry: dict[str, Any] = {
        "skill": grp.skill,
        "teach_mode": grp.teach_mode,
        "venue_type": grp.venue_type,
        "sample_count": sample_count,
    }
    if sample_count < LOW_CONFIDENCE_THRESHOLD:
        entry["low_confidence"] = True

    # total_fee_per_partner
    if grp.total_fees:
        entry["total_fee_per_partner"] = {
            "min": min(grp.total_fees),
            "p25": _percentile(grp.total_fees, 25),
            "median": int(statistics.median(grp.total_fees)),
            "p75": _percentile(grp.total_fees, 75),
            "max": max(grp.total_fees),
        }

    # peer_labor_fee (순수 노동료)
    if grp.peer_labor_fees:
        entry["peer_labor_fee"] = {
            "p25": _percentile(grp.peer_labor_fees, 25),
            "median": int(statistics.median(grp.peer_labor_fees)),
            "p75": _percentile(grp.peer_labor_fees, 75),
        }

    # passthrough (실비) 평균
    if grp.passthroughs:
        avg_pass = int(round(statistics.mean(grp.passthroughs)))
        avg_material = int(round(statistics.mean(grp.material_costs))) if grp.material_costs else 0
        avg_venue = int(round(statistics.mean(grp.venue_rentals))) if grp.venue_rentals else 0
        avg_equip = int(round(statistics.mean(grp.equipment_rentals))) if grp.equipment_rentals else 0
        entry["passthrough_average"] = {
            "total": avg_pass,
            "material_cost": avg_material,
            "venue_rental": avg_venue,
            "equipment_rental": avg_equip,
        }
        parts = []
        if avg_material:
            parts.append(f"재료 약 {avg_material:,}원")
        if avg_venue:
            parts.append(f"대관 약 {avg_venue:,}원")
        if avg_equip:
            parts.append(f"장비 약 {avg_equip:,}원")
        if parts:
            entry["passthrough_note"] = "실비(" + ", ".join(parts) + ")"
        elif avg_pass == 0:
            entry["passthrough_note"] = "실비 없음 (장소/재료 비용 별도 안 드는 조합이에요)"

    # recommended_range_label — p25 ~ p75 를 1,000 단위 반올림 후 라벨화
    if grp.total_fees:
        p25 = _percentile(grp.total_fees, 25)
        p75 = _percentile(grp.total_fees, 75)
        low = _format_won(p25)
        high = _format_won(p75)
        if low == high:
            entry["recommended_range_label"] = f"1인 약 {low}"
        else:
            entry["recommended_range_label"] = f"1인 약 {low} ~ {high}"

    # 레벨별 분포 (데이터 있을 때만)
    if grp.by_level:
        by_level_out: dict[str, Any] = {}
        for lvl in sorted(grp.by_level.keys()):
            samples = grp.by_level[lvl]
            by_level_out[f"L{lvl}"] = {
                "median": int(statistics.median(samples)),
                "sample_count": len(samples),
            }
        entry["by_level"] = by_level_out

    return entry


def _sort_key(entry: dict[str, Any]) -> tuple:
    return (
        entry["skill"],
        TEACH_MODE_ORDER.get(entry["teach_mode"], 99),
        VENUE_ORDER.get(entry["venue_type"], 99),
    )


# ────────────────────────────────────────────────────────────────────────────
# 출력
# ────────────────────────────────────────────────────────────────────────────


def build_fee_reference() -> dict[str, Any]:
    events = _collect_events(EVENT_LOG_PATH)
    groups = _build_groups(events)
    entries = [_build_entry(g) for g in groups.values()]
    entries.sort(key=_sort_key)

    # source_spot_count = 고유 spot_id 개수 (event_log 의 CREATE_TEACH_SPOT 기준)
    spot_ids = {ev.get("spot_id") for ev in events if ev.get("spot_id")}

    return {
        "schema_version": 1,
        "generated_at": date.today().isoformat(),
        "source_event_log": "../spot-simulator/output/event_log.jsonl",
        "source_event_count": len(events),
        "source_spot_count": len(spot_ids),
        "notes": (
            "앱 cold start writing helper — 사용자가 '이 스킬 가르치면 얼마 받아야 돼?' "
            "질문에 대답할 때 참고하는 시세표. peer_labor_fee 는 순수 노동 대가, "
            "passthrough 는 재료/대관/장비 실비. 앱은 두 값을 합쳐 total_fee_per_partner "
            "로 노출하고, '실비 별도' 안내를 붙이면 좋음."
        ),
        "entries": entries,
    }


def _str_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def main() -> None:
    payload = build_fee_reference()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    yaml.add_representer(str, _str_representer)
    text = yaml.dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )
    OUTPUT_PATH.write_text(text, encoding="utf-8")

    print(f"[build_fee_reference] wrote {OUTPUT_PATH}")
    print(f"  entries          : {len(payload['entries'])}")
    print(f"  source events    : {payload['source_event_count']}")
    print(f"  source spots     : {payload['source_spot_count']}")


if __name__ == "__main__":
    main()
