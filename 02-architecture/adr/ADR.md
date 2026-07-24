# Architecture Decision Records (ADR) — taskq

> Scope: Phase 2 architecture decisions for `taskq`, a local task-queue CLI. Each ADR below traces to `02-architecture/SAD.md` and, where noted, to the SAB/security blocks embedded there. Source of truth for requirements is the `SPEC.md` v4.0.0 specification and its `01-requirements/SRS.md` transcription. The Traceability Matrix immediately below maps every ADR to the FR-IDs/NFR-IDs it satisfies and the specification source for each.

## Traceability Matrix

Each ADR below owns one or more FR/NFR-IDs from `01-requirements/SRS.md`, itself a transcription of the `SPEC.md` specification. Most requirements have one clear owning decision; three (FR-01, NFR-01, NFR-04) are cross-cutting — no single ADR designs them in isolation — so their row names the ADRs/modules that jointly satisfy them, per SAD.md, rather than inventing a sole owner.

| ADR | Decision | FR/NFR Served | Specification Source |
|-----|----------|----------------|------------------------|
| ADR-001 | stdlib-only runtime | NFR-06 | SPEC.md §4 NFR-06 / SRS.md NFR-06 |
| ADR-002 | layered module architecture, one-directional dependency graph | NFR-05 | SPEC.md §4 NFR-05 / SRS.md NFR-05 |
| ADR-003 | atomic persistence via temp-file + `os.replace` | NFR-03, NFR-07 | SPEC.md §4 NFR-03 / NFR-07 |
| ADR-004 | persisted circuit-breaker state machine | FR-03, NFR-03 | SPEC.md §3 FR-03 |
| ADR-005 | `ThreadPoolExecutor` for `run --all` | NFR-09 | SPEC.md §4 NFR-09 |
| ADR-006 | best-effort cross-process file locking | NFR-08 | SPEC.md §4 NFR-08 |
| ADR-007 | `shell=False` + injection guard | FR-02, NFR-02 | SPEC.md §3 FR-02 / §4 NFR-02 |
| ADR-008 | `sha256(command)`-keyed TTL cache | FR-04 | SPEC.md §3 FR-04 |
| ADR-009 | versioned persistence with migration | NFR-10 | SPEC.md §4 NFR-10 |
| ADR-010 | stable CLI façade and explicit service interfaces | FR-05 | SPEC.md §3 FR-05 |
| cross-cutting | FR-01's validation rules split across ADR-002 (`cli`/`models` placement) and ADR-007 (the injection-character rejection ADR-007 decision (1) describes is the same check FR-01 requires); no ADR owns FR-01 alone | FR-01 | SPEC.md §3 FR-01 |
| cross-cutting | NFR-01's `submit`+`status` p95 latency budget is a property of ADR-002's thin entry layer plus ADR-003's atomic-write cost, not a dedicated decision | NFR-01 | SPEC.md §4 NFR-01 |
| cross-cutting | NFR-04 secret redaction is implemented in `taskq.executor` per SAD.md's NFR-04 row (`test_secret_redaction_before_persist`); ADR-005 mentions the redaction regex only as a GIL/CPU-cost aside, not as the decision governing redaction design | NFR-04 | SPEC.md §4 NFR-04 |

---

## ADR-001: Python 3.11 standard-library-only runtime

### Status
Accepted

### Context
`taskq` must be deployable as a self-contained CLI (SAD §1, NFR-06) with no install-time dependency resolution. The development environment's interpreter is CPython 3.11.15 (`.venv/bin/python --version`). SPEC.md mandates zero runtime dependencies outside the standard library.

### Decision
Target Python 3.11 and restrict all runtime imports to the standard library (`argparse`, `subprocess`, `shlex`, `json`, `threading`, `concurrent.futures`, `fcntl`/`msvcrt`, `hashlib`, `os`, `dataclasses`, etc.). No third-party package is imported by `src/taskq/*`.

### Rationale
A stdlib-only runtime removes the need for a package manager, a lockfile, or a virtual environment at deployment time — the SAD states deployment is "a directory plus environment" (NFR-06). It also removes an entire class of supply-chain and version-drift risk for a single-purpose local tool.

### Consequences
- Positive: zero install step; no dependency-version conflicts; smallest possible attack surface for supply-chain risk.
- Positive: NFR-06 (deployability) is satisfied structurally, not by convention.
- Negative: reimplementation of conveniences a third-party library would provide (e.g., no `tenacity` for retry, no `pydantic` for validation) — this cost is absorbed by ADR-004 and hand-written validation in `taskq.cli`.
- Negative: locking is done through low-level, platform-specific primitives (`fcntl` vs `msvcrt`), each needing its own code path (see ADR-006).

### Alternatives Considered
- **Allow a minimal dependency set (e.g., `click`, `pydantic`)** — rejected: SPEC.md's zero-dependency mandate is explicit, and the CLI surface (5 subcommands) is small enough that `argparse` is sufficient.
- **Target Python 3.9 for broader compatibility** — rejected: no compatibility requirement exists in SPEC.md; 3.11 is the interpreter actually available and offers `tomllib`/typing improvements the project can use later without a new decision.

---

## ADR-002: Layered module architecture with a one-directional dependency graph

### Status
Accepted

### Context
SPEC.md §6 fixes the file tree; SAD §2.1–§2.4 requires that no module become a "god module" and that no dependency cycle exist. Five concerns — CLI dispatch, execution policy, circuit-breaker state, TTL cache, and persistence — must all share `tasks.json`-style storage without importing each other in conflicting directions.

### Decision
Adopt a five-layer architecture, encoded machine-readably in the SAD §5 SAB block: `entry` (`__main__`, `cli`) → `execution` (`executor`) → `policy` (`breaker`, `cache`) → `persistence` (`store`) → `foundation` (`config`, `models`). Dependencies flow strictly downward; `foundation` modules import nothing application-specific. `breaker` and `cache` are siblings in `policy` and both depend only on `persistence`/`foundation`, never on `execution` or each other.

### Rationale
A strict downward-only graph makes cycle detection a structural property instead of a code-review discretion. Placing `breaker` and `cache` as siblings under `policy` — rather than folding their logic into `executor` — keeps each persistence owner (`store`, `breaker`, `cache`) responsible for exactly one JSON file (`tasks.json`, `breaker.json`, `cache.json`), which is the basis for ADR-003's per-file atomic-write guarantee.

### Consequences
- Positive: `architecture_constraints: ["no_circular_dependencies"]` in the SAB is enforceable by static import analysis.
- Positive: `config` and `models` being leaf modules means the two most-imported modules can be tested without any I/O or process mocking.
- Negative: cross-cutting changes (e.g., a new field on the task record) must propagate through `models` and be re-validated at every layer that touches it, since no shortcut import path exists.

### Alternatives Considered
- **Single flat `taskq/core.py` module** — rejected: directly reintroduces the god-module risk the SAD explicitly forbids.
- **`executor` owning breaker and cache logic directly (no separate modules)** — rejected: would blend three independent JSON files and three independent policies (execution, breaker, cache) into one module's responsibility, weakening the "does not do" boundaries in SAD §2.3.

---

## ADR-003: Atomic persistence via temp-file-plus-`os.replace`

### Status
Accepted

### Context
`tasks.json`, `breaker.json`, and `cache.json` must survive interruption (process kill, disk-full, mid-write OSError) without corruption (SAD §1 invariants, NFR-03, NFR-07). POSIX and Windows both guarantee `os.replace` is atomic within the same filesystem, but a naive `open(path, "w")` followed by `json.dump` is not.

### Decision
All three persistence owners write through one shared primitive in `taskq.store`: serialize to a temporary file in the same directory, `flush`/`fsync` it, then `os.replace(tmp, target)`. Interruption before the replace leaves the original file untouched; interruption during is not possible because `os.replace` is a single filesystem-level rename. Startup validates JSON and `version` before exposing records; unrecoverable corruption produces an explicit stderr message and exit 1 rather than a silent rebuild.

### Rationale
This is the standard "write-new, rename-over" pattern for crash-safe file updates and is the only approach that meets NFR-03's "valid JSON after interruption" requirement using only the standard library. Routing all three files through one `store` primitive (rather than each module implementing its own write) guarantees the guarantee is uniform and centrally testable.

### Consequences
- Positive: NFR-03 and NFR-07 (fault scenarios: `corrupt-mid-write`, `oserror-on-write`, `disk-full`, `kill-mid-write`) reduce to tests against one code path instead of three.
- Positive: never silently rebuilds state — failures are observable, which SAD §3.4 makes an explicit non-negotiable behavior.
- Negative: each mutation briefly doubles the affected file's disk footprint (temp file coexists with the original until replace); acceptable given task-store files are small JSON documents, not large blobs.
- Negative: atomicity is per-file; a crash between updating `tasks.json` and `breaker.json` can leave the two files in a mutually inconsistent (but each individually valid) state. The design accepts this because breaker/cache state is auxiliary and re-derivable from task history, whereas `tasks.json` is authoritative.

### Alternatives Considered
- **SQLite with WAL mode** — rejected: introduces a schema-migration and query surface disproportionate to a 3-file, single-process-family CLI, and nominally still stdlib (`sqlite3`) but changes the entire persistence model the SPEC already fixes as JSON files.
- **`fsync`-only in-place writes** — rejected: in-place writes cannot be made atomic against a kill signal; a partial write is directly observable as invalid JSON, which NFR-03 forbids.

---

## ADR-004: Circuit breaker as a persisted, global state machine

### Status
Accepted

### Context
FR-03 requires retry-with-backoff and a circuit breaker that prevents repeatedly re-running a command class that is failing systemically. Because `taskq` is a CLI invoked as separate OS processes rather than one long-lived server, breaker state cannot live in memory between invocations.

### Decision
Implement the breaker as an explicit `CLOSED → OPEN → HALF_OPEN → CLOSED/OPEN` state machine (SAD §3.4) owned by `taskq.breaker`, persisted to `breaker.json` through `taskq.store`'s atomic-write primitive (ADR-003). `executor.before_run()` consults breaker state before invoking a subprocess; `record_success()`/`record_failure()` update it. Cooldown is timestamp-based so that a fresh process reading `breaker.json` can independently derive whether cooldown has elapsed, without needing a running daemon.

### Rationale
Persisting the state machine — instead of, e.g., keeping breaker state only per-invocation — is the only way a stateless-per-invocation CLI can honor "recovery ≤ cooldown + 1 s" (NFR-03) across separate `taskq run` calls. Making breaker global (not per-command) matches SPEC's framing of the breaker as protecting the executor as a whole, and keeps the state machine's cardinality at one record instead of one per distinct command string.

### Rationale for placement: making `breaker` a `policy`-layer sibling of `cache` (not part of `executor`) keeps circuit-breaker logic testable and swappable independent of subprocess invocation logic, per ADR-002.

### Consequences
- Positive: breaker state and recovery timing survive process restarts, satisfying NFR-03's cross-invocation recovery bound.
- Positive: `HALF_OPEN` admission is a pure function of persisted timestamps, so no background thread or daemon is required.
- Negative: breaker is global rather than per-command; a systemic failure in one command class can block admission for all commands until cooldown, which is an explicit design trade-off favoring simplicity over granularity, consistent with SPEC's single global-breaker requirement.
- Negative: every `run` invocation pays one extra `breaker.json` read/write, adding I/O beyond the task record itself.

### Alternatives Considered
- **In-memory breaker scoped to one process** — rejected: cannot satisfy cross-invocation cooldown behavior since each CLI call is a fresh process.
- **Per-command breaker keyed by command signature** — rejected: not specified by SPEC.md, and would require unbounded state growth as distinct command strings accumulate; a global breaker keeps `breaker.json` a single fixed-shape record.

---

## ADR-005: `ThreadPoolExecutor` for `run --all` parallelism

### Status
Accepted

### Context
`run --all` must process potentially many pending tasks (NFR-09: 100-task `run --all` with no loss, 1000-task p95 targets) while `subprocess.run` itself is a blocking call. `taskq` is stdlib-only (ADR-001), ruling out third-party async or process-pool frameworks beyond what the standard library provides.

### Decision
Use `concurrent.futures.ThreadPoolExecutor(max_workers=TASKQ_MAX_WORKERS)` in `taskq.executor.run_all()`. Each worker thread runs one task's full lifecycle (subprocess invocation, breaker/cache interaction, result persistence); persistence operations acquire the shared in-process lock, and the file adapter additionally acquires the cross-process lock (ADR-006) for the actual write.

### Rationale
Threads, not processes, are the correct concurrency unit here because the actual work being parallelized is `subprocess.run` — which releases the GIL while the child process runs — not CPU-bound Python computation. A `ThreadPoolExecutor` gives bounded concurrency (`TASKQ_MAX_WORKERS`) with a stdlib-only dependency, directly satisfying ADR-001, while `multiprocessing` would add process-spawn overhead and IPC complexity with no compute benefit since the actual computation happens in a child OS process either way.

### Consequences
- Positive: bounded worker count caps peak memory and concurrent file-lock contention, supporting NFR-09's <100 MB peak-memory target.
- Positive: threads share the in-process lock trivially (a single `threading.Lock`), simplifying the "no worker writes JSON directly" invariant in SAD §3.3.
- Negative: Python's GIL means CPU-bound work inside the Python side of each task (JSON serialization, redaction regex) is not truly parallel — acceptable because that work is small relative to subprocess wall-clock time.
- Negative: a slow or hanging subprocess in one worker does not block others, but does hold a thread for its full timeout duration, so `TASKQ_MAX_WORKERS` directly caps effective throughput under many slow tasks.

### Alternatives Considered
- **`multiprocessing.Pool`** — rejected: adds process-spawn and pickling overhead for no parallel-compute gain, since the parallelism already comes from OS-level subprocesses; also complicates sharing the in-process lock and breaker/cache state across pool workers.
- **Sequential execution of `run --all`** — rejected: cannot meet NFR-09's throughput target for 100 pending tasks within a reasonable wall-clock bound.
- **`asyncio` with `asyncio.create_subprocess_exec`** — rejected: would require rewriting `executor`, `store`, `breaker`, and `cache` as async-aware, a broader change than the concurrency requirement justifies; `ThreadPoolExecutor` achieves the same bounded-parallelism goal without an async rewrite of the whole module graph.

---

## ADR-006: Best-effort cross-process file locking layered over atomic writes

### Status
Accepted

### Context
NFR-08 requires that four concurrent `taskq` processes preserve integrity of all three JSON files, and that degraded locking on network filesystems be observable rather than silently unsafe. `os.replace` (ADR-003) is atomic for a single write but does not serialize concurrent read-modify-write sequences across processes (two processes could each read, modify, and replace, with the second silently discarding the first's update).

### Decision
Layer an advisory file lock on top of the atomic-write primitive: `fcntl.flock` on POSIX, `msvcrt.locking` on Windows, shared locks for reads and exclusive locks for writes. This lock serializes the read-modify-write critical section; the atomic write remains the guarantee against partial-write corruption if a process is killed while holding the lock. When the lock is detected as unsupported or degraded (e.g., certain network filesystems), the store emits a `WARNING` rather than silently proceeding unprotected.

### Rationale
Locking and atomic-write solve different problems: `os.replace` prevents corruption from a torn write; `flock`/`msvcrt.locking` prevents lost updates from interleaved read-modify-write across processes. Both are needed together to satisfy NFR-08 ("preserve all three JSON files" under 4 concurrent processes) without a database or lock-server dependency, consistent with ADR-001's stdlib-only constraint.

### Consequences
- Positive: normal local-filesystem operation gets real mutual exclusion; the design explicitly documents (rather than hides) the network-filesystem degradation case via a `WARNING` log, meeting NFR-08's disclosure requirement.
- Positive: platform-specific code (`fcntl` vs `msvcrt`) is isolated to one adapter in `taskq.store`, not duplicated across `breaker`/`cache`.
- Negative: file locking is advisory, not mandatory — a process that does not go through `taskq.store` (e.g., a hand-edit of `tasks.json`) is not protected.
- Negative: on network filesystems where locking is unreliable, the guarantee silently narrows to "atomic write only, no cross-process serialization"; this is accepted as a documented degradation rather than a blocking requirement, since SPEC.md treats network-FS as a warn-and-continue case, not a hard failure.

### Alternatives Considered
- **Atomic write only, no locking** — rejected: does not prevent lost updates from concurrent read-modify-write, failing NFR-08's 4-process integrity test.
- **A lock file plus PID-based staleness detection** — rejected: reimplements what `flock`/`msvcrt.locking` already provide at the OS level, adding staleness-detection edge cases (stale PID reuse) without benefit.
- **SQLite for its built-in locking** — rejected for the same reasons as in ADR-003 (persistence model mismatch with SPEC's fixed JSON-file design).

---

## ADR-007: Subprocess execution via `shlex.split` with `shell=False`, plus a source-level `shell=True` guard

### Status
Accepted

### Context
FR-02 requires executing an arbitrary user-submitted command string as a subprocess. NFR-02 mandates no shell-injection vector and forbids `shell=True` anywhere in the codebase. The threat model in SAD §6 (T-01, T-02) treats both malicious input and accidental regression as first-class threats.

### Decision
Two independent controls, both required: (1) `taskq.cli` rejects command strings containing `; | & $ > < ` `` ` `` before a task is ever persisted, returning exit code 2; (2) `taskq.executor` tokenizes the command with `shlex.split` and invokes it via `subprocess.run(argv, shell=False, ...)`, never interpolating the raw string into a shell. A source-level guard test (`test_no_shell_true_in_source`) greps the codebase to fail the build if `shell=True` is ever introduced.

### Rationale
Rejecting shell metacharacters at the CLI boundary stops obviously malicious input early and cheaply (before any file I/O). Using `shlex.split` + `shell=False` in the executor is the defense that actually matters, because it removes the shell interpreter from the execution path entirely — even a command that somehow bypassed the blacklist cannot achieve shell injection with `shell=False`, since there is no shell to inject into. The source-grep guard exists because NFR-02 treats a future regression (someone adding `shell=True` for convenience) as a threat in its own right (T-02), not just a design given.

### Consequences
- Positive: two independent layers (input validation + execution-mode enforcement) mean a single mistake in one layer does not by itself reopen the injection vector.
- Positive: the source-grep guard turns a future regression into a CI-visible test failure instead of a silent vulnerability.
- Negative: the metacharacter blacklist also rejects legitimate commands that happen to need those characters (e.g., a pipeline the user genuinely wants) — accepted per SPEC.md's explicit choice to disallow shell composition entirely rather than support a safe subset.
- Negative: `shlex.split` has its own parsing rules (quoting, escaping) that can surprise users relative to their shell's actual parsing; this is a documented behavior difference, not a defect.

### Alternatives Considered
- **`shell=True` with manual escaping (`shlex.quote`)** — rejected: escaping-based injection defense has a long history of bypasses; removing the shell entirely is strictly stronger and is what NFR-02 mandates.
- **Blacklist only, no `shell=False` enforcement** — rejected: a blacklist alone is a single point of failure; SAD §6 T-02 explicitly treats this combination as insufficient on its own.
- **Allowlist of permitted commands** — rejected: not specified by SPEC.md, and would turn `taskq` into a restricted-command runner rather than the general task-queue CLI the SPEC defines.

---

## ADR-008: Result cache keyed by `sha256(command)` with TTL

### Status
Accepted

### Context
FR-04 requires that a `run --cached` invocation can return a recent successful result without re-spawning a subprocess, bounded by a TTL. The cache must not collide across distinct commands and must not need to persist the full task history to serve a hit.

### Decision
`taskq.cache` computes `sha256(command)` as the cache key, stores `{result, timestamp}` under that key in `cache.json` (via `taskq.store`'s atomic-write primitive), and on `lookup` checks `now - timestamp < TASKQ_CACHE_TTL` before returning a hit. Only `done` (successful) results are cached; `executor` consults the cache before spawning a subprocess when `--cached` is passed.

### Rationale
`sha256` gives a fixed-width, collision-resistant key independent of command length, so `cache.json` entries are uniform regardless of how long a submitted command string is, and equal commands always map to the same entry without needing a separate lookup index. TTL-based expiry (rather than manual invalidation) is the simplest mechanism that satisfies FR-04's "recent" requirement without introducing a separate invalidation API.

### Consequences
- Positive: cache lookup is O(1) by key with no need to scan task history.
- Positive: caching only `done` results means a `failed`/`timeout` result never masks a real failure behind a stale success.
- Negative: `sha256(command)` caches on the literal command string; two commands that are semantically equivalent but textually different (e.g., differing whitespace) are treated as distinct cache entries — accepted as the simplest correct behavior, since normalizing "equivalent" commands is not in SPEC's scope.
- Negative: cache entries for commands never re-run are not proactively evicted; `cache.json` grows unboundedly over the life of the task store. This is accepted because SPEC.md does not specify an eviction policy beyond TTL-based staleness at read time, and `clear` provides a manual reset path.

### Alternatives Considered
- **Cache keyed by task id** — rejected: would only allow re-fetching a specific task's own prior result, not recognizing that a different task submitting the identical command could reuse it, which is the actual FR-04 intent (avoid re-running identical work).
- **In-memory-only cache** — rejected: does not survive across CLI invocations, which (like ADR-004) are separate processes; TTL-based reuse across invocations requires persistence.
- **LRU eviction with a size cap** — rejected as unspecified scope creep: SPEC.md defines only a TTL, not a size bound; adding one would be speculative design not traceable to a requirement.

---

## ADR-009: Versioned persistence roots with explicit migration and future-version rejection

### Status
Accepted

### Context
NFR-10 requires every persisted JSON root to carry `version: 1`, older versions to be migrated with a backup, future (higher) versions to be rejected, and migration failure to fail fast rather than guess. `store`, `breaker`, and `cache` each own a separate file, and the migration logic must be sharable rather than duplicated three times (per ADR-002's layering).

### Decision
`taskq.store` provides shared version-validation and migration primitives used by all three persistence owners. On read: version < 1 triggers a backup (`<file>.v<n>.bak`) then an atomic write-back of the migrated document (ADR-003); version > 1 is rejected with an explicit upgrade-required error; migration failure exits 1 with the backup preserved as evidence, never silently discarding or rebuilding state.

### Rationale
Centralizing migration logic in `store` (rather than each of `store`/`breaker`/`cache` reimplementing it) keeps the version-handling contract uniform across all three files and testable once. Rejecting future versions outright — rather than attempting a best-effort read — prevents an older `taskq` binary from misinterpreting a schema it does not understand, which is the failure mode NFR-10 is explicitly designed to prevent.

### Consequences
- Positive: schema evolution has one designed extension point (the shared migration primitive) instead of three independent, potentially inconsistent implementations.
- Positive: a backup file is always retained before migration or on migration failure, so the pre-migration state is recoverable evidence rather than lost.
- Negative: this is a startup-time cost paid once per outdated file, and backup files accumulate on disk if migration failures repeat without operator cleanup — accepted since NFR-10 explicitly prioritizes safety (evidence retention) over disk economy.
- Negative: rejecting future versions means a newer `taskq.json` written by a future binary version cannot be read by an older binary at all (not even in a degraded mode) — this is a deliberate fail-fast choice, not an oversight.

### Alternatives Considered
- **Silent best-effort read of any version** — rejected: SAD §3.4 explicitly forbids silently rebuilding state, and reading a future schema optimistically risks misinterpreting fields.
- **Per-module migration logic (each of `store`/`breaker`/`cache` handling its own versioning independently)** — rejected: triplicates logic that has identical shape across all three files, increasing the chance of an inconsistent implementation in one of the three.

---

## ADR-010: Stable CLI façade and explicit service interfaces

### Status
Accepted

### Context
`taskq` is invoked as a fresh process through `python -m taskq`, while persistence, execution, breaker, and cache behavior live in separate modules (SAD §2.3 and §3.1–§3.2). The five subcommands, machine-readable output, and exit statuses are user-facing compatibility contracts. Without one boundary that owns parsing, rendering, and error translation, service modules would become coupled to `argparse`, stdout/stderr formatting, and process exit behavior.

### Decision
Use `taskq.__main__` only to invoke `taskq.cli.main()` and return its status. Keep `taskq.cli` as the façade that owns the `submit`, `run`, `status`, `list`, and `clear` grammar; command validation orchestration; `--json`, `--cached`, `--all`, and test-only fault-injection parsing; output rendering; and exception-to-exit-code mapping. Successful `--json` output is one line, operational and validation errors go to stderr, and exit statuses retain the SAD contract: `0` success, `1` internal/persistence failure, `2` usage/validation/conflict/unknown task, `3` breaker open, and `4` single-task timeout.

Behind the façade, modules expose narrow service contracts rather than parsing CLI arguments: store CRUD/iteration/clear operations; `executor.run_task(id, use_cache)` and `executor.run_all()`; breaker admission and result-recording operations; and cache lookup/put operations. The executor and policy modules return records, summaries, admission decisions, or explicit errors; only `cli` converts those outcomes into text, JSON, and process status. Production invocations reject the fault-injection option before any fault hook runs.

### Rationale
A single façade keeps the external contract stable while allowing persistence and execution policy to be tested without constructing argv or capturing terminal output. Narrow service interfaces also enforce ADR-002's dependency direction: services do not import the entry layer, and only the entry layer coordinates user-visible outcomes across services.

### Consequences
- Positive: every command has one deterministic path for parsing, stderr/stdout selection, JSON encoding, and exit-code translation.
- Positive: service modules remain reusable from tests and from `run --all` workers without CLI dependencies.
- Positive: the production rejection of fault injection is enforced at the same trust boundary that accepts user-controlled argv.
- Negative: `taskq.cli` is the integration hub and therefore depends on several lower layers; it must remain limited to orchestration and formatting to avoid becoming a god module.
- Negative: exit-code meanings and single-line JSON output become compatibility commitments; changing them requires an explicit interface revision.

### Alternatives Considered
- **Let each service module parse arguments and print its own output** — rejected: duplicates formatting/error rules and reverses the dependency direction by coupling lower layers to the CLI.
- **Expose only one untyped `dispatch(command, payload)` service function** — rejected: hides the distinct store, execution, breaker, and cache contracts and concentrates unrelated responsibilities in one dispatcher.
- **Introduce a daemon or RPC interface** — rejected: separate lifecycle management and transport are unnecessary for the specified local, invocation-scoped CLI and would conflict with the stdlib-only, directory-plus-environment deployment model.
