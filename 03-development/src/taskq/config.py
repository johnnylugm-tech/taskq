"""Runtime configuration for taskq.

[FR-01]
Citations: SPEC.md lines 138-151.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    """Environment-backed taskq settings. [FR-01]

    Citations: SPEC.md lines 138-151.
    """

    home: Path
    max_workers: int
    task_timeout: float
    retry_limit: int
    backoff_base: float
    breaker_threshold: int
    breaker_cooldown: float
    cache_ttl: float


def load() -> Config:
    """Read all eight documented ``TASKQ_*`` settings. [FR-01]

    Citations: SPEC.md lines 138-151.
    """

    return Config(
        home=Path(os.environ.get("TASKQ_HOME", ".taskq")),
        max_workers=int(os.environ.get("TASKQ_MAX_WORKERS", "4")),
        task_timeout=float(os.environ.get("TASKQ_TASK_TIMEOUT", "10.0")),
        retry_limit=int(os.environ.get("TASKQ_RETRY_LIMIT", "2")),
        backoff_base=float(os.environ.get("TASKQ_BACKOFF_BASE", "0.1")),
        breaker_threshold=int(os.environ.get("TASKQ_BREAKER_THRESHOLD", "3")),
        breaker_cooldown=float(os.environ.get("TASKQ_BREAKER_COOLDOWN", "5.0")),
        cache_ttl=float(os.environ.get("TASKQ_CACHE_TTL", "3600")),
    )
