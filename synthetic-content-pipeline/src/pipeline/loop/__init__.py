"""pipeline.loop — generate → validate → retry 전체 오케스트레이션.

validator-engineer Phase 3. 생성 루프는 여기서만 들어온다. generators/ 의
``BaseGenerator.generate`` 는 rejection feedback 재시도를 이미 내장하고 있고,
이 모듈은 그 위에 Layer 4/5/6 (critic / diversity / scoring) + cross-reference
재생성을 얹는다.
"""
from pipeline.loop.generate_validate_retry import (
    ContentProcessResult,
    GENERATOR_FACTORIES,
    SpotProcessResult,
    process_single_content,
    process_spot_full,
)

__all__ = [
    "ContentProcessResult",
    "GENERATOR_FACTORIES",
    "SpotProcessResult",
    "process_single_content",
    "process_spot_full",
]
