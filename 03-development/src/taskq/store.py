"""Atomic JSON persistence for submitted tasks.

[FR-01]
Citations: SPEC.md lines 68-72, 125, 130, 153-159.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


# Shared in-process write lock; complements fcntl.flock for threads spawned
# inside a single Python process (e.g. ThreadPoolExecutor in FR-02).
_write_lock = threading.Lock()


def get_write_lock() -> threading.Lock:
    """Return the shared in-process write lock for the task store. [FR-02]

    Citations: SPEC.md lines FR-02 `--all` concurrency invariant.
    """

    return _write_lock


def _empty_state() -> dict:
    return {"version": 1, "tasks": {}}


@contextmanager
def _locked(home: Path, exclusive: bool) -> Iterator[None]:
    home.mkdir(parents=True, exist_ok=True)
    lock_path = home / "tasks.lock"
    with lock_path.open("a+") as lock_file:
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(lock_file.fileno(), operation)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_unlocked(path: Path) -> dict:
    if not path.exists():
        return _empty_state()
    with path.open(encoding="utf-8") as source:
        state = json.load(source)
    if state.get("version") != 1 or not isinstance(state.get("tasks"), dict):
        raise ValueError("unsupported tasks store schema")
    return state


def _write_unlocked(path: Path, state: dict) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".tasks.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump(state, target, separators=(",", ":"))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def read_state(home: Path) -> dict:
    """Return the current task state under a shared process lock. [FR-01]

    Citations: SPEC.md lines 130, 153-159.
    """

    with _locked(home, exclusive=False):
        return _read_unlocked(home / "tasks.json")


def write_state(home: Path, state: dict) -> None:
    """Atomically replace task state under an exclusive lock. [FR-01]

    Citations: SPEC.md lines 47-49, 125, 130.
    """

    with _locked(home, exclusive=True):
        _write_unlocked(home / "tasks.json", state)


def _new_pending_record(command: str, name: str | None) -> dict:
    """Build a fresh pending task record with a unique 8-hex id and UTC timestamp.

    Citations: SPEC.md lines 68-72.
    """

    return {
        "id": uuid.uuid4().hex[:8],
        "command": command,
        "name": name,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def add_task(home: Path, command: str, name: str | None = None) -> dict:
    """Create and atomically persist a pending task record. [FR-01]

    Citations: SPEC.md lines 68-72, 125, 130.
    """

    with _locked(home, exclusive=True):
        path = home / "tasks.json"
        state = _read_unlocked(path)
        record = _new_pending_record(command, name)
        state["tasks"][record["id"]] = record
        _write_unlocked(path, state)
        return record
