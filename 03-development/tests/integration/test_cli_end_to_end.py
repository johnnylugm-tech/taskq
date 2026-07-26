"""Integration-layer tests: exercise `python -m taskq` end-to-end via subprocess.

Unlike the FR-0N unit suites (which call `cli.main([...])` in-process or seed
`tasks.json` directly), every test here spawns a real out-of-process
`python -m taskq` child against a fresh, unseeded `$TASKQ_HOME` — proving the
CLI, executor, store, breaker, and cache modules are wired together correctly
as a real system, not just unit-testable in isolation.

Citations: 02-architecture/TEST_SPEC.md "Infrastructure & Middleware
Integration" (`test_cli_entrypoint_wires_cache_end_to_end`) and "Deployment
Smoke" (`test_cli_entrypoint_starts_and_help_exits_0`).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src"
PROJECT_ROOT = SRC_ROOT.parent.parent
_SUBPROCESS_COVERAGERC = PROJECT_ROOT / "subprocess-coverage.ini"


@pytest.fixture
def taskq_home(tmp_path):
    """Function-scoped, unseeded `$TASKQ_HOME` — no `tasks.json` exists yet."""
    home = tmp_path / ".taskq"
    return home


def _env(taskq_home: Path, **overrides: str) -> dict:
    """Build a child environment: fresh `TASKQ_HOME` + `PYTHONPATH` propagation.

    `PYTHONPATH` must be forwarded explicitly — pytest's own path setup does
    not reach an out-of-process `python -m taskq` child.

    `COVERAGE_PROCESS_START` enables coverage.py's standard subprocess
    measurement (via the `coverage.process_startup()` sitecustomize hook) so
    the out-of-process `python -m taskq` child is included in
    `--cov=03-development/src`; the referenced config sets `parallel = true`
    so each child's data file is combined rather than overwritten.
    """
    env = os.environ.copy()
    env["TASKQ_HOME"] = str(taskq_home)
    env["PYTHONPATH"] = str(SRC_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["COVERAGE_PROCESS_START"] = str(_SUBPROCESS_COVERAGERC)
    env.update(overrides)
    return env


def _run_cli(args: list[str], env: dict, timeout: float = 30):
    import subprocess

    return subprocess.run(
        [sys.executable, "-m", "taskq", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Deployment smoke
# ---------------------------------------------------------------------------


def test_cli_entrypoint_starts_and_help_exits_0(taskq_home):
    """The module entrypoint starts and exits cleanly on `--help`."""
    completed = _run_cli(["--help"], _env(taskq_home))
    assert completed.returncode == 0
    assert "submit" in completed.stdout


# ---------------------------------------------------------------------------
# Full submit -> run -> status -> list -> clear pipeline (fresh, unseeded home)
# ---------------------------------------------------------------------------


def test_submit_run_status_list_clear_roundtrip(taskq_home):
    """Fresh `$TASKQ_HOME`: submit two tasks, run them, inspect, then clear."""
    env = _env(taskq_home)

    submitted_ok = _run_cli(["submit", "echo hello", "--json"], env)
    assert submitted_ok.returncode == 0
    task_ok = json.loads(submitted_ok.stdout)
    assert task_ok["status"] == "pending"

    submitted_fail = _run_cli(["submit", "false", "--json"], env)
    assert submitted_fail.returncode == 0
    task_fail = json.loads(submitted_fail.stdout)

    run_all = _run_cli(["run", "--all"], env)
    assert run_all.returncode == 0

    status_ok = _run_cli(["status", task_ok["id"], "--json"], env)
    assert status_ok.returncode == 0
    record_ok = json.loads(status_ok.stdout)
    assert record_ok["status"] == "done"
    assert record_ok["exit_code"] == 0

    status_fail = _run_cli(["status", task_fail["id"], "--json"], env)
    record_fail = json.loads(status_fail.stdout)
    assert record_fail["status"] == "failed"
    assert record_fail["exit_code"] != 0

    listed = _run_cli(["list", "--json"], env)
    assert listed.returncode == 0
    all_tasks = json.loads(listed.stdout)
    assert {t["id"] for t in all_tasks} == {task_ok["id"], task_fail["id"]}

    listed_done = _run_cli(["list", "--status", "done", "--json"], env)
    done_tasks = json.loads(listed_done.stdout)
    assert {t["id"] for t in done_tasks} == {task_ok["id"]}

    cleared = _run_cli(["clear", "--json"], env)
    assert cleared.returncode == 0
    assert json.loads(cleared.stdout) == {"cleared": True}

    # Regression: reading state after `clear` must not raise (the cleared
    # store must round-trip through the same schema `read_state` validates).
    listed_after_clear = _run_cli(["list", "--json"], env)
    assert listed_after_clear.returncode == 0
    assert json.loads(listed_after_clear.stdout) == []

    # The store must still accept new submissions after a clear.
    resubmitted = _run_cli(["submit", "echo again", "--json"], env)
    assert resubmitted.returncode == 0
    assert json.loads(resubmitted.stdout)["status"] == "pending"


# ---------------------------------------------------------------------------
# FR-04 + FR-05: cache wiring, end to end (TEST_SPEC "Infrastructure &
# Middleware Integration" #1 — test_cli_entrypoint_wires_cache_end_to_end)
# ---------------------------------------------------------------------------


def test_cli_entrypoint_wires_cache_end_to_end(taskq_home):
    """A `--cached` re-run of the same command replays the cached result."""
    env = _env(taskq_home)

    submitted = _run_cli(["submit", "echo cache-me", "--json"], env)
    task = json.loads(submitted.stdout)

    first_run = _run_cli(["run", task["id"], "--cached"], env)
    assert first_run.returncode == 0

    status_first = json.loads(_run_cli(["status", task["id"], "--json"], env).stdout)
    assert status_first["status"] == "done"
    assert not status_first.get("cached")

    submitted_2 = _run_cli(["submit", "echo cache-me", "--json"], env)
    task_2 = json.loads(submitted_2.stdout)

    second_run = _run_cli(["run", task_2["id"], "--cached"], env)
    assert second_run.returncode == 0

    status_second = json.loads(_run_cli(["status", task_2["id"], "--json"], env).stdout)
    assert status_second["status"] == "done"
    assert status_second.get("cached") is True


# ---------------------------------------------------------------------------
# FR-03: retries + circuit breaker, driven entirely through the CLI
# ---------------------------------------------------------------------------


def test_retry_then_breaker_opens_and_refuses_admission(taskq_home):
    """Enough consecutive failures trip the breaker; the next run is refused."""
    env = _env(
        taskq_home,
        TASKQ_RETRY_LIMIT="0",
        TASKQ_BACKOFF_BASE="0.01",
        TASKQ_BREAKER_THRESHOLD="2",
        TASKQ_BREAKER_COOLDOWN="60",
    )

    failing_ids = []
    for _ in range(2):
        submitted = _run_cli(["submit", "false", "--json"], env)
        failing_ids.append(json.loads(submitted.stdout)["id"])

    for task_id in failing_ids:
        completed = _run_cli(["run", task_id], env)
        assert completed.returncode == 0  # a `failed` outcome is not a CLI error

    submitted_extra = _run_cli(["submit", "echo should-be-refused", "--json"], env)
    extra_id = json.loads(submitted_extra.stdout)["id"]

    refused = _run_cli(["run", extra_id], env)
    assert refused.returncode == 3
    assert "breaker open" in refused.stderr


# ---------------------------------------------------------------------------
# FR-02: timeout path, out of process
# ---------------------------------------------------------------------------


def test_run_all_timeout_reported_via_exit_code_4(taskq_home):
    """A task that outlives `TASKQ_TASK_TIMEOUT` finishes `timeout`; run --all exits 4."""
    env = _env(taskq_home, TASKQ_TASK_TIMEOUT="0.5", TASKQ_RETRY_LIMIT="0")

    submitted = _run_cli(["submit", "sleep 5", "--json"], env)
    task = json.loads(submitted.stdout)

    run_all = _run_cli(["run", "--all"], env, timeout=30)
    assert run_all.returncode == 4

    status = json.loads(_run_cli(["status", task["id"], "--json"], env).stdout)
    assert status["status"] == "timeout"


# ---------------------------------------------------------------------------
# FR-05: corrupted store fails fast (AC-5.7) without silent rebuild
# ---------------------------------------------------------------------------


def test_corrupted_store_exits_1_and_clear_recovers(taskq_home):
    """A corrupted `tasks.json` fails fast on read; `clear` recovers it."""
    env = _env(taskq_home)

    # Seed a corrupted store directly (bypassing the CLI, which never writes
    # invalid JSON on its own).
    taskq_home.mkdir(parents=True, exist_ok=True)
    (taskq_home / "tasks.json").write_text("{not valid json")

    broken = _run_cli(["list"], env)
    assert broken.returncode == 1
    assert "store corrupted" in broken.stderr

    cleared = _run_cli(["clear"], env)
    assert cleared.returncode == 0

    healthy = _run_cli(["submit", "echo recovered", "--json"], env)
    assert healthy.returncode == 0
    assert json.loads(healthy.stdout)["status"] == "pending"
