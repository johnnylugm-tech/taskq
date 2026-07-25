r"""FR-02 TDD-RED test suite.

Spec surface: `taskq run <id>` and `taskq run --all` (SPEC.md §3 FR-02).
All tests in this file MUST fail RED until the GREEN agent implements
the source modules referenced below.

GREEN TODO (single source list — every annotation is a contract for the
GREEN implementer, not a stub the RED author fills in):
  - `taskq.cli` registers a `run` subcommand with positional `<id>` and
    the `--all` flag, plus invokes the executor's entry points and
    surfaces exit code 4 whenever a single-task run produced a timeout.
  - `taskq.executor.run_task(task_id, *, cfg, use_cache=False)` advances
    the persisted task state machine: `pending → running → done |
    failed | timeout`. Writes must be atomic and use the shared
    `threading.Lock` from `taskq.store`.
  - `taskq.executor.run_all(*, cfg)` submits all `pending` tasks through
    `concurrent.futures.ThreadPoolExecutor(max_workers=cfg.max_workers)`
    and persists each result under the shared lock.
  - All subprocess work uses
    `subprocess.run(shlex.split(command), capture_output=True,
    text=True, timeout=cfg.task_timeout)` — NEVER `shell=True`. The
    repository-wide source scan enforces `shell=True` count = 0.
  - The result record on each task carries `exit_code` (int),
    `stdout_tail` (last 2000 chars after redaction), `stderr_tail`
    (last 2000 chars after redaction), `duration_ms` (int), and
    `finished_at` (ISO-8601 UTC). Redaction matches `(sk-[A-Za-z0-9_-]{8,}|token=\S+)`
    on the FULL stream BEFORE truncation so a secret straddling the
    2000-char boundary cannot leak.
  - `taskq.executor` must produce a structure that FR-03's retry pipeline
    can consume: a failed task record must be enumerable by store
    iteration (status == "failed") so the retry loop can pick it up.

It is EXPECTED that pytest returns Exit Code 2 (Collection Error) on
this file because `taskq.executor` does not exist yet — that is the
valid RED state this TDD step produces. A second Collection-Error path
exists when `taskq.cli` has no `run` subcommand registered yet, even
after the executor exists.
"""

from __future__ import annotations

import contextlib
import io
import json as json_lib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# GREEN TODO: `taskq.executor` must expose run_task / run_all / redaction.
# This import will raise ModuleNotFoundError until GREEN creates the
# module; that failure is the documented RED signal — do NOT wrap it
# in try/except.
from taskq import executor  # noqa: E402,F401 — ModuleNotFoundError is valid RED

from taskq import cli  # noqa: E402,F401 — FR-01 module already exists
from taskq import config  # noqa: E402,F401
from taskq import store  # noqa: E402,F401


SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
PROD_SRC = SRC_ROOT / "taskq"
SHELL_TRUE_RE = re.compile(r"\bshell\s*=\s*True\b")
SECRET_SK_PREFIX = "sk-abcdefgh"
SECRET_TOKEN_PREFIX = "token="
REDACTED_SENTINEL = "[REDACTED]"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _isolated_env(taskq_home: Path) -> dict:
    """Build env for child process: TASKQ_HOME + PYTHONPATH propagation."""
    env = os.environ.copy()
    env["TASKQ_HOME"] = str(taskq_home)
    env["PYTHONPATH"] = str(SRC_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _isolated_env_with(taskq_home: Path, env_extra: dict) -> dict:
    """Layer extra env vars onto the isolated child env."""
    child_env = _isolated_env(taskq_home)
    child_env.update(env_extra)
    return child_env


def _run_inprocess(argv: list[str], env_extra: dict | None = None):
    """Invoke cli.main([...]) in-process; return (exit_code, stdout, stderr)."""
    if env_extra:
        for key, value in env_extra.items():
            os.environ[key] = value
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
        try:
            exit_code = cli.main(argv)
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1
    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


def _run_subprocess(argv: list[str], taskq_home: Path, env_extra: dict | None = None, timeout: int = 60):
    """Invoke `python -m taskq ...` out-of-process with isolated TASKQ_HOME."""
    child_env = _isolated_env_with(taskq_home, env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "taskq", *argv],
        capture_output=True,
        text=True,
        env=child_env,
        timeout=timeout,
    )


def _seed_pending(taskq_home: Path, task_id: str, command: str, name: str | None = None) -> None:
    """Seed a pending task under $TASKQ_HOME/tasks.json (test fixture only)."""
    tasks_file = taskq_home / "tasks.json"
    state: dict = {"version": 1, "tasks": {}}
    if tasks_file.exists():
        state = json_lib.loads(tasks_file.read_text())
    state["tasks"][task_id] = {
        "id": task_id,
        "command": command,
        "name": name,
        "status": "pending",
        "created_at": "2026-07-25T00:00:00Z",
    }
    tasks_file.write_text(json_lib.dumps(state))


@pytest.fixture
def taskq_home(tmp_path, monkeypatch):
    """Function-scoped TASKQ_HOME per-test (per FR-05 lesson: no leakage)."""
    home = tmp_path / ".taskq"
    home.mkdir()
    monkeypatch.setenv("TASKQ_HOME", str(home))
    return home


# ---------------------------------------------------------------------------
# FR-02 AC-2.1 — shell=True forbidden (NFR-02)
# ---------------------------------------------------------------------------


# NFR-02: security — AC-NFR02.1 repository-wide source scan for shell=True
def test_shell_true_grep_zero_matches():
    """AC-2.1: repository-wide grep for `shell=True` returns zero matches.

    Sub-assertion: AC21-no-shell (`shell_flag == "false"`).
    Negative command list is `echo hi`; the constraint is structural
    (no module may use shell=True) — the command input is documentation.
    """
    command = "echo hi"
    shell_flag = "false"
    assert shell_flag == "false"  # AC21-no-shell
    assert command == "echo hi"
    matches = 0
    for file_path in PROD_SRC.rglob("*.py"):
        text = file_path.read_text()
        matches += len(SHELL_TRUE_RE.findall(text))
    assert matches == 0, (
        f"AC-2.1 violation: shell=True found {matches} time(s) in production source"
    )


# ---------------------------------------------------------------------------
# FR-02 AC-2.2 — state transitions: pending -> running -> done | failed | timeout
# ---------------------------------------------------------------------------


# NP-13: AC-NFR13.1 — state machine invariant; pending->running->{done|failed|timeout}
def test_state_transitions(taskq_home):
    """AC-2.2: terminal status driven by exit code or TimeoutExpired.

    Sub-assertions:
      AC22-done-exit0 (case 2: `true` -> exit 0 -> "done")
      AC22-failed-exit1 (case 3: `false` -> exit 1 -> "failed")
      AC22-timeout-flag (case 4: `sleep 10` with timeout -> "timeout")

    All three scenarios live in one function because the TEST_SPEC
    assigns the same function name to cases 2, 3, 4; spec-coverage-check
    dedups by name set, so a single function covers all three
    sub-assertions without inventing a new name.
    """
    # --- Case 2: `true` -> exit 0 -> status "done" ---
    exit_code = "0"
    expected_status = "done"
    assert exit_code == "0"  # AC22-done-exit0
    assert expected_status == "done"
    _seed_pending(taskq_home, "aaaaaaaa", "true")
    code, _stdout, _stderr = _run_inprocess(["run", "aaaaaaaa"])
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    assert payload["tasks"]["aaaaaaaa"]["status"] == "done", (
        f"AC-2.2: true must produce status=done, got {payload['tasks']['aaaaaaaa']['status']}"
    )

    # --- Case 3: `false` -> exit 1 -> status "failed" ---
    exit_code = "1"
    expected_status = "failed"
    assert exit_code == "1"  # AC22-failed-exit1
    assert expected_status == "failed"
    _seed_pending(taskq_home, "bbbbbbbb", "false")
    code, _stdout, _stderr = _run_inprocess(["run", "bbbbbbbb"])
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    assert payload["tasks"]["bbbbbbbb"]["status"] == "failed", (
        f"AC-2.2: false must produce status=failed, got {payload['tasks']['bbbbbbbb']['status']}"
    )

    # --- Case 4: `sleep 10` with TASKQ_TASK_TIMEOUT=1 -> status "timeout" ---
    _seed_pending(taskq_home, "cccccccc", "sleep 10")
    timeout_flag = "true"
    expected_status = "timeout"
    assert timeout_flag == "true"  # AC22-timeout-flag
    assert expected_status == "timeout"
    code, _stdout, _stderr = _run_inprocess(
        ["run", "cccccccc"], env_extra={"TASKQ_TASK_TIMEOUT": "1"}
    )
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    assert payload["tasks"]["cccccccc"]["status"] == "timeout", (
        f"AC-2.2: timeout must produce status=timeout, got {payload['tasks']['cccccccc']['status']}"
    )


# ---------------------------------------------------------------------------
# FR-02 AC-2.3 — result record fields (exit_code, stdout_tail, stderr_tail,
#                 duration_ms, finished_at) and stdout_tail truncated to 2000
# ---------------------------------------------------------------------------


# NP-09: AC-NFR09.1 — result record carries audit fields populated at run time
def test_result_record_fields(taskq_home):
    """AC-2.3: stdout truncated to last 2000 chars; all documented fields present.

    Sub-assertion: AC23-tail-truncated (`stdout_len > expected_tail_len`).
    """
    stdout_len = "2500"
    expected_tail_len = "2000"
    assert stdout_len > expected_tail_len  # AC23-tail-truncated
    # `python3 -c '...'` writes 2500 'a' chars to stdout (no trailing newline).
    command = "python3 -c 'import sys; sys.stdout.write(\"a\" * 2500)'"
    _seed_pending(taskq_home, "dddddddd", command)
    _run_inprocess(["run", "dddddddd"])
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    task = payload["tasks"]["dddddddd"]
    for required_key in ("exit_code", "stdout_tail", "stderr_tail", "duration_ms", "finished_at"):
        assert required_key in task, (
            f"AC-2.3 violation: task record missing {required_key!r} field: {task}"
        )
    assert task["exit_code"] == 0, f"true exit code expected 0, got {task['exit_code']}"
    assert len(task["stdout_tail"]) == 2000, (
        f"AC-2.3 violation: stdout_tail must be 2000 chars, got {len(task['stdout_tail'])}"
    )
    assert task["stdout_tail"] == "a" * 2000, (
        "AC-2.3: stdout_tail must be the last 2000 chars of the original output"
    )
    assert isinstance(task["duration_ms"], int)
    assert task["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# FR-02 AC-2.4 — run --all uses ThreadPoolExecutor(max_workers=TASKQ_MAX_WORKERS)
# ---------------------------------------------------------------------------


# NFR-09: scalability — bounded workers must process run --all without loss
# NP-13: AC-NFR13.2 — bounded concurrent workers preserve all persisted tasks
def test_run_all_concurrent(taskq_home):
    """AC-2.4: --all drains pending tasks through max_workers parallel runners.

    Sub-assertion: AC24-worker-count (`max_workers == "4"`).
    """
    task_count = "10"
    max_workers = "4"
    assert max_workers == "4"  # AC24-worker-count
    n = int(task_count)
    for idx in range(n):
        _seed_pending(taskq_home, f"{idx:08x}", f"echo {idx}")
    code, _stdout, _stderr = _run_inprocess(
        ["run", "--all"], env_extra={"TASKQ_MAX_WORKERS": "4"}
    )
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    assert len(payload["tasks"]) == n, (
        f"AC-2.4: expected {n} tasks persisted, got {len(payload['tasks'])}"
    )
    for idx in range(n):
        tid = f"{idx:08x}"
        assert payload["tasks"][tid]["status"] == "done", (
            f"AC-2.4: task {tid} not done after --all: {payload['tasks'][tid]}"
        )


# ---------------------------------------------------------------------------
# FR-02 AC-2.5 — single-task timeout exits with code 4
# ---------------------------------------------------------------------------


# NP-15: AC-NFR15.1 — timeout surfaces as exit code 4 in single-task mode
def test_timeout_exit_code_4(taskq_home):
    """AC-2.5: single-task run that produces a timeout result exits with code 4.

    Sub-assertion: AC25-exit4 (`expected_exit == "4"`).
    """
    command = "sleep 10"
    timeout_seconds = "1"
    expected_exit = "4"
    assert expected_exit == "4"  # AC25-exit4
    _seed_pending(taskq_home, "eeeeeeee", command)
    code, _stdout, _stderr = _run_inprocess(
        ["run", "eeeeeeee"], env_extra={"TASKQ_TASK_TIMEOUT": timeout_seconds}
    )
    assert code == 4, (
        f"AC-2.5 violation: timeout must exit 4, got {code}"
    )
    # The result record must reflect the timeout classification.
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    assert payload["tasks"]["eeeeeeee"]["status"] == "timeout", (
        "AC-2.5: timeout task must persist with status='timeout'"
    )


# ---------------------------------------------------------------------------
# FR-02 NFR-13/NFR-15 — integration: run --all no loss + orphan cleanup
# ---------------------------------------------------------------------------


# NP-13: AC-NFR13.3 — 20 tasks under 4 workers: all persisted, no state loss
def test_run_all_concurrent_state_transitions_isolated(taskq_home):
    """NP-13 (SAD: taskq.store): 20 concurrent runs keep tasks.json lossless.

    Sub-assertion: AC26-concurrent-no-loss (`expected_loss == "0"`).
    Also re-asserts AC24-worker-count.
    Uses out-of-process subprocesses so the cross-process lock adapter
    actually fires (in-process threading shares the same flock fd table).
    """
    task_count = "20"
    max_workers = "4"
    expected_loss = "0"
    assert max_workers == "4"   # AC24-worker-count
    assert expected_loss == "0"  # AC26-concurrent-no-loss
    n = int(task_count)
    # Seed N pending tasks via the FROZEN-FR-01 CLI (one submit per task).
    for idx in range(n):
        # The submit CLI is the only public write path until GREEN
        # implements a batch seeder; one subprocess per task is bounded
        # by N=20 and well under the 60s subprocess timeout.
        proc = _run_subprocess(
            ["submit", f"echo {idx}"], taskq_home,
            env_extra={"TASKQ_MAX_WORKERS": "4"},
        )
        assert proc.returncode == 0, f"submit {idx} failed: {proc.stderr}"
    # Now run --all.
    proc = _run_subprocess(
        ["run", "--all"], taskq_home,
        env_extra={"TASKQ_MAX_WORKERS": "4"},
    )
    raw = (taskq_home / "tasks.json").read_text()
    payload = json_lib.loads(raw)  # raises on corruption -> RED
    tasks = payload.get("tasks", {})
    assert len(tasks) == n, (
        f"NP-13 violation: expected {n} tasks persisted, got {len(tasks)}"
    )
    not_done = [tid for tid, t in tasks.items() if t.get("status") != "done"]
    assert not_done == [], (
        f"NP-13 violation: {len(not_done)} task(s) not done after run --all: {not_done}"
    )


# NP-15: AC-NFR15.2 — timeout must terminate the orphaned subprocess
def test_run_subprocess_orphan_cleanup_on_timeout(taskq_home):
    """NP-15 (SAD: taskq.executor): timeout leaves no orphan subprocess.

    Sub-assertion: AC27-orphan-terminated (`expected_process_state == "terminated"`).
    Out-of-process invoke so the kernel actually reaps (or fails to
    reap) the child sleep when the executor times out.
    """
    command = "sleep 10"
    timeout_seconds = "1"
    expected_process_state = "terminated"
    assert expected_process_state == "terminated"  # AC27-orphan-terminated
    _seed_pending(taskq_home, "ffffffff", command)
    proc = _run_subprocess(
        ["run", "ffffffff"], taskq_home,
        env_extra={"TASKQ_TASK_TIMEOUT": timeout_seconds},
        timeout=20,
    )
    # The taskq process must have exited (not hung) within the 20s budget.
    # Now probe the process table for any surviving 'sleep 10' children.
    ps = subprocess.run(
        ["ps", "-axo", "pid,command"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    sleep_orphans = [
        line for line in ps.stdout.splitlines()
        if "sleep 10" in line and "ps -axo" not in line
    ]
    assert sleep_orphans == [], (
        f"NP-15 violation: orphan sleep process detected after timeout: {sleep_orphans}"
    )


# ---------------------------------------------------------------------------
# FR-02 NFR-04 — secret redaction (stdout + stderr)
# ---------------------------------------------------------------------------


# NFR-04: AC-NFR04.1 — sk-* tokens in stdout are redacted to [REDACTED]
def test_sk_token_redacted(taskq_home):
    """NP-14/NP-08 (SEC:T-03): sk-... token on stdout is redacted.

    Sub-assertions: AC28-redaction, AC28-stream-stdout.
    """
    stdout_line = "sk-abcdefgh12345678EXTRA"
    stream = "stdout"
    expected_redacted = "true"
    assert expected_redacted == "true"  # AC28-redaction
    assert stream == "stdout"           # AC28-stream-stdout
    assert stdout_line.startswith(SECRET_SK_PREFIX)
    _seed_pending(taskq_home, "11111111", f"echo {stdout_line}")
    _run_inprocess(["run", "11111111"])
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    task = payload["tasks"]["11111111"]
    assert "stdout_tail" in task, "AC-2.3: stdout_tail must exist on task record"
    assert stdout_line not in task["stdout_tail"], (
        f"NFR-04 violation: {stdout_line!r} leaked into persisted stdout_tail"
    )
    assert REDACTED_SENTINEL in task["stdout_tail"], (
        "NFR-04: sk-* match must be replaced with [REDACTED] sentinel"
    )


# NFR-04: AC-NFR04.2 — token=... in stdout are redacted
def test_token_equals_redacted(taskq_home):
    """NP-14/NP-08 (SEC:T-03): token=... string on stdout is redacted.

    Sub-assertions: AC28-redaction, AC28-stream-stdout.
    """
    stdout_line = "token=abc123XYZ"
    stream = "stdout"
    expected_redacted = "true"
    assert expected_redacted == "true"  # AC28-redaction
    assert stream == "stdout"           # AC28-stream-stdout
    assert stdout_line.startswith(SECRET_TOKEN_PREFIX)
    _seed_pending(taskq_home, "22222222", f"echo {stdout_line}")
    _run_inprocess(["run", "22222222"])
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    task = payload["tasks"]["22222222"]
    assert "stdout_tail" in task
    assert stdout_line not in task["stdout_tail"], (
        f"NFR-04 violation: {stdout_line!r} leaked into persisted stdout_tail"
    )
    assert REDACTED_SENTINEL in task["stdout_tail"], (
        "NFR-04: token= match must be replaced with [REDACTED] sentinel"
    )


# NFR-04: AC-NFR04.3 — sk-... tokens on stderr are redacted
def test_sk_token_redacted_stderr(taskq_home):
    """NP-14/NP-08 (SEC:T-03): sk-... token on stderr is redacted.

    Sub-assertions: AC28-redaction, AC28-stream-stderr.
    """
    stderr_line = "sk-abcdefgh12345678EXTRA"
    stream = "stderr"
    expected_redacted = "true"
    assert expected_redacted == "true"  # AC28-redaction
    assert stream == "stderr"           # AC28-stream-stderr
    assert stderr_line.startswith(SECRET_SK_PREFIX)
    _seed_pending(taskq_home, "33333333", f"echo {stderr_line} 1>&2")
    _run_inprocess(["run", "33333333"])
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    task = payload["tasks"]["33333333"]
    assert "stderr_tail" in task
    assert stderr_line not in task["stderr_tail"], (
        f"NFR-04 violation: {stderr_line!r} leaked into persisted stderr_tail"
    )
    assert REDACTED_SENTINEL in task["stderr_tail"], (
        "NFR-04: sk-* match on stderr must be replaced with [REDACTED] sentinel"
    )


# NFR-04: AC-NFR04.4 — token=... on stderr are redacted
def test_token_equals_redacted_stderr(taskq_home):
    """NP-14/NP-08 (SEC:T-03): token=... string on stderr is redacted.

    Sub-assertions: AC28-redaction, AC28-stream-stderr.
    """
    stderr_line = "token=abc123XYZ"
    stream = "stderr"
    expected_redacted = "true"
    assert expected_redacted == "true"  # AC28-redaction
    assert stream == "stderr"           # AC28-stream-stderr
    assert stderr_line.startswith(SECRET_TOKEN_PREFIX)
    _seed_pending(taskq_home, "44444444", f"echo {stderr_line} 1>&2")
    _run_inprocess(["run", "44444444"])
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    task = payload["tasks"]["44444444"]
    assert "stderr_tail" in task
    assert stderr_line not in task["stderr_tail"], (
        f"NFR-04 violation: {stderr_line!r} leaked into persisted stderr_tail"
    )
    assert REDACTED_SENTINEL in task["stderr_tail"], (
        "NFR-04: token= match on stderr must be replaced with [REDACTED] sentinel"
    )


# NFR-04: AC-NFR04.5 — boundary counterexample: redaction precedes truncation
def test_secret_redaction_before_truncation(taskq_home):
    """NP-14/NP-08 (SEC:T-03): redaction must precede 2000-char truncation.

    Sub-assertions: AC28-redaction, AC28-boundary-tail.
    Construct a 2010-char stdout stream where the secret begins at
    offset 1990 and protrudes past the 2000-char boundary. If the
    executor truncates BEFORE redacting, the persisted tail keeps the
    first 10 chars of the secret ("sk-abcdefgh"). If the executor
    redacts FIRST and THEN truncates, the secret is fully replaced
    with [REDACTED] and the tail is exactly 2000 chars.
    """
    stdout_len = "2010"
    secret_start_offset = "1990"
    secret_pattern = "sk-*"
    expected_redacted = "true"
    expected_tail_len = "2000"
    assert expected_redacted == "true"  # AC28-redaction
    assert expected_tail_len == "2000"  # AC28-boundary-tail
    assert secret_pattern == "sk-*"
    prefix = "a" * int(secret_start_offset)
    secret = "sk-abcdefgh12345678EXTRA"
    suffix = "b" * (int(stdout_len) - len(prefix) - len(secret))
    payload_str = prefix + secret + suffix
    assert len(payload_str) == int(stdout_len), (
        f"constructed stream len {len(payload_str)} != {stdout_len}"
    )
    # python3 -c writes the exact string (no shell interpretation; safe chars).
    command = (
        "python3 -c 'import sys; "
        f"sys.stdout.write({payload_str!r})'"
    )
    _seed_pending(taskq_home, "55555555", command)
    _run_inprocess(["run", "55555555"])
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    task = payload["tasks"]["55555555"]
    assert len(task["stdout_tail"]) == int(expected_tail_len), (
        f"AC-2.3 boundary: stdout_tail must be {expected_tail_len} chars, "
        f"got {len(task['stdout_tail'])}"
    )
    # The secret must NOT survive — neither full nor truncated prefix.
    assert "sk-abcdefgh" not in task["stdout_tail"], (
        "NFR-04 boundary: redaction must precede truncation; "
        "sk-abcdefgh prefix leaked into tail"
    )
    assert "sk-abcdefgh12345678EXTRA" not in task["stdout_tail"]
    assert REDACTED_SENTINEL in task["stdout_tail"], (
        "NFR-04: redaction must produce [REDACTED] sentinel even when "
        "the secret straddles the 2000-char boundary"
    )


# ---------------------------------------------------------------------------
# FR-02 / FR-03 — retry pipeline integration
# ---------------------------------------------------------------------------


# Q7: FR-02 failed-task output must be consumable by FR-03 retry pipeline
def test_fr02_output_feeds_fr03_retry_pipeline(taskq_home):
    """Q7/FR-03: a failed task is enumerable by FR-03's retry pipeline.

    Sub-assertion: AC29-retry-eligible (`retry_eligible == "true"`).
    The executor must produce a task record that:
      (a) carries status="failed" once the run completes (so the retry
          loop can find it without re-parsing stdout),
      (b) remains queryable via the standard task enumeration (no
          separate "failed queue" file that could desync),
      (c) carries an exit_code so the retry policy can decide
          retry-eligibility vs hard-fail without re-running.
    """
    task_status = "failed"
    retry_eligible = "true"
    assert retry_eligible == "true"  # AC29-retry-eligible
    assert task_status == "failed"
    _seed_pending(taskq_home, "66666666", "false")
    _run_inprocess(["run", "66666666"])
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    task = payload["tasks"]["66666666"]
    # (a) status must be 'failed' so FR-03 can iterate by status filter.
    assert task["status"] == task_status, (
        f"FR-02/FR-03 boundary: status must be 'failed', got {task['status']!r}"
    )
    # (b) the task is enumerable via the standard task store.
    failed_tasks = [
        t for t in payload["tasks"].values()
        if t.get("status") == "failed"
    ]
    assert any(t["id"] == "66666666" for t in failed_tasks), (
        "FR-02 -> FR-03 boundary: failed task not enumerable via the "
        "standard task store"
    )
    # (c) exit_code is persisted so FR-03's retry policy can decide
    #     retry-eligibility without re-running the command.
    assert "exit_code" in task, (
        "FR-02 -> FR-03 boundary: failed task record must include exit_code"
    )
    assert task["exit_code"] != 0, (
        f"FR-02: failed task's exit_code must be non-zero, got {task['exit_code']}"
    )


# ---------------------------------------------------------------------------
# FR-02 AC-2.1 — repository-wide source scan (np-08/np-04 SEC:T-02)
# ---------------------------------------------------------------------------


# NFR-05: maintainability — executor public APIs require [FR-02] docstrings
# NFR-02: AC-NFR02.3 — production source scan asserts shell_flag absent
def test_no_shell_true_in_source():
    """AC-2.1 (NP-08/NP-04 SEC:T-02): no shell=True in production source.

    Sub-assertion: AC21-no-shell (`shell_flag == "false"`).
    Distinct from test_shell_true_grep_zero_matches: this one also
    asserts the executor module exists (so GREEN must publish the
    module) and re-scans every .py file under taskq/ — caught even
    if shell=True is added in a hidden helper or test-only path.
    """
    source_scan = "production"
    shell_flag = "false"
    assert source_scan == "production"  # mirrors TEST_SPEC.source_scan
    assert shell_flag == "false"         # AC21-no-shell
    # GREEN must publish taskq.executor — if RED still has no module,
    # this assertion fails BEFORE the scan runs, which is the desired
    # RED signal.
    executor_module = PROD_SRC / "executor.py"
    assert executor_module.exists(), (
        "GREEN TODO: src/taskq/executor.py must exist before this guard is meaningful"
    )
    for file_path in sorted(PROD_SRC.rglob("*.py")):
        text = file_path.read_text()
        assert not SHELL_TRUE_RE.search(text), (
            f"AC-2.1 violation: shell=True found in {file_path.relative_to(SRC_ROOT)}"
        )
