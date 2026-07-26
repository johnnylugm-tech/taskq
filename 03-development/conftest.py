"""Make `taskq` importable from repo root without a manually-set PYTHONPATH.

The harness's Gate 1 `pytest-cov` tool runner invokes pytest with cwd=repo
root and no PYTHONPATH (harness/harness/tool_runners.py), so `import taskq`
fails collection for every test_fr0N.py file unless `src` is on sys.path.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
