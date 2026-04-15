"""lint_no_api — src/ 트리에서 OpenAI/Anthropic SDK 사용을 차단한다.

검사 대상:
    - `import openai`, `from openai`
    - `import anthropic`, `from anthropic`
    - `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`
    - `subprocess.run(["codex"` 호출이 codex_client.py / health.py 외부에 존재
      (codex_bridge_engineer 가 phase1 에서 추가)

발견 시 exit 1.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple

PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("import openai", re.compile(r"^\s*import\s+openai(\s|$|\.)", re.MULTILINE)),
    ("from openai", re.compile(r"^\s*from\s+openai\s+import", re.MULTILINE)),
    ("import anthropic", re.compile(r"^\s*import\s+anthropic(\s|$|\.)", re.MULTILINE)),
    ("from anthropic", re.compile(r"^\s*from\s+anthropic\s+import", re.MULTILINE)),
    ("OPENAI_API_KEY", re.compile(r"OPENAI_API_KEY")),
    ("ANTHROPIC_API_KEY", re.compile(r"ANTHROPIC_API_KEY")),
]

# codex CLI subprocess 호출은 codex_client.py / health.py 만 허용.
_CODEX_SUBPROCESS_PATTERN = re.compile(r"""subprocess\.\w+\(\s*\[\s*["']codex["']""")
_CODEX_ALLOWED_FILENAMES = {"codex_client.py", "health.py"}


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print("usage: lint_no_api.py <path>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.exists():
        print(f"path not found: {root}", file=sys.stderr)
        return 2

    violations: List[Tuple[Path, str, int]] = []
    py_files = list(root.rglob("*.py")) if root.is_dir() else [root]
    for py in py_files:
        # 자기 자신은 검사 skip (패턴 정의 자체에 키워드 등장).
        if py.resolve() == Path(__file__).resolve():
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for label, pat in PATTERNS:
            for match in pat.finditer(text):
                line_no = text[: match.start()].count("\n") + 1
                violations.append((py, label, line_no))
        # codex subprocess 호출은 화이트리스트 파일에서만 허용
        if py.name not in _CODEX_ALLOWED_FILENAMES:
            for match in _CODEX_SUBPROCESS_PATTERN.finditer(text):
                line_no = text[: match.start()].count("\n") + 1
                violations.append((py, "subprocess(codex,...) outside bridge", line_no))

    if violations:
        print("FAIL: forbidden API/SDK references found", file=sys.stderr)
        for path, label, line_no in violations:
            print(f"  {path}:{line_no}  [{label}]", file=sys.stderr)
        return 1

    print(f"OK: no forbidden API/SDK references in {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
