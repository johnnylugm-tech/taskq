# Software Requirements Specification (SRS) — taskq

> INGESTION MODE — canonical source: [`SPEC.md` v4.0.0](../SPEC.md).
> Agent A transcribes 100% of `### FR-01..FR-05` and `### NFR-01..NFR-10`
> headings from SPEC.md; no invention, no omission. Every AC below
> cites the canonical line.
> Source-of-truth file: `SPEC.md` (project root). Version baseline: v4.0.0
> (2026-07-11). Companion: `PROJECT_BRIEF.md` (5 FR / 10 NFR / 8 env in sync).

---

## 1. Introduction

### 1.1 Purpose
This SRS specifies the requirements for `taskq`, a local task queue CLI
tool written in Python 3.11 (runtime: zero external dependencies). The
specification is the basis for design, implementation, verification, and
change control.

### 1.2 Scope
`taskq` accepts shell commands as task submissions, executes them under
controlled concurrency / timeout / retry / circuit breaker / TTL result
cache, and exposes status / list / clear queries. Entry point is
`python -m taskq`. (Source: SPEC.md §1.)

### 1.3 Audience
- Project owner / product manager: johnnylugm-tech
- harness-methodology v2.9 pipeline validation target
- Downstream agents (B architecture / C TDD / D verification / E quality)

### 1.4 Document structure
- §2 Constraints (technology, atomicity, security, reliability, scope)
- §3 Functional Requirements (FR-01 .. FR-05)
- §4 Non-Functional Requirements (NFR-01 .. NFR-10)
- §5 Acceptance Criteria Summary
- §6 Out-of-Scope
- §7 Open Issues
- §8 Risks
- §9 Glossary

### 1.5 References
- SPEC.md v4.0.0 — single source of truth
- PROJECT_BRIEF.md — executive summary + inventories
- .env.example — 8 TASKQ_* environment variable declarations

---

## 2. Constraints

C1. **Technical**: Python 3.11 stdlib only at runtime; `python -m taskq`
    CLI entry; `shell=True` is forbidden everywhere (NFR-02);
    `ThreadPoolExecutor` for `run --all` with shared `threading.Lock` over
    store (FR-02). (Source: SPEC.md §2 / PROJECT_BRIEF.md Key Constraints.)

C2. **Atomicity**: All three data files (`tasks.json`, `breaker.json`,
    `cache.json`) written via tmp + `os.replace`; mid-write crash must
    leave valid JSON (NFR-03). (Source: SPEC.md §4 NFR-03.)

C3. **Security**: Injection character blacklist (`; | & $ > < \``) on
    `submit` (NFR-02); secret-line redaction on `stdout_tail` /
    `stderr_tail` pattern `(sk-[A-Za-z0-9_-]{8,}|token=\S+)` (NFR-04).
    (Source: SPEC.md §4 NFR-02 / NFR-04.)

C4. **Reliability**: Circuit breaker opens at consecutive final-failure
    threshold and refuses until cooldown; `tasks.json` corruption is
    detected and surfaced (exit 1) rather than silently rebuilt (NFR-03,
    FR-03). (Source: SPEC.md §4 NFR-03 / §3 FR-03.)

C5. **Performance**: `submit` + `status` combined p95 < 50ms over 100
    iterations (NFR-01). (Source: SPEC.md §4 NFR-01.)

C6. **Architecture**: `no_circular_dependencies` among the 8 modules;
    `taskq.executor` and `taskq.store` are framework-classified
    high-risk modules. (Source: SPEC.md §10 framework alignment.)

C7. **Resilience**: Three data files must survive fault-injection
    scenarios (mid-write corruption / `OSError` / disk-full) — either
    recover from backup or fail-fast with explicit stderr + non-zero
    exit; never silently rebuild or swallow errors (NFR-07).

C8. **Concurrency**: Multiple `python -m taskq` processes operating on
    the same `$TASKQ_HOME` must not corrupt the three data files; use
    `fcntl.flock` / `msvcrt.locking` as best-effort enhancement layered
    on top of NFR-03 atomic write (NFR-08).

C9. **Scalability**: 1000-task scale `submit` + `status` p95 < 100ms;
    `run --all` on 100 tasks leaves `tasks.json` valid with no task
    loss; streaming iterator (no full load in memory) (NFR-09).

C10. **Evolvability**: Data files carry a `version` field at root;
     reading `version < 1` triggers automatic migration; reading
     `version > 1` refuses with upgrade prompt; pre-migration backup
     as `<file>.v<n>.bak` retained on failure (NFR-10).

---

## 3. Functional Requirements

### FR-01: Task submission and validation

Spec surface: `taskq submit "<command>" [--name NAME]`. Validation rules
— any violation triggers **exit 2** + stderr error message, no store
write. (Source: SPEC.md §3 FR-01.)

#### AC-1.1
Command is non-empty after whitespace stripping. (Source: SPEC.md §3
FR-01 rule "非空".)

#### AC-1.2
Command length ≤ 1000 characters. (Source: SPEC.md §3 FR-01 rule
"長度".)

#### AC-1.3
Command contains none of the injection characters `; | & $ > < \``;.
    (Source: SPEC.md §3 FR-01 rule "注入字元".)

#### AC-1.4
If `--name` is supplied, it does not collide with any existing task
in `pending` or `running` state. (Source: SPEC.md §3 FR-01 rule "名稱唯一".)

#### AC-1.5
On validation pass, task id is generated as the first 8 hex chars of
uuid4; status is `pending`; `command`, `name`, `created_at` are recorded;
write to `$TASKQ_HOME/tasks.json` is atomic. (Source: SPEC.md §3 FR-01
"通過驗證".)

#### AC-1.6
On success, stdout prints the task id; with `--json` flag, stdout prints
`{"id": ..., "status": "pending"}` as single-line JSON. (Source: SPEC.md
§3 FR-01 "通過驗證".)

---

### FR-02: Task executor

Spec surface: `taskq run <id>` or `taskq run --all`. (Source: SPEC.md §3
FR-02.)

#### AC-2.1
Execution uses `subprocess.run(shlex.split(command), capture_output=True,
text=True, timeout=TASKQ_TASK_TIMEOUT)`; **no code path uses `shell=True`**.
(Source: SPEC.md §3 FR-02 first paragraph.)

#### AC-2.2
State transitions: `pending → running → done | failed | timeout`.
Exit 0 → `done`; non-zero exit → `failed`; `TimeoutExpired` →
`timeout`. (Source: SPEC.md §3 FR-02 state machine.)

#### AC-2.3
Result record contains `exit_code`, `stdout_tail` (last 2000 chars),
`stderr_tail` (last 2000 chars), `duration_ms`, `finished_at`.
(Source: SPEC.md §3 FR-02 "結果欄位".)

#### AC-2.4
`run --all` uses `ThreadPoolExecutor(max_workers=TASKQ_MAX_WORKERS)` to
process all `pending` tasks concurrently; store writes are
thread-safe via shared `threading.Lock`. (Source: SPEC.md §3 FR-02
"`--all`".)

#### AC-2.5
Single-task `run` that produces a `timeout` result exits with code 4.
(Source: SPEC.md §3 FR-02 last paragraph.)

---

### FR-03: Retry and circuit breaker

> DERIVED: SPEC.md §3 FR-03 — split canonical state-machine description
> into 5 ACs (AC-3-1 retry policy, AC-3-2 OPEN threshold, AC-3-3 OPEN
> refusal, AC-3-4 HALF_OPEN probing, AC-3-5 persistence) to make each
> clause individually testable.

Retries run on `failed`/`timeout` results, up to `TASKQ_RETRY_LIMIT`; the
nth retry waits `TASKQ_BACKOFF_BASE × 2^n` seconds (exponential
backoff); sleep must be injectable for testability. The breaker is
global, cross-task, cross-process. (Source: SPEC.md §3 FR-03.)

#### AC-3.1
On `failed` or `timeout`, retries are issued up to `TASKQ_RETRY_LIMIT`
times; sleep duration uses `TASKQ_BACKOFF_BASE × 2^n` exponential
backoff; sleep function is injectable. (Source: SPEC.md §3 FR-03 "重試".)

#### AC-3.2
Consecutive final-failure count (retry exhausted but still
`failed`/`timeout`) ≥ `TASKQ_BREAKER_THRESHOLD` → breaker transitions
to `OPEN`. (Source: SPEC.md §3 FR-03 "斷路器".)

#### AC-3.3
While `OPEN`, any `run` immediately refuses with **exit 3** + stderr
`breaker open`; no subprocess is executed. (Source: SPEC.md §3 FR-03
"OPEN 期間".)

#### AC-3.4
After `TASKQ_BREAKER_COOLDOWN` seconds, breaker transitions to
`HALF_OPEN`; one task is allowed through; success → `CLOSED` with
counter reset; failure → back to `OPEN`. (Source: SPEC.md §3 FR-03
"HALF_OPEN".)

#### AC-3.5
Breaker state is persisted to `$TASKQ_HOME/breaker.json` via atomic
write. (Source: SPEC.md §3 FR-03 "狀態持久化".)

---

### FR-04: Result TTL cache

Cache signature = `sha256(command)`. The `--cached` flag replays the
most recent `done` result for the same signature when within
`TASKQ_CACHE_TTL` seconds; the replay short-circuits subprocess
execution and marks the task as `done` with `cached: true`. Cache reads
and writes are atomic and thread-safe. (Source: SPEC.md §3 FR-04.)

#### AC-4.1
Cache signature is computed as `sha256(command)`. (Source: SPEC.md §3
FR-04 first line.)

#### AC-4.2
Invocation `taskq run <id> --cached` with same signature and a `done`
result within `TASKQ_CACHE_TTL` seconds replays the cached
`exit_code` and `stdout_tail`; no subprocess is spawned; task is marked
`done` with `cached: true`. (Source: SPEC.md §3 FR-04 second paragraph.)

#### AC-4.3
On cache miss or expiry, the task executes normally; on success
(`done`), the result is written to `$TASKQ_HOME/cache.json`.
(Source: SPEC.md §3 FR-04 third paragraph.)

#### AC-4.4
Cache read/write is atomic and thread-safe (coexists with FR-02
concurrency). (Source: SPEC.md §3 FR-04 last paragraph.)

---

### FR-05: CLI integration

argparse subcommands; entry point is `python -m taskq`. (Source: SPEC.md
§3 FR-05.)

#### AC-5.1
Subcommands `submit`, `run`, `status`, `list`, `clear` are wired through
argparse with the documented behaviour (see table). (Source: SPEC.md §3
FR-05 subcommand table.)

#### AC-5.2
`run` accepts positional `<id>` plus `--cached` and `--all` per the
combinations documented. (Source: SPEC.md §3 FR-05 subcommand table.)

#### AC-5.3
A global `--json` flag switches output to machine-readable single-line
JSON. (Source: SPEC.md §3 FR-05 "全域 flag".)

#### AC-5.4
Exit codes follow the canonical map: `0` success / `2` input validation
error (including unknown task id) / `3` breaker open / `4` task timeout
(single-task mode only) / `1` other internal error. (Source: SPEC.md
§3 FR-05 "Exit codes".)

#### AC-5.5
`status <id>` outputs the full task record; `list [--status S]` lists
tasks optionally filtered by status; `clear` empties `$TASKQ_HOME`.
(Source: SPEC.md §3 FR-05 subcommand table.)

#### AC-5.6
Unknown task id at `status` / `run` / `clear` → exit 2 + stderr
`unknown task: <id>` (verbatim phrase). (Source: SPEC.md §7.)

#### AC-5.7
Startup with corrupted `tasks.json` (non-parseable JSON) → exit 1 +
stderr `store corrupted`; the file is **not** silently rebuilt.
(Source: SPEC.md §7.)

#### AC-5.8
No code path uses a bare `except:` (or `except Exception:` without
specific subtype) to swallow unexpected exceptions; the canonical
behaviour for unexpected exceptions is exit 1 (SPEC.md §7).
(Source: SPEC.md §7.)

---

## 4. Non-Functional Requirements

### NFR-01: Performance

> DERIVED: SPEC.md §4 NFR-01 — split canonical latency requirement into
> AC-NFR01-1 (p95 < 50ms over 100 iter assertion) for direct test
> harness binding; canonical phrasing "p95 < 50ms" preserved verbatim.

`submit` + `status` combined operation (excluding subprocess execution)
p95 < 50ms over 100 iterations, measured via pytest-benchmark.
(Source: SPEC.md §4 NFR-01.)

#### AC-NFR01.1
Over 100 iterations of `submit` + `status` (no subprocess work), the
p95 latency is strictly less than 50 ms as asserted by a
pytest-benchmark test. (Source: SPEC.md §4 NFR-01.)

---

### NFR-02: Security

> DERIVED: SPEC.md §4 NFR-02 — split into AC-NFR02-1 (shell=True grep
> gate) and AC-NFR02-2 (per-character injection test coverage); AC
> bullets spell out the 7 injection characters enumerated in SPEC.md
> §3 FR-01 for direct test enumeration.

`shell=True` is forbidden in the entire codebase, and the FR-01
injection character blacklist must have test coverage. (Source: SPEC.md
§4 NFR-02.)

#### AC-NFR02.1
Repository-wide grep for `shell=True` returns zero matches in
production code. (Source: SPEC.md §4 NFR-02.)

#### AC-NFR02.2
Each of the FR-01 injection characters (`; | & $ > < \``) has at least
one negative test that issues `submit` with the character and asserts
exit 2. (Source: SPEC.md §4 NFR-02 / §3 FR-01.)

---

### NFR-03: Reliability

> DERIVED: SPEC.md §4 NFR-03 — split into AC-NFR03-1 (atomic write
> protocol), AC-NFR03-2 (mid-write crash survivability), and
> AC-NFR03-3 (breaker recovery ≤ cooldown + 1s) so each clause asserted
> independently by the test harness.

All three data files are written atomically (tmp + `os.replace`); a
process interruption leaves a valid JSON file; breaker `OPEN → CLOSED`
recovery time ≤ `TASKQ_BREAKER_COOLDOWN` + 1s. (Source: SPEC.md §4
NFR-03.)

#### AC-NFR03.1
Each of `tasks.json`, `breaker.json`, `cache.json` is written via a
temp file followed by `os.replace`; the resulting file is parseable
JSON after the write completes. (Source: SPEC.md §4 NFR-03.)

#### AC-NFR03.2
After a simulated mid-write crash (e.g. `kill -9` during write), the
target file is still parseable JSON on next startup, OR the process
fails fast with explicit stderr and a non-zero exit code; no silent
rewrite and no latent restoreability (canonical NFR-07 requires an
actual startup restore or explicit fail-fast outcome, not a passive
"could be restored"). (Source: SPEC.md §4 NFR-03 / §4 NFR-07.)

#### AC-NFR03.3
Breaker `OPEN → CLOSED` recovery time is bounded by
`TASKQ_BREAKER_COOLDOWN` + 1 second as asserted by an integration
test. (Source: SPEC.md §4 NFR-03.)

---

### NFR-04: Security — secret redaction

> DERIVED: SPEC.md §4 NFR-04 — split canonical redaction regex into
> AC-NFR04-1 (`sk-…` keyword) and AC-NFR04-2 (`token=…` keyword) so
> each pattern is asserted independently; verbatim regex
> `(sk-[A-Za-z0-9_-]{8,}|token=\S+)` preserved.

Lines in `stdout_tail` / `stderr_tail` matching
`(sk-[A-Za-z0-9_-]{8,}|token=\S+)` are replaced wholesale by
`[REDACTED]` before the value is persisted. (Source: SPEC.md §4 NFR-04.)

#### AC-NFR04.1
A `stdout_tail` or `stderr_tail` line containing `sk-abcdefghijklmnop`
(or any 8+ char `-_-` token following `sk-`) is replaced with
`[REDACTED]` before persistence. (Source: SPEC.md §4 NFR-04.)

#### AC-NFR04.2
A `stdout_tail` or `stderr_tail` line containing `token=...` (any
non-whitespace suffix) is replaced with `[REDACTED]` before
persistence. (Source: SPEC.md §4 NFR-04.)

---

### NFR-05: Maintainability

> DERIVED: SPEC.md §4 NFR-05 — AC-NFR05-1 spells out the inspection
> mechanism (every public function/class must surface a docstring with
> at least one `[FR-XX]` or `[NFR-XX]` tag) so the test harness can
> enforce coverage mechanically.

Every public function/class in `src/taskq` carries a docstring that
includes a `[FR-XX]` cross-reference. (Source: SPEC.md §4 NFR-05.)

#### AC-NFR05.1
Inspection of every public function/class in `src/taskq` confirms a
docstring containing at least one `[FR-XX]` tag (the canonical
requirement is strictly `[FR-XX]`; `[NFR-XX]` alone is not sufficient).
(Source: SPEC.md §4 NFR-05.)

---

### NFR-06: Deployability

> DERIVED: SPEC.md §4 NFR-06 — split into AC-NFR06-1 (config.py exposes
> 8 TASKQ_* readers with defaults) and AC-NFR06-2 (.env.example declares
> all 8 vars) so each clause asserted independently.

All 8 `TASKQ_*` parameters are read from environment variables (via
`config.py` with defaults); `.env.example` declares each one with a
comment. (Source: SPEC.md §4 NFR-06.)

#### AC-NFR06.1
`config.py` exposes a uniform reader for the 8 `TASKQ_*` variables
listed in §5.1 of SPEC.md, each with the documented default. (Source:
SPEC.md §4 NFR-06 / §5.1.)

#### AC-NFR06.2
`.env.example` lists each of the 8 `TASKQ_*` variables with a brief
comment. (Source: SPEC.md §4 NFR-06.)

---

### NFR-07: Resilience — fault injection

> DERIVED: SPEC.md §4 NFR-07 / §5.3 — enumerated 4 fault scenarios from
> canonical §5.3 (`corrupt-mid-write`, `oserror-on-write`, `disk-full`,
> `kill-mid-write`) into AC-NFR07-1..AC-NFR07-4 and added AC-NFR07-5
> for the "test-only flag" prohibition; verbatim CLI flag
> `--inject-fault=<scenario>` preserved.

The three data files must survive fault-injection scenarios —
mid-write corruption, simulated `OSError`, simulated disk-full, simulated
`kill -9` mid-write — by either auto-recovery (detection + backup
restore on next startup) or fail-fast (explicit stderr + non-zero exit).
Silent rebuild or silent error swallowing is disallowed. Fault
injection is triggered only via CLI flag `--inject-fault=<scenario>` or
test monkeypatch; production execution paths never enable it.
(Source: SPEC.md §4 NFR-07.)

#### AC-NFR07.1
A `--inject-fault=corrupt-mid-write` invocation triggers the
mid-write corruption scenario; subsequent `taskq` startup either
restores from backup or fails fast with explicit stderr and non-zero
exit code; no silent rewrite. (Source: SPEC.md §4 NFR-07 / §5.3.)

#### AC-NFR07.2
A `--inject-fault=oserror-on-write` invocation triggers a simulated
`OSError` on write; behaviour matches AC-NFR07.1 (recovery or
fail-fast). (Source: SPEC.md §4 NFR-07 / §5.3.)

#### AC-NFR07.3
A `--inject-fault=disk-full` invocation triggers a simulated
disk-full write; behaviour matches AC-NFR07.1. (Source: SPEC.md §4
NFR-07 / §5.3.)

#### AC-NFR07.4
A `--inject-fault=kill-mid-write` invocation triggers a simulated
`kill -9` mid-write; behaviour matches AC-NFR07.1. (Source: SPEC.md §4
NFR-07 / §5.3.)

#### AC-NFR07.5
The `--inject-fault` flag is rejected on production CLI surfaces (not
accepted in normal runs). (Source: SPEC.md §4 NFR-07 / §5.3.)

---

### NFR-08: Concurrency — cross-process safety

> DERIVED: SPEC.md §4 NFR-08 — split into AC-NFR08-1 (POSIX/Windows
> flock primitive), AC-NFR08-2 (network-fs degradation + WARNING), and
> AC-NFR08-3 (4-process concurrent write integrity from §11 monitoring
> table); canonical best-effort enhancement language preserved.

Multiple `python -m taskq` processes operating on the same
`$TASKQ_HOME` must not corrupt the three data files. Write operations
acquire an exclusive lock; reads acquire a shared lock. POSIX uses
`fcntl.flock`; Windows uses `msvcrt.locking`. The file lock is a
best-effort enhancement — the primary safety mechanism remains NFR-03
atomic write. NFS / network file systems degrade to "no flock but
maintain atomic write" with a `WARNING`. (Source: SPEC.md §4 NFR-08.)

#### AC-NFR08.1
POSIX deployment uses `fcntl.flock` for write-exclusive and
read-shared locks; Windows deployment uses `msvcrt.locking`. (Source:
SPEC.md §4 NFR-08.)

#### AC-NFR08.2
On NFS / network file system detection, flock is disabled and a
`WARNING` is emitted; atomic write remains the primary safety net.
(Source: SPEC.md §4 NFR-08.)

#### AC-NFR08.3
A 4-process concurrent write test on the same `$TASKQ_HOME` results
in three valid JSON files with no corruption. (Source: SPEC.md §4
NFR-08 / §11 monitoring table.)

---

### NFR-09: Scalability

> DERIVED: SPEC.md §4 NFR-09 — split into AC-NFR09-1 (1000-task p95 <
> 100ms), AC-NFR09-2 (run --all 100 tasks no loss + valid JSON), and
> AC-NFR09-3 (memory < 100MB peak via streaming iterator); canonical
> NFR-01 100-iter 50ms upper bound preserved by reference.

1000-task scale `submit` + `status` combined operation p95 < 100ms
(single 100-iteration scale < 50ms remains covered by NFR-01);
`run --all` over 100 tasks leaves `tasks.json` as valid JSON and **no
task is lost**; memory usage stays < 100MB peak via streaming iterator
(no full load in memory). (Source: SPEC.md §4 NFR-09.)

#### AC-NFR09.1
Over 1000 tasks, `submit` + `status` combined operation p95 < 100ms
per pytest-benchmark. (Source: SPEC.md §4 NFR-09.)

#### AC-NFR09.2
After `run --all` over 100 tasks, `tasks.json` parses as valid JSON
and contains all 100 task records (no loss). (Source: SPEC.md §4
NFR-09.)

#### AC-NFR09.3
Memory usage during 1000-task operations stays below 100MB peak via
streaming iterator (no full in-memory load). (Source: SPEC.md §4
NFR-09.)

---

### NFR-10: Evolvability — schema migration

> DERIVED: SPEC.md §4 NFR-10 — split into AC-NFR10-1 (version=1
> invariant), AC-NFR10-2 (v0→v1 auto-migrate with backup), AC-NFR10-3
> (v>1 upgrade refusal), AC-NFR10-4 (fail-fast on migration error);
> verbatim `<file>.v<n>.bak` backup pattern preserved.

Each data file's root includes a `version` field (current v1). Reading
a file with `version < 1` triggers automatic migration to v1 and
write-back. Reading a file with `version > 1` (future version) refuses
to read and prompts an upgrade tool. Before migration, the original
file is backed up as `<file>.v<n>.bak`. On migration failure, the
backup is retained and the process exits 1 (fail-fast). (Source: SPEC.md
§4 NFR-10.)

#### AC-NFR10.1
Each of `tasks.json`, `breaker.json`, `cache.json` has a `version`
field at root equal to `1`. (Source: SPEC.md §4 NFR-10 / §5.2.)

#### AC-NFR10.2
Reading a file with `version < 1` triggers an automatic migration to
v1 and writes back; the original is preserved as `<file>.v<n>.bak`.
(Source: SPEC.md §4 NFR-10.)

#### AC-NFR10.3
Reading a file with `version > 1` refuses to read and emits an
upgrade prompt; no in-place migration is attempted. (Source: SPEC.md
§4 NFR-10.)

#### AC-NFR10.4
On migration failure, the `<file>.v<n>.bak` backup is retained and
the process exits with code 1 (fail-fast). (Source: SPEC.md §4 NFR-10.)

---

## 5. Acceptance Criteria Summary

The 10 acceptance items from SPEC.md §8 must all pass:

1. `pytest tests/ -q` — all green.
2. `python -m taskq submit "echo hi"` → 8-hex id; `run <id>` → `done`;
   `status <id>` shows `exit_code: 0`.
3. `python -m taskq submit ""` → exit 2.
4. `python -m taskq submit "echo hi; rm x"` → exit 2 (injection).
5. `TASKQ_TASK_TIMEOUT=1` `run` of a `sleep 5` task → status `timeout`,
   exit 4.
6. After 3 consecutive final-failure tasks, the 4th `run` exits with
   code 3 (breaker OPEN); after cooldown, execution resumes.
7. Within TTL, `run <id> --cached` (same command signature) replays
   `cached: true` without spawning subprocess.
8. `.env.example` declares all 8 `TASKQ_*` variables.
9. After concurrent `run --all`, `tasks.json` is valid JSON and no task
   is lost.
10. Public function docstrings include `[FR-XX]` cross-references.

(Source: SPEC.md §8.)

---

## 6. Out-of-Scope

> Scope boundaries are defined by SPEC.md §1 (概述) and §2 (技術架構).
> Items below are surfaced to avoid scope creep during Phase 3
> implementation; the original SPEC.md contains no explicit out-of-scope
> declarations, so this list is intentionally minimal and non-prescriptive.

- OS-level process supervision / daemonization — `taskq` is invoked
  per-command; systemd / launchd integration is the host's
  responsibility (SPEC.md §1 "命令列工具,`python -m taskq` 進入").
- Non-Python 3.11 runtime paths (SPEC.md §1 "Python 3.11 ... runtime 零外部依賴").

---

## 7. Open Issues

| ID | Item | Status | Owner |
|----|------|--------|-------|
| NFR-99 | TBD / TODO / placeholder phrases in SPEC.md scan (none found in v4.0.0) | RESOLVED — no deferred items | Agent A |
| NFR-99a | Resolve NFR-07 test-only CLI surface ambiguity — AC-NFR07.1..4 require `--inject-fault=<scenario>` CLI invocations to trigger scenarios, while AC-NFR07.5 requires the same flag to be rejected on production CLI surfaces. Canonical SPEC.md §5.3 names the flag but does not define the activation mechanism (separate test binary? hidden subcommand? environment-gated path?). Test harness to confirm with stakeholder which interface owns `--inject-fault`. | DEFERRED — implementation choice pending | Agent A |
| FR-01-deferred | none | — | — |
| FR-02-deferred | none | — | — |
| FR-03-deferred | none | — | — |
| FR-04-deferred | none | — | — |
| FR-05-deferred | none | — | — |

No prompt-injection patterns detected in SPEC.md v4.0.0 during the
ingestion scan. Reference: SPEC.md §0 (single source of truth).

---

## 8. Risks

| ID | Risk | Impact | Likelihood | Mitigation | Source |
|----|------|--------|-----------|------------|--------|
| R1 | 并发写入损坏 tasks.json | High | Med | Lock + atomic write (NFR-03) | SPEC.md §9 R1 |
| R2 | subprocess 懸掛/殭屍 | Med | Med | timeout 必設 (FR-02) | SPEC.md §9 R2 |
| R3 | breaker 誤鎖死 | Med | Low | cooldown + HALF_OPEN (FR-03) | SPEC.md §9 R3 |
| R4 | cache 回放陳舊結果 | Low | Med | TTL 過期重執行 (FR-04) | SPEC.md §9 R4 |
| R5 | secret 落盤洩漏 | High | Med | stdout_tail/stderr_tail redaction (NFR-04) | SPEC.md §9 R5 |
| R6 | fault injection 干擾正常測試 | Med | Med | 觸發僅透過顯式 CLI flag 或 monkeypatch;正式執行不接受 (NFR-07) | SPEC.md §9 R6 |
| R7 | cross-process flock 在網路 fs 失效 | Med | Med | flock 為 best-effort;偵測到網路 fs 降級並 WARNING (NFR-08) | SPEC.md §9 R7 |
| R8 | scale 1000 tasks 觸發 memory limit | Med | Low | streaming iterator (NFR-09) | SPEC.md §9 R8 |
| R9 | schema migration 失敗導致資料遺失 | High | Low | 備份為 `<file>.v<n>.bak`;失敗時保留備份 exit 1 (NFR-10) | SPEC.md §9 R9 |

---

## 9. Glossary

| Term | Definition |
|------|------------|
| `$TASKQ_HOME` | Directory containing the three data files; defaults to `.taskq` (SPEC.md §5.1). |
| `done` | Task status when subprocess exits 0 (SPEC.md §3 FR-02). |
| `failed` | Task status when subprocess exits non-zero (SPEC.md §3 FR-02). |
| `timeout` | Task status when `TimeoutExpired` is raised (SPEC.md §3 FR-02). |
| `CLOSED` / `OPEN` / `HALF_OPEN` | Circuit breaker states (SPEC.md §3 FR-03). |
| `done` result caching | Replay of a recent `done` result for the same `sha256(command)` within TTL (SPEC.md §3 FR-04). |
| atomic write | Temp file + `os.replace` pattern producing valid JSON on crash (SPEC.md §4 NFR-03). |
| `pip-freeze` lock | "Zero runtime external dependencies" means only Python 3.11 stdlib is imported at runtime (SPEC.md §1). |
| best-effort flock | Cross-process lock that is opportunistically applied; falls back to atomic write on network FS (SPEC.md §4 NFR-08). |
| `version` field | Schema version marker at the root of each data file; current v1 (SPEC.md §4 NFR-10). |
| `<file>.v<n>.bak` | Pre-migration backup file name pattern (SPEC.md §4 NFR-10). |
| `--inject-fault` | CLI flag that triggers NFR-07 fault injection scenarios; test-only (SPEC.md §4 NFR-07 / §5.3). |
| `pytest-benchmark` | Latency measurement harness used for NFR-01 / NFR-09 acceptance (SPEC.md §4). |
| `TASKQ_HOME` flock | Cross-process file lock on the three data files (SPEC.md §4 NFR-08). |
| streaming iterator | In-memory partial loading discipline for 1000-task scale (SPEC.md §4 NFR-09). |
| `[FR-XX]` docstring tag | Cross-reference marker required on every public function (SPEC.md §4 NFR-05). |
| exit code map | 0 / 1 / 2 / 3 / 4 — see SPEC.md §3 FR-05 / §7. |

---

## 10. Cross-Cutting Test Requirements

> Phase 1 hook — narrative intent. Authoritative binding is in
> `02-architecture/SAD.md` §6 STRIDE-lite threat model and
> `derive_test_cases.md` Step 1c; per-FR coverage is enforced by
> `check-spec-alignment` and `verify-spec-compliance`.

### 10.1 API Completeness (per endpoint)
- [ ] `test_<scenario>_returns_<status>` per FR-01 / FR-02 / FR-04 / FR-05
- [ ] `test_<scenario>_exit_code` per FR-01 / FR-02 / FR-03 / FR-05
- [ ] `test_submit_injection_<char>_rejected` × 7 (per FR-01 + NFR-02)
- [ ] `test_runner_breaker_open_refuses` (FR-03)
- [ ] `test_runner_retry_exhausted_breaker_count` (FR-03)
- [ ] `test_cache_replay_<case>` (FR-04)

### 10.2 Security Red Team
- [ ] `test_redteam_prompt_injection_<vector>` (NFR-02)
- [ ] `test_redteam_secret_in_stdout_redacted_<pattern>` (NFR-04)

### 10.3 KPI Gates (pytest-benchmark + monitoring thresholds)
- [ ] `test_kpi_submit_status_p95_<50ms>_100_iter` (NFR-01)
- [ ] `test_kpi_submit_status_p95_<100ms>_1000_iter` (NFR-09)
- [ ] `test_kpi_run_all_100_tasks_no_loss` (NFR-09)

### 10.4 Resilience / Fault Injection
- [ ] `test_resilience_<scenario>_recovery_or_failfast` × 4 (NFR-07)
- [ ] `test_resilience_4_process_concurrent_no_corruption` (NFR-08)

### 10.5 Schema Migration
- [ ] `test_migration_v0_to_v1_with_backup` (NFR-10)
- [ ] `test_migration_v2_refuses` (NFR-10)

### 10.6 Maintainability
- [ ] `test_docstring_fr_xx_tag_coverage` (NFR-05)
- [ ] `test_env_example_declares_all_8_vars` (NFR-06)

### 10.7 Version Consistency
- [ ] `test_backward_compat_phase<N-1>_tests_pass_in_phase<N>_env`

---

## 11. FR Block (machine-readable)

<!-- FR:START -->
```json
{
  "version": "1.0",
  "created_at": "2026-07-23",
  "phase": 1,
  "project": "taskq",
  "canonical_source": "SPEC.md v4.0.0",
  "functional_requirements": [
    {
      "id": "FR-01",
      "title": "Task submission and validation",
      "spec_section": "SPEC.md §3 FR-01",
      "implementation_functions": ["taskq.cli.submit_command", "taskq.store.add_task"],
      "verification_method": "pytest tests/cli/test_submit.py + tests/store/test_validation.py"
    },
    {
      "id": "FR-02",
      "title": "Task executor",
      "spec_section": "SPEC.md §3 FR-02",
      "implementation_functions": ["taskq.executor.run_task", "taskq.executor.run_all"],
      "verification_method": "pytest tests/executor/test_run.py + tests/integration/test_run_all.py"
    },
    {
      "id": "FR-03",
      "title": "Retry and circuit breaker",
      "spec_section": "SPEC.md §3 FR-03",
      "implementation_functions": ["taskq.executor.run_with_retry", "taskq.breaker.CircuitBreaker"],
      "verification_method": "pytest tests/breaker/test_state_machine.py + tests/integration/test_retry.py"
    },
    {
      "id": "FR-04",
      "title": "Result TTL cache",
      "spec_section": "SPEC.md §3 FR-04",
      "implementation_functions": ["taskq.cache.Cache.get", "taskq.cache.Cache.put"],
      "verification_method": "pytest tests/cache/test_ttl.py + tests/integration/test_cached_run.py"
    },
    {
      "id": "FR-05",
      "title": "CLI integration",
      "spec_section": "SPEC.md §3 FR-05",
      "implementation_functions": ["taskq.cli.main", "taskq.cli.build_parser"],
      "verification_method": "pytest tests/cli/test_argparse.py + tests/integration/test_cli_exit_codes.py"
    }
  ],
  "non_functional_requirements": [
    {
      "id": "NFR-01",
      "type": "performance",
      "spec_section": "SPEC.md §4 NFR-01",
      "description": "submit+status p95 < 50ms over 100 iter",
      "test_method": "pytest-benchmark tests/perf/test_p95_latency.py"
    },
    {
      "id": "NFR-02",
      "type": "security",
      "spec_section": "SPEC.md §4 NFR-02",
      "description": "shell=True forbidden; FR-01 blacklist has test coverage",
      "test_method": "grep gate + pytest tests/security/test_injection_blacklist.py"
    },
    {
      "id": "NFR-03",
      "type": "reliability",
      "spec_section": "SPEC.md §4 NFR-03",
      "description": "atomic writes; breaker recovery ≤ cooldown+1s",
      "test_method": "pytest tests/integration/test_atomic_write.py + tests/integration/test_breaker_recovery.py"
    },
    {
      "id": "NFR-04",
      "type": "security",
      "spec_section": "SPEC.md §4 NFR-04",
      "description": "stdout_tail/stderr_tail redact (sk-*/token=) before persistence",
      "test_method": "pytest tests/security/test_secret_redaction.py"
    },
    {
      "id": "NFR-05",
      "type": "maintainability",
      "spec_section": "SPEC.md §4 NFR-05",
      "description": "public fns have docstring with [FR-XX] tag",
      "test_method": "pytest tests/static/test_docstring_fr_tags.py"
    },
    {
      "id": "NFR-06",
      "type": "deployability",
      "spec_section": "SPEC.md §4 NFR-06",
      "description": "8 TASKQ_* env vars read via config.py; .env.example declared",
      "test_method": "pytest tests/config/test_env_loader.py + tests/deploy/test_env_example.py"
    },
    {
      "id": "NFR-07",
      "type": "resilience",
      "spec_section": "SPEC.md §4 NFR-07",
      "description": "fault injection scenarios handled (recovery or fail-fast); --inject-fault test-only",
      "test_method": "pytest tests/integration/test_fault_injection.py"
    },
    {
      "id": "NFR-08",
      "type": "concurrency",
      "spec_section": "SPEC.md §4 NFR-08",
      "description": "cross-process flock; network-fs degrade + WARNING",
      "test_method": "pytest tests/integration/test_cross_process.py"
    },
    {
      "id": "NFR-09",
      "type": "scalability",
      "spec_section": "SPEC.md §4 NFR-09",
      "description": "1000-task p95 < 100ms; run --all 100 tasks no loss; < 100MB peak",
      "test_method": "pytest-benchmark tests/perf/test_scalability.py + tests/integration/test_run_all_no_loss.py"
    },
    {
      "id": "NFR-10",
      "type": "evolvability",
      "spec_section": "SPEC.md §4 NFR-10",
      "description": "version field; v0→v1 migrate; backup retained; upgrade refusal on v>1",
      "test_method": "pytest tests/integration/test_schema_migration.py"
    }
  ]
}
```
<!-- FR:END -->
