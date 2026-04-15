"""LLM 브리지 예외 계층 — codex CLI 호출 실패 분류."""
from __future__ import annotations


class CodexBridgeError(Exception):
    """모든 codex 브리지 에러의 부모."""


class CodexLoginError(CodexBridgeError):
    """`codex login status` 실패. 파이프라인은 fail-fast 해야 한다."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"codex login check failed: {reason}")
        self.reason = reason


class CodexCallError(CodexBridgeError):
    """`codex exec` 일반 비정상 종료."""

    def __init__(self, exit_code: int, stderr_tail: str) -> None:
        super().__init__(
            f"codex exec exited with {exit_code}\n--- stderr (tail) ---\n{stderr_tail}"
        )
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail


class CodexTimeoutError(CodexBridgeError):
    """`codex exec` subprocess 타임아웃."""

    def __init__(self, timeout: int) -> None:
        super().__init__(f"codex exec timed out after {timeout}s")
        self.timeout = timeout


class CodexSchemaError(CodexBridgeError):
    """codex 응답이 JSON 파싱 실패 또는 스키마 위반."""

    def __init__(self, message: str, raw_excerpt: str = "") -> None:
        super().__init__(f"codex schema/parse error: {message}")
        self.raw_excerpt = raw_excerpt


class CodexRateLimitError(CodexBridgeError):
    """rate limit / quota / 429 검출. 30s backoff 후 1회 재시도 권장."""

    def __init__(self, stderr_tail: str) -> None:
        super().__init__("codex rate-limit or quota signal detected")
        self.stderr_tail = stderr_tail
