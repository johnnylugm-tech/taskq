"""Executable ``python -m taskq`` entry point.

[FR-01]
Citations: SPEC.md lines 102-115.
"""

from .cli import main

raise SystemExit(main())
