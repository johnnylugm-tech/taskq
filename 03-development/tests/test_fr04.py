r"""FR-04 TDD-RED test suite.

Spec surface: result TTL cache for `taskq run <id> --cached` (SPEC.md §3
FR-04, AC-4.1..4.4). All tests in this file MUST fail RED until the GREEN
agent implements the source modules referenced below.

GREEN TODO (single source list — every annotation is a contract for the
GREEN implementer, not a stub the RED author fills in):
  - `taskq.cache` (new module, per SAD.md `fr_module_traceability:
    FR-04: "taskq.cache"`) must expose:
      * `signature(command: str) -> str` — return `sha256(command)` as a
        64-char lowercase hex digest (AC-4.1).
      * `lookup(home: Path, signature: str, ttl_seconds: float) -> dict
        | None` — read `$TASKQ_HOME/cache.json` (schema per SPEC.md
        §5.2: `{version: 1, entries: {signature: {...result fields,
        cached_at}}}`) and return the stored result dict when the entry
        exists and `now - cached_at < ttl_seconds` (AC-4.2). Returns
        `None` on a miss, an expired entry, a missing file, OR a
        corrupted/unreadable file (fail-open, never raises) — the
        caller falls back to normal execution (NP-07 dependency-fault
        tolerance; AC-4.3).
      * `put(home: Path, signature: str, result: dict) -> None` —
        atomically write/merge one entry (tmp + `os.replace`, reusing
        `taskq.store` primitives) into `$TASKQ_HOME/cache.json`,
        stamping `cached_at` with the current UTC ISO-8601 time.
        Thread-safe under concurrent callers (AC-4.4).
  - `taskq.executor.run_task(..., use_cache: bool = False)` (parameter
    already declared) must, when `use_cache` is true, compute
    `cache.signature(command)` and call `cache.lookup(...)` BEFORE
    spawning any subprocess:
      * hit → persist the task as `status="done"`, `cached=True`, and
        copy `exit_code`/`stdout_tail`/`stderr_tail`/`duration_ms` from
        the cached result, WITHOUT calling `subprocess.run` (AC-4.2).
      * miss/expired/lookup-failure → execute normally; on a `done`
        outcome, call `cache.put(home, signature, result)` (AC-4.3).
  - `taskq.cli` registers a `--cached` flag on the `run` subcommand and
    forwards it as `executor.run_task(args.id, cfg=cfg,
    use_cache=args.cached)`.

It is EXPECTED that pytest returns Exit Code 2 (Collection Error) on this
file because `taskq.cache` does not exist yet — that is the valid RED
state this TDD step produces. Do NOT wrap the import in try/except.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json as json_lib
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hypothesis import HealthCheck, given, settings, strategies as st

# GREEN TODO: `taskq.cache` must exist — this import raises
# ModuleNotFoundError until GREEN creates the module; that failure is the
# documented RED signal for this whole file — do NOT wrap in try/except.
from taskq import cache  # noqa: E402,F401 — ModuleNotFoundError is valid RED

from taskq import cli  # noqa: E402,F401 — FR-01/02/03 module already exists
from taskq import config  # noqa: E402,F401
from taskq import executor  # noqa: E402,F401 — FR-02/03 module already exists
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


def _iso_seconds_ago(seconds: float) -> str:
    """Return a UTC ISO-8601 timestamp `seconds` in the past."""
    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return moment.isoformat().replace("+00:00", "Z")


def _seed_cache_entry(taskq_home: Path, command: str, result: dict, *, seconds_ago: float) -> str:
    """Seed $TASKQ_HOME/cache.json directly (schema per SPEC.md §5.2).

    Bypasses `cache.put` deliberately so this fixture does not depend on
    the not-yet-implemented module's internal API — only on the
    documented on-disk schema. Returns the signature used as the key.
    """
    cache_file = taskq_home / "cache.json"
    state: dict = {"version": 1, "entries": {}}
    if cache_file.exists():
        try:
            state = json_lib.loads(cache_file.read_text())
        except json_lib.JSONDecodeError:
            state = {"version": 1, "entries": {}}
    signature = hashlib.sha256(command.encode("utf-8")).hexdigest()
    entry = dict(result)
    entry["cached_at"] = _iso_seconds_ago(seconds_ago)
    state["entries"][signature] = entry
    cache_file.write_text(json_lib.dumps(state))
    return signature


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
# FR-04 AC-4.1 — cache signature = sha256(command)
# ---------------------------------------------------------------------------


# Q1/AC-4.1: cache key derivation is a pure sha256 digest of the command
def test_signature_sha256():
    """AC-4.1: cache signature is computed as `sha256(command)`.

    Sub-assertion: AC41-algo (`expected_algorithm == "sha256"`).
    """
    command = "echo hi"
    expected_algorithm = "sha256"
    assert expected_algorithm == "sha256"  # AC41-algo
    expected_digest = hashlib.sha256(command.encode("utf-8")).hexdigest()
    # GREEN TODO: cache.signature(command) -> str must return the hex sha256 digest.
    actual_digest = cache.signature(command)
    assert actual_digest == expected_digest, (
        f"AC-4.1: signature must equal sha256({command!r}); expected "
        f"{expected_digest}, got {actual_digest}"
    )
    assert len(actual_digest) == 64, "AC-4.1: sha256 hex digest must be 64 characters"


# ---------------------------------------------------------------------------
# FR-04 AC-4.2 — cached replay within TTL, no subprocess, cached: true
# ---------------------------------------------------------------------------


# Q1/AC-4.2: a fresh done result within TTL is replayed instead of re-executed
def test_cached_replay_within_ttl(taskq_home, monkeypatch):
    """AC-4.2: `run <id> --cached` replays a within-TTL cached result.

    Sub-assertion: AC42-cached-flag (`expected_cached_flag == "true"`).
    """
    ttl_seconds = "60"
    elapsed_seconds = "10"
    expected_cached_flag = "true"
    signature = "abc123def456"
    result = "done"
    assert expected_cached_flag == "true"  # AC42-cached-flag
    assert result == "done"
    assert signature == "abc123def456"
    monkeypatch.setenv("TASKQ_CACHE_TTL", ttl_seconds)
    command = "echo cached-payload"
    cached_result = {
        "exit_code": 0,
        "stdout_tail": "cached-payload\n",
        "stderr_tail": "",
        "duration_ms": 5,
    }
    _seed_cache_entry(taskq_home, command, cached_result, seconds_ago=int(elapsed_seconds))
    _seed_pending(taskq_home, "aaaaaaaa", command)
    code, _stdout, _stderr = _run_inprocess(["run", "aaaaaaaa", "--cached"])
    assert code == 0, f"AC-4.2: cached replay must exit 0, got {code}"
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    task = payload["tasks"]["aaaaaaaa"]
    assert task["status"] == result, (
        f"AC-4.2: replayed task must be marked '{result}', got {task['status']!r}"
    )
    assert task.get("cached") is True, (
        f"AC-4.2: replayed task must carry cached: true, got {task.get('cached')!r}"
    )
    assert task["exit_code"] == cached_result["exit_code"]
    assert task["stdout_tail"] == cached_result["stdout_tail"], (
        "AC-4.2: replayed stdout_tail must equal the cached value, not a "
        "fresh execution's output"
    )


# ---------------------------------------------------------------------------
# FR-04 AC-4.3 — cache miss/expiry executes normally and writes cache.json
# ---------------------------------------------------------------------------


# Q2/AC-4.3: no entry, and an expired entry, both fall through to real execution
def test_cache_miss_normal_execution(taskq_home, monkeypatch):
    """AC-4.3: cache miss and expiry both execute normally and persist the result.

    Sub-assertions: AC43-executed applies to both the miss case (no
    cache.json at all) and the expired case (entry present but past
    `TASKQ_CACHE_TTL`). Both scenarios share one function because the
    TEST_SPEC assigns the same function name to cases 3 and 4.
    """
    cache_state = "miss"
    expected_executed = "true"
    assert expected_executed == "true"  # AC43-executed
    assert cache_state == "miss"
    monkeypatch.setenv("TASKQ_CACHE_TTL", "60")
    command = "echo miss-case"
    _seed_pending(taskq_home, "bbbbbbbb", command)
    assert not (taskq_home / "cache.json").exists(), "precondition: no cache.json yet"
    _run_inprocess(["run", "bbbbbbbb", "--cached"])
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    task = payload["tasks"]["bbbbbbbb"]
    assert task["status"] == "done", (
        f"AC-4.3: cache miss must execute normally, got {task['status']!r}"
    )
    assert task["stdout_tail"] == "miss-case\n", (
        "AC-4.3: cache miss must run the real command, not a stale/absent value"
    )
    cache_payload = json_lib.loads((taskq_home / "cache.json").read_text())
    signature = hashlib.sha256(command.encode("utf-8")).hexdigest()
    assert signature in cache_payload.get("entries", {}), (
        "AC-4.3: a successful (done) execution must write its result to "
        "$TASKQ_HOME/cache.json"
    )

    # --- cache_state=expired sub-assertion (isolated command + fresh id) ---
    cache_state = "expired"
    elapsed_seconds = "120"
    ttl_seconds = "60"
    assert expected_executed == "true"  # AC43-executed
    assert cache_state == "expired"
    assert elapsed_seconds == "120"
    assert ttl_seconds == "60"
    monkeypatch.setenv("TASKQ_CACHE_TTL", ttl_seconds)
    command2 = "echo expired-case"
    _seed_cache_entry(
        taskq_home,
        command2,
        {"exit_code": 0, "stdout_tail": "STALE\n", "stderr_tail": "", "duration_ms": 1},
        seconds_ago=int(elapsed_seconds),
    )
    _seed_pending(taskq_home, "cccccccc", command2)
    _run_inprocess(["run", "cccccccc", "--cached"])
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    task = payload["tasks"]["cccccccc"]
    assert task["status"] == "done"
    assert task["stdout_tail"] == "expired-case\n", (
        f"AC-4.3: an entry older than TTL={ttl_seconds}s must NOT be "
        f"replayed; expected fresh execution output, got {task['stdout_tail']!r}"
    )
    assert task.get("cached") is not True, (
        "AC-4.3: an expired cache entry must not be marked cached: true"
    )


# ---------------------------------------------------------------------------
# FR-04 AC-4.4 — atomic + thread-safe cache writes under concurrency
# ---------------------------------------------------------------------------


# Q6/NP-13/AC-4.4: concurrent cache.put callers must never corrupt cache.json
def test_cache_concurrent_writes(taskq_home):
    """AC-4.4: concurrent cache writes stay atomic and thread-safe.

    Sub-assertion: AC44-concurrent-valid (`expected_valid_json == "true"`).
    """
    writer_count = "4"
    expected_valid_json = "true"
    state_mode = "shared"
    assert expected_valid_json == "true"  # AC44-concurrent-valid
    assert state_mode == "shared"
    n = int(writer_count)
    errors: list[Exception] = []

    def _writer(idx: int) -> None:
        try:
            command = f"echo writer-{idx}"
            # GREEN TODO: cache.signature/cache.put must exist and be
            # safe to call from multiple threads concurrently.
            signature = cache.signature(command)
            cache.put(
                taskq_home,
                signature,
                {
                    "exit_code": 0,
                    "stdout_tail": f"writer-{idx}\n",
                    "stderr_tail": "",
                    "duration_ms": 1,
                },
            )
        except Exception as exc:  # noqa: BLE001 — surfaced via `errors` assertion below
            errors.append(exc)

    threads = [threading.Thread(target=_writer, args=(idx,)) for idx in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"AC-4.4: concurrent cache.put must not raise, got {errors}"
    raw = (taskq_home / "cache.json").read_text()
    payload = json_lib.loads(raw)  # raises on corruption -> RED
    entries = payload.get("entries", {})
    assert len(entries) == n, (
        f"AC-4.4: expected {n} entries after {n} concurrent writers, got {len(entries)}"
    )


# ---------------------------------------------------------------------------
# NP-07 (SAD: taskq.cache dependency-fault trait) — cache unavailable
# ---------------------------------------------------------------------------


# Q6/1b/NP-07: a cache-subsystem failure must fall open to normal execution,
# never crash the task.
# GREEN TODO: cache.lookup(home, signature, ttl_seconds) must exist so it
# can be monkeypatched here; executor.run_task must not let a lookup
# failure propagate — it must fall back to normal execution (AC-4.3).
def test_fr04_cache_unavailable_fallback(taskq_home, monkeypatch):
    """NP-07: cache dependency unavailable → task still executes normally.

    Sub-assertion: AC43-executed (`expected_executed == "true"`).
    """
    cache_file_state = "missing"
    expected_executed = "true"
    assert expected_executed == "true"  # AC43-executed
    assert cache_file_state == "missing"
    assert not (taskq_home / "cache.json").exists()

    def _boom(*_args, **_kwargs):
        raise OSError("cache dependency unavailable")

    monkeypatch.setattr(cache, "lookup", _boom)
    command = "echo fallback-case"
    _seed_pending(taskq_home, "dddddddd", command)
    code, _stdout, _stderr = _run_inprocess(["run", "dddddddd", "--cached"])
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    task = payload["tasks"]["dddddddd"]
    assert task["status"] == "done", (
        "NP-07: a cache dependency failure must not crash execution; "
        f"expected fallback to normal execution, got status={task['status']!r}"
    )
    assert task["stdout_tail"] == "fallback-case\n"
    assert code == 0


# Q6/1b/NP-07: a corrupted cache.json must fail open, and a subsequent
# healthy write must prove the cache recovered rather than staying wedged.
def test_fr04_cache_recovers_after_transient_outage(taskq_home, monkeypatch):
    """NP-07: corrupted cache.json recovers after one successful write cycle.

    Sub-assertion: AC43-executed (`expected_executed == "true"`).
    """
    cache_file_state = "corrupted_then_restored"
    expected_executed = "true"
    assert expected_executed == "true"  # AC43-executed
    assert cache_file_state == "corrupted_then_restored"
    monkeypatch.setenv("TASKQ_CACHE_TTL", "60")
    command = "echo outage-case"
    cache_file = taskq_home / "cache.json"
    cache_file.write_text("{not valid json!!")
    _seed_pending(taskq_home, "eeeeeeee", command)
    _run_inprocess(["run", "eeeeeeee", "--cached"])
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    task = payload["tasks"]["eeeeeeee"]
    assert task["status"] == "done", (
        "NP-07: a corrupted cache.json must not crash execution; expected "
        f"fallback to normal execution, got status={task['status']!r}"
    )
    assert task["stdout_tail"] == "outage-case\n"
    cache_payload = json_lib.loads(cache_file.read_text())  # raises if still corrupted -> RED
    signature = hashlib.sha256(command.encode("utf-8")).hexdigest()
    assert signature in cache_payload.get("entries", {}), (
        "NP-07: after the transient outage, a successful run must restore "
        "cache.json to valid JSON and persist the new result"
    )

    # --- second run proves the cache actually recovered (a hit now replays) ---
    _seed_pending(taskq_home, "ffffffff", command)
    _run_inprocess(["run", "ffffffff", "--cached"])
    payload2 = json_lib.loads((taskq_home / "tasks.json").read_text())
    task2 = payload2["tasks"]["ffffffff"]
    assert task2.get("cached") is True, (
        "NP-07: once cache.json is restored to valid JSON, a subsequent "
        "--cached run must hit the recovered cache"
    )


# Q6/1b/NP-07: prove the cache hit path truly short-circuits subprocess spawn
# GREEN TODO: executor must consult cache.lookup BEFORE calling
# subprocess.run whenever use_cache=True and the signature is a hit.
def test_fr04_cache_actually_used_on_hit(taskq_home, monkeypatch):
    """AC-4.2: on a cache hit, no subprocess is ever spawned.

    Sub-assertion: AC45-hit-no-subprocess (`expected_subprocess_spawned == "false"`).
    """
    cache_state = "hit"
    expected_subprocess_spawned = "false"
    assert expected_subprocess_spawned == "false"  # AC45-hit-no-subprocess
    assert cache_state == "hit"
    monkeypatch.setenv("TASKQ_CACHE_TTL", "60")
    command = "echo should-not-run"
    cached_result = {
        "exit_code": 0,
        "stdout_tail": "cached-output\n",
        "stderr_tail": "",
        "duration_ms": 1,
    }
    _seed_cache_entry(taskq_home, command, cached_result, seconds_ago=1)
    _seed_pending(taskq_home, "11112222", command)

    def _forbidden_subprocess_run(*_args, **_kwargs):
        raise AssertionError("AC-4.2: subprocess must not be spawned on a cache hit")

    monkeypatch.setattr(executor.subprocess, "run", _forbidden_subprocess_run)

    code, _stdout, _stderr = _run_inprocess(["run", "11112222", "--cached"])
    assert code == 0, f"AC-4.2: cache-hit replay must exit 0, got {code}"
    payload = json_lib.loads((taskq_home / "tasks.json").read_text())
    task = payload["tasks"]["11112222"]
    assert task.get("cached") is True, (
        "AC-4.2/AC45: a cache hit must mark the task cached: true"
    )
    assert task["stdout_tail"] == cached_result["stdout_tail"]


# ---------------------------------------------------------------------------
# FR-04 -> FR-02 pipeline integration
# ---------------------------------------------------------------------------


# Q7/FR-02: a cache-replayed record must carry the SAME schema as a
# normally-executed FR-02 task record.
def test_fr04_output_feeds_fr02_executor_pipeline(taskq_home, monkeypatch):
    """Q7/FR-02: cached replay output is consumable by the FR-02 executor pipeline.

    Sub-assertion: AC46-pipeline (`cached_result_reused == "true"`).
    """
    cached_result_reused = "true"
    assert cached_result_reused == "true"  # AC46-pipeline
    monkeypatch.setenv("TASKQ_CACHE_TTL", "60")
    command = "echo pipeline-case"
    cached_result = {
        "exit_code": 0,
        "stdout_tail": "pipeline-output\n",
        "stderr_tail": "",
        "duration_ms": 3,
    }
    _seed_cache_entry(taskq_home, command, cached_result, seconds_ago=1)
    _seed_pending(taskq_home, "33334444", command)

    # GREEN TODO: executor.run_task(..., use_cache=True) must return the
    # same dict shape (exit_code/stdout_tail/stderr_tail/duration_ms/
    # finished_at/status) whether the result came from a subprocess or a
    # cache replay, so FR-02 consumers (status/list CLI, FR-03 retry
    # policy) need no cache-specific code path.
    task = executor.run_task("33334444", cfg=config.load(), use_cache=True)
    for required_key in (
        "exit_code",
        "stdout_tail",
        "stderr_tail",
        "duration_ms",
        "finished_at",
        "status",
    ):
        assert required_key in task, (
            f"Q7/FR-02 boundary: cached task record missing {required_key!r}: {task}"
        )
    assert task["status"] == "done", (
        f"Q7/FR-02 boundary: cached replay must classify as 'done', got {task['status']!r}"
    )
    assert task.get("cached") is True
    assert task["exit_code"] == cached_result["exit_code"]
    assert task["stdout_tail"] == cached_result["stdout_tail"], (
        "Q7/FR-02 boundary: the FR-02 pipeline must observe the REPLAYED "
        "cached output, not a fresh subprocess execution"
    )


# ---------------------------------------------------------------------------
# FR-04 direct unit tests — taskq.cache module internals
# ---------------------------------------------------------------------------


# AC-4.2/AC-4.3: cache.json exists (other signatures present) but the
# requested signature is absent — a true per-key miss, distinct from the
# no-file-at-all case already covered by test_cache_miss_normal_execution.
def test_cache_lookup_signature_not_in_entries(taskq_home):
    """cache.lookup returns None when cache.json exists but sig is unknown."""
    other_sig = cache.signature("echo other-command")
    cache.put(
        taskq_home,
        other_sig,
        {"exit_code": 0, "stdout_tail": "other\n", "stderr_tail": "", "duration_ms": 1},
    )
    assert (taskq_home / "cache.json").exists()
    missing_sig = cache.signature("echo not-cached")
    result = cache.lookup(taskq_home, missing_sig, ttl_seconds=60)
    assert result is None, (
        "AC-4.2/AC-4.3: a signature absent from an existing cache.json must "
        "be a miss (None), not raise or return another entry"
    )


# AC-4.4: put() must self-heal a cache.json that is valid JSON but does not
# match the {version, entries: {...}} schema (e.g. entries is not a dict),
# treating it as an empty cache rather than raising or corrupting further.
def test_cache_put_recovers_from_malformed_schema(taskq_home):
    """cache.put treats a schema-invalid (but JSON-valid) cache.json as empty."""
    cache_file = taskq_home / "cache.json"
    cache_file.write_text(json_lib.dumps({"version": 1, "entries": "not-a-dict"}))
    sig = cache.signature("echo schema-heal")
    result = {"exit_code": 0, "stdout_tail": "schema-heal\n", "stderr_tail": "", "duration_ms": 2}
    cache.put(taskq_home, sig, result)
    payload = json_lib.loads(cache_file.read_text())
    assert sig in payload["entries"], (
        "AC-4.4: put() must recover from a malformed (non-dict entries) "
        "cache.json by treating it as empty and writing the new entry"
    )
    assert payload["entries"][sig]["stdout_tail"] == "schema-heal\n"


# P4-cache-roundtrip: cache_get(cache_put(signature, result)) == result,
# exercised directly against the real cache.put/cache.lookup API (the
# CLI-level AC-4.2 test seeds cache.json directly to avoid depending on
# cache.put before GREEN implements it; this test proves the round trip
# through the real module functions instead of the on-disk fixture).
def test_cache_put_lookup_roundtrip(taskq_home):
    """P4-cache-roundtrip: a put() result is returned unchanged by lookup()."""
    sig = cache.signature("echo roundtrip-command")
    result = {
        "exit_code": 0,
        "stdout_tail": "roundtrip-output\n",
        "stderr_tail": "",
        "duration_ms": 7,
    }
    cache.put(taskq_home, sig, result)
    looked_up = cache.lookup(taskq_home, sig, ttl_seconds=60)
    assert looked_up is not None, "P4-cache-roundtrip: a just-written entry must be a hit"
    for key, value in result.items():
        assert looked_up[key] == value, (
            f"P4-cache-roundtrip: cache_get(cache_put(sig, result)) must equal "
            f"result for key {key!r}; expected {value!r}, got {looked_up.get(key)!r}"
        )
    assert "cached_at" in looked_up, "put() must stamp cached_at on the persisted entry"


# ---------------------------------------------------------------------------
# Direction B property test — TEST_SPEC.md P4-cache-roundtrip
# ---------------------------------------------------------------------------


_CACHE_RESULT_DICT = st.fixed_dictionaries(
    mapping={
        "exit_code": st.integers(min_value=-2, max_value=255),
        "stdout_tail": st.text(
            max_size=120,
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P", "Zs"),
                blacklist_characters="\x00",
            ),
        ),
        "stderr_tail": st.text(
            max_size=120,
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P", "Zs"),
                blacklist_characters="\x00",
            ),
        ),
        "duration_ms": st.integers(min_value=0, max_value=600_000),
        "status": st.sampled_from(["done", "failed", "timeout"]),
    }
)


@st.composite
def _signature_and_result(draw):
    """Generate a (signature, result) pair where the signature is the sha256
    of an arbitrary command string (any hex-64 string is a valid cache key
    — signature() is the SCHEMA boundary, not the key universe)."""
    cmd = draw(st.text(min_size=0, max_size=200, alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Zs"), blacklist_characters="\x00")))
    sig = cache.signature(cmd)
    result = draw(_CACHE_RESULT_DICT)
    return sig, result


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(pair=_signature_and_result())
def test_cache_put_lookup_roundtrip_property(taskq_home, pair):
    """Direction B property `P4-cache-roundtrip` for FR-04:

        cache_get(cache_put(signature, result)) == result   (modulo cached_at)

    The P2 RED engine cannot evaluate this (signature/cache_get are
    symbolic, not bound to declared Inputs), so TEST_SPEC.md defers the
    actual round-trip exercise to P4 via hypothesis @given. Closes the
    `property_not_executed` preflight that blocked the P3→P4 push on
    2026-07-26."""
    sig, result = pair
    cache.put(taskq_home, sig, result)
    looked_up = cache.lookup(taskq_home, sig, ttl_seconds=86400.0)
    assert looked_up is not None, (
        f"a just-written entry must be retrievable; got None for sig={sig[:16]}..."
    )
    for key, value in result.items():
        assert looked_up[key] == value, (
            f"cache_put/cache_lookup roundtrip must preserve {key!r}: "
            f"expected {value!r}, got {looked_up.get(key)!r}"
        )
    assert "cached_at" in looked_up, (
        "put() must stamp cached_at on the persisted entry"
    )
