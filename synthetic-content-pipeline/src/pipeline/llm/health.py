"""Codex 로그인 상태 확인 — 파이프라인 시작 시점 1회 호출."""
from __future__ import annotations

import subprocess  # codex CLI 호출은 codex_client 와 health 만 허용 (lint marker: ALLOW_CODEX_SUBPROCESS)
import sys

from pipeline.llm.errors import CodexLoginError

_login_ok: bool | None = None


def check_codex_login() -> bool:
    """`codex login status` exit 0 이면 True. 실패 시 CodexLoginError raise."""
    try:
        proc = subprocess.run(  # ALLOW_CODEX_SUBPROCESS
            ["codex", "login", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as e:
        raise CodexLoginError(f"codex CLI not found in PATH: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise CodexLoginError("codex login status timed out (>10s)") from e

    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-30:])
        raise CodexLoginError(tail or proc.stdout.strip() or "non-zero exit")
    return True


def ensure_codex_ready() -> None:
    """파이프라인 진입점에서 1회만 호출. 실패 시 sys.exit(2)."""
    global _login_ok
    if _login_ok:
        return
    try:
        _login_ok = check_codex_login()
    except CodexLoginError as e:
        print(f"[FATAL] {e}", file=sys.stderr)
        print(
            "[HINT] run `codex login` to sign in with your Codex subscription.",
            file=sys.stderr,
        )
        sys.exit(2)
