"""validators package — Layer 1~6 검증 모듈.

Phase 1 범위:
    - schema (Layer 1): JSON Schema + 길이/문장 규칙 (feed)
    - rules  (Layer 2): deterministic 비즈니스 규칙 (feed)

Phase 2 범위 (이번 단계):
    - schema 확장     : detail / plan / messages / review Draft7 validators
    - detail_rules    : SpotDetail 전용 Layer 2 rule
    - plan_rules      : SpotPlan 전용 Layer 2 rule
    - messages_rules  : Messages 전용 Layer 2 rule
    - review_rules    : Review 전용 Layer 2 rule
    - cross_reference : Layer 3 스팟 단위 5쌍 교차 검증
    - dispatch        : content_type → validator 디스패처

Phase 3 (별도 단계):
    - critic          (Layer 4)
    - diversity       (Layer 5)
    - scoring         (Layer 6)
"""
from pipeline.validators.types import Rejection, ValidationResult

__all__ = ["Rejection", "ValidationResult"]
