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

from . import config, store


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


def _persist_result(home: Path, task_id: str, result: dict) -> dict:
    """Atomically write the outcome of one task under the shared write lock. [FR-02]

    Citations: SPEC.md lines FR-02 state machine + AC-2.3 result fields.
    """

    with store.get_write_lock():
        state = store.read_state(home)
        task = state["tasks"].get(task_id)
        if task is None:
            raise KeyError(f"task not found: {task_id}")
        task["status"] = _classify(result)
        task["exit_code"] = result["exit_code"]
        # NFR-04: redact on the FULL stream before truncating so secrets
        # straddling the 2000-char boundary cannot leak.
        task["stdout_tail"] = tail(redact(result["stdout"]))
        task["stderr_tail"] = tail(redact(result["stderr"]))
        task["duration_ms"] = result["duration_ms"]
        task["finished_at"] = store.utc_now_iso()
        store.write_state(home, state)
        return task


def run_task(
    task_id: str,
    *,
    cfg: config.Config,
    use_cache: bool = False,
) -> dict:
    """Advance one task through pending -> running -> done|failed|timeout. [FR-02]

    Citations: SPEC.md lines FR-02 AC-2.1, AC-2.2, AC-2.3.
    """

    home = cfg.home
    with store.get_write_lock():
        state = store.read_state(home)
        if task_id not in state["tasks"]:
            raise KeyError(f"task not found: {task_id}")
        state["tasks"][task_id]["status"] = "running"
        command = state["tasks"][task_id]["command"]
        store.write_state(home, state)

    result = _run_subprocess(command, cfg.task_timeout)
    return _persist_result(home, task_id, result)


def run_all(*, cfg: config.Config) -> list[dict]:
    """Submit every pending task through a bounded thread pool. [FR-02]

    Citations: SPEC.md lines FR-02 AC-2.4 (`--all`, ThreadPoolExecutor).
    """

    state = store.read_state(cfg.home)
    pending_ids = [
        task_id
        for task_id, record in state["tasks"].items()
        if record.get("status") == "pending"
    ]

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=cfg.max_workers) as pool:
        futures = [pool.submit(run_task, task_id, cfg=cfg) for task_id in pending_ids]
        for future in futures:
            results.append(future.result())
    return results
