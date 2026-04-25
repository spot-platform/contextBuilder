"""Attractiveness scoring service — FE handoff 2026-04-24.

FE의 ``GET /api/v1/feed/{feed_id}/attractiveness`` 응답
(``AttractivenessReport``) 을 만드는 순수 함수 집합.

설계 원칙:
    - 입력은 이미 DB/Redis 에서 조합된 **feed 시그널 벡터 + fee 분포**.
      본 모듈은 DB 접근 안 함. BE API 라우터 또는 recompute Celery task
      가 데이터를 모아 호출한다.
    - 출력은 BACKEND_HANDOFF_ENTITIES.md §AttractivenessReport 스키마
      dict. 봉투 없음 — 호출자가 ``ApiResponse`` 로 포장.
    - ``verdict`` 는 2026-04-24 확정 enum 4종 (`too_cheap` / `competitive`
      / `slightly_high` / `too_high`) 만 반환.
    - 8개 signal 은 전부 ``[0, 1]`` 구간. ``composite_score`` 는 signal
      가중평균.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, TypedDict

from app.services.scoring_service import clip01, weighted_avg

# FE ``AttractivenessVerdict`` enum (2026-04-24 확정).
Verdict = Literal["too_cheap", "competitive", "slightly_high", "too_high"]

# FE ``AttractivenessSignal`` enum 8종. 가중치는 §3 설명서에 따른
# 초기값 — 튜닝 가능하도록 상수로 노출.
SIGNAL_WEIGHTS: Mapping[str, float] = {
    "title_hookiness": 0.15,
    "price_reasonableness": 0.15,
    "venue_accessibility": 0.10,
    "host_reputation_fit": 0.15,
    "time_slot_demand": 0.10,
    "skill_rarity_bonus": 0.10,
    "narrative_authenticity": 0.15,
    "bonded_repeat_potential": 0.10,
}


class AttractivenessSignals(TypedDict):
    title_hookiness: float
    price_reasonableness: float
    venue_accessibility: float
    host_reputation_fit: float
    time_slot_demand: float
    skill_rarity_bonus: float
    narrative_authenticity: float
    bonded_repeat_potential: float


class PriceBenchmark(TypedDict):
    p25: int
    p50: int
    p75: int
    p90: int
    verdict: Verdict


class AttractivenessReport(TypedDict):
    composite_score: float
    signals: AttractivenessSignals
    improvement_hints: list[str]
    price_benchmark: PriceBenchmark


def classify_verdict(your_fee: int, p25: int, p75: int, p90: int) -> Verdict:
    """Map ``your_fee`` onto the 4-bucket FE verdict enum.

    Boundaries (closed on left, open on right):
      - ``your_fee < p25``              → ``too_cheap``
      - ``p25 <= your_fee < p75``       → ``competitive``
      - ``p75 <= your_fee < p90``       → ``slightly_high``
      - ``your_fee >= p90``             → ``too_high``

    Legacy mock strings (``below_p50`` / ``slightly_above_p50`` / ``above_p75``)
    are never returned — 회의 결정이다.
    """

    if your_fee < p25:
        return "too_cheap"
    if your_fee < p75:
        return "competitive"
    if your_fee < p90:
        return "slightly_high"
    return "too_high"


def compute_composite(signals: AttractivenessSignals) -> float:
    """Weighted average of 8 signals clipped to ``[0, 1]``.

    ``SIGNAL_WEIGHTS`` is the single source of truth — tests compare
    against this dict, not against reconstructed literals.
    """

    pairs = [(clip01(signals[name]), w) for name, w in SIGNAL_WEIGHTS.items()]
    return weighted_avg(pairs)


def build_report(
    *,
    signals: AttractivenessSignals,
    your_fee: int,
    fee_distribution: Mapping[str, int],
    improvement_hints: Iterable[str] | None = None,
) -> AttractivenessReport:
    """Assemble the FE-facing ``AttractivenessReport``.

    ``fee_distribution`` must supply keys ``p25, p50, p75, p90`` (ints,
    원화). Missing keys raise KeyError — caller is expected to have
    validated upstream.
    """

    p25 = int(fee_distribution["p25"])
    p50 = int(fee_distribution["p50"])
    p75 = int(fee_distribution["p75"])
    p90 = int(fee_distribution["p90"])

    verdict = classify_verdict(your_fee, p25, p75, p90)
    composite = compute_composite(signals)

    return AttractivenessReport(
        composite_score=round(composite, 3),
        signals=signals,
        improvement_hints=list(improvement_hints or []),
        price_benchmark=PriceBenchmark(
            p25=p25, p50=p50, p75=p75, p90=p90, verdict=verdict
        ),
    )
