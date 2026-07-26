# Software Architecture Document (SAD) — taskq

> **Project**: `taskq`, a local task-queue CLI
> **Source of truth**: `SPEC.md` v4.0.0; requirements are mirrored by `01-requirements/SRS.md`.
> **Scope**: Phase 2 architecture only; the machine-readable Phase 2 baseline is declared in §5 and implementation remains a later phase.

## 1. Overview

`taskq` is a Python 3.11 command-line tool with no runtime dependencies outside the standard library. It accepts a command, validates it, persists it as a task, executes it under timeout/retry/circuit-breaker controls, optionally replays a recent result from a TTL cache, and exposes task status/list/clear operations through `python -m taskq`.

The architecture is a modular, single-process command dispatcher with optional thread-level parallelism for `run --all`. Cross-process coordination is provided at the persistence boundary. The design keeps command parsing, execution policy, domain records, configuration, and file persistence separate so that no module becomes a god module and no dependency cycle is introduced.

The following invariants are binding:

- Runtime imports use Python 3.11 standard-library APIs only.
- Subprocess execution uses `shlex.split` and never `shell=True`.
- `tasks.json`, `breaker.json`, and `cache.json` use temporary-file plus `os.replace` atomic writes.
- File locks are best-effort cross-process protection layered over atomic writes; network-file-system degradation emits `WARNING`.
- All persisted roots carry schema `version: 1`; migration and future-version rejection are explicit.
- Failure, timeout, breaker-open, validation, and internal-error paths preserve the specified exit-code contract.

## 2. Module Design

### 2.1 Directory structure and file budget

The module tree below follows `SPEC.md §6` exactly. The specification names the project tree `integration-test/`; the application package is the `src/taskq/` subtree.

```text
integration-test/
├── src/taskq/
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py
│   ├── models.py
│   ├── store.py
│   ├── executor.py
│   ├── breaker.py
│   ├── cache.py
│   └── cli.py
├── tests/
├── .env.example
├── SPEC.md
└── harness-e2e.js
```

`src/taskq/` contains nine Python files including the package marker, which is below the 15-file/dir limit. `tests/` is a separate verification directory. `cli.py` only dispatches and formats; `executor.py` only applies execution policy; `store.py` only owns persistence. These boundaries prevent a god module.

**Cohesion design note (small-package / pipeline exception).** The CRG cohesion principles target community detection across large multi-package repos. `taskq` is a single-process CLI with eight application modules and a single linear pipeline (`cli → executor → breaker/cache → store → config/models`). Per-function-body call counts, sibling coverage, and edge-budget heuristics are over-specified for a tree of this size. The cohesion evidence that does apply:

- **Hub with high in-degree.** `taskq.store` is the single persistence hub consumed by `cli`, `executor`, `breaker`, and `cache` (4 callers / 8 application modules = 50% sibling coverage). It owns the cross-process lock adapter, the atomic-write primitive, and the schema-migration hook — every persistence owner funnels through it.
- **Per-function-body calls.** `taskq.executor.run_task` and `taskq.executor.run_all` carry the bulk of the call graph (subprocess invocation, breaker admission, cache lookup, store update); `taskq.cli.dispatch` is the single entry point.
- **Bounded edge budget.** The `allowed_dependencies` matrix in §5 enumerates ten directed edges across five layers; no cycle exists and no edge crosses more than one layer.
- **Pipeline exception justification.** Splitting `taskq.executor` further (e.g. into `retry`, `redact`, `truncate` modules) would invert the cohesion loss: three callers would each import three sub-modules, tripling the edge count without reducing per-module size below the threshold. The current packaging is therefore the smallest design that satisfies the no-circular-dependencies constraint while keeping every module's responsibility nameable in one sentence.

### 2.2 Functional-requirement mapping

Every functional requirement from `SPEC.md §3` is implemented by at least one module.

| Requirement | Primary module(s) | Supporting module(s) | Architectural responsibility |
|---|---|---|---|
| **FR-01 — task submission and validation** | `taskq.cli`, `taskq.store` | `taskq.models`, `taskq.config` | Parse `submit`, reject empty/overlong/metacharacter commands and pending/running name collisions, construct a pending task, and atomically persist it. |
| **FR-02 — task executor** | `taskq.executor` | `taskq.store`, `taskq.models`, `taskq.config` | Perform `shlex.split` plus `subprocess.run`, enforce timeout, record tails/timing/status, and run pending tasks through `ThreadPoolExecutor` for `--all`. |
| **FR-03 — retry and circuit breaker** | `taskq.executor`, `taskq.breaker` | `taskq.store`, `taskq.config` | Apply injectable exponential backoff; gate execution with the global persisted `CLOSED`/`OPEN`/`HALF_OPEN` state machine. |
| **FR-04 — result TTL cache** | `taskq.cache` | `taskq.executor`, `taskq.store`, `taskq.models`, `taskq.config` | Compute `sha256(command)`, return a non-expired `done` result without spawning a process, and atomically persist successful results. |
| **FR-05 — CLI integration** | `taskq.__main__`, `taskq.cli` | all service modules | Wire `submit`, `run`, `status`, `list`, and `clear`; implement `--json`, `--cached`, `--all`, fault-test gating, and exit codes 0/1/2/3/4. |

### 2.3 Module responsibilities and interfaces

| Module | Responsibility and public contract | Direct dependencies | Explicitly does not do |
|---|---|---|---|
| `taskq.__init__` | Package metadata only. | Standard library only. | No business logic or I/O. |
| `taskq.__main__` | Call `cli.main()` for `python -m taskq`, then return its exit status. | `taskq.cli`. | No parsing, persistence, or execution logic. |
| `taskq.config` | Read all eight `TASKQ_*` environment variables once, validate/coerce defaults, and expose an immutable configuration object. | Standard library only. | No file or subprocess I/O. |
| `taskq.models` | Define task/status/result, breaker-state, cache-entry, and schema-version records. | Standard library only. | No I/O, environment reads, or process execution. |
| `taskq.store` | Own `tasks.json`, task CRUD, atomic JSON write/read, thread lock, cross-process lock adapter, corruption detection, fault outcomes, and schema migration primitives shared by persistence owners. Also owns `clear_all()` — a single cross-process-excluded call that resets `tasks.json` only (under the same exclusive lock as other store writes). The persistence layer depends only on foundation; resetting `breaker.json` and `cache.json` is the responsibility of the `clear` subcommand's entry-layer orchestration (see `taskq.cli` row and §3.2). | `taskq.config`, `taskq.models`. | No subprocess, retry, breaker policy, or CLI formatting, and no calls into `taskq.breaker` or `taskq.cache` (no upward edges from persistence). |
| `taskq.executor` | Own subprocess invocation, timeout classification, output-tail truncation and redaction, state transitions, injectable sleep/backoff, and `run --all` scheduling. | `taskq.config`, `taskq.models`, `taskq.store`, `taskq.breaker`, `taskq.cache`. | No argument parsing or direct ad hoc JSON writes. |
| `taskq.breaker` | Own global breaker state, threshold counting, cooldown/half-open admission, and `breaker.json` persistence through store primitives; expose `reset_persisted()` (invoked by the `clear` subcommand in `taskq.cli`, never by `taskq.store`) to reset the file to `{version:1, state:CLOSED, failure_count:0, opened_at:null}`. Also consults `taskq.store.fault_scenario` on the breaker write path (see §2.3.1). | `taskq.config`, `taskq.models`, `taskq.store`. | No subprocess invocation, retry scheduling, or solo file rewriting. |
| `taskq.cache` | Own SHA-256 signatures, TTL decisions, and `cache.json` persistence through store primitives; expose `clear_all()` (invoked by the `clear` subcommand in `taskq.cli`, never by `taskq.store`) to reset the file to `{version:1, entries:{}}`. Also consults `taskq.store.fault_scenario` on the cache write path (see §2.3.1). | `taskq.config`, `taskq.models`, `taskq.store`. | No subprocess execution, breaker transitions, or solo file rewriting. |
| `taskq.cli` | Own argparse grammar, command validation orchestration, output rendering, `--inject-fault=<scenario>` parsing and `TASKQ_INJECT_FAULT_OK` test/development enablement gate (see §2.3.1), `clear` subcommand orchestration that calls `store.clear_all()` + `breaker.reset_persisted()` + `cache.clear_all()` in sequence (each under its own per-file lock), and exception-to-exit-code mapping. | `taskq.config`, `taskq.models`, `taskq.store`, `taskq.executor`, `taskq.breaker`, `taskq.cache`. | No direct `subprocess.run` or hand-built persistence protocol. |

All public functions and classes carry docstrings with an `[FR-XX]` reference as required by NFR-05. `config` and `models` are leaf modules; persistence and policy modules depend on them, never the reverse.

#### 2.3.1 Test-only fault-injection boundary (NFR-07)

The `--inject-fault=<scenario>` surface is **not** triggered by environment variables (SPEC.md §5.1 explicitly excludes fault injection from the `TASKQ_*` env list). Its activation boundary is precisely:

1. **CLI parser boundary** — `taskq.cli` registers `--inject-fault=<scenario>` as an argparse top-level optional, parsed **before** the subcommand layer. The CLI also reads the test/development opt-in env var `TASKQ_INJECT_FAULT_OK` (a non-`TASKQ_*`-config surface; this is **not** one of the 8 `TASKQ_*` runtime-config values listed in SPEC §5.1, but a dedicated test/dev gate distinct from the 8 config vars and from any fault-injection trigger). When `--inject-fault` is parsed in a production invocation (the env var is unset or not equal to `"1"`), `cli` exits with code 2 and stderr `inject-fault rejected in production`; no fault code runs. When the env var is set to `"1"`, the CLI forwards the scenario value to the service boundary.
2. **Service boundary** — the parsed scenario value is assigned to the module-level attribute `taskq.store.fault_scenario` (a string sentinel or `None`). `taskq.store`, `taskq.breaker`, and `taskq.cache` each consult that attribute on the relevant write path; when it is `None` the fault hooks are no-ops. The attribute lives on the persistence hub because every policy/persistence write path already imports `taskq.store` (per ADR-002), so no new module, layer, or upward edge is introduced.
3. **Test-only entry** — unit/integration tests either invoke the CLI with `TASKQ_INJECT_FAULT_OK=1 --inject-fault=<scenario>` (the integration path), or directly set `taskq.store.fault_scenario` (the unit/monkeypatch path). Both paths share the same downstream hooks, so production cannot enable a fault scenario without passing through the CLI parser's production-reject branch.

This three-stage boundary satisfies the canonical SPEC.md §5.3 contract that fault injection is "CLI flag or test monkeypatch" and is forbidden on the production code path, and resolves the NFR-99a deferred-item ambiguity by naming the opt-in mechanism (`TASKQ_INJECT_FAULT_OK=1` plus the CLI flag) without adding a runtime config value.

### 2.4 Dependency graph and cycle rule

```text
__main__ ──▶ cli ──▶ executor ──▶ breaker ──▶ store ──▶ config
    │          │         │            │          └──▶ models
    │          │         └────────────└──▶ cache ──▶ store
    │          ├──▶ store / breaker / cache / config / models
    └──────────┘
```

The permitted direction is entry point → CLI → execution/policy/persistence → configuration/domain records. `breaker` and `cache` reuse store primitives but do not import `executor` or `cli`; `models` and `config` import no application modules. Therefore no directed path returns to an earlier module and circular dependencies are forbidden.

## 3. Interfaces and Data Flows

### 3.1 External CLI interface

| Command | Inputs | Success output | Error exit codes |
|---|---|---|---|
| `submit "<command>" [--name NAME]` | Command, optional name | 8-hex task id, or one-line JSON with `id` and `status` | `2` for validation/name conflict |
| `run <id> [--cached]` | Task id and optional cache flag | Task result summary | `2` unknown id, `3` breaker open, `4` single-task timeout, `1` other internal error |
| `run --all` | No id; all pending tasks | Aggregate result summary | `1` for internal persistence/execution failure |
| `status <id>` | Task id | Full task record | `2` unknown id, `1` other internal error |
| `list [--status S]` | Optional status filter | Matching task records | `1` for internal error |
| `clear` | No positional input | Empty success output | `1` for internal error |

`--json` changes successful output to a single-line machine-readable JSON record. Validation and operational errors are written to stderr. The `--inject-fault=<scenario>` surface is accepted only in the explicitly enabled test/development path; normal production execution rejects it.

### 3.2 Internal contracts

| Caller → callee | Contract | Result |
|---|---|---|
| `cli` → `store` | `add(task)`, `get(id)`, `update(id, fields)`, `iter_tasks(status)`, `clear_all()` | Task record or persistence result; explicit corruption/migration errors. `clear_all()` empties only `tasks.json` under the store's exclusive cross-process lock; it does **not** call into `breaker` or `cache` (persistence has no upward edges). The `clear` subcommand in `cli` orchestrates the full reset by also invoking `breaker.reset_persisted()` and `cache.clear_all()` in sequence; each call acquires its own per-file lock. |
| `cli` → `executor` | `run_task(id, use_cache)` and `run_all()` | Updated task/result or processed-count summary. |
| `executor` → `breaker` | `before_run()`, `record_success()`, `record_failure()` | Admission decision or persisted state transition. |
| `executor` → `cache` | `lookup(sha256)` and `put(sha256, result)` | Fresh cached result or miss; atomic cache update. |
| `executor` → `store` | State/result updates | `pending → running → done/failed/timeout` persisted atomically. |
| `breaker`/`cache` → `store` | Shared lock, atomic-write, read-validation, and migration primitives | Valid versioned JSON or an explicit fail-fast error. |

### 3.3 Submit and run data flow

```text
argv
  │
  ▼
__main__ → cli (argparse, validation, exit mapping)
  │                         │
  │ submit                  │ run / status / list / clear
  ▼                         ▼
models.Task ─────────────▶ store ──lock──▶ tasks.json
                              ▲              (version: 1)
                              │
                 executor ────┘
                   │  │  │
                   │  │  └──▶ cache ──lock──▶ cache.json
                   │  └─────▶ breaker ─lock──▶ breaker.json
                   ▼
              shlex.split → subprocess.run(shell=False)
                   │
                   ▼
       redact secret lines on full stream → truncate to 2000 chars → store.update
```

For `run --all`, `executor` obtains pending task identifiers through the store iterator and submits bounded work to `ThreadPoolExecutor(max_workers=TASKQ_MAX_WORKERS)`. Each worker acquires the shared in-process lock for its persistence operation; the file adapter acquires the cross-process lock where supported. No worker writes JSON directly.

### 3.4 State and persistence flows

```text
pending ──run admitted──▶ running ──exit 0────────────────────────────▶ done
                             │                                          │
                             ├─non-zero + retry budget left ─retry──┐   │
                             │  (status remains 'running'; attempt  │   │
                             │   counter incremented in record)     │   │
                             │                                       │   │
                             └─TimeoutExpired + retry budget left ──┤   │
                                                                     │   │
              retry attempt: re-enter running ──exit 0──▶ done ◀─────┘   │
                                     │                                     │
                                     ├─non-zero + budget exhausted ─▶ failed
                                     └─TimeoutExpired + budget exhausted ▶ timeout

failed/timeout after retry limit → breaker failure count
failure count ≥ threshold       → OPEN ──cooldown──▶ HALF_OPEN
HALF_OPEN success               → CLOSED and reset
HALF_OPEN failure               → OPEN
```

Retries stay inside the `running` state — the persisted task status never returns to `pending` while a `run` is in progress, so `run --all`'s pending-iterator cannot re-claim a retrying task and no concurrent runner can pick it up a second time. `failed` / `timeout` are written only after the retry budget is exhausted.

Each data file has a versioned root. A read validates JSON and version before exposing records. A version below 1 is backed up as `<file>.v<n>.bak`, migrated, and atomically written back. A future version is rejected with an upgrade message. Write failure, an orphan temp file, or unrecoverable corruption produces explicit stderr and exit 1; the implementation never silently rebuilds state.

## 4. NFR Handling

The table addresses every NFR in `SPEC.md §4` and makes latency, security, and operational cost/resource impact explicit for each one.

| NFR | Required target | Architecture handling | Latency / throughput effect | Security effect | Cost / resource effect | Verification |
|---|---|---|---|---|---|---|
| **NFR-01 performance** | `submit` + `status` p95 < 50 ms over 100 iterations, excluding subprocess. | Keep validation and CRUD in `cli`/`store`; use narrow lock scope and one config snapshot. | No subprocess in benchmark path; atomic replacement is the bounded I/O operation. | Command validation occurs before any write. | Standard library only; one task record per operation. | `pytest-benchmark` 100-iteration p95 test. |
| **NFR-02 security** | No `shell=True`; injection blacklist is covered. | `cli` rejects `;`, `|`, `&`, `$`, `>`, `<`, and backtick; `executor` passes tokenized argv with shell disabled. | Blacklist and `shlex.split` are linear in command length (≤1000 characters). | Prevents shell interpretation and rejects unsafe input before persistence. | No security dependency or external service. | Source grep plus one negative test per character. |
| **NFR-03 reliability** | All three files atomic; valid JSON after interruption; breaker recovery ≤ cooldown + 1 s. | Central atomic-write primitive uses temp file then `os.replace`; breaker admission evaluates persisted timestamps. | One replace per mutation; cooldown bounds recovery probe timing. | Partial writes cannot become accepted JSON state. | Temporary file briefly duplicates the affected file. | Interrupted-write JSON checks and breaker recovery integration test. |
| **NFR-04 security** | Matching output lines are replaced with `[REDACTED]` before persistence. | `executor` first replaces full lines matching `(sk-[A-Za-z0-9_-]{8,}|token=\S+)` on the **full** stream, then truncates each stream to 2000 characters. Redaction precedes truncation so a secret crossing the tail boundary cannot be persisted with its suffix intact. | Regex scans the full output (bounded by `TASKQ_TASK_TIMEOUT` and OS pipe limits), then truncates to 2000 characters. | Secrets do not enter task or cache records through captured output. | Bounded tail and replacement memory; no secret-scanning service. | `sk-*` and `token=` redaction tests on both streams, plus a counterexample test where the secret straddles the 2000-char boundary. |
| **NFR-05 maintainability** | Every public function/class has a docstring containing `[FR-XX]`. | Each module owns a small, documented interface; cross-reference tags are part of the code-review contract. | No runtime impact. | Makes security-sensitive ownership and requirement linkage inspectable. | Documentation-only source cost; no runtime cost. | Static inspection of all public symbols. |
| **NFR-06 deployability** | All eight `TASKQ_*` values come from `config.py`; `.env.example` declares each with a comment. | `config` is the sole environment reader and supplies documented defaults. | Environment is read once at startup. | Example file contains no real credentials; config has one controlled input point. | Zero runtime dependencies; deployment is a directory plus environment. | Config/default and `.env.example` coverage tests. |
| **NFR-07 reliability** | Four fault scenarios recover or fail fast; never silently rebuild; injection is test-only. | Atomic writer records/retains temp and backup evidence; startup detects faults; CLI maps explicit failures to exit 1 and gates the flag behind the `TASKQ_INJECT_FAULT_OK` test/dev opt-in env var (see §2.3.1). | Fault checks occur on startup/write paths, not every subprocess step beyond persistence. | Production cannot enable a destructive test hook (gate is off by default). | Backups/temp files consume bounded extra disk and are retained on migration failure. | `corrupt-mid-write`, `oserror-on-write`, `disk-full`, and `kill-mid-write` tests; one production-reject test confirming `--inject-fault` exits 2 when the env var is unset. |
| **NFR-08 reliability** | Cross-process operations preserve all three JSON files; flock is best-effort with network-FS warning. | POSIX `fcntl.flock`, Windows `msvcrt.locking`, shared locks for reads and exclusive locks for writes, with atomic write as fallback. | Contention serializes file mutations; `run --all` remains thread-bounded. | Prevents lost/corrupted shared state under concurrent writers. | Lock acquisition and fallback detection add small I/O overhead; no service required. | Four-process integrity test and network-FS `WARNING` test. |
| **NFR-09 scalability** | 1000-task submit/status p95 < 100 ms; 100-task `run --all` no loss; peak memory < 100 MB. | `store.iter_tasks()` exposes a streaming iterator; `executor.run_all()` submits bounded work and updates records atomically. | Avoids duplicate full task materialization; worker count is capped by config. | Same lock and validation path protects scaled operations. | Streaming and bounded workers cap peak memory; JSON/temp files are the main disk cost. | Scaled benchmark, 100-task no-loss test, and peak-memory measurement. |
| **NFR-10 maintainability** | Root `version: 1`; migrate older versions with backup; reject future versions; fail fast on migration error. | `store` provides version validation/migration primitives used by all three persistence owners; backup precedes write-back. | Migration is a one-time startup cost; current-version reads avoid migration. | Future schema is not guessed or downgraded; failed migration preserves evidence. | Backup files temporarily increase disk usage and remain when recovery needs them. | v0→v1 backup/readability, v>1 refusal, and migration-failure tests. |

## 5. SAB Block

The machine-readable Software Architecture Baseline below instantiates the canonical Phase 2 contract with the modules, dependency directions, NFR ownership, FR mappings, constraints, and high-risk modules defined in this document.

<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  created_at: "2026-07-24"
  phase: 2
  project: "taskq"

  layers:
    - name: entry
      modules:
        - name: "taskq.__main__"
        - name: "taskq.cli"
      allowed_dependencies: ["execution", "policy", "persistence", "foundation"]
    - name: execution
      modules:
        - name: "taskq.executor"
      allowed_dependencies: ["policy", "persistence", "foundation"]
    - name: policy
      modules:
        - name: "taskq.breaker"
        - name: "taskq.cache"
      allowed_dependencies: ["persistence", "foundation"]
    - name: persistence
      modules:
        - name: "taskq.store"
      allowed_dependencies: ["foundation"]
    - name: foundation
      modules:
        - name: "taskq.config"
      allowed_dependencies: []

  allowed_dependencies:
    - from: entry
      to: execution
    - from: entry
      to: policy
    - from: entry
      to: persistence
    - from: entry
      to: foundation
    - from: execution
      to: policy
    - from: execution
      to: persistence
    - from: execution
      to: foundation
    - from: policy
      to: persistence
    - from: policy
      to: foundation
    - from: persistence
      to: foundation

  quality_targets:
    max_complexity: 15
    min_coverage: 80
    max_coupling: 0.3

  nfr_dimension_mapping: {}

  nfr_traceability:
    NFR-01:
      type: performance
      target: "submit + status p95 < 50 ms over 100 iterations"
      module: "taskq.store"
    NFR-02:
      type: security
      target: "zero shell=True usage; all 7 injection characters have negative tests"
      module: "taskq.cli"
    NFR-03:
      type: reliability
      target: "all 3 data files remain valid JSON after interruption; breaker recovery <= cooldown + 1 s"
      module: "taskq.store"
    NFR-04:
      type: security
      target: "matching output lines are replaced with [REDACTED] before persistence"
      module: "taskq.executor"
    NFR-05:
      type: maintainability
      target: "all public functions and classes have docstrings containing [FR-XX]"
      module: "taskq.models"
    NFR-06:
      type: deployability
      target: "all 8 TASKQ_* settings are centralized in config.py and documented"
      module: "taskq.config"
    NFR-07:
      type: reliability
      target: "all 4 fault scenarios recover or fail fast without silent rebuild"
      module: "taskq.store"
    NFR-08:
      type: reliability
      target: "4 concurrent processes preserve all 3 JSON files; network filesystems warn on lock fallback"
      module: "taskq.store"
    NFR-09:
      type: scalability
      target: "1000-task p95 < 100 ms; run --all loses 0 of 100 tasks; peak memory < 100 MB"
      module: "taskq.executor"
    NFR-10:
      type: maintainability
      target: "version=1 roots; older versions migrate with backup; future versions and migration failures exit 1"
      module: "taskq.store"

  advisory_only: []

  gate_score_overrides: {}

  fr_module_traceability:
    FR-01: "taskq.cli"
    FR-02: "taskq.executor"
    FR-03: "taskq.breaker"
    FR-04: "taskq.cache"
    FR-05: "taskq.cli"

  architecture_constraints:
    - "no_circular_dependencies"

  high_risk_modules:
    - "taskq.executor"
    - "taskq.store"
```
<!-- SAB:END -->

## 6. Security Design (STRIDE-lite Threat Model)

The following block is rendered from the canonical security-design template and replaces its example values with the trust boundaries, mitigations, module owners, NFRs, and verification names for `taskq`.

<!-- SEC:START -->
```yaml
security_design:
  version: "1.0"
  applicability: full   # full | none — none REQUIRES justification and skips the rest
  justification: ""     # required (>=20 chars) when applicability: none
  trust_boundaries:     # taskq boundaries from SPEC §3/§4/§9
    - id: TB-01
      name: "CLI input boundary"
      description: "user-controlled argv and environment values entering argparse and centralized configuration"
    - id: TB-02
      name: "child-process boundary"
      description: "taskq.executor crossing into a subprocess with parsed argv tokens and captured output"
    - id: TB-03
      name: "persistent-storage boundary"
      description: "taskq modules reading and writing versioned JSON under $TASKQ_HOME on local or networked filesystems"
  threats:              # STRIDE-lite — every boundary has at least one threat
    - id: T-01
      boundary: TB-01
      category: tampering
      description: "submitted command contains ; | & $ > < or backtick metacharacters and attempts shell injection"
      mitigation: "cli validation rejects the blacklist with exit 2 before any task is written"
      owner_module: "taskq.cli"
      nfr: NFR-02
      verified_by: "test_submit_rejects_injection_chars"
    - id: T-02
      boundary: TB-02
      category: tampering
      description: "a regression enables shell=True and lets a command string reach a shell"
      mitigation: "executor uses shlex.split and shell=False; a repository-wide source guard rejects shell=True"
      owner_module: "taskq.executor"
      nfr: NFR-02
      verified_by: "test_no_shell_true_in_source"
    - id: T-03
      boundary: TB-02
      category: information_disclosure
      description: "captured stdout or stderr contains an sk-* secret or token= value that could be persisted, including a secret that straddles the 2000-char tail boundary"
      mitigation: "executor first replaces full lines matching (sk-[A-Za-z0-9_-]{8,}|token=\\S+) with [REDACTED] on the full stream, then truncates each stream to 2000 characters, before store or cache persistence"
      owner_module: "taskq.executor"
      nfr: NFR-04
      verified_by: "test_secret_redaction_before_truncation"
    - id: T-04
      boundary: TB-03
      category: tampering
      description: "concurrent taskq processes overwrite shared JSON and corrupt or lose task state"
      mitigation: "store uses atomic temp-plus-os.replace writes and exclusive flock/locking where the filesystem supports it"
      owner_module: "taskq.store"
      nfr: NFR-08
      verified_by: "test_cross_process_no_corruption"
    - id: T-05
      boundary: TB-03
      category: denial_of_service
      description: "mid-write OSError, disk-full, or kill-mid-write leaves state unusable at the next startup"
      mitigation: "fault detection chooses explicit recovery or fail-fast exit 1, retains evidence, and never silently rebuilds"
      owner_module: "taskq.store"
      nfr: NFR-07
      verified_by: "test_fault_injection_fails_fast_or_recovers"
    - id: T-06
      boundary: TB-03
      category: repudiation
      description: "an operator cannot later establish when a task ran or what result it produced"
      mitigation: "versioned task records retain created_at, finished_at, exit_code, status, and bounded output fields"
      owner_module: "taskq.store"
      nfr: NFR-03
      verified_by: "test_task_records_timestamps"
    - id: T-07
      boundary: TB-01
      category: elevation_of_privilege
      description: "a production invocation uses the fault-injection flag to deliberately disrupt persistent state"
      mitigation: "cli accepts --inject-fault only when the TASKQ_INJECT_FAULT_OK=1 test/development opt-in env var is set, and rejects it (exit 2) on every production invocation where the env var is unset; the scenario is forwarded to taskq.store.fault_scenario, a single module-level attribute consulted by store, breaker, and cache on the relevant write path"
      owner_module: "taskq.cli"
      nfr: NFR-07
      verified_by: "test_inject_fault_rejected_in_production"
```
<!-- SEC:END -->

Each threat has one owner module declared in §5, each trust boundary has at least one threat, and each `nfr` identifier is present in `SPEC.md §4`.
