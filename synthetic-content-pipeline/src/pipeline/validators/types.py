"""ValidationResult / Rejection — 모든 layer가 공유하는 데이터 클래스.

generator-engineer가 재생성 루프에서 그대로 read 할 수 있도록
``rejection.instruction`` 은 사람/LLM 모두가 따를 수 있는 한국어
재생성 지시문이어야 한다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal


SeverityLiteral = Literal["reject", "warn"]
LayerLiteral = Literal["schema", "rule", "cross_ref", "critic", "diversity"]


@dataclass
class Rejection:
    """단일 위반 사항.

    Attributes:
        layer: 어느 검증 Layer에서 발생했는지.
        rejected_field: 위반이 일어난 payload 필드 경로.
            예: ``"title"``, ``"summary"``, ``"tags[0]"``, ``"__schema__"``.
        reason: 짧은 머신 읽기용 코드. (예: ``"category_mismatch"``)
        detail: 사람이 읽는 설명.
        instruction: generator-engineer에게 줄 재생성 지시문.
        severity: ``"reject"`` 면 즉시 재생성 대상. ``"warn"`` 은 점수 감점만.
    """

    layer: LayerLiteral
    rejected_field: str
    reason: str
    detail: str
    instruction: str
    severity: SeverityLiteral = "reject"

    def to_dict(self) -> Dict[str, Any]:
        """JSON 직렬화용 dict."""
        return asdict(self)


@dataclass
class ValidationResult:
    """단일 layer의 검증 결과.

    Attributes:
        ok: rejection 중 ``severity="reject"`` 가 하나도 없으면 True.
        layer: 어느 layer가 만든 결과인지 (logging용).
        rejections: 위반 목록 (warn + reject 모두).
        meta: 디버깅·튜닝용 부가 정보 (점수, 매칭 stats 등).
    """

    ok: bool
    layer: LayerLiteral
    rejections: List[Rejection] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def hard_rejections(self) -> List[Rejection]:
        """severity=reject 만 필터링."""
        return [r for r in self.rejections if r.severity == "reject"]

    @property
    def warnings(self) -> List[Rejection]:
        """severity=warn 만 필터링."""
        return [r for r in self.rejections if r.severity == "warn"]

    def to_dict(self) -> Dict[str, Any]:
        """JSON 직렬화용 dict."""
        return {
            "ok": self.ok,
            "layer": self.layer,
            "rejections": [r.to_dict() for r in self.rejections],
            "meta": self.meta,
        }

    @classmethod
    def from_rejections(
        cls,
        layer: LayerLiteral,
        rejections: List[Rejection],
        meta: Dict[str, Any] | None = None,
    ) -> "ValidationResult":
        """rejection 목록으로부터 결과를 합성. 하나라도 reject면 ok=False."""
        ok = not any(r.severity == "reject" for r in rejections)
        return cls(ok=ok, layer=layer, rejections=list(rejections), meta=meta or {})


__all__ = ["Rejection", "ValidationResult", "SeverityLiteral", "LayerLiteral"]
