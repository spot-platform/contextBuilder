"""Pytest bootstrap: add the spot-simulator root to sys.path.

Lets tests do `from models.agent import ...` without installing the package.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
