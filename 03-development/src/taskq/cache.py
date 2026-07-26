"""Result TTL cache for `taskq run <id> --cached`.

[FR-04]
Citations: SPEC.md §3 FR-04 (AC-4.1..4.4); SPEC.md §5.2 cache.json schema.
"""

from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import fcntl

from . import store

_write_lock = threading.Lock()

_CACHE_FILE = "cache.json"
_LOCK_FILE = "cache.lock"


def signature(command: str) -> str:
    """Return the sha256 hex digest of `command`. [FR-04]

    Citations: SPEC.md §3 FR-04 AC-4.1.
    """

    return hashlib.sha256(command.encode("utf-8")).hexdigest()


def _empty_state() -> dict:
    return {"version": 1, "entries": {}}


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


def lookup(home: Path, sig: str, ttl_seconds: float) -> dict | None:
    """Return the cached result for `sig` if present and within TTL. [FR-04]

    Fails open (returns ``None``) on a cache miss, an expired entry, a
    missing file, or a corrupted/unreadable file, so a cache-subsystem
    fault never blocks task execution (NP-07 dependency-fault tolerance).

    Citations: SPEC.md §3 FR-04 AC-4.2, AC-4.3.
    """

    try:
        with _locked(home):
            path = home / _CACHE_FILE
            if not path.exists():
                return None
            state = json.loads(path.read_text(encoding="utf-8"))
        entries = state.get("entries", {})
        entry = entries.get(sig)
        if entry is None:
            return None
        cached_at = datetime.fromisoformat(entry["cached_at"].replace("Z", "+00:00"))
        age_seconds = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age_seconds >= ttl_seconds:
            return None
        return entry
    except Exception:
        return None


def put(home: Path, sig: str, result: dict) -> None:
    """Atomically merge one entry into `$TASKQ_HOME/cache.json`. [FR-04]

    Reads, merges, and writes the whole read-modify-write cycle under one
    exclusive lock so concurrent callers cannot interleave (AC-4.4). A
    corrupted existing file is treated as an empty cache rather than
    raising, so a transient outage self-heals on the next successful write.

    Citations: SPEC.md §3 FR-04 AC-4.4; §5.2 cache.json schema.
    """

    with _locked(home):
        path = home / _CACHE_FILE
        try:
            state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else _empty_state()
            if not isinstance(state, dict) or not isinstance(state.get("entries"), dict):
                state = _empty_state()
        except Exception:
            state = _empty_state()
        entry = dict(result)
        entry["cached_at"] = store.utc_now_iso()
        state["version"] = 1
        state["entries"][sig] = entry
        store._write_unlocked(path, state)
