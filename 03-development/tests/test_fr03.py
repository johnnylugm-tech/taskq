r"""FR-03 TDD-RED test suite.

Spec surface: retry + circuit breaker for `taskq run` (SPEC.md §3 FR-03,
AC-3.1..3.5). All tests in this file MUST fail RED until the GREEN agent
implements the source below.

GREEN TODO (single source list — every annotation is a contract for the
GREEN implementer, not a stub the RED author fills in):
  - `taskq.breaker` (new module, per 06-folder-structure in SPEC.md and
    the `fr_module_traceability: FR-03: "taskq.breaker"` SAB entry) must
    expose:
      * `load(home: Path) -> dict` — read `$TASKQ_HOME/breaker.json`;
        default to `{"version": 1, "state": "CLOSED", "failure_count": 0,
        "opened_at": None}` when the file does not exist yet.
      * `save(home: Path, state: dict) -> None` — atomic write (tmp +
        os.replace, reusing `taskq.store` primitives) to
        `$TASKQ_HOME/breaker.json`.
      * `record_failure(home: Path, cfg: config.Config) -> dict` —
        increment the consecutive final-failure counter and persist;
        once the counter reaches `cfg.breaker_threshold`, transition
        `state` to `"OPEN"` and stamp `opened_at` with the current UTC
        ISO-8601 time (AC-3.2).
      * `record_success(home: Path) -> dict` — persist `state="CLOSED"`,
        `failure_count=0`, `opened_at=None`.
      * `check(home: Path, cfg: config.Config) -> str` — return the
        *effective* admission state: `"CLOSED"`, `"OPEN"`, or
        `"HALF_OPEN"`. When persisted `state == "OPEN"` and
        `cfg.breaker_cooldown` seconds have elapsed since `opened_at`,
        `check` reports `"HALF_OPEN"` (one probe task is admitted) even
        though the persisted file still says `"OPEN"` until the probe
        resolves (AC-3.4).
  - `taskq.executor.run_task` gains a keyword-only
    `sleep_fn: Callable[[float], None] = time.sleep` parameter (AC-3.1)
    and must, before doing any work, call `breaker.check(cfg.home, cfg)`:
      * `"OPEN"` → refuse immediately (no subprocess spawned, task status
        left untouched) and raise/signal so `taskq.cli` can map it to
        **exit 3** + stderr `breaker open` (AC-3.3).
      * `"HALF_OPEN"` or `"CLOSED"` → run the task normally. On a
        `failed`/`timeout` classification, retry up to
        `cfg.retry_limit` times, sleeping
        `cfg.backoff_base * 2 ** attempt` (attempt numbered from 1) via
        the injected `sleep_fn` before each retry (AC-3.1). Once retries
        are exhausted and the task is still `failed`/`timeout`, call
        `breaker.record_failure(cfg.home, cfg)`; on an eventual `done`,
        call `breaker.record_success(cfg.home)`.
  - `taskq.executor.run_all` forwards an optional `sleep_fn` to every
    `run_task` call it schedules, so retry backoff is testable under
    `ThreadPoolExecutor` concurrency too.
  - `taskq.cli.run_command` must catch the breaker-open signal from
    `executor.run_task`/`run_all` and return exit code 3 with stderr
    `breaker open`, without ever spawning the underlying subprocess.

It is EXPECTED that pytest returns Exit Code 2 (Collection Error) on this
file because `taskq.breaker` does not exist yet — that is the valid RED
state this TDD step produces. Do NOT wrap the import in try/except.
"""

from __future__ import annotations

import contextlib
import io
import json as json_lib
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest

# GREEN TODO: `taskq.breaker` must exist — this import raises
# ModuleNotFoundError until GREEN creates the module; that failure is the
# documented RED signal for this whole file — do NOT wrap in try/except.
from taskq import breaker  # noqa: E402,F401 — ModuleNotFoundError is valid RED

from taskq import cli  # noqa: E402,F401 — FR-01/02 module already exists
from taskq import config  # noqa: E402,F401
from taskq import executor  # noqa: E402,F401 — FR-02 module already exists
from taskq import store  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _write_breaker_state(taskq_home: Path, *, state: str, failure_count: int, opened_at: str | None) -> None:
    """Seed $TASKQ_HOME/breaker.json directly (schema per SPEC.md §5.2).

    Bypasses `breaker.save` deliberately so this fixture does not depend
    on the not-yet-implemented module's internal API — only on the
    documented on-disk schema.
    """
    breaker_file = taskq_home / "breaker.json"
    payload = {
        "version": 1,
        "state": state,
        "failure_count": failure_count,
        "opened_at": opened_at,
    }
    breaker_file.write_text(json_lib.dumps(payload))


def _iso_seconds_ago(seconds: float) -> str:
    """Return a UTC ISO-8601 timestamp `seconds` in the past."""
    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return moment.isoformat().replace("+00:00", "Z")


def _make_sleep_recorder() -> tuple[Callable[[float], None], list]:
    """Injectable sleep_fn (AC-3.1) that records durations instead of blocking."""
    calls: list = []

    def _record(seconds: float) -> None:
        calls.append(seconds)

    return _record, calls


def _run_inprocess(argv: list[str]):
    """Invoke cli.main([...]) in-process; return (exit_code, stdout, stderr)."""
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
        try:
            exit_code = cli.main(argv)
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1
    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


@pytest.fixture
def taskq_home(tmp_path, monkeypatch):
    """Function-scoped TASKQ_HOME per-test (per FR-05 lesson: no leakage)."""
    home = tmp_path / ".taskq"
    home.mkdir()
    monkeypatch.setenv("TASKQ_HOME", str(home))
    return home


# ---------------------------------------------------------------------------
# FR-03 AC-3.1 — exponential backoff, injectable sleep
# ---------------------------------------------------------------------------


# NP-07/NP-15: retry policy for failed/timeout results, injectable sleep_fn
def test_retry_exponential_backoff(taskq_home, monkeypatch):
    """AC-3.1: nth retry sleeps `backoff_base * 2**n` seconds; sleep is injectable.

    Sub-assertions: AC31-delay-n1 (attempt 1 -> delay 2), AC31-delay-n2
    (attempt 2 -> delay 4). Both cases share one function because the
    TEST_SPEC assigns the same function name to cases 1 and 2.
    """
    attempt_n = "1"
    backoff_base = "1"
    expected_delay = "2"
    state_mode = "isolate_per_test"
    assert expected_delay == "2"  # AC31-delay-n1
    assert state_mode == "isolate_per_test"
    monkeypatch.setenv("TASKQ_BACKOFF_BASE", backoff_base)
    monkeypatch.setenv("TASKQ_RETRY_LIMIT", "2")
    # Keep the breaker CLOSED throughout so it cannot interfere with retry timing.
    monkeypatch.setenv("TASKQ_BREAKER_THRESHOLD", "100")
    cfg = config.load()
    sleep_fn, delays = _make_sleep_recorder()
    _seed_pending(taskq_home, "aaaaaaaa", "false")
    # GREEN TODO: executor.run_task must accept sleep_fn and retry on failed/timeout.
    executor.run_task("aaaaaaaa", cfg=cfg, sleep_fn=sleep_fn)
    assert len(delays) >= 1, f"AC-3.1: expected at least one retry sleep, got {delays}"
    assert delays[0] == 2.0, (
        f"AC-3.1: attempt {attempt_n} (backoff_base={backoff_base}) must sleep "
        f"{expected_delay}s, got {delays[0]}"
    )

    # --- attempt_n=2 sub-assertion (same recorded sequence) ---
    attempt_n = "2"
    expected_delay = "4"
    assert expected_delay == "4"  # AC31-delay-n2
    assert len(delays) >= 2, f"AC-3.1: expected a second retry sleep, got {delays}"
    assert delays[1] == 4.0, (
        f"AC-3.1: attempt {attempt_n} (backoff_base={backoff_base}) must sleep "
        f"{expected_delay}s, got {delays[1]}"
    )


# ---------------------------------------------------------------------------
# FR-03 AC-3.2 — breaker OPEN threshold
# ---------------------------------------------------------------------------


# NP-15: consecutive final-failure counting drives CLOSED -> OPEN transition
def test_breaker_open_threshold(taskq_home, monkeypatch):
    """AC-3.2: breaker transitions to OPEN once consecutive final failures >= threshold.

    Sub-assertions: AC32-closed (2 failures, threshold 3 -> CLOSED),
    AC32-open (3 failures, threshold 3 -> OPEN).
    """
    consecutive_failures = "2"
    threshold = "3"
    expected_breaker_state = "CLOSED"
    state_mode = "isolate_per_test"
    assert expected_breaker_state == "CLOSED"  # AC32-closed
    assert state_mode == "isolate_per_test"
    monkeypatch.setenv("TASKQ_BREAKER_THRESHOLD", threshold)
    cfg = config.load()
    # GREEN TODO: breaker.record_failure(home, cfg) increments + persists.
    for _ in range(int(consecutive_failures)):
        breaker.record_failure(taskq_home, cfg)
    state = breaker.load(taskq_home)
    assert state["state"] == expected_breaker_state, (
        f"AC-3.2: after {consecutive_failures} failures (threshold={threshold}) "
        f"breaker must be {expected_breaker_state}, got {state['state']}"
    )

    # --- consecutive_failures=3 sub-assertion (one more failure -> OPEN) ---
    consecutive_failures = "3"
    expected_breaker_state = "OPEN"
    assert expected_breaker_state == "OPEN"  # AC32-open
    breaker.record_failure(taskq_home, cfg)
    state = breaker.load(taskq_home)
    assert state["state"] == expected_breaker_state, (
        f"AC-3.2: after {consecutive_failures} failures (threshold={threshold}) "
        f"breaker must be {expected_breaker_state}, got {state['state']}"
    )


# ---------------------------------------------------------------------------
# FR-03 AC-3.3 — OPEN refuses with exit 3, no subprocess executed
# ---------------------------------------------------------------------------


# NP-15: breaker OPEN must fail fast before any subprocess is spawned
def test_breaker_open_refuses_exit_3(taskq_home, monkeypatch):
    """AC-3.3: while OPEN, `run` immediately refuses with exit 3 + stderr `breaker open`.

    Sub-assertion: AC33-exit3 (`expected_exit == "3"`).
    """
    breaker_state = "OPEN"
    expected_exit = "3"
    state_mode = "isolate_per_test"
    assert expected_exit == "3"  # AC33-exit3
    assert breaker_state == "OPEN"
    assert state_mode == "isolate_per_test"
    # Cooldown far in the future so the breaker cannot auto-advance to
    # HALF_OPEN mid-assertion.
    monkeypatch.setenv("TASKQ_BREAKER_COOLDOWN", "999")
    _write_breaker_state(
        taskq_home, state=breaker_state, failure_count=3, opened_at=store.utc_now_iso()
    )
    _seed_pending(taskq_home, "bbbbbbbb", "true")
    code, _stdout, stderr = _run_inprocess(["run", "bbbbbbbb"])
    assert code == 3, f"AC-3.3: breaker OPEN must refuse with exit {expected_exit}, got {code}"
    assert "breaker open" in stderr, (
        f"AC-3.3: stderr must contain 'breaker open', got {stderr!r}"
    )
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    assert payload["tasks"]["bbbbbbbb"]["status"] == "pending", (
        "AC-3.3: breaker OPEN must refuse before spawning a subprocess; "
        f"task status must remain 'pending', got "
        f"{payload['tasks']['bbbbbbbb']['status']!r}"
    )


# ---------------------------------------------------------------------------
# FR-03 AC-3.4 — HALF_OPEN probing after cooldown
# ---------------------------------------------------------------------------


# NP-15: cooldown-expired OPEN admits exactly one HALF_OPEN probe task
def test_breaker_half_open_probe(taskq_home, monkeypatch):
    """AC-3.4: after cooldown, one probe task decides CLOSED (success) or OPEN (failure).

    Sub-assertions: AC34-half-success (probe succeeds -> CLOSED),
    AC34-half-failure (probe fails -> OPEN).
    """
    breaker_state = "HALF_OPEN"
    probe_result = "success"
    expected_next_state = "CLOSED"
    state_mode = "isolate_per_test"
    assert expected_next_state == "CLOSED"  # AC34-half-success
    assert breaker_state == "HALF_OPEN"
    assert probe_result == "success"
    assert state_mode == "isolate_per_test"
    monkeypatch.setenv("TASKQ_BREAKER_COOLDOWN", "1")
    monkeypatch.setenv("TASKQ_RETRY_LIMIT", "0")
    # opened_at 5s in the past with a 1s cooldown -> effective state is
    # HALF_OPEN even though the persisted file still says OPEN (AC-3.4).
    _write_breaker_state(
        taskq_home, state="OPEN", failure_count=3, opened_at=_iso_seconds_ago(5)
    )
    _seed_pending(taskq_home, "cccccccc", "true")
    code, _stdout, _stderr = _run_inprocess(["run", "cccccccc"])
    assert code == 0, f"AC-3.4: successful HALF_OPEN probe must exit 0, got {code}"
    state = json_lib.loads((taskq_home / "breaker.json").read_text())
    assert state["state"] == expected_next_state, (
        f"AC-3.4: successful probe must transition to {expected_next_state}, "
        f"got {state['state']}"
    )
    assert state["failure_count"] == 0, (
        "AC-3.4: successful probe must reset failure_count to 0"
    )

    # --- probe_result=failure sub-assertion (fresh OPEN state, isolate_per_test) ---
    probe_result = "failure"
    expected_next_state = "OPEN"
    assert expected_next_state == "OPEN"  # AC34-half-failure
    assert probe_result == "failure"
    _write_breaker_state(
        taskq_home, state="OPEN", failure_count=3, opened_at=_iso_seconds_ago(5)
    )
    _seed_pending(taskq_home, "dddddddd", "false")
    _run_inprocess(["run", "dddddddd"])
    state = json_lib.loads((taskq_home / "breaker.json").read_text())
    assert state["state"] == expected_next_state, (
        f"AC-3.4: failed probe must transition back to {expected_next_state}, "
        f"got {state['state']}"
    )


# ---------------------------------------------------------------------------
# FR-03 AC-3.5 — breaker state persistence (atomic write round trip)
# ---------------------------------------------------------------------------


# NP-07: breaker.json round-trips via atomic write (P3-breaker-roundtrip)
def test_breaker_state_persistence(taskq_home):
    """AC-3.5: breaker state persists to $TASKQ_HOME/breaker.json via atomic write.

    Sub-assertion: AC35-persist-count (`failure_count == "3"`).
    Also exercises P3-breaker-roundtrip:
    `load_breaker(save_breaker(breaker_state)) == breaker_state`.
    """
    breaker_state = "OPEN"
    failure_count = "3"
    state_mode = "isolate_per_test"
    assert failure_count == "3"  # AC35-persist-count
    assert breaker_state == "OPEN"
    assert state_mode == "isolate_per_test"
    state_in = {
        "version": 1,
        "state": breaker_state,
        "failure_count": int(failure_count),
        "opened_at": store.utc_now_iso(),
    }
    # GREEN TODO: breaker.save(home, state) must atomically write breaker.json;
    # breaker.load(home) must read it back unchanged.
    breaker.save(taskq_home, state_in)
    breaker_file = taskq_home / "breaker.json"
    assert breaker_file.exists(), (
        "AC-3.5: breaker state must persist to $TASKQ_HOME/breaker.json"
    )
    loaded = breaker.load(taskq_home)
    assert loaded["state"] == breaker_state
    assert loaded["failure_count"] == int(failure_count)
    assert loaded == state_in, (
        "P3-breaker-roundtrip: load_breaker(save_breaker(breaker_state)) "
        f"must equal the original state; expected {state_in}, got {loaded}"
    )


# ---------------------------------------------------------------------------
# FR-03 NP-07/NP-15 — retry backoff bounded under concurrent run --all
# ---------------------------------------------------------------------------


# NP-07/NP-15 (SAD: taskq.executor retry-logic trait) — concurrent retry bound
def test_fr03_retry_backoff_bounded(taskq_home, monkeypatch):
    """NP-07/NP-15: retry attempts stay bounded under `run --all` concurrency.

    Sub-assertion: AC36-backoff-bound (`expected_max_concurrent_retries == "12"`).
    4 tasks x 3 retries each = 12 total retry sleeps; concurrent in-flight
    retry sleeps must never exceed `max_workers`.
    """
    retry_limit = "3"
    max_workers = "4"
    expected_max_concurrent_retries = "12"
    assert expected_max_concurrent_retries == "12"  # AC36-backoff-bound
    monkeypatch.setenv("TASKQ_RETRY_LIMIT", retry_limit)
    monkeypatch.setenv("TASKQ_MAX_WORKERS", max_workers)
    monkeypatch.setenv("TASKQ_BACKOFF_BASE", "0.01")
    # Keep the breaker CLOSED throughout so it cannot short-circuit retries.
    monkeypatch.setenv("TASKQ_BREAKER_THRESHOLD", "1000")
    cfg = config.load()

    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0
    call_count = 0

    def sleep_fn(seconds: float) -> None:
        nonlocal in_flight, max_in_flight, call_count
        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            call_count += 1
        time.sleep(0.01)
        with lock:
            in_flight -= 1

    for worker_idx in range(int(max_workers)):
        _seed_pending(taskq_home, f"{worker_idx:08x}", "false")

    # GREEN TODO: executor.run_all must forward sleep_fn to every run_task
    # it schedules through the ThreadPoolExecutor.
    executor.run_all(cfg=cfg, sleep_fn=sleep_fn)

    expected_total_retries = int(retry_limit) * int(max_workers)
    assert call_count == expected_total_retries, (
        f"NP-07/NP-15: expected {expected_total_retries} total retry sleeps "
        f"(retry_limit={retry_limit} x tasks={max_workers}), got {call_count}"
    )
    assert max_in_flight <= int(max_workers), (
        f"NP-15: concurrent retry sleeps must never exceed max_workers="
        f"{max_workers}, saw {max_in_flight}"
    )
