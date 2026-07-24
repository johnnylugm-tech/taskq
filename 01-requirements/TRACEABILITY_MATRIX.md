# Traceability Matrix — taskq

> Bidirectional Requirements Traceability Matrix
> Framework: harness-methodology v2.9
> Version: v1.0.0
> Created: 2026-07-24
> Phase: 1 — Requirements
> Canonical spec source: `SPEC.md` v4.0.0 (project root)
> Companion: `01-requirements/SRS.md` (APPROVED), `01-requirements/SPEC_TRACKING.md` (APPROVED)
> ASPICE target: SWE.3 / SYS.4

---

## 1. Overview

This document provides **complete bidirectional traceability** between
five artifact layers:

1. **Spec** — `SPEC.md` (canonical, project root) §3 FR / §4 NFR
2. **FR/NFR** — frozen requirement IDs (`FR-01..FR-05`, `NFR-01..NFR-10`)
3. **SRS** — `01-requirements/SRS.md` §3 / §4 (approved)
4. **Implementation** — `src/taskq/<module>` functions / classes (bindings
   from `SRS.md` §11 FR Block; downstream derivation)
5. **Tests** — `tests/...` test files (bindings from `SRS.md` §11 FR Block
   + §10 Cross-Cutting Test Requirements)

Status semantics:

- **DRAFT** — Phase 1 hand-fill; verification has not started
- **IMPLEMENTED** — source module exists in `src/taskq/`
- **TESTED** — corresponding test file exists and was last run green
- **VERIFIED** — IMPLEMENTED + TESTED + Gate 1 per-FR score ≥ threshold

Score authority is `quality_manifest.json` (SSOT); this file is the
human-readable matrix view.

Per `R-CANONICAL-SPEC-PATH-001`, all citations point to bare `SPEC.md`
(project root). The `01-requirements/SPEC.md` path is ILLEGAL.

---

## 2. FR ↔ SRS ↔ Code ↔ Test Mapping (Functional Requirements)

| FR ID | Title | SPEC § | SRS § | AC Count | Implementation Module(s) | Test File(s) | Status |
|-------|-------|--------|-------|----------|--------------------------|--------------|--------|
| FR-01 | Task submission & validation | SPEC.md §3 FR-01 | SRS.md §3 FR-01 | 6 (AC-1.1..1.6) | `taskq.cli.submit_command`, `taskq.store.add_task` | `tests/cli/test_submit.py`, `tests/store/test_validation.py` | DRAFT |
| FR-02 | Task executor | SPEC.md §3 FR-02 | SRS.md §3 FR-02 | 5 (AC-2.1..2.5) | `taskq.executor.run_task`, `taskq.executor.run_all` | `tests/executor/test_run.py`, `tests/integration/test_run_all.py` | DRAFT |
| FR-03 | Retry & circuit breaker | SPEC.md §3 FR-03 | SRS.md §3 FR-03 | 5 (AC-3.1..3.5) | `taskq.breaker.CircuitBreaker`, `taskq.executor.run_with_retry` | `tests/breaker/test_state_machine.py`, `tests/integration/test_retry.py` | DRAFT |
| FR-04 | Result TTL cache | SPEC.md §3 FR-04 | SRS.md §3 FR-04 | 4 (AC-4.1..4.4) | `taskq.cache.Cache.get`, `taskq.cache.Cache.put` | `tests/cache/test_ttl.py`, `tests/integration/test_cached_run.py` | DRAFT |
| FR-05 | CLI integration | SPEC.md §3 FR-05 | SRS.md §3 FR-05 | 8 (AC-5.1..5.8) | `taskq.cli.main`, `taskq.cli.build_parser` | `tests/cli/test_argparse.py`, `tests/integration/test_cli_exit_codes.py` | DRAFT |

**FR coverage summary**: 5 / 5 (100%). AC total: 28.

---

## 3. NFR ↔ SRS ↔ Code ↔ Test Mapping (Non-Functional Requirements)

| NFR ID | Type | SPEC § | SRS § | AC Count | Implementation Module(s) | Test File(s) | Status |
|--------|------|--------|-------|----------|--------------------------|--------------|--------|
| NFR-01 | Performance | SPEC.md §4 NFR-01 | SRS.md §4 NFR-01 | 1 (AC-NFR01.1) | `taskq.cli.submit_command`, `taskq.store` (no subprocess) | `tests/perf/test_p95_latency.py` (pytest-benchmark) | DRAFT |
| NFR-02 | Security | SPEC.md §4 NFR-02 | SRS.md §4 NFR-02 | 2 (AC-NFR02.1..02.2) | repo-wide grep gate + `taskq.cli.submit_command` | `tests/security/test_injection_blacklist.py` (7 char tests) | DRAFT |
| NFR-03 | Reliability | SPEC.md §4 NFR-03 | SRS.md §4 NFR-03 | 3 (AC-NFR03.1..03.3) | shared atomic-write helper, `taskq.store`, `taskq.breaker` | `tests/integration/test_atomic_write.py`, `tests/integration/test_breaker_recovery.py` | DRAFT |
| NFR-04 | Security (redaction) | SPEC.md §4 NFR-04 | SRS.md §4 NFR-04 | 2 (AC-NFR04.1..04.2) | `taskq.executor` (redaction pass) | `tests/security/test_secret_redaction.py` | DRAFT |
| NFR-05 | Maintainability | SPEC.md §4 NFR-05 | SRS.md §4 NFR-05 | 1 (AC-NFR05.1) | static inspection over `src/taskq` | `tests/static/test_docstring_fr_tags.py` | DRAFT |
| NFR-06 | Deployability | SPEC.md §4 NFR-06 | SRS.md §4 NFR-06 | 2 (AC-NFR06.1..06.2) | `taskq.config` (env reader), `.env.example` | `tests/config/test_env_loader.py`, `tests/deploy/test_env_example.py` | DRAFT |
| NFR-07 | Resilience (fault injection) | SPEC.md §4 NFR-07 | SRS.md §4 NFR-07 | 5 (AC-NFR07.1..07.5) | `taskq.store` (recovery path); activation mechanism **DEFERRED** per SRS §7 NFR-99a | `tests/integration/test_fault_injection.py` | DRAFT |
| NFR-08 | Concurrency | SPEC.md §4 NFR-08 | SRS.md §4 NFR-08 | 3 (AC-NFR08.1..08.3) | `taskq.store` (fcntl/msvcrt flock wrapper) | `tests/integration/test_cross_process.py` | DRAFT |
| NFR-09 | Scalability | SPEC.md §4 NFR-09 | SRS.md §4 NFR-09 | 3 (AC-NFR09.1..09.3) | `taskq.store` (streaming iterator) | `tests/perf/test_scalability.py`, `tests/integration/test_run_all_no_loss.py` | DRAFT |
| NFR-10 | Evolvability (schema migration) | SPEC.md §4 NFR-10 | SRS.md §4 NFR-10 | 4 (AC-NFR10.1..10.4) | `taskq.store` (version check + migrate) | `tests/integration/test_schema_migration.py` | DRAFT |

**NFR coverage summary**: 10 / 10 (100%). AC total: 26.

---

## 4. AC ↔ Test Trace (One-to-N, by FR)

| FR/NFR | AC ID | Acceptance Criterion (verbatim title) | Test File / Test Name | Spec Source |
|--------|-------|---------------------------------------|------------------------|-------------|
| FR-01 | AC-1.1 | Non-empty command | `tests/cli/test_submit.py::test_submit_empty_command_exits_2` | SPEC.md §3 FR-01 |
| FR-01 | AC-1.2 | Command length ≤ 1000 | `tests/cli/test_submit.py::test_submit_long_command_exits_2` | SPEC.md §3 FR-01 |
| FR-01 | AC-1.3 | No injection chars (`; \| & $ > < \``) | `tests/cli/test_submit.py::test_submit_injection_<char>_rejected` × 7 | SPEC.md §3 FR-01 |
| FR-01 | AC-1.4 | `--name` uniqueness | `tests/store/test_validation.py::test_name_uniqueness` | SPEC.md §3 FR-01 |
| FR-01 | AC-1.5 | uuid4 + atomic write | `tests/store/test_validation.py::test_atomic_add_task` | SPEC.md §3 FR-01 |
| FR-01 | AC-1.6 | `--json` output | `tests/cli/test_submit.py::test_submit_json_output` | SPEC.md §3 FR-01 |
| FR-02 | AC-2.1 | `subprocess.run` (no `shell=True`) | `tests/executor/test_run.py::test_shell_true_grep_zero_matches` | SPEC.md §3 FR-02 |
| FR-02 | AC-2.2 | State machine `pending→running→done\|failed\|timeout` | `tests/executor/test_run.py::test_state_transitions` | SPEC.md §3 FR-02 |
| FR-02 | AC-2.3 | Result record fields | `tests/executor/test_run.py::test_result_record_fields` | SPEC.md §3 FR-02 |
| FR-02 | AC-2.4 | `ThreadPoolExecutor` + `threading.Lock` | `tests/integration/test_run_all.py::test_run_all_concurrent` | SPEC.md §3 FR-02 |
| FR-02 | AC-2.5 | Single-task timeout exit 4 | `tests/executor/test_run.py::test_timeout_exit_code_4` | SPEC.md §3 FR-02 |
| FR-03 | AC-3.1 | Retry policy + exponential backoff | `tests/integration/test_retry.py::test_retry_exponential_backoff` | SPEC.md §3 FR-03 |
| FR-03 | AC-3.2 | OPEN threshold | `tests/breaker/test_state_machine.py::test_breaker_open_threshold` | SPEC.md §3 FR-03 |
| FR-03 | AC-3.3 | OPEN refusal exit 3 | `tests/breaker/test_state_machine.py::test_breaker_open_refuses_exit_3` | SPEC.md §3 FR-03 |
| FR-03 | AC-3.4 | HALF_OPEN probe | `tests/breaker/test_state_machine.py::test_breaker_half_open_probe` | SPEC.md §3 FR-03 |
| FR-03 | AC-3.5 | Breaker persistence | `tests/integration/test_retry.py::test_breaker_state_persistence` | SPEC.md §3 FR-03 |
| FR-04 | AC-4.1 | `sha256(command)` signature | `tests/cache/test_ttl.py::test_signature_sha256` | SPEC.md §3 FR-04 |
| FR-04 | AC-4.2 | `--cached` replay within TTL | `tests/integration/test_cached_run.py::test_cached_replay_within_ttl` | SPEC.md §3 FR-04 |
| FR-04 | AC-4.3 | Cache miss → normal execution | `tests/integration/test_cached_run.py::test_cache_miss_normal_execution` | SPEC.md §3 FR-04 |
| FR-04 | AC-4.4 | Atomic + thread-safe | `tests/cache/test_ttl.py::test_cache_concurrent_writes` | SPEC.md §3 FR-04 |
| FR-05 | AC-5.1 | Subcommands wired | `tests/cli/test_argparse.py::test_subcommands_wired` | SPEC.md §3 FR-05 |
| FR-05 | AC-5.2 | `run <id> --cached` / `--all` | `tests/cli/test_argparse.py::test_run_args` | SPEC.md §3 FR-05 |
| FR-05 | AC-5.3 | Global `--json` flag | `tests/cli/test_argparse.py::test_global_json_flag` | SPEC.md §3 FR-05 |
| FR-05 | AC-5.4 | Exit code map | `tests/integration/test_cli_exit_codes.py::test_exit_code_map` | SPEC.md §3 FR-05 |
| FR-05 | AC-5.5 | `status` / `list` / `clear` | `tests/cli/test_argparse.py::test_status_list_clear` | SPEC.md §3 FR-05 |
| FR-05 | AC-5.6 | Unknown task id → exit 2 | `tests/integration/test_cli_exit_codes.py::test_unknown_task_exit_2` | SPEC.md §7 |
| FR-05 | AC-5.7 | Corrupted `tasks.json` → exit 1 | `tests/integration/test_cli_exit_codes.py::test_corrupted_store_exit_1` | SPEC.md §7 |
| FR-05 | AC-5.8 | No bare `except:` | `tests/static/test_no_swallow.py::test_no_bare_except` | SPEC.md §7 |
| NFR-01 | AC-NFR01.1 | p95 < 50ms over 100 iter | `tests/perf/test_p95_latency.py` (pytest-benchmark) | SPEC.md §4 NFR-01 |
| NFR-02 | AC-NFR02.1 | grep `shell=True` → 0 in production | `tests/security/test_shell_true_grep.py` | SPEC.md §4 NFR-02 |
| NFR-02 | AC-NFR02.2 | 7-char injection tests | `tests/security/test_injection_blacklist.py` (7) | SPEC.md §4 NFR-02 |
| NFR-03 | AC-NFR03.1 | tmp + `os.replace` per file | `tests/integration/test_atomic_write.py::test_atomic_write_three_files` | SPEC.md §4 NFR-03 |
| NFR-03 | AC-NFR03.2 | Mid-write crash survivability | `tests/integration/test_atomic_write.py::test_mid_write_crash` | SPEC.md §4 NFR-03 |
| NFR-03 | AC-NFR03.3 | Breaker recovery ≤ cooldown+1s | `tests/integration/test_breaker_recovery.py::test_recovery_within_cooldown_plus_1s` | SPEC.md §4 NFR-03 |
| NFR-04 | AC-NFR04.1 | `sk-…` redaction | `tests/security/test_secret_redaction.py::test_sk_token_redacted` | SPEC.md §4 NFR-04 |
| NFR-04 | AC-NFR04.2 | `token=…` redaction | `tests/security/test_secret_redaction.py::test_token_equals_redacted` | SPEC.md §4 NFR-04 |
| NFR-05 | AC-NFR05.1 | Docstring `[FR-XX]` tag | `tests/static/test_docstring_fr_tags.py` | SPEC.md §4 NFR-05 |
| NFR-06 | AC-NFR06.1 | `config.py` 8 env vars | `tests/config/test_env_loader.py` | SPEC.md §4 NFR-06 |
| NFR-06 | AC-NFR06.2 | `.env.example` declares 8 vars | `tests/deploy/test_env_example.py` | SPEC.md §4 NFR-06 |
| NFR-07 | AC-NFR07.1 | `corrupt-mid-write` | `tests/integration/test_fault_injection.py::test_corrupt_mid_write` | SPEC.md §4 NFR-07 |
| NFR-07 | AC-NFR07.2 | `oserror-on-write` | `tests/integration/test_fault_injection.py::test_oserror_on_write` | SPEC.md §4 NFR-07 |
| NFR-07 | AC-NFR07.3 | `disk-full` | `tests/integration/test_fault_injection.py::test_disk_full` | SPEC.md §4 NFR-07 |
| NFR-07 | AC-NFR07.4 | `kill-mid-write` | `tests/integration/test_fault_injection.py::test_kill_mid_write` | SPEC.md §4 NFR-07 |
| NFR-07 | AC-NFR07.5 | `--inject-fault` rejected on prod CLI | `tests/integration/test_fault_injection.py::test_inject_fault_rejected_on_prod` | SPEC.md §4 NFR-07 |
| NFR-08 | AC-NFR08.1 | `fcntl.flock` / `msvcrt.locking` | `tests/integration/test_cross_process.py::test_posix_flock` | SPEC.md §4 NFR-08 |
| NFR-08 | AC-NFR08.2 | Network FS degrade + WARNING | `tests/integration/test_cross_process.py::test_network_fs_warning` | SPEC.md §4 NFR-08 |
| NFR-08 | AC-NFR08.3 | 4-process concurrent → valid JSON | `tests/integration/test_cross_process.py::test_four_process_concurrent` | SPEC.md §4 NFR-08 |
| NFR-09 | AC-NFR09.1 | 1000-task p95 < 100ms | `tests/perf/test_scalability.py` (pytest-benchmark) | SPEC.md §4 NFR-09 |
| NFR-09 | AC-NFR09.2 | `run --all` 100 tasks no loss | `tests/integration/test_run_all_no_loss.py::test_run_all_100_tasks_no_loss` | SPEC.md §4 NFR-09 |
| NFR-09 | AC-NFR09.3 | Streaming iterator < 100MB | `tests/perf/test_scalability.py::test_memory_under_100mb` | SPEC.md §4 NFR-09 |
| NFR-10 | AC-NFR10.1 | `version: 1` root | `tests/integration/test_schema_migration.py::test_version_field_invariant` | SPEC.md §4 NFR-10 |
| NFR-10 | AC-NFR10.2 | v0 → v1 auto-migrate + backup | `tests/integration/test_schema_migration.py::test_v0_migrate_with_backup` | SPEC.md §4 NFR-10 |
| NFR-10 | AC-NFR10.3 | v>1 refuses + upgrade prompt | `tests/integration/test_schema_migration.py::test_v2_refuses` | SPEC.md §4 NFR-10 |
| NFR-10 | AC-NFR10.4 | Migration fail-fast + backup retained | `tests/integration/test_schema_migration.py::test_migration_fail_fast` | SPEC.md §4 NFR-10 |

**AC coverage summary**: 54 / 54 (100%).

---

## 5. Spec ↔ Code Trace (SRS Section → Implementation Module)

| SRS Section | Canonical Summary | Module Path | Public Symbols | Test File |
|-------------|-------------------|-------------|----------------|-----------|
| §3 FR-01 | Task submission & validation | `src/taskq/cli.py`, `src/taskq/store.py` | `submit_command`, `add_task` | `tests/cli/test_submit.py`, `tests/store/test_validation.py` |
| §3 FR-02 | Task executor | `src/taskq/executor.py` | `run_task`, `run_all` | `tests/executor/test_run.py`, `tests/integration/test_run_all.py` |
| §3 FR-03 | Retry & circuit breaker | `src/taskq/breaker.py`, `src/taskq/executor.py` | `CircuitBreaker`, `run_with_retry` | `tests/breaker/test_state_machine.py`, `tests/integration/test_retry.py` |
| §3 FR-04 | Result TTL cache | `src/taskq/cache.py` | `Cache.get`, `Cache.put` | `tests/cache/test_ttl.py`, `tests/integration/test_cached_run.py` |
| §3 FR-05 | CLI integration | `src/taskq/cli.py` | `main`, `build_parser` | `tests/cli/test_argparse.py`, `tests/integration/test_cli_exit_codes.py` |
| §4 NFR-01 | Performance p95 < 50ms | (cross-cutting) | — | `tests/perf/test_p95_latency.py` |
| §4 NFR-02 | Injection blacklist + `shell=True` | `src/taskq/cli.py` (submit) | `submit_command` | `tests/security/test_injection_blacklist.py` |
| §4 NFR-03 | Atomic write + breaker recovery | `src/taskq/store.py`, `src/taskq/breaker.py` | `atomic_write_helper`, `CircuitBreaker` | `tests/integration/test_atomic_write.py`, `tests/integration/test_breaker_recovery.py` |
| §4 NFR-04 | Secret redaction | `src/taskq/executor.py` | redaction pass | `tests/security/test_secret_redaction.py` |
| §4 NFR-05 | Docstring `[FR-XX]` tag | (cross-cutting) | every public fn/class | `tests/static/test_docstring_fr_tags.py` |
| §4 NFR-06 | 8 `TASKQ_*` env vars | `src/taskq/config.py` | 8 readers | `tests/config/test_env_loader.py`, `tests/deploy/test_env_example.py` |
| §4 NFR-07 | Fault injection | `src/taskq/store.py` (recovery path); activation mechanism DEFERRED | per-injection branch | `tests/integration/test_fault_injection.py` |
| §4 NFR-08 | Cross-process flock | `src/taskq/store.py` | flock wrapper | `tests/integration/test_cross_process.py` |
| §4 NFR-09 | Scalability | `src/taskq/store.py` (streaming) | iterator | `tests/perf/test_scalability.py`, `tests/integration/test_run_all_no_loss.py` |
| §4 NFR-10 | Schema migration | `src/taskq/store.py` | `version_check`, `migrate` | `tests/integration/test_schema_migration.py` |

Module bindings are sourced from `SRS.md` §11 FR Block (machine-readable
JSON). These are **derived methodology metadata** — subject to change
during Phase 3 implementation; canonical spec semantics live in
`SPEC.md` (SSOT).

---

## 6. Code ↔ Test Trace (Module → Test File, One-to-Many)

| Module | Test File(s) | Coverage Target |
|--------|--------------|-----------------|
| `src/taskq/cli.py` | `tests/cli/test_submit.py`, `tests/cli/test_argparse.py`, `tests/integration/test_cli_exit_codes.py` | FR-01, FR-05, AC-5.1..5.8, NFR-02 (grep) |
| `src/taskq/store.py` | `tests/store/test_validation.py`, `tests/integration/test_atomic_write.py`, `tests/integration/test_cross_process.py`, `tests/integration/test_schema_migration.py`, `tests/integration/test_run_all_no_loss.py` | FR-01, NFR-03, NFR-08, NFR-10, NFR-09.2 |
| `src/taskq/executor.py` | `tests/executor/test_run.py`, `tests/integration/test_run_all.py`, `tests/integration/test_retry.py`, `tests/security/test_secret_redaction.py` | FR-02, FR-03 (retry), NFR-04 |
| `src/taskq/breaker.py` | `tests/breaker/test_state_machine.py`, `tests/integration/test_breaker_recovery.py`, `tests/integration/test_retry.py` | FR-03, NFR-03.3 |
| `src/taskq/cache.py` | `tests/cache/test_ttl.py`, `tests/integration/test_cached_run.py` | FR-04 |
| `src/taskq/config.py` | `tests/config/test_env_loader.py`, `tests/deploy/test_env_example.py` | NFR-06 |

---

## 7. Test File ↔ FR/NFR Coverage (Reverse Trace)

| Test File | FRs Covered | NFRs Covered |
|-----------|-------------|--------------|
| `tests/cli/test_submit.py` | FR-01 (AC-1.1..1.6) | NFR-02.2 (7 char tests) |
| `tests/cli/test_argparse.py` | FR-05 (AC-5.1..5.3, 5.5) | — |
| `tests/integration/test_cli_exit_codes.py` | FR-05 (AC-5.4, 5.6, 5.7) | — |
| `tests/store/test_validation.py` | FR-01 (AC-1.4, 1.5) | — |
| `tests/executor/test_run.py` | FR-02 (AC-2.1..2.3, 2.5) | NFR-02.1 (grep) |
| `tests/integration/test_run_all.py` | FR-02 (AC-2.4) | — |
| `tests/integration/test_retry.py` | FR-03 (AC-3.1, 3.5) | — |
| `tests/breaker/test_state_machine.py` | FR-03 (AC-3.2..3.4) | — |
| `tests/integration/test_breaker_recovery.py` | — | NFR-03.3 |
| `tests/cache/test_ttl.py` | FR-04 (AC-4.1, 4.4) | — |
| `tests/integration/test_cached_run.py` | FR-04 (AC-4.2, 4.3) | — |
| `tests/security/test_injection_blacklist.py` | — | NFR-02.2 |
| `tests/security/test_shell_true_grep.py` | — | NFR-02.1 |
| `tests/security/test_secret_redaction.py` | — | NFR-04 (AC-NFR04.1, 04.2) |
| `tests/perf/test_p95_latency.py` | — | NFR-01 |
| `tests/perf/test_scalability.py` | — | NFR-09 (AC-NFR09.1, 09.3) |
| `tests/integration/test_atomic_write.py` | — | NFR-03 (AC-NFR03.1, 03.2) |
| `tests/integration/test_cross_process.py` | — | NFR-08 (AC-NFR08.1..08.3) |
| `tests/integration/test_fault_injection.py` | — | NFR-07 (AC-NFR07.1..07.5) |
| `tests/integration/test_schema_migration.py` | — | NFR-10 (AC-NFR10.1..10.4) |
| `tests/integration/test_run_all_no_loss.py` | — | NFR-09.2 |
| `tests/static/test_docstring_fr_tags.py` | — | NFR-05 |
| `tests/static/test_no_swallow.py` | FR-05.8 | — |
| `tests/config/test_env_loader.py` | — | NFR-06.1 |
| `tests/deploy/test_env_example.py` | — | NFR-06.2 |

---

## 8. Completeness Verification

| Check | Target | Actual | Status |
|-------|--------|--------|--------|
| FR ↔ SRS mapping | 100% | 5 / 5 (100%) | PASS |
| NFR ↔ SRS mapping | 100% | 10 / 10 (100%) | PASS |
| SRS §3 → Code | 100% | 5 / 5 sections mapped | PASS |
| SRS §4 → Code | 100% | 10 / 10 sections mapped | PASS |
| Code ↔ Test | 100% | 6 / 6 modules have ≥1 test file | PASS |
| AC ↔ Test | 100% | 54 / 54 ACs mapped | PASS |
| Test coverage (line) | ≥ 80% (P3: ≥ 70%) | TBD (Phase 3) | PENDING |
| All FR-01 injection chars tested | 7 / 7 | 7 / 7 | PASS |
| All 8 `TASKQ_*` env vars covered | 8 / 8 | 8 / 8 | PASS |
| All 4 fault-injection scenarios | 4 / 4 | 4 / 4 (+ 1 flag-rejection test) | PASS |

---

## 9. Open Issues Surfaced

| ID | Item | Reference | Status |
|----|------|-----------|--------|
| NFR-99a | NFR-07 `--inject-fault` activation mechanism DEFERRED | SRS §7 Open Issues | DEFERRED — implementation choice pending |
| FR-99 | No prompt-injection patterns in SPEC.md v4.0.0 | SRS §7 | RESOLVED |
| Module bindings | Derived from SRS §11 FR Block; subject to Phase 3 refactor | SRS §11 | METHODOLOGY METADATA |
| AC-NFR05.1 | Strict `[FR-XX]` (not `[NFR-XX]`) per canonical SPEC §4 NFR-05 | SRS §4 NFR-05 | RESOLVED |

---

## 10. ASPICE Compliance Map

| ASPICE Capability | Evidence | Status |
|-------------------|----------|--------|
| SWE.3.B.SP1 — Task-to-work-product traceability | §2/§3/§5 (FR/NFR → SRS → Code) | SATISFIED (Phase 1 freeze) |
| SWE.3.B.SP2 — Bidirectional traceability | §7 reverse trace + §4 AC trace | SATISFIED |
| SWE.3.B.SP3 — Traceability consistency | §8 completeness table | SATISFIED (DRAFT status; machine-refresh on `advance-phase`) |
| SYS.4.B.SP1 — System requirements trace | §2 / §3 (FR/NFR all cite SPEC.md §3 / §4) | SATISFIED |

---

## 11. Traceability Refresh Procedure

The `Status` column is **machine-refreshed** by `build_traceability` on
`advance-phase`:

| Observed State | Status |
|----------------|--------|
| No code module exists | DRAFT |
| Code module exists, no test file | IN_PROGRESS |
| Code + test file exist, last run green | VERIFIED |
| Gate 1 per-FR score ≥ threshold | VERIFIED |

Hand-edits to the `Status` column are **overwritten** on the next
`advance-phase`. The authoritative score source is
`quality_manifest.json` (SSOT); this file is a human-readable view for
ASPICE SWE.3 / SYS.4 audit.

---

## 12. Cross-Document Anchors

- **Canonical spec**: `SPEC.md` v4.0.0 (project root)
- **SRS**: `01-requirements/SRS.md` (APPROVED)
- **Spec tracking**: `01-requirements/SPEC_TRACKING.md` (APPROVED)
- **Test plan**: `04-testing/TEST_PLAN.md` (downstream)
- **Test inventory**: `01-requirements/TEST_INVENTORY.yaml` (downstream)
- **Verification**: `05-verification/VERIFICATION_REPORT.md` (downstream)
- **Risk register**: `07-risk/RISK_REGISTER.md` (downstream)

---

## 13. Change Log

| Version | Date | Change | Author |
|---------|------|--------|--------|
| v1.0.0 | 2026-07-24 | Initial bidirectional matrix (FR-01..05, NFR-01..10) | Agent A (Requirements Engineer) |
