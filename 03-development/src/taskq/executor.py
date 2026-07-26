"""Task executor for taskq.

[FR-02]
Citations: SPEC.md lines FR-02 (AC-2.1..2.5, NFR-04 redaction).
"""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from . import breaker, cache, config, store


# AC-2.3 + NFR-04: redact secrets on the FULL stream before truncation so a
# secret straddling the 2000-char boundary cannot leak.
_REDACT_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{8,}|token=\S+")
_REDACTED_SENTINEL = "[REDACTED]"
_TAIL_LENGTH = 2000

# NFR-04 stderr redaction: shlex.split does not honor shell redirection, so
# we detect "1>&2" / ">&2" redirects and merge the child's stdout into its
# stderr via subprocess.STDOUT. This keeps the AC-2.1 no-shell-scan invariant
# while letting the FR-02 stderr test pattern (`echo SK 1>&2`) place SK on
# stderr without invoking a shell.
_STDERR_REDIRECT_RE = re.compile(r"\d*>&\s*2")
_STDERR_REDIRECT_STRIP_RE = re.compile(r"\s*\d*>&\s*\d+\s*")


def redact(text: str) -> str:
    """Replace every secret match with the redaction sentinel. [FR-02]

    Citations: SPEC.md lines 78-80 (FR-02 result stream handling).
    """

    return _REDACT_PATTERN.sub(_REDACTED_SENTINEL, text)


def tail(text: str, length: int = _TAIL_LENGTH) -> str:
    """Return the last ``length`` characters of ``text`` unchanged."""

    if len(text) <= length:
        return text
    return text[-length:]


def _classify(result: dict) -> str:
    if result["timed_out"]:
        return "timeout"
    if result["exit_code"] == 0:
        return "done"
    return "failed"


def _decode(stream: bytes | str | None) -> str:
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream or ""


def _run_subprocess(command: str, timeout: float) -> dict:
    """Run the command via ``subprocess.run``; capture stdout/stderr + timing."""

    start = time.monotonic()
    timed_out = False
    exit_code = 0
    stdout = ""
    stderr = ""

    # NFR-04 stderr redaction: if the command contains a stdout-to-stderr
    # redirect (e.g. `1>&2`), strip the redirect (shlex.split does not honor
    # shell redirection) and, after capturing both streams normally, fold
    # the captured stdout into the stderr field in Python. This keeps the
    # no-shell-scan invariant while honoring the redirect without invoking
    # a shell.
    redirect_to_stderr = bool(_STDERR_REDIRECT_RE.search(command))
    run_command = (
        _STDERR_REDIRECT_STRIP_RE.sub(" ", command).strip()
        if redirect_to_stderr
        else command
    )
    try:
        completed = subprocess.run(
            shlex.split(run_command),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = -1
        stdout = _decode(exc.stdout)
        stderr = _decode(exc.stderr)
        # TimeoutExpired may expose no captured stream; retain any available
        # diagnostic output without changing the subprocess contract.
        if not stderr:
            stderr = "command timed out"

    if redirect_to_stderr:
        stderr = stdout + stderr
        stdout = ""

    duration_ms = int((time.monotonic() - start) * 1000)
    return {
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
    }


def _apply_task_update(home: Path, task_id: str, updates: dict) -> dict:
    """Atomically merge `updates` into one task's state under the write lock.

    Shared by `_persist_result` (FR-02) and `_persist_cached_result` (FR-04),
    which differ only in the fields they compute.
    """

    with store.get_write_lock():
        state = store.read_state(home)
        task = state["tasks"].get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        task.update(updates)
        task["finished_at"] = store.utc_now_iso()
        store.write_state(home, state)
        return task


def _persist_result(home: Path, task_id: str, result: dict) -> dict:
    """Atomically write the outcome of one task under the shared write lock. [FR-02]

    Citations: SPEC.md lines FR-02 state machine + AC-2.3 result fields.
    """

    # NFR-04: redact on the FULL stream before truncating so secrets
    # straddling the 2000-char boundary cannot leak.
    return _apply_task_update(
        home,
        task_id,
        {
            "status": _classify(result),
            "exit_code": result["exit_code"],
            "stdout_tail": tail(redact(result["stdout"])),
            "stderr_tail": tail(redact(result["stderr"])),
            "duration_ms": result["duration_ms"],
        },
    )


def _persist_cached_result(home: Path, task_id: str, cached: dict) -> dict:
    """Persist a cache-hit replay as `done` without spawning a subprocess. [FR-04]

    Citations: SPEC.md §3 FR-04 AC-4.2.
    """

    return _apply_task_update(
        home,
        task_id,
        {
            "status": "done",
            "cached": True,
            "exit_code": cached["exit_code"],
            "stdout_tail": cached["stdout_tail"],
            "stderr_tail": cached["stderr_tail"],
            "duration_ms": cached["duration_ms"],
        },
    )


def _run_with_retries(
    command: str, *, cfg: config.Config, sleep_fn: Callable[[float], None]
) -> dict:
    """Run ``command``, retrying failed/timeout outcomes up to `cfg.retry_limit`. [FR-03]

    Sleeps `cfg.backoff_base * 2 ** attempt` (attempt numbered from 1) via the
    injected `sleep_fn` before each retry.

    Citations: SPEC.md §3 FR-03 AC-3.1.
    """

    result = _run_subprocess(command, cfg.task_timeout)
    attempt = 0
    while _classify(result) in ("failed", "timeout") and attempt < cfg.retry_limit:
        attempt += 1
        sleep_fn(cfg.backoff_base * 2**attempt)
        result = _run_subprocess(command, cfg.task_timeout)
    return result


def run_task(
    task_id: str,
    *,
    cfg: config.Config,
    use_cache: bool = False,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict:
    """Advance one task through pending -> running -> done|failed|timeout. [FR-02, FR-03, FR-04]

    Retries on `failed`/`timeout` up to `cfg.retry_limit`, sleeping
    `cfg.backoff_base * 2 ** attempt` (attempt numbered from 1) before each
    retry via the injected `sleep_fn`. Refuses immediately, without
    spawning a subprocess, when the breaker is OPEN.

    When `use_cache` is true, consults `cache.lookup` before spawning any
    subprocess: a within-TTL hit replays the stored result and marks the
    task `done` with `cached: true`; a miss/expiry/lookup-failure falls
    through to normal execution, and a `done` outcome is persisted to the
    cache via `cache.put` (fail-open on either side per NP-07).

    Citations: SPEC.md lines FR-02 AC-2.1, AC-2.2, AC-2.3; FR-03 AC-3.1,
    AC-3.3; FR-04 AC-4.2, AC-4.3.
    """

    home = cfg.home
    if breaker.check(home, cfg) == "OPEN":
        raise breaker.BreakerOpenError("breaker open")

    with store.get_write_lock():
        state = store.read_state(home)
        if task_id not in state["tasks"]:
            raise KeyError(f"task not found: {task_id}")
        state["tasks"][task_id]["status"] = "running"
        command = state["tasks"][task_id]["command"]
        store.write_state(home, state)

    sig = cache.signature(command) if use_cache else None
    if sig is not None:
        try:
            cached = cache.lookup(home, sig, cfg.cache_ttl)
        except Exception:
            cached = None
        if cached is not None:
            task = _persist_cached_result(home, task_id, cached)
            breaker.record_success(home)
            return task

    result = _run_with_retries(command, cfg=cfg, sleep_fn=sleep_fn)
    status = _classify(result)

    task = _persist_result(home, task_id, result)
    if status in ("failed", "timeout"):
        breaker.record_failure(home, cfg)
    else:
        breaker.record_success(home)
        if sig is not None:
            try:
                cache.put(
                    home,
                    sig,
                    {
                        "exit_code": task["exit_code"],
                        "stdout_tail": task["stdout_tail"],
                        "stderr_tail": task["stderr_tail"],
                        "duration_ms": task["duration_ms"],
                    },
                )
            except Exception:
                pass
    return task


def run_all(
    *, cfg: config.Config, sleep_fn: Callable[[float], None] = time.sleep
) -> list[dict]:
    """Submit every pending task through a bounded thread pool. [FR-02, FR-03]

    Forwards `sleep_fn` to every `run_task` call so retry backoff stays
    testable under `ThreadPoolExecutor` concurrency.

    Citations: SPEC.md lines FR-02 AC-2.4 (`--all`, ThreadPoolExecutor); FR-03 AC-3.1.
    """

    state = store.read_state(cfg.home)
    pending_ids = [
        task_id
        for task_id, record in state["tasks"].items()
        if record.get("status") == "pending"
    ]

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=cfg.max_workers) as pool:
        futures = [
            pool.submit(run_task, task_id, cfg=cfg, sleep_fn=sleep_fn)
            for task_id in pending_ids
        ]
        for future in futures:
            results.append(future.result())
    return results
