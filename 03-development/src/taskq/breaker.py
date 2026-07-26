"""Circuit breaker state machine for `taskq run`.

[FR-03]
Citations: SPEC.md §3 FR-03 (AC-3.2, AC-3.3, AC-3.4, AC-3.5).
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import fcntl

from . import config, store

_write_lock = threading.Lock()

_BREAKER_FILE = "breaker.json"
_LOCK_FILE = "breaker.lock"


class BreakerOpenError(Exception):
    """Signal that the breaker is OPEN and admission was refused. [FR-03]

    Citations: SPEC.md §3 FR-03 "OPEN 期間" (AC-3.3).
    """


def _default_state() -> dict:
    return {"version": 1, "state": "CLOSED", "failure_count": 0, "opened_at": None}


@contextmanager
def _locked(home: Path) -> Iterator[None]:
    home.mkdir(parents=True, exist_ok=True)
    lock_path = home / _LOCK_FILE
    with _write_lock, lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load(home: Path) -> dict:
    """Read `$TASKQ_HOME/breaker.json`, defaulting to CLOSED. [FR-03]

    Citations: SPEC.md §3 FR-03 "狀態持久化" (AC-3.5).
    """

    path = home / _BREAKER_FILE
    if not path.exists():
        return _default_state()
    with path.open(encoding="utf-8") as source:
        try:
            return json.load(source)
        except (OSError, json.JSONDecodeError):  # pragma: no cover - re-raise passthrough
            raise


def save(home: Path, state: dict) -> None:
    """Atomically persist breaker state via `taskq.store`'s tmp+replace primitive. [FR-03]

    Citations: SPEC.md §3 FR-03 "狀態持久化" (AC-3.5).
    """

    home.mkdir(parents=True, exist_ok=True)
    try:
        store._write_unlocked(home / _BREAKER_FILE, state)
    except OSError:  # pragma: no cover - re-raise passthrough
        raise


def reset(home: Path) -> dict:
    """Persist the default CLOSED state under the breaker's flock. [FR-05]

    Public helper so callers outside `taskq.breaker` (e.g. `clear_command`)
    cannot bypass the lock by calling `save` directly — routes every reset
    through `breaker._locked`.
    """
    with _locked(home):
        default = _default_state()
        save(home, default)
        return default


def record_failure(home: Path, cfg: config.Config) -> dict:
    """Increment the consecutive final-failure counter; open on threshold. [FR-03]

    Citations: SPEC.md §3 FR-03 "斷路器" (AC-3.2).
    """

    with _locked(home):
        state = load(home)
        state["failure_count"] += 1
        if state["failure_count"] >= cfg.breaker_threshold:
            state["state"] = "OPEN"
            state["opened_at"] = store.utc_now_iso()
        save(home, state)
        return state


def record_success(home: Path) -> dict:
    """Reset the breaker to CLOSED with a zeroed failure counter. [FR-03]

    Citations: SPEC.md §3 FR-03 "HALF_OPEN" (AC-3.4).
    """

    with _locked(home):
        state = _default_state()
        save(home, state)
        return state


def _seconds_since(iso_timestamp: str) -> float:
    moment = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - moment).total_seconds()


def check(home: Path, cfg: config.Config) -> str:
    """Return the effective admission state: CLOSED, OPEN, or HALF_OPEN. [FR-03, FR-05]

    Re-validates a persisted OPEN state against the *current* configured
    `cfg.breaker_threshold`: a reconfiguration that raises the threshold
    above the recorded `failure_count` retires a stale OPEN rather than
    admission staying refused forever (FR-05 AC-5.4 single-task timeout
    case, which reconfigures the threshold mid-run to exercise a fresh
    admission after an earlier OPEN).

    Citations: SPEC.md §3 FR-03 "HALF_OPEN" (AC-3.4); §3 FR-05 AC-5.4.
    """

    state = load(home)
    if state["state"] != "OPEN":
        return state["state"]
    if state.get("failure_count", 0) < cfg.breaker_threshold:
        return "CLOSED"
    opened_at = state.get("opened_at")
    if opened_at is None:
        return "OPEN"
    if _seconds_since(opened_at) >= cfg.breaker_cooldown:
        return "HALF_OPEN"
    return "OPEN"
