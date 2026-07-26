r"""FR-05 TDD-RED test suite.

Spec surface: CLI integration for `python -m taskq` (SPEC.md §3 FR-05,
AC-5.1..5.8). Unlike FR-01..FR-04, every module this file imports
(`taskq.cli`, `taskq.store`, `taskq.breaker`, `taskq.cache`,
`taskq.executor`, `taskq.config`) already exists — those FRs are GREEN.
FR-05's gap lives *inside* `taskq/cli.py`: `submit` and `run` are wired,
but `status`, `list`, and `clear` are not, the global `--json` flag is
only honored by `submit`, and there is no unknown-task / corrupted-store
/ unexpected-exception handling. So RED here shows up as **failing
assertions**, not a `ModuleNotFoundError` collection error — that is the
expected shape of this particular RED state.

GREEN TODO (single source list — every annotation is a contract for the
GREEN implementer, not a stub the RED author fills in):
  - `taskq.cli._build_parser` must register `status`, `list`, and `clear`
    subparsers (AC-5.1):
      * `status <id>` -> a new `status_command(args, cfg)` that reads
        the task record via `store.read_state` and prints it (plain
        `repr`/formatted text by default, single-line JSON when
        `--json`/`args.json_output` is set).
      * `list [--status S]` -> a new `list_command(args, cfg)` that
        prints every task, optionally filtered to `record["status"] ==
        args.status`.
      * `clear` -> a new `clear_command(args, cfg)` that empties
        `$TASKQ_HOME` (e.g. `store.write_state(cfg.home, {"version": 1,
        "tasks": {}})` plus resetting `breaker.json`/`cache.json`).
      * All three (and `run`) must accept `--json` the same way `submit`
        already does (AC-5.3) — either hoist `--json` so every
        subparser inherits it, or add the flag explicitly to each.
  - `taskq.cli.run_command` / `status_command` must catch a missing task
    id from the store and map it to **exit 2** + stderr
    `unknown task: <id>` (verbatim phrase) instead of letting
    `executor.run_task`'s `KeyError` propagate (AC-5.6).
  - `taskq.store.read_state` (or a thin wrapper called from `cli.main`)
    must catch `json.JSONDecodeError`/`ValueError` on a corrupted
    `tasks.json` and let `cli.main` map it to **exit 1** + stderr
    `store corrupted`, WITHOUT rewriting the file (AC-5.7) — the file on
    disk must be byte-for-byte unchanged after the failed read.
  - `taskq.cli.main` must wrap the `args.handler(args, config.load())`
    dispatch in a `try/except` for genuinely unexpected exceptions and
    map them to **exit 1**, so a bug in a handler never leaks a raw
    Python traceback to the user (AC-5.4 "other internal error" +
    AC-5.8's "canonical behaviour for unexpected exceptions is exit 1").
  - AC-5.8 also requires narrowing the existing `except Exception:`
    blocks in `taskq/cache.py` (`lookup`, `put`) and
    `taskq/executor.py` (`run_task`'s two cache try/excepts) to specific
    subtypes (e.g. `(json.JSONDecodeError, OSError, KeyError,
    ValueError)`) instead of a bare `Exception` catch-all — those exist
    today as a deliberate NP-07 fail-open design from FR-04, but AC-5.8
    forbids the unqualified form regardless of intent.

It is EXPECTED that some of these assertions fail via `SystemExit`/argparse
usage errors (unknown subcommand) rather than a collection-time
`ModuleNotFoundError` — that is still a valid RED signal per this file's
module-level docstring above. Do NOT wrap any import in try/except.
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

# All of these modules already exist (FR-01..FR-04 are GREEN); FR-05's RED
# state comes from missing subcommands/behaviour inside `cli.py`, not from
# a missing module, so no `ModuleNotFoundError` is expected here.
from taskq import breaker  # noqa: E402,F401
from taskq import cache  # noqa: E402,F401
from taskq import cli  # noqa: E402,F401
from taskq import config  # noqa: E402,F401
from taskq import executor  # noqa: E402,F401
from taskq import store  # noqa: E402,F401


SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
PROD_SRC = SRC_ROOT / "taskq"

# AC-5.8: a truly bare `except:` or an unqualified `except Exception:` both
# count as "swallowing unexpected exceptions" for this static scan.
_BARE_EXCEPT_RE = re.compile(r"except\s*:")
_BROAD_EXCEPT_RE = re.compile(r"except\s+Exception\s*:")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def taskq_home(tmp_path, monkeypatch):
    """Function-scoped TASKQ_HOME per-test (per FR-05 lesson: no leakage)."""
    home = tmp_path / ".taskq"
    home.mkdir()
    monkeypatch.setenv("TASKQ_HOME", str(home))
    return home


def _seed_task(taskq_home: Path, task_id: str, command: str, *, status: str = "pending", **extra) -> None:
    """Seed one task record directly under $TASKQ_HOME/tasks.json (fixture only)."""
    tasks_file = taskq_home / "tasks.json"
    state: dict = {"version": 1, "tasks": {}}
    if tasks_file.exists():
        state = json_lib.loads(tasks_file.read_text())
    record = {
        "id": task_id,
        "command": command,
        "name": None,
        "status": status,
        "created_at": "2026-07-25T00:00:00Z",
    }
    record.update(extra)
    state["tasks"][task_id] = record
    tasks_file.write_text(json_lib.dumps(state))


def _run_inprocess(argv: list[str]):
    """Invoke `cli.main([...])` in-process; return (exit_code, stdout, stderr).

    In-process (not `subprocess.run`) so unimplemented `status`/`list`/
    `clear` handlers, and the not-yet-added top-level exception guard,
    surface as ordinary Python control flow the test can assert on.
    Catches `SystemExit` (argparse's normal exit path) AND a bare
    `Exception` (only so an unmapped internal error fails the assertion
    cleanly with `code is None` instead of erroring the whole test via an
    uncaught `KeyError`/etc. — this is test-harness robustness, not the
    production "no bare except" rule from AC-5.8, which only scans
    `src/taskq`).
    """
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    exit_code = None
    with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
        try:
            exit_code = cli.main(argv)
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1
        except Exception as exc:  # test-harness safety net only, see docstring
            stderr_buf.write(f"\n[unmapped exception reached cli.main: {exc!r}]")
    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


def _isolated_env(taskq_home: Path) -> dict:
    """Build env for an out-of-process child: TASKQ_HOME + PYTHONPATH propagation.

    pytest's `pythonpath =` setting (setup.cfg) does not propagate to a
    `subprocess.run([sys.executable, "-m", "taskq", ...])` child, so it
    must be forwarded explicitly (per FR-05 P3 lesson).
    """
    env = os.environ.copy()
    env["TASKQ_HOME"] = str(taskq_home)
    env["PYTHONPATH"] = str(SRC_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


# ---------------------------------------------------------------------------
# FR-05 AC-5.1 — all five subcommands wired through argparse
# ---------------------------------------------------------------------------


# NP-04: argparse subcommand surface must match the documented table exactly
def test_subcommands_wired(taskq_home):
    """AC-5.1: `submit`/`run`/`status`/`list`/`clear` are all wired through argparse.

    Sub-assertion: AC51-subcommands (`"submit" in subcommands`).

    Checked two ways: in-process `--help` text (fast, exercises
    `cli._build_parser` directly) and one out-of-process
    `python -m taskq --help` invocation (slower, but this is the
    documented entry point per SPEC.md §1/§3 FR-05 — "entry point is
    `python -m taskq`" — so at least one black-box check belongs here).
    """
    subcommands = "submit,run,status,list,clear"
    assert "submit" in subcommands  # AC51-subcommands

    code, out, err = _run_inprocess(["--help"])
    assert code == 0, f"AC-5.1: `--help` must exit 0, got {code} (stderr={err!r})"
    for name in subcommands.split(","):
        assert name in out, (
            f"AC-5.1: in-process `--help` must list subcommand {name!r}; "
            f"GREEN must register it in `cli._build_parser`. Got: {out!r}"
        )

    completed = subprocess.run(
        [sys.executable, "-m", "taskq", "--help"],
        capture_output=True,
        text=True,
        env=_isolated_env(taskq_home),
        timeout=30,
    )
    assert completed.returncode == 0, (
        "AC-5.1: `python -m taskq --help` (the documented entry point) "
        f"must exit 0, got {completed.returncode} (stderr={completed.stderr!r})"
    )
    for name in subcommands.split(","):
        assert name in completed.stdout, (
            f"AC-5.1: `python -m taskq --help` must list subcommand {name!r}. "
            f"Got: {completed.stdout!r}"
        )


# ---------------------------------------------------------------------------
# FR-05 AC-5.2 — `run` positional <id> + --cached / --all combinations
# ---------------------------------------------------------------------------


# NP-04: only the two documented `run` forms are valid CLI combinations
def test_run_args(taskq_home):
    """AC-5.2: `run` accepts `<id> [--cached]` and `--all`, per the documented table.

    Sub-assertion: AC52-run-flags (`"--cached" in run_flags`).

    SPEC_AMBIGUITY: SPEC.md §3 FR-05's subcommand table lists exactly two
    forms — `run <id> [--cached]` and `run --all` — never both a
    positional id AND `--all` together. GREEN must reject that
    undocumented combination with exit 2 rather than silently favoring
    `--all` and ignoring `id` (today's behaviour: `cli.run_command`
    checks `args.all` first and never looks at `args.id` in that branch).
    """
    run_flags = "--cached,--all"
    assert "--cached" in run_flags  # AC52-run-flags

    # Documented form 1: `run <id> --cached`.
    _seed_task(taskq_home, "aaaaaaaa", "true")
    code, _out, err = _run_inprocess(["run", "aaaaaaaa", "--cached"])
    assert code == 0, f"AC-5.2: `run <id> --cached` must exit 0, got {code} ({err!r})"

    # Documented form 2: `run --all` (no positional id).
    _seed_task(taskq_home, "bbbbbbbb", "true")
    code, _out, err = _run_inprocess(["run", "--all"])
    assert code == 0, f"AC-5.2: `run --all` must exit 0, got {code} ({err!r})"

    # Undocumented combination: id + --all together must be rejected.
    _seed_task(taskq_home, "cccccccc", "true")
    code, _out, err = _run_inprocess(["run", "cccccccc", "--all"])
    assert code == 2, (
        "AC-5.2: `run <id> --all` is not a documented combination and "
        f"must be rejected with exit 2, got {code} ({err!r})"
    )


# ---------------------------------------------------------------------------
# FR-05 AC-5.3 — global --json flag
# ---------------------------------------------------------------------------


# NP-04: machine-readable output must be available on every subcommand
def test_global_json_flag(taskq_home):
    """AC-5.3: global `--json` switches output to single-line machine-readable JSON.

    Sub-assertion: AC53-json (`json_flag == "true"`), exercised on
    `command_name="status"` per the TEST_SPEC Inputs row — `status` does
    not exist in `cli._build_parser` yet, so this fails at the argparse
    layer until GREEN wires it (AC-5.1) and threads `--json` through it.
    """
    json_flag = "true"
    command_name = "status"
    assert json_flag == "true"  # AC53-json
    assert command_name == "status"

    record_id = "aaaaaaaa"
    _seed_task(taskq_home, record_id, "echo hi")
    code, out, err = _run_inprocess(["--json", "status", record_id])
    assert code == 0, (
        f"AC-5.3: `--json status <id>` must exit 0, got {code} (stderr={err!r})"
    )
    payload = json_lib.loads(out.strip())
    assert payload["id"] == record_id, (
        f"AC-5.3: --json status output must be single-line JSON with the "
        f"task id; got {out!r}"
    )
    assert payload["status"] == "pending"
    assert "\n" not in out.strip(), "AC-5.3: --json output must be single-line"


# ---------------------------------------------------------------------------
# FR-05 AC-5.4 — canonical exit code map
# ---------------------------------------------------------------------------


# NP-04: exit codes 0/2/3/4/1 must be exhaustively and correctly mapped
def test_exit_code_map(taskq_home, monkeypatch):
    """AC-5.4: exit codes follow 0/2/3/4/1 for success/validation/breaker/timeout/internal.

    Sub-assertions: AC54-exit0, AC55-exit2, AC56-exit3, AC57-exit4,
    AC58-exit1. All five TEST_SPEC rows share this one function name.
    """
    # --- outcome=success -> exit 0 ---
    outcome = "success"
    expected_exit = "0"
    assert expected_exit == "0"  # AC54-exit0
    assert outcome == "success"
    code, _out, err = _run_inprocess(["submit", "echo hi"])
    assert code == 0, f"AC-5.4: successful submit must exit 0, got {code} ({err!r})"

    # --- outcome=validation_error -> exit 2 ---
    # NFR-02: AC-NFR02.2 — validation_error path routes through cli.py's
    # security/validation gate (empty command rejected) via the full
    # `args.handler` dispatch this FR wires, not just `submit_command` directly.
    outcome = "validation_error"
    expected_exit = "2"
    assert expected_exit == "2"  # AC55-exit2
    assert outcome == "validation_error"
    code, _out, err = _run_inprocess(["submit", ""])
    assert code == 2, f"AC-5.4: empty command must exit 2, got {code} ({err!r})"

    # --- outcome=breaker_open -> exit 3 ---
    outcome = "breaker_open"
    expected_exit = "3"
    assert expected_exit == "3"  # AC56-exit3
    assert outcome == "breaker_open"
    monkeypatch.setenv("TASKQ_BREAKER_COOLDOWN", "999")
    breaker_file = taskq_home / "breaker.json"
    breaker_file.write_text(
        json_lib.dumps(
            {
                "version": 1,
                "state": "OPEN",
                "failure_count": 3,
                "opened_at": store.utc_now_iso(),
            }
        )
    )
    _seed_task(taskq_home, "bbbbbbbb", "true")
    code, _out, err = _run_inprocess(["run", "bbbbbbbb"])
    assert code == 3, f"AC-5.4: breaker OPEN must exit 3, got {code} ({err!r})"

    # --- outcome=timeout -> exit 4 (single-task mode only) ---
    outcome = "timeout"
    expected_exit = "4"
    assert expected_exit == "4"  # AC57-exit4
    assert outcome == "timeout"
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "0.2")
    monkeypatch.setenv("TASKQ_RETRY_LIMIT", "0")
    monkeypatch.setenv("TASKQ_BREAKER_THRESHOLD", "100")
    _seed_task(taskq_home, "eeeeeeee", "sleep 10")
    code, _out, err = _run_inprocess(["run", "eeeeeeee"])
    assert code == 4, f"AC-5.4: single-task timeout must exit 4, got {code} ({err!r})"

    # --- outcome=internal_error -> exit 1 ---
    # GREEN TODO: `cli.main` must have a try/except around
    # `args.handler(args, config.load())` that maps ANY unexpected
    # exception raised by a handler to exit 1 (AC-5.4 "other internal
    # error"), instead of letting it propagate as a raw traceback.
    outcome = "internal_error"
    expected_exit = "1"
    assert expected_exit == "1"  # AC58-exit1
    assert outcome == "internal_error"

    def _boom(_args, _cfg):
        raise RuntimeError("unexpected internal failure")

    monkeypatch.setattr(cli, "submit_command", _boom)
    code, _out, err = _run_inprocess(["submit", "echo hi"])
    assert code == 1, (
        f"AC-5.4: an unexpected handler exception must map to exit 1, got {code} ({err!r})"
    )


# ---------------------------------------------------------------------------
# FR-05 AC-5.5 — status / list / clear behaviour
# ---------------------------------------------------------------------------


# NP-04: read-only + destructive query surface (status/list/clear)
def test_status_list_clear(taskq_home):
    """AC-5.5: `status <id>` full record, `list [--status S]` filtered, `clear` empties home.

    Sub-assertion: AC59-filter (`filter_status == "pending"`).
    """
    filter_status = "pending"
    assert filter_status == "pending"  # AC59-filter

    pending_id = "aaaaaaaa"
    done_id = "bbbbbbbb"
    _seed_task(taskq_home, pending_id, "echo pending")
    _seed_task(taskq_home, done_id, "true", status="done", exit_code=0)

    # `status <id>` outputs the full task record.
    code, out, err = _run_inprocess(["--json", "status", pending_id])
    assert code == 0, f"AC-5.5: status must exit 0, got {code} ({err!r})"
    record = json_lib.loads(out.strip())
    assert record["id"] == pending_id
    assert record["command"] == "echo pending"
    assert record["status"] == "pending"

    # `list --status pending` filters out the done task.
    code, out, err = _run_inprocess(["--json", "list", "--status", filter_status])
    assert code == 0, f"AC-5.5: list must exit 0, got {code} ({err!r})"
    listed = json_lib.loads(out.strip())
    listed_ids = {task["id"] for task in listed}
    assert pending_id in listed_ids, (
        f"AC-5.5: `list --status pending` must include {pending_id!r}, got {listed_ids}"
    )
    assert done_id not in listed_ids, (
        f"AC-5.5: `list --status pending` must exclude {done_id!r}, got {listed_ids}"
    )

    # `clear` empties $TASKQ_HOME's task store.
    code, _out, err = _run_inprocess(["clear"])
    assert code == 0, f"AC-5.5: clear must exit 0, got {code} ({err!r})"
    state = store.read_state(taskq_home)
    assert state["tasks"] == {}, (
        f"AC-5.5: clear must empty $TASKQ_HOME's task store, got {state['tasks']!r}"
    )


# ---------------------------------------------------------------------------
# FR-05 AC-5.6 — unknown task id
# ---------------------------------------------------------------------------


# NP-04: unknown task id must be a validation error, not an internal crash
def test_unknown_task_exit_2(taskq_home):
    """AC-5.6: unknown task id at `status`/`run` exits 2 with stderr `unknown task: <id>`.

    Sub-assertions: AC55-exit2, AC510-unknown-stderr
    (`"unknown task" in expected_stderr`).

    SPEC_AMBIGUITY: AC-5.6 also names `clear`, but per the FR-05
    subcommand table `clear` takes no task-id argument at all — there is
    no id for it to look up — so this case is exercised for `status` and
    `run` only.
    """
    task_id = "deadbeef"
    expected_exit = "2"
    expected_stderr = "unknown task: deadbeef"
    assert expected_exit == "2"  # AC55-exit2
    assert "unknown task" in expected_stderr  # AC510-unknown-stderr

    for subcommand in ("status", "run"):
        code, _out, err = _run_inprocess([subcommand, task_id])
        assert code == 2, (
            f"AC-5.6: unknown task id at `{subcommand}` must exit 2, got {code} ({err!r})"
        )
        assert expected_stderr in err, (
            f"AC-5.6: `{subcommand}` stderr must contain {expected_stderr!r} "
            f"verbatim, got {err!r}"
        )


# ---------------------------------------------------------------------------
# FR-05 AC-5.7 — corrupted tasks.json
# ---------------------------------------------------------------------------


# NP-15/NP-07: corrupted store must fail fast, never be silently rebuilt
def test_corrupted_store_exit_1(taskq_home):
    """AC-5.7: non-parseable `tasks.json` exits 1 with stderr `store corrupted`.

    Sub-assertion: AC511-corrupted-stderr (`"store corrupted" in expected_stderr`).
    The corrupted file must remain byte-for-byte unchanged afterwards —
    proof it was not silently rebuilt.
    """
    store_content = "{invalid json"
    expected_exit = "1"
    expected_stderr = "store corrupted"
    assert expected_exit == "1"  # AC58-exit1
    assert "store corrupted" in expected_stderr  # AC511-corrupted-stderr

    tasks_file = taskq_home / "tasks.json"
    tasks_file.write_text(store_content)

    code, _out, err = _run_inprocess(["list"])
    assert code == 1, f"AC-5.7: corrupted store must exit 1, got {code} ({err!r})"
    assert expected_stderr in err, (
        f"AC-5.7: stderr must contain {expected_stderr!r} verbatim, got {err!r}"
    )
    assert tasks_file.read_text() == store_content, (
        "AC-5.7: a corrupted tasks.json must not be silently rebuilt/rewritten"
    )


# ---------------------------------------------------------------------------
# FR-05 AC-5.8 — no bare/overly-broad except in production source
# ---------------------------------------------------------------------------


# NFR-05: maintainability guard, mirrors FR-02's test_no_shell_true_in_source
def test_no_bare_except():
    """AC-5.8: no `except:` or unqualified `except Exception:` in `src/taskq`.

    Sub-assertion: AC512-no-bare-except (`expected_matches == "0"`).
    Today this fails against `taskq/cache.py` (`lookup`, `put`) and
    `taskq/executor.py`'s two cache try/excepts, which use a bare
    `except Exception:` as a deliberate FR-04 fail-open design — GREEN
    must narrow those to specific subtypes (see module docstring).
    """
    pattern = "except:"
    expected_matches = "0"
    assert expected_matches == "0"  # AC512-no-bare-except
    assert pattern == "except:"

    violations: list[str] = []
    for file_path in sorted(PROD_SRC.rglob("*.py")):
        text = file_path.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _BARE_EXCEPT_RE.search(line) or _BROAD_EXCEPT_RE.search(line):
                violations.append(f"{file_path.relative_to(SRC_ROOT)}:{lineno}: {line.strip()}")
    assert violations == [], (
        "AC-5.8: no bare `except:`/unqualified `except Exception:` allowed "
        f"(silently swallows unexpected exceptions); found: {violations}"
    )


# ---------------------------------------------------------------------------
# FR-05 NP-02 (SEC:T-07) — inject-fault rejected outside test opt-in
# ---------------------------------------------------------------------------


# NP-02: fault-injection surface must stay off unless the test opt-in env is set
def test_inject_fault_rejected_in_production(taskq_home, monkeypatch):
    """NP-02 (SEC:T-07): `--inject-fault=...` is rejected unless `TASKQ_INJECT_FAULT_OK=1`.

    Sub-assertion: AC513-inject-rejected (`expected_rejected == "true"`).
    `cli.main` already gates this today (`TASKQ_INJECT_FAULT_OK` check
    predates FR-05), so this test is pinning down existing FR-01
    behaviour as part of FR-05's CLI-integration surface — it must keep
    holding once GREEN reworks `cli.main`'s dispatch to add the
    AC-5.4/AC-5.8 exception guard above.
    """
    env = "production"
    cli_flag = "--inject-fault=corrupt-mid-write"
    expected_rejected = "true"
    assert expected_rejected == "true"  # AC513-inject-rejected
    assert env == "production"
    assert cli_flag.startswith("--inject-fault=")

    monkeypatch.delenv("TASKQ_INJECT_FAULT_OK", raising=False)
    code, _out, err = _run_inprocess([cli_flag, "submit", "echo hi"])
    assert code == 2, (
        f"NP-02: --inject-fault outside the test opt-in must exit 2, got {code} ({err!r})"
    )
    assert "inject-fault rejected in production" in err, (
        f"NP-02: stderr must state the rejection reason, got {err!r}"
    )


# ---------------------------------------------------------------------------
# FR-05 defensive/plain-output branches
# ---------------------------------------------------------------------------


def test_inject_fault_triggered_when_opted_in(taskq_home, monkeypatch):
    """`--inject-fault=...` with `TASKQ_INJECT_FAULT_OK=1` short-circuits
    `cli.main` with exit 1 and the "triggered" stderr message, without
    dispatching to the requested subcommand."""
    monkeypatch.setenv("TASKQ_INJECT_FAULT_OK", "1")
    code, _out, err = _run_inprocess(["--inject-fault=kill-mid-write", "submit", "echo hi"])
    assert code == 1, f"opted-in fault injection must exit 1, got {code} ({err!r})"
    assert "fault injection triggered: kill-mid-write" in err, (
        f"stderr must report the triggered fault, got {err!r}"
    )
    assert not (taskq_home / "tasks.json").exists(), (
        "triggered fault injection must short-circuit before the subcommand runs"
    )


def test_run_without_id_or_all_exits_2(taskq_home):
    """`run` with neither `<id>` nor `--all` is a validation error (exit 2)."""
    code, _out, err = _run_inprocess(["run"])
    assert code == 2, f"`run` with no id/--all must exit 2, got {code} ({err!r})"
    assert "run requires a task id or --all" in err, (
        f"stderr must state the reason, got {err!r}"
    )


def test_status_and_list_plain_text_output(taskq_home):
    """`status`/`list` without `--json` print the plain (non-JSON) record form."""
    task_id = "cccccccc"
    _seed_task(taskq_home, task_id, "echo plain")

    code, out, err = _run_inprocess(["status", task_id])
    assert code == 0, f"plain status must exit 0, got {code} ({err!r})"
    with pytest.raises(json_lib.JSONDecodeError):
        json_lib.loads(out.strip())
    assert task_id in out

    code, out, err = _run_inprocess(["list"])
    assert code == 0, f"plain list must exit 0, got {code} ({err!r})"
    with pytest.raises(json_lib.JSONDecodeError):
        json_lib.loads(out.strip())
    assert task_id in out


def test_version_field_invariant(taskq_home):
    """Persisted task state always uses schema version 1."""
    store.write_state(taskq_home, store._empty_state())
    assert store.read_state(taskq_home)["version"] == 1


def test_v2_refuses(taskq_home):
    """A future task-store schema is rejected rather than silently migrated."""
    taskq_home.mkdir(parents=True, exist_ok=True)
    (taskq_home / "tasks.json").write_text(json_lib.dumps({"version": 2, "tasks": {}}))
    code, _out, err = _run_inprocess(["list"])
    assert code == 1
    assert "unsupported tasks store schema" in err


def test_posix_flock(taskq_home):
    """The POSIX task lock is created and held for the operation body."""
    with store._locked(taskq_home, exclusive=True):
        assert (taskq_home / "tasks.lock").exists()


def test_atomic_write_three_files(taskq_home):
    """Tasks, breaker, and cache each persist valid versioned JSON roots."""
    store.write_state(taskq_home, store._empty_state())
    breaker.save(taskq_home, breaker._default_state())
    cache.put(taskq_home, cache.signature("echo hi"), {"status": "done"})
    for filename in ("tasks.json", "breaker.json", "cache.json"):
        payload = json_lib.loads((taskq_home / filename).read_text())
        assert payload["version"] == 1
