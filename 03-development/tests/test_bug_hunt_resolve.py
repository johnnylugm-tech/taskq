"""Bug-hunt resolution repro tests.

Anti-fabrication gate: each test below must RED-fail on the bug BEFORE the
source fix lands, and GREEN once the fix is applied.
"""

from __future__ import annotations

import contextlib
import io
import json as json_lib
import sys
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_ROOT))

from taskq import breaker, cli, store  # noqa: E402


@pytest.fixture
def taskq_home(tmp_path, monkeypatch):
    home = tmp_path / ".taskq"
    home.mkdir()
    monkeypatch.setenv("TASKQ_HOME", str(home))
    return home


def _run_inprocess(argv: list[str]):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            exit_code = cli.main(argv)
        except SystemExit as exc:  # pragma: no cover
            exit_code = exc.code if isinstance(exc.code, int) else 1
    return exit_code, out.getvalue(), err.getvalue()


# F1 (medium, concurrency): `clear_command` writes `breaker.json` via the
# internal `_write_unlocked` helper without acquiring the breaker's flock.
# This makes it the only producer of `breaker.json` that bypasses the
# `_locked` flock used by `record_failure`/`record_success`.
#
# Repro: the call to `breaker.save` triggered by `clear` must occur WHILE
# the breaker flock is held.
def test_clear_command_acquires_breaker_lock(taskq_home):
    """F1: when `cli.clear` persists the breaker, it must hold the flock.

    The probe wraps `breaker._locked` so we can observe whether
    `breaker.save` was invoked while the lock was held. Pre-fix, clear
    calls save() OUTSIDE any lock — flag flips False. Post-fix, clear
    routes through `breaker.reset` which acquires _locked first.
    """
    inside_lock_depth = {"value": 0}
    save_inside_lock: list[bool] = []
    real_save = breaker.save

    def spy_save(home, state):
        save_inside_lock.append(inside_lock_depth["value"] > 0)
        return real_save(home, state)

    import taskq.breaker as breaker_mod
    real_locked_cm = breaker_mod._locked

    from contextlib import contextmanager

    @contextmanager
    def wrapped_locked(home):
        inside_lock_depth["value"] += 1
        try:
            with real_locked_cm(home):
                yield
        finally:
            inside_lock_depth["value"] -= 1

    # Patch in BOTH the module and the attribute the cli captured at import
    # time so the wrap is observable from inside `clear_command`.
    breaker_mod.save = spy_save
    breaker_mod._locked = wrapped_locked
    cli.breaker.save = spy_save
    # `clear_command` calls `breaker.save` via cli.breaker.save; the wrapped
    # `_locked` defined here lives on the module, so cli.breaker._locked
    # resolves through the module attribute (same object).

    code, _out, _err = _run_inprocess(["clear"])
    assert code == 0, f"`clear` must exit 0, got {code}"

    # Filter: at least one save() call from clear must have been under
    # the lock. Pre-fix: False (clear bypasses). Post-fix: True.
    assert save_inside_lock, "no save() calls observed during clear"
    assert any(save_inside_lock), (
        "F1: clear_command wrote breaker.json OUTSIDE the breaker flock. "
        f"All save() depth flags observed: {save_inside_lock!r}. "
        "Fix: route clear_command through a `_locked(home)` block."
    )
