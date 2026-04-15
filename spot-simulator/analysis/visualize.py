"""Event-log I/O + pretty-printing for Phase 1 QA reports.

Kept deliberately dependency-free (stdlib only) so the validation path can
run in environments where matplotlib / pandas aren't installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Event log I/O
# ---------------------------------------------------------------------------


def load_event_log(path: str | Path) -> list[dict]:
    """Read a JSONL event log, returning one dict per line.

    Skips blank lines so an editor-added trailing newline doesn't crash the
    parser. Raises `ValueError` on malformed JSON (with the offending line
    number) so broken logs fail loudly during QA.
    """
    p = Path(path)
    rows: list[dict] = []
    with p.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"event_log {p} line {lineno} is not valid JSON: {exc}"
                ) from exc
    return rows


# ---------------------------------------------------------------------------
# Event type breakdown
# ---------------------------------------------------------------------------


def event_type_breakdown(event_log: list[dict]) -> dict[str, int]:
    """Return `{event_type: count}` sorted descending by count."""
    counts: dict[str, int] = {}
    for e in event_log:
        t = e.get("event_type", "?")
        counts[t] = counts.get(t, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


# ---------------------------------------------------------------------------
# Text histogram
# ---------------------------------------------------------------------------


def text_histogram(
    values: list[float],
    bins: int = 10,
    width: int = 40,
) -> str:
    """ASCII histogram for a list of floats.

    Returns a multi-line string where each row is
    `"[lo, hi)  ####... 12"` — bin range, bar, count. Empty `values` returns
    a placeholder notice so the caller can just drop the result into a
    report without special-casing.
    """
    if not values:
        return "(no values)"
    lo = min(values)
    hi = max(values)
    if lo == hi:
        return f"[{lo:.3f}, {hi:.3f}]  {'#' * width}  {len(values)}  (single value)"
    step = (hi - lo) / bins
    edges = [lo + i * step for i in range(bins + 1)]
    counts = [0] * bins
    for v in values:
        idx = int((v - lo) / step)
        if idx >= bins:
            idx = bins - 1
        counts[idx] += 1
    peak = max(counts) or 1
    lines: list[str] = []
    for i in range(bins):
        bar_len = int(round((counts[i] / peak) * width))
        bar = "#" * bar_len
        lines.append(
            f"[{edges[i]:7.3f}, {edges[i + 1]:7.3f})  {bar:<{width}}  {counts[i]}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 1 report printer
# ---------------------------------------------------------------------------


def _badge(ok: bool) -> str:
    return "[PASS]" if ok else "[FAIL]"


def print_phase1_report(report: dict) -> None:
    """Pretty-print the `validate_phase1` output to stdout."""
    rows: list[tuple[str, str, str]] = [
        (
            "total_events >= 30",
            str(report.get("total_events_count", "?")),
            _badge(report.get("total_events_ok", False)),
        ),
        (
            "CREATE_SPOT >= 5",
            str(report.get("create_spot_count", "?")),
            _badge(report.get("create_spot_ok", False)),
        ),
        (
            "JOIN_SPOT >= 10",
            str(report.get("join_spot_count", "?")),
            _badge(report.get("join_spot_ok", False)),
        ),
        (
            "SPOT_MATCHED >= 2",
            str(report.get("spot_matched_count", "?")),
            _badge(report.get("spot_matched_ok", False)),
        ),
        (
            "dawn ratio < 10%",
            f"{report.get('dawn_ratio', 0.0):.2%}",
            _badge(report.get("dawn_filter_ok", False)),
        ),
        (
            "fatigue variance > 0.005 & range > 0.05",
            (
                f"var={report.get('fatigue_variance', 0.0):.4f} "
                f"range=[{report.get('fatigue_range', (0.0, 0.0))[0]:.3f},"
                f"{report.get('fatigue_range', (0.0, 0.0))[1]:.3f}]"
            ),
            _badge(report.get("fatigue_variance_ok", False)),
        ),
        (
            "host_score top/bottom >= 1.3x",
            f"{report.get('host_score_top_bottom_ratio', 0.0):.2f}x",
            _badge(report.get("host_score_correlation_ok", False)),
        ),
    ]

    print("=" * 68)
    print("Phase 1 Validation Report (plan §2.8)")
    print("=" * 68)
    name_w = max(len(r[0]) for r in rows)
    val_w = max(len(r[1]) for r in rows)
    for name, val, badge in rows:
        print(f"  {name:<{name_w}}  {val:<{val_w}}  {badge}")
    print("-" * 68)
    overall = report.get("all_passed", False)
    print(f"  GATE VERDICT:  {_badge(overall)}  "
          f"({'all criteria passed' if overall else 'see failures above'})")
    print("=" * 68)


# ---------------------------------------------------------------------------
# Convenience: dump a breakdown block for report MD writers
# ---------------------------------------------------------------------------


def format_event_type_breakdown(counts: dict[str, int], width: int = 40) -> str:
    """Return an ASCII bar block suitable for embedding in a markdown file."""
    if not counts:
        return "(no events)"
    peak = max(counts.values()) or 1
    name_w = max(len(k) for k in counts.keys())
    lines: list[str] = []
    for name, n in counts.items():
        bar = "#" * int(round((n / peak) * width))
        lines.append(f"{name:<{name_w}}  {bar:<{width}}  {n}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tick-hour dawn breakdown (used by the report writer)
# ---------------------------------------------------------------------------


def per_hour_distribution(event_log: list[dict]) -> dict[int, int]:
    """Return `{hour_of_day: event_count}` for all 24 hours (0..23)."""
    per_hour: dict[int, int] = {h: 0 for h in range(24)}
    for e in event_log:
        h = int(e.get("tick", 0)) % 24
        per_hour[h] += 1
    return per_hour


def format_per_hour_block(per_hour: dict[int, int], width: int = 30) -> str:
    peak = max(per_hour.values()) or 1
    lines: list[str] = []
    for h in range(24):
        n = per_hour[h]
        bar = "#" * int(round((n / peak) * width))
        tag = " <- dawn" if 0 <= h <= 6 else ""
        lines.append(f"h{h:02d}  {bar:<{width}}  {n}{tag}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 2 — spot timeline ASCII (plan §6.2)
# ---------------------------------------------------------------------------


_SPOT_BOUND_EVENTS: tuple[str, ...] = (
    "CREATE_SPOT",
    "JOIN_SPOT",
    "CANCEL_JOIN",
    "SPOT_MATCHED",
    "SPOT_CONFIRMED",
    "SPOT_STARTED",
    "CHECK_IN",
    "NO_SHOW",
    "SPOT_TIMEOUT",
    "SPOT_COMPLETED",
    "SPOT_DISPUTED",
)


def _event_to_dict(ev: Any) -> dict:
    """Normalize an EventLog dataclass / plain dict to a dict with the
    keys the timeline renderer wants."""
    if isinstance(ev, dict):
        return ev
    return {
        "event_id": getattr(ev, "event_id", None),
        "tick": getattr(ev, "tick", None),
        "event_type": getattr(ev, "event_type", None),
        "agent_id": getattr(ev, "agent_id", None),
        "spot_id": getattr(ev, "spot_id", None),
        "region_id": getattr(ev, "region_id", None),
        "payload": getattr(ev, "payload", {}),
    }


def build_spot_timeline(spot: Any, event_log: list[Any]) -> list[str]:
    """Build the plan §6.2 ASCII timeline block for a single spot.

    Phase 2 version has no settlement/review lines — only created, joined,
    matched, confirmed, started, check_in, no_show, completed, timeout,
    disputed. Returned as a list of lines so the caller can join them with
    the newline flavor they want (markdown vs plain).

    Format:

        Spot S_001 [food] @ emd_yeonmu
        ├─ tick 14: A_023 created (capacity: 4, min: 2)
        ├─ tick 18: A_047 joined
        ├─ tick 19: A_012 joined → MATCHED
        ├─ tick 26: CONFIRMED
        ├─ tick 28: STARTED
        │   ├─ A_023 checked_in
        │   ├─ A_047 checked_in
        │   └─ A_012 no_show
        └─ tick 30: COMPLETED
    """
    spot_id = spot.spot_id
    header = (
        f"Spot {spot_id} [{spot.category}] @ {spot.region_id}"
    )
    lines: list[str] = [header]

    # Collect relevant events, in (tick, event_id) order so ties resolve
    # deterministically.
    rows: list[dict] = []
    for ev in event_log:
        d = _event_to_dict(ev)
        if d.get("spot_id") != spot_id:
            continue
        if d.get("event_type") not in _SPOT_BOUND_EVENTS:
            continue
        rows.append(d)
    rows.sort(key=lambda r: (int(r.get("tick", 0)), int(r.get("event_id", 0))))

    if not rows:
        lines.append("└─ (no events)")
        return lines

    # Pair CHECK_IN/NO_SHOW under the preceding SPOT_STARTED when same tick.
    last_idx = len(rows) - 1
    for i, r in enumerate(rows):
        etype = r.get("event_type")
        tick = r.get("tick")
        aid = r.get("agent_id")
        is_last = i == last_idx
        prefix = "└─" if is_last else "├─"

        if etype == "CREATE_SPOT":
            line = (
                f"{prefix} tick {tick}: {aid} created "
                f"(capacity: {spot.capacity}, min: {spot.min_participants})"
            )
        elif etype == "JOIN_SPOT":
            line = f"{prefix} tick {tick}: {aid} joined"
        elif etype == "CANCEL_JOIN":
            line = f"{prefix} tick {tick}: {aid} cancel_join"
        elif etype == "SPOT_MATCHED":
            line = f"{prefix} tick {tick}: MATCHED"
        elif etype == "SPOT_CONFIRMED":
            line = f"{prefix} tick {tick}: CONFIRMED"
        elif etype == "SPOT_STARTED":
            line = f"{prefix} tick {tick}: STARTED"
        elif etype == "CHECK_IN":
            line = f"{prefix} tick {tick}: {aid} checked_in"
        elif etype == "NO_SHOW":
            line = f"{prefix} tick {tick}: {aid} no_show"
        elif etype == "SPOT_TIMEOUT":
            line = f"{prefix} tick {tick}: CANCELED (timeout)"
        elif etype == "SPOT_COMPLETED":
            line = f"{prefix} tick {tick}: COMPLETED"
        elif etype == "SPOT_DISPUTED":
            line = f"{prefix} tick {tick}: DISPUTED"
        else:
            line = f"{prefix} tick {tick}: {etype}"

        lines.append(line)
    return lines


def sample_spot_timelines(
    spots: list[Any], event_log: list[Any], n: int = 3
) -> str:
    """Pick n interesting spots and return their timelines as one string.

    Prefers 1 COMPLETED (full lifecycle), 1 CANCELED (timeout), 1 DISPUTED;
    falls back to any spot with events attached if a preferred category is
    empty. Always returns exactly `n` blocks when enough spots exist, else
    as many as are available.
    """
    def _status(s: Any) -> str:
        return getattr(s.status, "value", s.status)

    completed = [s for s in spots if _status(s) == "COMPLETED"]
    canceled = [s for s in spots if _status(s) == "CANCELED"]
    disputed = [s for s in spots if _status(s) == "DISPUTED"]

    picks: list[Any] = []
    if completed:
        picks.append(completed[0])
    if canceled:
        picks.append(canceled[0])
    if disputed:
        picks.append(disputed[0])

    # Top up to n from the remaining pool if any category was empty.
    if len(picks) < n:
        remaining = [s for s in spots if s not in picks]
        for s in remaining:
            if len(picks) >= n:
                break
            picks.append(s)
    picks = picks[:n]

    blocks: list[str] = []
    for spot in picks:
        block_lines = build_spot_timeline(spot, event_log)
        blocks.append("\n".join(block_lines))
    return "\n\n".join(blocks) if blocks else "(no spots to sample)"


# ---------------------------------------------------------------------------
# Phase 2 report printer (plan §3.7)
# ---------------------------------------------------------------------------


def _phase2_row(
    name: str, target: str, actual: str, ok: bool, neutral: bool = False
) -> tuple[str, str, str, str]:
    if neutral:
        badge = "[NEUTRAL]"
    else:
        badge = _badge(ok)
    return (name, target, actual, badge)


def print_phase2_report(report: dict) -> None:
    """Pretty-print the `validate_phase2` output to stdout.

    NEUTRAL criteria (criterion 4 when trust_score has no variance) render
    with a distinct badge and do not block the gate verdict.
    """

    c1_d = report.get("criterion_1_detail", {})
    c2_d = report.get("criterion_2_detail", {})
    c3_d = report.get("criterion_3_detail", {})
    c4_d = report.get("criterion_4_detail", {})
    c5_d = report.get("criterion_5_detail", {})
    c6_d = report.get("criterion_6_detail", {})
    c7_d = report.get("criterion_7_detail", {})

    c4_neutral = bool(c4_d.get("neutral", False))

    rows: list[tuple[str, str, str, str]] = [
        _phase2_row(
            "1. full lifecycle exists (>=1 COMPLETED)",
            ">= 1",
            f"{c1_d.get('completed_count', 0)}/{c1_d.get('total_spots', 0)}",
            report.get("criterion_1_ok", False),
        ),
        _phase2_row(
            "2. CANCELED ratio in [0.15, 0.30]",
            "[0.15, 0.30]",
            f"{c2_d.get('ratio', 0.0):.2%} "
            f"({c2_d.get('canceled_count', 0)}/"
            f"{c2_d.get('total_spots', 0)})",
            report.get("criterion_2_ok", False),
        ),
        _phase2_row(
            "3. FOMO: mean fill_rate at MATCHED > 0.70",
            "> 0.70",
            f"{c3_d.get('mean_fill_rate', 0.0):.3f} "
            f"(n={c3_d.get('matched_spots', 0)})",
            report.get("criterion_3_ok", False),
        ),
        _phase2_row(
            "4. host_trust top/bottom >= 1.25x",
            ">= 1.25x",
            (
                "neutral (trust_score static in Phase 2)"
                if c4_neutral
                else f"{c4_d.get('ratio_x', 0.0):.2f}x "
                f"({c4_d.get('top_matched', 0)}/{c4_d.get('top_hosted', 0)} "
                f"vs {c4_d.get('bottom_matched', 0)}/"
                f"{c4_d.get('bottom_hosted', 0)})"
            ),
            report.get("criterion_4_ok", False),
            neutral=c4_neutral,
        ),
        _phase2_row(
            "5. avg lead time (MATCHED) >= 12 ticks",
            ">= 12",
            f"{c5_d.get('avg_lead_ticks', 0.0):.1f} "
            f"(p50={c5_d.get('p50', 0)}, p90={c5_d.get('p90', 0)}, "
            f"n={c5_d.get('matched_count', 0)})",
            report.get("criterion_5_ok", False),
        ),
        _phase2_row(
            "6. NO_SHOW / CHECK_IN in [0.05, 0.15]",
            "[0.05, 0.15]",
            f"{c6_d.get('ratio', 0.0):.2%} "
            f"({c6_d.get('no_show', 0)}/{c6_d.get('check_in', 0)})",
            report.get("criterion_6_ok", False),
        ),
        _phase2_row(
            "7. DISPUTED / COMPLETED in (0, 0.30]",
            "(0, 0.30]",
            f"{c7_d.get('ratio', 0.0):.2%} "
            f"({c7_d.get('disputed_count', 0)}/"
            f"{c7_d.get('completed_count', 0)})",
            report.get("criterion_7_ok", False),
        ),
    ]

    print("=" * 78)
    print("Phase 2 Validation Report (plan §3.7)")
    print("=" * 78)
    name_w = max(len(r[0]) for r in rows)
    target_w = max(len(r[1]) for r in rows)
    actual_w = max(len(r[2]) for r in rows)
    for name, target, actual, badge in rows:
        print(
            f"  {name:<{name_w}}  "
            f"{target:<{target_w}}  "
            f"{actual:<{actual_w}}  {badge}"
        )
    print("-" * 78)
    overall = report.get("all_passed", False)
    print(
        f"  GATE VERDICT:  {_badge(overall)}  "
        f"({'all criteria passed' if overall else 'see failures above'})"
    )
    counts = report.get("event_type_counts", {})
    if counts:
        print("-" * 78)
        print("  Event type counts:")
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"    {k:<16}  {v}")
    print("=" * 78)


_PHASE3_SPOT_BOUND_EVENTS: tuple[str, ...] = (
    "CREATE_SPOT",
    "JOIN_SPOT",
    "CANCEL_JOIN",
    "SPOT_MATCHED",
    "SPOT_CONFIRMED",
    "SPOT_STARTED",
    "CHECK_IN",
    "NO_SHOW",
    "SPOT_TIMEOUT",
    "SPOT_COMPLETED",
    "SPOT_DISPUTED",
    "WRITE_REVIEW",
    "SETTLE",
    "SPOT_SETTLED",
    "FORCE_SETTLED",
    "DISPUTE_RESOLVED",
    "SAVE_SPOT",
)


def build_phase3_spot_timeline(spot: Any, event_log: list[Any]) -> list[str]:
    """Build a Phase 3 timeline block for a single spot — same shape as
    `build_spot_timeline` but also renders WRITE_REVIEW / SETTLE /
    SPOT_SETTLED / FORCE_SETTLED / DISPUTE_RESOLVED / SAVE_SPOT rows so the
    post-lifecycle settlement story is visible."""
    spot_id = spot.spot_id
    header = f"Spot {spot_id} [{spot.category}] @ {spot.region_id}"
    lines: list[str] = [header]

    rows: list[dict] = []
    for ev in event_log:
        d = _event_to_dict(ev)
        if d.get("spot_id") != spot_id:
            continue
        if d.get("event_type") not in _PHASE3_SPOT_BOUND_EVENTS:
            continue
        rows.append(d)
    rows.sort(key=lambda r: (int(r.get("tick", 0)), int(r.get("event_id", 0))))

    if not rows:
        lines.append("└─ (no events)")
        return lines

    last_idx = len(rows) - 1
    for i, r in enumerate(rows):
        etype = r.get("event_type")
        tick = r.get("tick")
        aid = r.get("agent_id")
        payload = r.get("payload") or {}
        is_last = i == last_idx
        prefix = "└─" if is_last else "├─"

        if etype == "CREATE_SPOT":
            line = (
                f"{prefix} tick {tick}: {aid} created "
                f"(capacity: {spot.capacity}, min: {spot.min_participants})"
            )
        elif etype == "JOIN_SPOT":
            line = f"{prefix} tick {tick}: {aid} joined"
        elif etype == "CANCEL_JOIN":
            line = f"{prefix} tick {tick}: {aid} cancel_join"
        elif etype == "SPOT_MATCHED":
            line = f"{prefix} tick {tick}: MATCHED"
        elif etype == "SPOT_CONFIRMED":
            line = f"{prefix} tick {tick}: CONFIRMED"
        elif etype == "SPOT_STARTED":
            line = f"{prefix} tick {tick}: STARTED"
        elif etype == "CHECK_IN":
            line = f"{prefix} tick {tick}: {aid} checked_in"
        elif etype == "NO_SHOW":
            line = f"{prefix} tick {tick}: {aid} no_show"
        elif etype == "SPOT_TIMEOUT":
            line = f"{prefix} tick {tick}: CANCELED (timeout)"
        elif etype == "SPOT_COMPLETED":
            line = f"{prefix} tick {tick}: COMPLETED"
        elif etype == "SPOT_DISPUTED":
            line = f"{prefix} tick {tick}: DISPUTED"
        elif etype == "WRITE_REVIEW":
            sat = payload.get("satisfaction")
            line = f"{prefix} tick {tick}: {aid} wrote review (sat: {sat})"
        elif etype == "SETTLE":
            line = f"{prefix} tick {tick}: {aid} settle"
        elif etype == "SPOT_SETTLED":
            avg_sat = payload.get("avg_sat")
            line = f"{prefix} tick {tick}: SETTLED (avg_sat: {avg_sat})"
        elif etype == "FORCE_SETTLED":
            reason = payload.get("reason", "?")
            line = f"{prefix} tick {tick}: FORCE_SETTLED ({reason})"
        elif etype == "DISPUTE_RESOLVED":
            line = f"{prefix} tick {tick}: DISPUTE_RESOLVED"
        elif etype == "SAVE_SPOT":
            line = f"{prefix} tick {tick}: {aid} saved"
        else:
            line = f"{prefix} tick {tick}: {etype}"
        lines.append(line)
    return lines


def sample_phase3_spot_timelines(
    spots: list[Any], event_log: list[Any]
) -> str:
    """Return three timelines: one clean SETTLED, one DISPUTE_RESOLVED,
    one FORCE_SETTLED. Falls back gracefully when any category is missing.
    """
    def _status(s: Any) -> str:
        return getattr(s.status, "value", s.status)

    # Classify spots.
    settled = [s for s in spots if _status(s) == "SETTLED"]
    force_settled = [s for s in spots if _status(s) == "FORCE_SETTLED"]

    # A "clean SETTLED" is one that never entered DISPUTED; a
    # "DISPUTE_RESOLVED" is one that did (its timeline has SPOT_DISPUTED +
    # DISPUTE_RESOLVED). We detect those by inspecting the event log.
    disputed_ids: set[str] = set()
    for ev in event_log:
        d = _event_to_dict(ev)
        if d.get("event_type") == "SPOT_DISPUTED" and d.get("spot_id"):
            disputed_ids.add(d["spot_id"])

    clean_settled = next(
        (s for s in settled if s.spot_id not in disputed_ids), None
    )
    dispute_resolved = next(
        (s for s in settled if s.spot_id in disputed_ids), None
    )
    force_pick = force_settled[0] if force_settled else None

    picks: list[tuple[str, Any]] = []
    if clean_settled is not None:
        picks.append(("clean SETTLED", clean_settled))
    if dispute_resolved is not None:
        picks.append(("DISPUTE_RESOLVED", dispute_resolved))
    if force_pick is not None:
        picks.append(("FORCE_SETTLED", force_pick))

    if not picks:
        return "(no SETTLED/FORCE_SETTLED spots to sample)"

    blocks: list[str] = []
    for label, spot in picks:
        block_lines = build_phase3_spot_timeline(spot, event_log)
        blocks.append(f"[{label}]\n" + "\n".join(block_lines))
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Phase 3 — trust & satisfaction histograms (plan §4.6 / §6.3)
# ---------------------------------------------------------------------------


def trust_distribution(agents: list[Any]) -> str:
    """ASCII histogram of final `trust_score` across the agent population."""
    values = [float(getattr(a, "trust_score", 0.5)) for a in agents]
    if not values:
        return "(no agents)"
    peak_val = max(values)
    mean_val = sum(values) / len(values)
    low = sum(1 for v in values if v < 0.3)
    high = sum(1 for v in values if v > 0.7)
    header = (
        f"trust_score  n={len(values)}  mean={mean_val:.3f}  "
        f"max={peak_val:.3f}  <0.3={low}  >0.7={high}"
    )
    return header + "\n" + text_histogram(values, bins=10, width=40)


def satisfaction_histogram(spots: list[Any]) -> str:
    """ASCII histogram of per-spot `avg_satisfaction` (non-None only)."""
    values = [
        float(s.avg_satisfaction)
        for s in spots
        if getattr(s, "avg_satisfaction", None) is not None
    ]
    if not values:
        return "avg_satisfaction  (no settled spots)"
    mean_val = sum(values) / len(values)
    header = (
        f"avg_satisfaction  n={len(values)}  mean={mean_val:.3f}  "
        f"min={min(values):.3f}  max={max(values):.3f}"
    )
    return header + "\n" + text_histogram(values, bins=10, width=40)


# ---------------------------------------------------------------------------
# Phase 3 — aggregated metrics report (plan §6.3)
# ---------------------------------------------------------------------------


def aggregated_metrics_report(
    event_log: list[Any],
    agents: list[Any],
    spots: list[Any],
) -> str:
    """Render a plan §6.3 style aggregated metrics block.

    Includes: total spots, match rate, completion rate, settlement rate,
    mean satisfaction, top regions (matching rate), category breakdown with
    noshow ratio, per-persona participation (mean join count per persona).
    """

    def _status(s: Any) -> str:
        return getattr(s.status, "value", s.status)

    total_spots = len(spots)
    # Matching success: any spot that ever reached MATCHED or beyond.
    matched_set = {
        _status(s)
        for s in spots
    }
    matched = sum(
        1
        for s in spots
        if _status(s)
        in {
            "MATCHED",
            "CONFIRMED",
            "IN_PROGRESS",
            "COMPLETED",
            "DISPUTED",
            "SETTLED",
            "FORCE_SETTLED",
        }
    )
    completed = sum(
        1
        for s in spots
        if _status(s)
        in {"COMPLETED", "SETTLED", "FORCE_SETTLED"}
    )
    settled = sum(
        1
        for s in spots
        if _status(s) in {"SETTLED", "FORCE_SETTLED"}
    )
    avg_sats = [
        float(s.avg_satisfaction)
        for s in spots
        if getattr(s, "avg_satisfaction", None) is not None
    ]
    mean_sat = sum(avg_sats) / len(avg_sats) if avg_sats else 0.0

    lines: list[str] = []
    lines.append("=== 2주 시뮬레이션 결과 ===")
    lines.append(f"총 스팟 생성: {total_spots}")
    if total_spots > 0:
        lines.append(
            f"매칭 성공: {matched} "
            f"({matched / total_spots:.1%})"
        )
    else:
        lines.append("매칭 성공: 0 (0.0%)")
    if matched > 0:
        lines.append(
            f"완료: {completed} ({completed / matched:.1%} of matched)"
        )
    else:
        lines.append("완료: 0")
    if completed > 0:
        lines.append(
            f"정산 완료: {settled} ({settled / completed:.1%} of completed)"
        )
    else:
        lines.append("정산 완료: 0")
    lines.append(f"평균 만족도: {mean_sat:.3f}")
    lines.append("")

    # --- Region breakdown (top 3 by spot count) -------------------------
    region_tally: dict[str, dict[str, int]] = {}
    for s in spots:
        rec = region_tally.setdefault(
            s.region_id, {"total": 0, "matched": 0}
        )
        rec["total"] += 1
        if _status(s) in {
            "MATCHED",
            "CONFIRMED",
            "IN_PROGRESS",
            "COMPLETED",
            "DISPUTED",
            "SETTLED",
            "FORCE_SETTLED",
        }:
            rec["matched"] += 1
    top_regions = sorted(
        region_tally.items(), key=lambda kv: -kv[1]["total"]
    )[:3]
    lines.append("지역별 TOP 3:")
    for rid, rec in top_regions:
        mrate = rec["matched"] / rec["total"] if rec["total"] else 0.0
        lines.append(
            f"  {rid:<16} {rec['total']:>5} spots (매칭률 {mrate:.0%})"
        )
    lines.append("")

    # --- Category breakdown ---------------------------------------------
    category_tally: dict[str, dict[str, int]] = {}
    for s in spots:
        rec = category_tally.setdefault(
            s.category, {"total": 0, "noshow_participants": 0, "participants": 0}
        )
        rec["total"] += 1
        rec["noshow_participants"] += len(getattr(s, "noshow", set()))
        rec["participants"] += len(getattr(s, "participants", []))
    category_lines: list[tuple[str, int, float, float]] = []
    total = sum(v["total"] for v in category_tally.values())
    for cat, rec in sorted(category_tally.items(), key=lambda kv: -kv[1]["total"]):
        share = rec["total"] / total if total else 0.0
        ns_rate = (
            rec["noshow_participants"] / rec["participants"]
            if rec["participants"]
            else 0.0
        )
        category_lines.append((cat, rec["total"], share, ns_rate))
    lines.append("카테고리별:")
    for cat, count, share, ns_rate in category_lines[:6]:
        lines.append(
            f"  {cat:<12} {share:>5.0%} (n={count}, 노쇼율 {ns_rate:.0%})"
        )
    lines.append("")

    # --- Per-persona participation --------------------------------------
    # Count JOIN_SPOT events attributed to each agent, then group by persona.
    join_counts_by_agent: dict[str, int] = {}
    for ev in event_log:
        d = _event_to_dict(ev)
        if d.get("event_type") != "JOIN_SPOT":
            continue
        aid = d.get("agent_id")
        if aid is not None:
            join_counts_by_agent[aid] = join_counts_by_agent.get(aid, 0) + 1

    persona_joins: dict[str, list[int]] = {}
    for a in agents:
        persona_joins.setdefault(a.persona_type, []).append(
            join_counts_by_agent.get(a.agent_id, 0)
        )
    lines.append("페르소나별 참여율:")
    rows = []
    for persona, vals in persona_joins.items():
        mean_joins = sum(vals) / len(vals) if vals else 0.0
        rows.append((persona, mean_joins, len(vals)))
    rows.sort(key=lambda r: -r[1])
    for persona, mean_joins, n in rows:
        lines.append(
            f"  {persona:<20} 평균 {mean_joins:.1f}회/2주 (n={n})"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 3 report printer (plan §4.6)
# ---------------------------------------------------------------------------


def print_phase3_report(report: dict) -> None:
    """Pretty-print the `validate_phase3` output to stdout."""
    c1_d = report.get("criterion_1_detail", {})
    c2_d = report.get("criterion_2_detail", {})
    c3_d = report.get("criterion_3_detail", {})
    c4_d = report.get("criterion_4_detail", {})
    c5_d = report.get("criterion_5_detail", {})
    c6_d = report.get("criterion_6_detail", {})

    def _fmt_ratio(x: float) -> str:
        if x == float("inf"):
            return "inf"
        return f"{x:.2f}"

    rows: list[tuple[str, str, str, str]] = [
        (
            "1. COMPLETED -> SETTLED rate",
            ">= 80%",
            f"{c1_d.get('rate', 0.0):.1%} "
            f"({c1_d.get('settled_count', 0)}/"
            f"{c1_d.get('completed_count', 0)})",
            _badge(report.get("criterion_1_ok", False)),
        ),
        (
            "2. FORCE_SETTLED share (liberal)",
            "< 5%",
            f"{c2_d.get('liberal_ratio', c2_d.get('ratio', 0.0)):.1%} "
            f"({c2_d.get('force_settled_count', 0)}/"
            f"{c2_d.get('finished_count', 0)}) "
            f"[strict={c2_d.get('strict_ratio', 0.0):.1%}]",
            _badge(report.get("criterion_2_ok", False)),
        ),
        (
            "3. WRITE_REVIEW / CHECK_IN",
            "[30%, 50%]",
            f"{c3_d.get('rate', 0.0):.1%} "
            f"({c3_d.get('write_review', 0)}/"
            f"{c3_d.get('check_in', 0)})",
            _badge(report.get("criterion_3_ok", False)),
        ),
        (
            "4. host trust top/bot quintile",
            ">= 2.0x",
            f"{_fmt_ratio(c4_d.get('ratio', 0.0))}x "
            f"(top={c4_d.get('top_match_rate', 0.0):.2f} "
            f"bot={c4_d.get('bottom_match_rate', 0.0):.2f})",
            _badge(report.get("criterion_4_ok", False)),
        ),
        (
            "5. low-trust JOIN rate decay",
            "first > last",
            f"first={c5_d.get('first_half_rate', 0.0):.2f} "
            f"last={c5_d.get('last_half_rate', 0.0):.2f}",
            _badge(report.get("criterion_5_ok", False)),
        ),
        (
            "6. spot timeline extraction",
            "5/5 ok",
            f"{sum(1 for s in c6_d.get('spots', []) if s.get('ok'))}"
            f"/{c6_d.get('sampled', 0)}",
            _badge(report.get("criterion_6_ok", False)),
        ),
    ]

    print("=" * 78)
    print("Phase 3 Validation Report (plan §4.6)")
    print("=" * 78)
    name_w = max(len(r[0]) for r in rows)
    target_w = max(len(r[1]) for r in rows)
    actual_w = max(len(r[2]) for r in rows)
    for name, target, actual, badge in rows:
        print(
            f"  {name:<{name_w}}  "
            f"{target:<{target_w}}  "
            f"{actual:<{actual_w}}  {badge}"
        )
    print("-" * 78)
    overall = report.get("all_passed", False)
    print(
        f"  GATE VERDICT:  {_badge(overall)}  "
        f"({'all criteria passed' if overall else 'see failures above'})"
    )
    counts = report.get("event_type_counts", {})
    if counts:
        print("-" * 78)
        print("  Event type counts:")
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"    {k:<20}  {v}")
    print("=" * 78)


def build_lifecycle_flow(spots: list[Any]) -> str:
    """Count spot terminal + transit statuses for a textual flow block.

    Returns a lines-joined string like:

        total spots: 6974
        OPEN (leftover):      120
        MATCHED (no confirm):   0
        CONFIRMED (no start):  13
        IN_PROGRESS:            0
        CANCELED (timeout):   635
        COMPLETED:           3225
        DISPUTED:             698
    """
    tally: dict[str, int] = {}
    for s in spots:
        st = getattr(s.status, "value", s.status)
        tally[st] = tally.get(st, 0) + 1
    total = len(spots)
    order = [
        "OPEN",
        "MATCHED",
        "CONFIRMED",
        "IN_PROGRESS",
        "CANCELED",
        "COMPLETED",
        "DISPUTED",
    ]
    lines = [f"total spots: {total}"]
    for k in order:
        lines.append(f"  {k:<14}  {tally.get(k, 0)}")
    return "\n".join(lines)
