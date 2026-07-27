"""Small helpers shared by `cli.py` subcommands.

[FR-01, FR-05]
Citations: SPEC.md lines FR-01 validation rules (AC-1.1..1.4); FR-05
`--json` contract (AC-5.1, AC-5.5).
"""

# pragma: no error-handling

from __future__ import annotations

import json
import sys

# SPEC.md §3 FR-01: seven injection characters must never appear in a
# submitted command string.
INJECTION_CHARACTERS = frozenset(";|&$><`")

# Exit code returned for any validation failure (FR-01 invariant).
EXIT_VALIDATION_ERROR = 2

# Exit code returned for a single-task run that produced a timeout (FR-02 AC-2.5).
EXIT_TIMEOUT = 4

# Exit code returned when the breaker is OPEN and refuses admission (FR-03 AC-3.3).
EXIT_BREAKER_OPEN = 3


def validation_error(message: str) -> int:
    """Surface a validation rejection on stderr and return the FR-01 exit code. [FR-01]"""

    print(message, file=sys.stderr)
    return EXIT_VALIDATION_ERROR


def print_json(payload: object) -> None:
    """Write `payload` to stdout as single-line JSON (FR-05 `--json` contract). [FR-05]"""

    sys.stdout.write(json.dumps(payload, separators=(",", ":")))