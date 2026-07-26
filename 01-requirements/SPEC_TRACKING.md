# Specification Tracking Matrix — taskq

> On-demand Lazy Load template (populated).
> Canonical spec source: `SPEC.md` v4.0.0 (project root). All citations in
> the `Notes` column point to bare `SPEC.md` (NOT `01-requirements/SPEC.md`).
> Per `R-CANONICAL-SPEC-PATH-001`, the canonical source path is the
> repo-root `SPEC.md`; the harness `check_forward_refs` gate treats
> `01-requirements/SPEC.md` as ILLEGAL.

## Project Info
- Project Name: taskq
- Version: v1.0.0
- Created: 2026-07-24
- Phase: 1 — Requirements
- Canonical Source: SPEC.md v4.0.0 (project root)
- SRS Companion: 01-requirements/SRS.md (APPROVED)

## Specification Status

> **The Status column is machine-refreshed** — `advance-phase` overwrites each
> FR's Status from `build_traceability`'s live code/test scan (IN_PROGRESS once
> code/module exists, VERIFIED once code+test exist). The authoritative status is
> that scan / `quality_manifest.json`, NOT this hand-filled cell. Fill the
> semantic columns (Spec Description / Intent Class / Decision Framework / Notes);
> leave Status to refresh itself (a hand-edit is overwritten on the next advance).
>
> The **Decision Framework** column contains **derived methodology metadata**
> (implementation module paths, test file paths, AC identifiers) — these are
> downstream harness bindings, NOT canonical spec content. The authoritative
> spec semantics live in `SPEC.md` (SSOT); module/test paths in this column
> come from `SRS.md` §11 FR Block (approved) and are subject to change during
> Phase 3 implementation. **B-2 review marked these entries as methodology
> artifacts to preserve the canonical ↔ derived boundary**; treat them as
> implementation hints, not spec assertions. Per-row overrides call out
> specific rows where the canonical spec is ambiguous (e.g. NFR-07 activation
> mechanism DEFERRED per SRS §7 NFR-99a).

## Functional Requirements

| FR ID | Spec Description | Intent Class | Decision Framework | Status | Notes |
|-------|-----------------|--------------|-------------------|--------|-------|
| FR-01 | Task submission and validation — `taskq submit "<command>" [--name NAME]`; non-empty / length≤1000 / injection-char blacklist (`; \| & $ > < \``) / `--name` uniqueness; on pass emit 8-hex uuid4 id, status `pending`, atomic write to `tasks.json`; exit 2 on validation failure. | Functional / Validation | Argparse-driven CLI in `taskq.cli.submit_command`; validation in `taskq.store.add_task`; AC-1.1..1.6 verified by `pytest tests/cli/test_submit.py + tests/store/test_validation.py`. Ref: SPEC.md §3 FR-01. | VERIFIED | Owner: Agent A. Source: SPEC.md §3 FR-01. Cross-ref: SRS.md §3 FR-01. |
| FR-02 | Task executor — `taskq run <id>` / `taskq run --all`; `subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=TASKQ_TASK_TIMEOUT)` (no `shell=True`); state machine `pending → running → done\|failed\|timeout`; `ThreadPoolExecutor` + shared `threading.Lock` for `--all`; single-task `timeout` → exit 4; result record `exit_code`/`stdout_tail`/`stderr_tail`/`duration_ms`/`finished_at`. | Functional / Execution | Subprocess + thread pool in `taskq.executor.run_task` / `run_all`; store writes serialized by `threading.Lock`; AC-2.1..2.5 verified by `pytest tests/executor/test_run.py + tests/integration/test_run_all.py`. Ref: SPEC.md §3 FR-02. | VERIFIED | Owner: Agent A. Source: SPEC.md §3 FR-02. Cross-ref: SRS.md §3 FR-02. |
| FR-03 | Retry and circuit breaker — exponential backoff `TASKQ_BACKOFF_BASE × 2^n`, injectable sleep; breaker `CLOSED→OPEN` on `TASKQ_BREAKER_THRESHOLD` consecutive final failures; `OPEN` refuses (exit 3, stderr `breaker open`); after `TASKQ_BREAKER_COOLDOWN` → `HALF_OPEN` probe; state persisted to `breaker.json` atomically; breaker is global cross-task/cross-process. | Functional / Resilience | State machine in `taskq.breaker.CircuitBreaker`; retry in `taskq.executor.run_with_retry`; AC-3.1..3.5 verified by `pytest tests/breaker/test_state_machine.py + tests/integration/test_retry.py`. Ref: SPEC.md §3 FR-03. | VERIFIED | Owner: Agent A. Source: SPEC.md §3 FR-03. Cross-ref: SRS.md §3 FR-03. |
| FR-04 | Result TTL cache — key `sha256(command)`; `taskq run <id> --cached` replays most recent `done` within `TASKQ_CACHE_TTL` (no subprocess; `cached: true`); cache miss/expire → normal execution + write to `cache.json` on `done`; atomic + thread-safe read/write. | Functional / Performance | Key/value store in `taskq.cache.Cache` (get/put); AC-4.1..4.4 verified by `pytest tests/cache/test_ttl.py + tests/integration/test_cached_run.py`. Ref: SPEC.md §3 FR-04. | VERIFIED | Owner: Agent A. Source: SPEC.md §3 FR-04. Cross-ref: SRS.md §3 FR-04. |
| FR-05 | CLI integration — argparse subcommands `submit`/`run`/`status`/`list`/`clear`; entry `python -m taskq`; global `--json`; exit-code map `0/1/2/3/4`. `clear` is store-wide (no task id; SPEC §3 FR-05 subcommand table); unknown task id behavior (applies to `status`/`run` on non-existent id per SPEC §7) → exit 2 + stderr `unknown task: <id>`; corrupted `tasks.json` → exit 1 + stderr `store corrupted` (no silent rebuild); no bare `except:` swallow. | Functional / CLI | **Derived methodology metadata** — module paths (`taskq.cli.main` / `taskq.cli.build_parser`), test file paths (`tests/cli/test_argparse.py`, `tests/integration/test_cli_exit_codes.py`), and AC identifiers (AC-5.1..5.8) are downstream harness bindings sourced from SRS.md §11 FR Block; NOT canonical spec content. Canonical ref: SPEC.md §3 FR-05 / §7. | VERIFIED | Owner: Agent A. Source: SPEC.md §3 FR-05. Cross-ref: SRS.md §3 FR-05. |

## Non-Functional Requirements

| FR ID | Spec Description | Intent Class | Decision Framework | Status | Notes |
|-------|-----------------|--------------|-------------------|--------|-------|
| NFR-01 | Performance — `submit` + `status` (no subprocess) p95 < 50ms over 100 iterations (pytest-benchmark). | NFR / Performance | pytest-benchmark gate at `tests/perf/test_p95_latency.py`; AC-NFR01.1. Ref: SPEC.md §4 NFR-01. | DRAFT | Owner: Agent A. Source: SPEC.md §4 NFR-01. Cross-ref: SRS.md §4 NFR-01. |
| NFR-02 | Security — `shell=True` forbidden in production code (repo-wide grep returns 0); each of the 7 injection chars (`; \| & $ > < \``) has a negative test that asserts exit 2. | NFR / Security | Repo-wide grep gate + `pytest tests/security/test_injection_blacklist.py` (7 negative tests); AC-NFR02.1..02.2. Ref: SPEC.md §4 NFR-02 / §3 FR-01. | DRAFT | Owner: Agent A. Source: SPEC.md §4 NFR-02. Cross-ref: SRS.md §4 NFR-02. |
| NFR-03 | Reliability — all 3 data files written via tmp + `os.replace`; mid-write crash leaves valid JSON; breaker `OPEN→CLOSED` recovery ≤ `TASKQ_BREAKER_COOLDOWN` + 1s. | NFR / Reliability | Atomic-write helper shared by `taskq.store` / `taskq.breaker` / `taskq.cache`; `pytest tests/integration/test_atomic_write.py + tests/integration/test_breaker_recovery.py`; AC-NFR03.1..03.3. Ref: SPEC.md §4 NFR-03. | DRAFT | Owner: Agent A. Source: SPEC.md §4 NFR-03. Cross-ref: SRS.md §4 NFR-03. |
| NFR-04 | Security — secret redaction — lines matching `(sk-[A-Za-z0-9_-]{8,}|token=\S+)` in `stdout_tail` / `stderr_tail` replaced wholesale by `[REDACTED]` before persistence. | NFR / Security | Redaction pass in `taskq.executor` before result write; `pytest tests/security/test_secret_redaction.py`; AC-NFR04.1..04.2. Ref: SPEC.md §4 NFR-04. | DRAFT | Owner: Agent A. Source: SPEC.md §4 NFR-04. Cross-ref: SRS.md §4 NFR-04. |
| NFR-05 | Maintainability — every public function/class in `src/taskq` carries a docstring containing at least one `[FR-XX]` tag. | NFR / Maintainability | Static inspection `pytest tests/static/test_docstring_fr_tags.py` walks `taskq.*` and asserts at least one `[FR-XX]` token per public object; AC-NFR05.1. Ref: SPEC.md §4 NFR-05. | DRAFT | Owner: Agent A. Source: SPEC.md §4 NFR-05. Cross-ref: SRS.md §4 NFR-05. |
| NFR-06 | Deployability — 8 `TASKQ_*` env vars read via `config.py` with defaults; `.env.example` declares each one with a comment. | NFR / Deployability | Centralized reader in `taskq.config`; `pytest tests/config/test_env_loader.py + tests/deploy/test_env_example.py`; AC-NFR06.1..06.2. Ref: SPEC.md §4 NFR-06 / §5.1. | DRAFT | Owner: Agent A. Source: SPEC.md §4 NFR-06. Cross-ref: SRS.md §4 NFR-06. |
| NFR-07 | Resilience / fault injection — 4 fault scenarios (`corrupt-mid-write`, `oserror-on-write`, `disk-full`, `kill-mid-write`) handled by recovery or fail-fast (no silent rebuild / no silent swallow); trigger via CLI flag `--inject-fault=<scenario>` OR unit test monkeypatch; formal execution paths do not enable either (per SPEC §4 NFR-07). | NFR / Resilience | Canonical trigger surfaces (per SPEC §4 NFR-07): CLI flag `--inject-fault=<scenario>` (test paths) OR unit test monkeypatch; formal execution paths do not enable either. The activation mechanism (e.g. separate test binary, hidden subcommand, env-gated path) is **DEFERRED** per SRS §7 Open Issues NFR-99a — implementation bindings (which module owns the flag, `taskq.store` recovery path, test file paths, AC-NFR07.1..07.5 numbering) are **derived methodology metadata**, NOT canonical spec. Canonical ref: SPEC.md §4 NFR-07 / §5.3. | DRAFT | Owner: Agent A. Source: SPEC.md §4 NFR-07. Cross-ref: SRS.md §4 NFR-07. Note: §7 Open Issues NFR-99a — `--inject-fault` activation mechanism DEFERRED. |
| NFR-08 | Concurrency / cross-process safety — POSIX `fcntl.flock` (write-exclusive / read-shared); Windows `msvcrt.locking`; NFS/network FS detected → flock disabled + WARNING; atomic write remains primary safety net; 4-process concurrent write leaves all 3 files as valid JSON. | NFR / Concurrency | Best-effort flock wrapper in `taskq.store` layered atop atomic write; `pytest tests/integration/test_cross_process.py`; AC-NFR08.1..08.3. Ref: SPEC.md §4 NFR-08 / §11. | DRAFT | Owner: Agent A. Source: SPEC.md §4 NFR-08. Cross-ref: SRS.md §4 NFR-08. |
| NFR-09 | Scalability — 1000-task `submit`+`status` p95 < 100ms; `run --all` over 100 tasks leaves `tasks.json` valid + no task lost; peak memory < 100MB via streaming iterator. | NFR / Scalability | Streaming read/write in `taskq.store`; `pytest-benchmark tests/perf/test_scalability.py + tests/integration/test_run_all_no_loss.py`; AC-NFR09.1..09.3. Ref: SPEC.md §4 NFR-09. | DRAFT | Owner: Agent A. Source: SPEC.md §4 NFR-09. Cross-ref: SRS.md §4 NFR-09. |
| NFR-10 | Evolvability / schema migration — each data file root has `version: 1`; `version<1` triggers auto-migration with backup `<file>.v<n>.bak`; `version>1` refuses + upgrade prompt; migration failure retains backup + exit 1. | NFR / Evolvability | Version check + migrate in `taskq.store`; `pytest tests/integration/test_schema_migration.py`; AC-NFR10.1..10.4. Ref: SPEC.md §4 NFR-10 / §5.2. | DRAFT | Owner: Agent A. Source: SPEC.md §4 NFR-10. Cross-ref: SRS.md §4 NFR-10. |

## Completeness Validation

- FR coverage: 5 / 5 (FR-01, FR-02, FR-03, FR-04, FR-05) — matches SPEC.md §3.
- NFR coverage: 10 / 10 (NFR-01 .. NFR-10) — matches SPEC.md §4.
- Each row cites `SPEC.md` (root) per `R-CANONICAL-SPEC-PATH-001`.
- Status column is intentionally `DRAFT` (Phase 1 hand-fill); machine-refreshed on `advance-phase` by `build_traceability`.
- No Gate-score column — score authority is `quality_manifest.json` (SSOT), this file is human-readable view only.
