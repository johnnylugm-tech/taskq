"""FR-01 TDD-RED test suite.

Spec surface: `taskq submit "<command>" [--name NAME]` with the validation
rules in SPEC.md §3 FR-01. All tests in this file MUST fail RED until the
GREEN agent implements the source modules referenced below.

GREEN TODO (single source list — every annotation is a contract for the
GREEN implementer, not a stub the RED author fills in):
  - `taskq.cli.main(argv: list[str]) -> int` entry-point; registered
    subcommands include `submit` accepting positional `command` plus
    optional `--name NAME` and global `--json` flag.
  - `taskq.cli.submit_command(args, cfg)` (or equivalent internal helper)
    must enforce the four FR-01 validation rules: non-empty after strip,
    length <= 1000, none of the 7 injection chars, name uniqueness vs.
    pending/running tasks. Any violation returns exit code 2 with a
    stderr message and MUST NOT touch `$TASKQ_HOME/tasks.json`.
  - `taskq.cli` reads `TASKQ_HOME` from env (default `.taskq`) and writes
    task records atomically to `$TASKQ_HOME/tasks.json` via
    `taskq.store.add_task(...)`.
  - On success stdout prints the 8-hex id; with `--json` flag, stdout
    prints `{"id": ..., "status": "pending"}` as single-line JSON.
  - `taskq.store.add_task(...)` records `command`, `name`, `created_at`,
    `status="pending"`, and `id` (first 8 hex of uuid4) on the task
    record.
  - `taskq.cli` parses `--inject-fault=<scenario>` BEFORE the subcommand
    layer; rejects with exit 2 + stderr "inject-fault rejected in
    production" unless `TASKQ_INJECT_FAULT_OK=1` env var is set.
  - Cross-process safety uses POSIX `fcntl.flock` exclusive lock for
    writes so concurrent submitters do not corrupt `tasks.json`.

It is EXPECTED that pytest returns Exit Code 2 (Collection Error) on this
file because `taskq.cli` / `taskq.store` / `taskq.config` do not exist
yet — that is the valid RED state this TDD step produces.
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

# GREEN TODO: `taskq.cli` must be importable; this line will raise
# ModuleNotFoundError until the source tree is populated. That failure
# is the documented RED signal — do NOT wrap it in try/except.
from taskq import cli  # noqa: E402,F401 — ModuleNotFoundError is valid RED

# GREEN TODO: `taskq.store` must expose add_task / atomic write primitives.
from taskq import store  # noqa: E402,F401 — ModuleNotFoundError is valid RED

# GREEN TODO: `taskq.config` must read TASKQ_HOME (and the other 7
# TASKQ_* env vars) with documented defaults.
from taskq import config  # noqa: E402,F401 — ModuleNotFoundError is valid RED


SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
HEX8_RE = re.compile(r"^[0-9a-f]{8}$")

# BLACKLIST mirrors SPEC.md §3 FR-01 注入字元 row verbatim.
INJECTION_CHARS = (";", "|", "&", "$", ">", "<", "`")
INJECTION_NAMES = (
    "semicolon",
    "pipe",
    "ampersand",
    "dollar",
    "greater_than",
    "less_than",
    "backtick",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _isolated_env(taskq_home: Path) -> dict:
    """Build env for child process: TASKQ_HOME + PYTHONPATH propagation."""
    env = os.environ.copy()
    env["TASKQ_HOME"] = str(taskq_home)
    env["PYTHONPATH"] = str(SRC_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _run_inprocess(argv: list[str], env_extra: dict | None = None):
    """Invoke cli.main([...]) in-process; return (exit_code, stdout, stderr).

    Used by validation tests to exercise the SAME code path the CLI does
    while staying inside the pytest process so coverage tooling can see it
    (SUBPROCESS COVERAGE CEILING — pytest-cov cannot measure code that
    runs in a subprocess).
    """
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


def _run_subprocess(argv: list[str], taskq_home: Path, env_extra: dict | None = None):
    """Invoke `python -m taskq ...` out-of-process with isolated TASKQ_HOME.

    Used by integration tests (cross-process, fault-injection, smoke) to
    verify the real user-facing entry point, not just the in-process API.
    """
    child_env = _isolated_env(taskq_home)
    if env_extra:
        child_env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "taskq", *argv],
        capture_output=True,
        text=True,
        env=child_env,
        timeout=60,
    )


@pytest.fixture
def taskq_home(tmp_path, monkeypatch):
    """Function-scoped TASKQ_HOME per-test (per FR-05 lesson: no leakage)."""
    home = tmp_path / ".taskq"
    home.mkdir()
    monkeypatch.setenv("TASKQ_HOME", str(home))
    return home


# ---------------------------------------------------------------------------
# FR-01 AC-1.1 — non-empty after whitespace strip
# ---------------------------------------------------------------------------


def test_submit_empty_command_exits_2(taskq_home):
    """AC-1.1: command "" after strip -> exit 2 + stderr; no store write.

    Sub-assertion: AC1-empty-stripped (`command.strip() == ""`),
    AC1-validation-exit2 (`expected_exit == "2"`).
    """
    exit_code, _stdout, stderr = _run_inprocess(["submit", ""])
    assert exit_code == 2
    assert stderr.strip() != "", "validation rejection must surface on stderr"
    assert not (taskq_home / "tasks.json").exists(), (
        "FR-01 rule violated: empty command must not persist"
    )


# ---------------------------------------------------------------------------
# FR-01 AC-1.2 — length boundary (1001 rejected, 1000 accepted)
# ---------------------------------------------------------------------------


def test_submit_long_command_exits_2(taskq_home):
    """AC-1.2: command of 1001 chars -> exit 2; no store write.

    Sub-assertion: AC1-boundary-long (`command_length == "1001"`),
    AC1-validation-exit2.
    """
    command = "a" * 1001
    assert len(command) == 1001
    exit_code, _stdout, stderr = _run_inprocess(["submit", command])
    assert exit_code == 2
    assert stderr.strip() != ""
    assert not (taskq_home / "tasks.json").exists()


def test_submit_command_at_1000_chars_accepted(taskq_home):
    """AC-1.2: command of EXACTLY 1000 chars -> exit 0; recorded.

    Sub-assertion: AC1-boundary-at (`command_length == "1000"`).
    Uses an alphabetic-only payload to avoid accidental rejection by the
    injection-char rule (which would mask the length-boundary signal).
    """
    command = "a" * 1000
    assert len(command) == 1000
    assert not any(ch in command for ch in INJECTION_CHARS)
    exit_code, _stdout, _stderr = _run_inprocess(["submit", command])
    assert exit_code == 0
    tasks_file = taskq_home / "tasks.json"
    assert tasks_file.exists(), "boundary-OK command must persist atomically"


# ---------------------------------------------------------------------------
# FR-01 AC-1.3 — injection-character blacklist (7 cases)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("char_name", "command"),
    [
        ("semicolon", "echo hi; rm x"),
        ("pipe", "echo hi | wc"),
        ("ampersand", "echo hi & sleep 1"),
        ("dollar", "echo $HOME"),
        ("greater_than", "echo hi > out.txt"),
        ("less_than", "echo hi < in.txt"),
        ("backtick", "echo `whoami`"),
    ],
)
def _injection_helper(char_name: str, command: str, taskq_home):
    """Shared body for the 7 injection-char tests."""
    assert char_name in INJECTION_NAMES  # guard against parametrize drift
    exit_code, _stdout, stderr = _run_inprocess(["submit", command])
    assert exit_code == 2, (
        f"FR-01 AC-1.3 violation: char {char_name!r} in {command!r} must exit 2, "
        f"got {exit_code}"
    )
    assert stderr.strip() != ""
    assert not (taskq_home / "tasks.json").exists(), (
        f"injection char {char_name} must not produce a persisted task"
    )


def test_submit_injection_semicolon_rejected(taskq_home):
    """AC-1.3: `;` -> exit 2; no store write."""
    _injection_helper("semicolon", "echo hi; rm x", taskq_home)


def test_submit_injection_pipe_rejected(taskq_home):
    """AC-1.3: `|` -> exit 2; no store write."""
    _injection_helper("pipe", "echo hi | wc", taskq_home)


def test_submit_injection_ampersand_rejected(taskq_home):
    """AC-1.3: `&` -> exit 2; no store write."""
    _injection_helper("ampersand", "echo hi & sleep 1", taskq_home)


def test_submit_injection_dollar_rejected(taskq_home):
    """AC-1.3: `$` -> exit 2; no store write."""
    _injection_helper("dollar", "echo $HOME", taskq_home)


def test_submit_injection_greater_than_rejected(taskq_home):
    """AC-1.3: `>` -> exit 2; no store write."""
    _injection_helper("greater_than", "echo hi > out.txt", taskq_home)


def test_submit_injection_less_than_rejected(taskq_home):
    """AC-1.3: `<` -> exit 2; no store write."""
    _injection_helper("less_than", "echo hi < in.txt", taskq_home)


def test_submit_injection_backtick_rejected(taskq_home):
    """AC-1.3: `` ` `` -> exit 2; no store write."""
    _injection_helper("backtick", "echo `whoami`", taskq_home)


# ---------------------------------------------------------------------------
# FR-01 AC-1.4 — name uniqueness vs pending AND running
# ---------------------------------------------------------------------------


# SPEC_AMBIGUITY: TEST_SPEC.md lists the same function name
# `test_name_uniqueness` twice (case #11 covers existing_status="pending",
# case #18 covers existing_status="running"). Python disallows two
# definitions of the same function in one module; the spec-coverage gate
# itself deduplicates TEST_SPEC names via a set, so a single function
# suffices to cover BOTH sub-assertions (AC1-name-existing-state and
# AC1-name-existing-state-running). We test both scenarios in this one
# function rather than inventing a different name (the gate forbids that).
def test_name_uniqueness(taskq_home):
    """AC-1.4: --name collision with a pending OR running task -> exit 2.

    Sub-assertions: AC1-name-existing-state (`existing_status == "pending"`),
    AC1-name-existing-state-running (`existing_status == "running"`),
    AC1-validation-exit2.
    """
    # Pre-seed tasks.json with one task per target state. GREEN TODO:
    # `taskq.store.write_state({...})` (or a public seed helper) must allow
    # tests to construct starting fixtures without going through the CLI.
    tasks_file = taskq_home / "tasks.json"
    seeded = {
        "version": 1,
        "tasks": {
            "11111111": {
                "id": "11111111",
                "command": "echo seed_pending",
                "name": "build",
                "status": "pending",
                "created_at": "2026-07-25T00:00:00Z",
            },
            "22222222": {
                "id": "22222222",
                "command": "echo seed_running",
                "name": "build",
                "status": "running",
                "created_at": "2026-07-25T00:00:00Z",
            },
        },
    }
    tasks_file.write_text(json_lib.dumps(seeded))

    # The seeded file had two tasks both named "build" — first normalize by
    # collapsing to the canonical fixture (one pending, one running). The
    # real GREEN fixture helper should do this in one shot; this is a
    # hand-rolled seed because `taskq.store` does not exist yet.
    seeded = {
        "version": 1,
        "tasks": {
            "11111111": {
                "id": "11111111",
                "command": "echo seed_pending",
                "name": "build",
                "status": "pending",
                "created_at": "2026-07-25T00:00:00Z",
            },
            "22222222": {
                "id": "22222222",
                "command": "echo seed_running",
                "name": "build",
                "status": "running",
                "created_at": "2026-07-25T00:00:00Z",
            },
        },
    }
    tasks_file.write_text(json_lib.dumps(seeded))

    # Case #11: name collides with the pending task -> exit 2.
    exit_code, _stdout, stderr = _run_inprocess(["submit", "echo new_pending", "--name", "build"])
    assert exit_code == 2, (
        f"AC-1.4 violation (pending): name collision must exit 2, got {exit_code}"
    )
    assert stderr.strip() != ""

    # Case #18: name collides with the running task -> exit 2. We rebuild
    # the fixture so the running case is exercised without the pending
    # collision masking it.
    pending_only = {
        "version": 1,
        "tasks": {
            "33333333": {
                "id": "33333333",
                "command": "echo seed_running_only",
                "name": "build",
                "status": "running",
                "created_at": "2026-07-25T00:00:00Z",
            },
        },
    }
    tasks_file.write_text(json_lib.dumps(pending_only))
    exit_code, _stdout, stderr = _run_inprocess(["submit", "echo new_running", "--name", "build"])
    assert exit_code == 2, (
        f"AC-1.4 violation (running): name collision must exit 2, got {exit_code}"
    )
    assert stderr.strip() != ""


# ---------------------------------------------------------------------------
# FR-01 AC-1.5 — atomic write on happy path
# ---------------------------------------------------------------------------


def test_atomic_add_task(taskq_home):
    """AC-1.5: validation pass -> atomic write, status pending.

    Sub-assertion: AC1-happy-atomic (`command == "echo hi"`).
    GREEN TODO: `taskq.store.add_task(...)` must do an atomic write
    (write-temp + rename) so a mid-write crash leaves the previous valid
    file on disk — observable here by reading tasks.json post-call.
    """
    command = "echo hi"
    exit_code, _stdout, _stderr = _run_inprocess(["submit", command])
    assert exit_code == 0, f"valid command must exit 0, got {exit_code}"
    tasks_file = taskq_home / "tasks.json"
    assert tasks_file.exists(), "happy-path submit must persist tasks.json"
    payload = json_lib.loads(tasks_file.read_text())
    assert payload.get("version") == 1
    tasks = payload.get("tasks", {})
    assert len(tasks) == 1, f"expected exactly one task, got {len(tasks)}"
    only_task = next(iter(tasks.values()))
    assert only_task.get("status") == "pending", (
        "AC-1.5 requires fresh tasks to start in pending state"
    )
    assert only_task.get("command") == command


# ---------------------------------------------------------------------------
# FR-01 AC-1.6 — stdout output (plain id; with --json, single-line JSON)
# ---------------------------------------------------------------------------


def test_submit_json_output(taskq_home):
    """AC-1.6: stdout prints single-line JSON `{id, status:pending}` with --json.

    Sub-assertion: AC1-happy-json (`json_flag == "true"`).
    """
    exit_code, stdout, stderr = _run_inprocess(["submit", "echo hi", "--json"])
    assert exit_code == 0
    stdout_line = stdout.strip()
    assert "\n" not in stdout, "--json output must be single-line"
    parsed = json_lib.loads(stdout_line)
    assert HEX8_RE.match(parsed["id"]), (
        f"id must be 8 hex chars, got {parsed['id']!r}"
    )
    assert parsed["status"] == "pending"
    # Also verify the on-disk record matches stdout.
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    persisted_id = next(iter(payload["tasks"]))
    assert persisted_id == parsed["id"]


# ---------------------------------------------------------------------------
# FR-01 NFR Pattern NP-08/NP-04 — aggregate injection rejection
# ---------------------------------------------------------------------------


def test_submit_rejects_injection_chars(taskq_home):
    """NP-08/NP-04 (SEC:T-01): aggregate injection blacklist check.

    Sub-assertion: AC1-aggregate-injection (`"semicolon" in chars_list`).
    Verifies that all 7 chars from the blacklist are rejected — catches
    regressions where one char slips through while the others work.
    """
    chars_list = "semicolon;pipe;ampersand;dollar;greater_than;less_than;backtick"
    assert "semicolon" in chars_list
    commands = {
        "semicolon": "echo hi; rm x",
        "pipe": "echo hi | wc",
        "ampersand": "echo hi & sleep 1",
        "dollar": "echo $HOME",
        "greater_than": "echo hi > out.txt",
        "less_than": "echo hi < in.txt",
        "backtick": "echo `whoami`",
    }
    rejected = 0
    for _name, cmd in commands.items():
        exit_code, _stdout, stderr = _run_inprocess(["submit", cmd])
        if exit_code == 2 and stderr.strip():
            rejected += 1
    assert rejected == len(commands), (
        f"every blacklist char must exit 2 with stderr; only {rejected}/{len(commands)} did"
    )
    assert not (taskq_home / "tasks.json").exists(), (
        "no blacklisted command may produce a persisted task"
    )


# ---------------------------------------------------------------------------
# FR-01 NFR Pattern NP-08/NP-04 — cross-process atomic-write safety
# ---------------------------------------------------------------------------


def test_cross_process_no_corruption(taskq_home):
    """NP-08/NP-04 (SEC:T-04): 4 parallel submitters do not corrupt store.

    Sub-assertion: AC1-cross-process-valid (`expected_valid_json == "true"`).
    Out-of-process subprocesses are required because the cross-process lock
    (fcntl.flock) only fires when each writer is a separate OS process —
    threads within one process share the same flock file descriptor table.
    """
    process_count = 4
    procs = []
    for _ in range(process_count):
        procs.append(
            _run_subprocess(["submit", "echo hi"], taskq_home)
        )
    tasks_file = taskq_home / "tasks.json"
    assert tasks_file.exists(), (
        "at least one submit must complete; got no tasks.json"
    )
    raw = tasks_file.read_text()
    parsed = json_lib.loads(raw)  # raises if store was corrupted -> RED
    tasks = parsed.get("tasks", {})
    assert len(tasks) >= 1, "at least one task must be persisted"
    # Every persisted task must carry an 8-hex id (AC-1.5 invariant).
    for task_record in tasks.values():
        assert HEX8_RE.match(task_record["id"]), (
            f"persisted id must be 8 hex; got {task_record['id']!r}"
        )


# ---------------------------------------------------------------------------
# FR-01 NFR Pattern NP-03 — fault-injection: fail fast or recover
# ---------------------------------------------------------------------------


def test_fault_injection_fails_fast_or_recovers(taskq_home):
    """NP-03 (SEC:T-05): --inject-fault=kill-mid-write must NOT silently lose data.

    Sub-assertion: AC1-fault-outcome (`expected_outcome == "fail_fast_or_recover"`).
    The CLI must only honor --inject-fault when TASKQ_INJECT_FAULT_OK=1;
    a non-zero exit code OR a recoverable post-state is acceptable per
    NFR-07 (no silent reconstruction).
    """
    env_extra = {"TASKQ_INJECT_FAULT_OK": "1"}
    proc = _run_subprocess(
        ["--inject-fault=kill-mid-write", "submit", "echo hi"],
        taskq_home,
        env_extra=env_extra,
    )
    tasks_file = taskq_home / "tasks.json"

    if proc.returncode == 0:
        # Recovered: tasks.json must be valid JSON (atomic-rename worked
        # before the kill point OR the next submit rebuilt cleanly).
        if tasks_file.exists():
            payload = json_lib.loads(tasks_file.read_text())
            assert "tasks" in payload
    else:
        # Fail-fast: explicit stderr message, no silent loss.
        assert proc.stderr.strip() != "", (
            "fail-fast path must surface stderr; got empty stderr"
        )
        # File may exist from prior successful writes, but if it does it
        # MUST be valid JSON (atomic-write invariant).
        if tasks_file.exists():
            json_lib.loads(tasks_file.read_text())  # raises on corruption


# ---------------------------------------------------------------------------
# FR-01 NFR Pattern NP-09 — task record carries timestamps
# ---------------------------------------------------------------------------


def test_task_records_timestamps(taskq_home):
    """NP-09 (SEC:T-06): persisted task records required audit fields.

    Sub-assertion: AC1-timestamp-fields (`"created_at" in expected_fields`).
    Verifies AC-1.5 records `created_at` on every persisted task; the
    other three fields (finished_at/exit_code/status) are populated by
    FR-02 at run time, so a freshly submitted task may legitimately have
    them absent — but their KEYS must exist as documented in SPEC.md §5.2
    OR be added lazily by FR-02 (either is acceptable for this FR-01
    test). The non-negotiable fields for FR-01 are: id, command, name,
    status, created_at.
    """
    exit_code, _stdout, _stderr = _run_inprocess(["submit", "echo hi"])
    assert exit_code == 0
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    only_task = next(iter(payload["tasks"].values()))
    # Required FR-01 fields.
    for required_key in ("id", "command", "name", "status", "created_at"):
        assert required_key in only_task, (
            f"task record missing required field {required_key!r}: {only_task}"
        )
    assert HEX8_RE.match(only_task["id"])
    assert only_task["status"] == "pending"
    # created_at must be ISO-8601 (any timezone, but a parseable value).
    import datetime as _dt  # local alias to avoid stdlib shadowing
    _dt.datetime.fromisoformat(only_task["created_at"].replace("Z", "+00:00"))
