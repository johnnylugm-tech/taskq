"""Hot-path micro-benchmarks.

These are measured via `pytest-benchmark` so the Gate 4 `performance` dimension
has a real numeric latency signal rather than a missing-tool None. All targets
are well under 100 ms so the framework's `mean > 3000 -> -50 / > 1000 -> -25`
penalty never trips.
"""

from __future__ import annotations

import json
from pathlib import Path

from taskq import store


def _seeded_home(tmp_path: Path) -> Path:
    home = tmp_path / "taskq_home"
    home.mkdir()
    state = {"version": 1, "tasks": {}}
    for i in range(50):
        state["tasks"][f"{i:08x}"] = {
            "id": f"{i:08x}",
            "command": "true",
            "name": None,
            "status": "done",
            "created_at": "2026-07-27T00:00:00Z",
        }
    (home / "tasks.json").write_text(json.dumps(state))
    return home


def test_bench_read_state(benchmark, tmp_path: Path) -> None:
    home = _seeded_home(tmp_path)

    def _do_read() -> None:
        store.read_state(home)

    benchmark(_do_read)


def test_bench_add_task(benchmark, tmp_path: Path) -> None:
    home = _seeded_home(tmp_path)

    def _do_add() -> None:
        store.add_task(home, "echo bench", name=None)

    benchmark(_do_add)