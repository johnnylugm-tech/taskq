# RISK_REGISTER.md — taskq

> **Phase**: 7 — Risk Management
> **Project**: taskq
> **Date**: 2026-07-27
> **Framework**: harness-methodology v2.12.0
> **Entry state**: Gate 4 PASS (composite 97.2, `quality_complete = true`, `open_critical = 0`, `open_high = 0`)
> **Baseline seed**: `SPEC.md` §9 risk matrix (R1–R9)

---

## 1. Scoring Model

| Axis | 5 | 4 | 3 | 2 | 1 |
|------|---|---|---|---|---|
| **Likelihood** | near-certain in normal operation | likely under common conditions | plausible under realistic load/config | needs an uncommon trigger | requires a contrived setup |
| **Impact** | silent data loss / corruption / secret disclosure | wrong result surfaced as success | task fails loudly, state intact | degraded UX or maintenance drag | cosmetic / internal only |

**Score** = Likelihood × Impact.
**Bands**: `HIGH` ≥ 9 · `MEDIUM` 4–8 · `LOW` ≤ 3.

Two scores are tracked per risk:

- **Inherent** — the score assuming the mitigation were absent. This is the value that classifies a risk as HIGH and therefore requires a formal plan in `RISK_MITIGATION_PLANS.md`.
- **Residual** — the score after the mitigation shipped in P3 and verified in P4/P5/P6. Residual likelihood is only lowered when a named executable test or tool output backs it.

SPEC §9 uses 高/中/低; the mapping used here is 高 → 5 (impact) / 3 (likelihood), 中 → 3, 低 → 2.

## 2. Source Inventory

| Source | Status | Contribution |
|--------|--------|--------------|
| `SPEC.md` §9 | present | R1–R9 (seed set) |
| `.methodology/gate3_result.json` | present | 0 critical / 0 high; coverage + readability + assertion-quality issues |
| `.methodology/gate4_result.json` | present | 0 critical / 0 high; bandit B404/B603, MI 56.73/58.05, coverage 97.1, `da_waiver.architecture = true` |
| `.methodology/mutation_survivors.json` | present | 14 survivors reported in raw mutmut output (breaker.py 4, store.py 10); `survivor_count` field reads 0 — see R11 |
| `.methodology/bug_hunt_report.json` | present | 4 raw findings → 1 confirmed (fixed), 3 refuted |
| `FINAL_SIGN_OFF.md` / `RELEASE_NOTES.md` | present | 5 documented non-blocking limitations |
| `.methodology/deferred_fixes.md` | **absent** | no deferred fixes recorded for this project; nothing to import |
| `.sessi-work/issue_registry.json` | **absent** | no separate issue registry file exists; issue state was read from the gate result JSONs instead |

## 3. Register

| ID | Risk | Category | L | I | Inherent | Res. L | Res. I | Residual | Band (inherent) | Mitigation approach | Status |
|----|------|----------|---|---|----------|--------|--------|----------|-----------------|---------------------|--------|
| R1 | Concurrent writes corrupt `tasks.json` | data-integrity / concurrency | 3 | 5 | **15** | 1 | 5 | 5 | HIGH | `fcntl.flock` exclusive-on-write / shared-on-read plus atomic `tmp` + `os.replace` on all three data files (NFR-03 + NFR-08) | MITIGATED |
| R2 | Subprocess hangs or leaves a zombie | resilience | 3 | 3 | **9** | 1 | 3 | 3 | HIGH | Mandatory `timeout=` on every `subprocess.run`; orphan kill on timeout; exit code 4; `preflight_reliability_lint` blocks any timeout-less call from P4 onward (FR-02) | MITIGATED |
| R3 | Breaker latches OPEN and never recovers | availability | 2 | 3 | 6 | 1 | 3 | 3 | MEDIUM | Cooldown + `HALF_OPEN` probe; recovery asserted ≤ `TASKQ_BREAKER_COOLDOWN` + 1s; missing `opened_at` treated as recoverable, not fatal (FR-03) | MITIGATED |
| R4 | Cache replays a stale result | correctness | 3 | 2 | 6 | 1 | 2 | 2 | MEDIUM | SHA-256 command signature + TTL expiry forcing re-execution; cache failures fail-open to real execution (FR-04) | MITIGATED |
| R5 | Secret written to disk in `stdout_tail` / `stderr_tail` | security | 3 | 5 | **15** | 1 | 5 | 5 | HIGH | Whole-line `[REDACTED]` substitution for `(sk-[A-Za-z0-9_-]{8,}\|token=\S+)`, applied **before** truncation; gitleaks clean over 83 commits (NFR-04) | MITIGATED |
| R6 | Fault-injection hooks reachable on the production path | security / test-isolation | 3 | 3 | **9** | 1 | 3 | 3 | HIGH | `--inject-fault` accepted only under an explicit opt-in; rejected on the production path; no env-var trigger (NFR-07) | MITIGATED |
| R7 | `flock` is a no-op on NFS / network filesystems | concurrency | 3 | 3 | **9** | 2 | 3 | 6 | HIGH | flock declared best-effort; network-fs detection degrades to atomic-write-only and emits `WARNING`; atomic write remains the primary defence (NFR-08) | MITIGATED (residual: detection is heuristic) |
| R8 | 1000-task scale breaches the memory budget | scalability | 2 | 3 | 6 | 1 | 3 | 3 | MEDIUM | Streaming iterator instead of whole-file materialisation; < 100 MB peak and no task loss over `run --all` × 100 (NFR-09) | MITIGATED |
| R9 | Schema migration loses data | data-integrity / evolvability | 2 | 5 | **10** | 1 | 5 | 5 | HIGH | `<file>.v<n>.bak` backup taken before migration; failure keeps the backup and exits 1 fail-fast; `version > 1` refuses to read (NFR-10) | MITIGATED |
| R10 | Untested branches in `cli.py` (78%) and `store.py` (84%) hide a regression | test-coverage | 3 | 2 | 6 | 3 | 2 | 6 | MEDIUM | Accepted gap — total coverage 97.1% is above the 80 threshold; uncovered lines are `store.py` 41–65/99–110 and `cli.py` json-flag + argparse help paths. Monitor, do not chase 100% | OPEN — accepted |
| R11 | Mutation survivors on high-risk modules (`breaker.py` 4, `store.py` 10) | test-effectiveness | 3 | 3 | **9** | 2 | 3 | 6 | HIGH | Per-FR mutation gating at Gate 1; survivors are concentrated in state-transition and write-path lines. `survivor_count: 0` disagrees with the raw output, so the metric itself is treated as untrustworthy pending re-run | OPEN — HIGH |
| R12 | Maintainability index below 65 on `cli.py` (56.73) and `executor.py` (58.05) | maintainability | 3 | 2 | 6 | 3 | 2 | 6 | MEDIUM | Helper extraction already reduced `cli.py` from 6 to 5 dispatchers; both files still pass the readability threshold (80.4 vs 80). Further decomposition deferred as it would be a refactor with no functional driver | OPEN — accepted |
| R13 | `architecture` dimension passed via devil's-advocate waiver, not tool evidence | process / architecture | 2 | 3 | 6 | 2 | 3 | 6 | MEDIUM | `da_waiver.architecture = true` in `gate4_result.json`; the `no_circular_dependencies` constraint is the only hard architectural rule and is separately asserted. Flagged for human confirmation | OPEN — needs human sign-off |
| R14 | Command-injection denylist is bypassable (denylist, not allowlist) | security | 2 | 5 | **10** | 2 | 5 | 10 | HIGH | 7-character blocklist (`;` `\|` `&` `$` `>` `<` `` ` ``) with a negative test per character, plus a source-level assertion that `shell=True` appears nowhere. Bandit B404/B603 are the intentional residue of this design (NFR-02) | OPEN — HIGH, design-accepted |
| R15 | Refuted-but-real ergonomics defects from the bug hunt | usability / diagnosability | 3 | 1 | 3 | 3 | 1 | 3 | LOW | `cli#2` (unsupported schema reported as "internal error"), `executor#3` (misleading "unknown task" on a mid-run vanish), `store#4` (lock file close/unlink cadence on unclean exit). All three refuted as defects; recorded as diagnosability debt | OPEN — accepted |
| R16 | NFR-01 / NFR-09 p95 numbers are not aggregated into one report | performance-observability | 3 | 2 | 6 | 3 | 2 | 6 | MEDIUM | Both KPIs have passing benchmarks, but no single artifact aggregates them, so a slow drift across releases would not be visible | OPEN — accepted |
| R17 | Harness golden-fixture drift and missing root `tests/integration/` weaken the CI signal | ci-integrity | 2 | 2 | 4 | 2 | 2 | 4 | MEDIUM | Documented in `FINAL_SIGN_OFF.md` as non-blocking; integration tests live at `03-development/tests/integration/` and do run | OPEN — accepted |

**Totals**: 17 risks — 8 HIGH by inherent score (R1, R2, R5, R6, R7, R9, R11, R14), 8 MEDIUM, 1 LOW.
**By residual score**: 1 HIGH (R14), 10 MEDIUM, 6 LOW.

## 4. Verification Evidence

Each mitigation below is backed by a test that exists in `03-development/tests/`.

| ID | Verifying tests / tool output |
|----|-------------------------------|
| R1 | `test_cross_process_no_corruption`, `test_four_process_concurrent`, `test_posix_flock`, `test_atomic_write_three_files`, `test_atomic_add_task`, `test_run_all_concurrent`, `test_cache_concurrent_writes`, `test_kill_mid_write`, `test_corrupt_mid_write`, `test_mid_write_crash`, `test_write_unlocked_cleans_up_temp_file_on_failure` |
| R2 | `test_timeout_exit_code_4`, `test_run_subprocess_orphan_cleanup_on_timeout`, `test_run_all_with_timeout_returns_4`, `test_run_all_timeout_reported_via_exit_code_4`, `test_run_timeout_status_returns_4`, `test_run_all_no_timeout_returns_0` |
| R3 | `test_breaker_open_threshold`, `test_breaker_half_open_probe`, `test_recovery_within_cooldown_plus_1s`, `test_breaker_check_open_missing_opened_at`, `test_state_transitions`, `test_breaker_state_persistence`, `test_retry_then_breaker_opens_and_refuses_admission` |
| R4 | `test_cached_replay_within_ttl`, `test_cache_miss_normal_execution`, `test_signature_sha256`, `test_cache_put_lookup_roundtrip`, `test_fr04_cache_actually_used_on_hit`, `test_cache_put_failure_is_fail_open`, `test_run_task_cache_lookup_exception_fails_open`, `test_cache_put_recovers_from_malformed_schema` |
| R5 | `test_sk_token_redacted`, `test_token_equals_redacted`, `test_sk_token_redacted_stderr`, `test_token_equals_redacted_stderr`, `test_secret_redaction_before_truncation`; gitleaks — 83 commits, 0 leaks |
| R6 | `test_inject_fault_rejected_in_production`, `test_inject_fault_rejected_on_prod`, `test_main_inject_fault_rejected_without_env`, `test_inject_fault_triggered_when_opted_in`, `test_main_inject_fault_triggered_with_env`, `test_fault_injection_fails_fast_or_recovers` |
| R7 | `test_network_fs_warning`, `test_posix_flock` |
| R8 | `test_memory_under_100mb`, `test_run_all_100_tasks_no_loss`, `test_kpi_submit_status_p95_under_100ms_1000_tasks`, `test_bench_add_task`, `test_bench_read_state` |
| R9 | `test_v0_migrate_with_backup`, `test_migration_fail_fast`, `test_v2_refuses`, `test_add_task_on_v0_migrates`, `test_version_field_invariant`, `test_read_unlocked_rejects_unsupported_schema` |
| R10 | `gate4_result.json.breakdown.test_coverage` — 97.1% total, `store.py` 84%, `cli.py` 78% |
| R11 | `.methodology/mutation_survivors.json` raw output — `breaker.py` 57/59-60/76, `store.py` 2/8/11/23-26/32/35/45 |
| R12 | `gate4_result.json.breakdown.readability` — 80.4, `cli.py` MI 56.73, `executor.py` MI 58.05 |
| R13 | `gate4_result.json.da_waiver = {"architecture": true}` |
| R14 | `test_submit_injection_semicolon_rejected`, `..._pipe_...`, `..._ampersand_...`, `..._dollar_...`, `..._greater_than_...`, `..._less_than_...`, `..._backtick_...`, `test_submit_rejects_injection_chars`, `test_no_shell_true_in_source`, `test_shell_true_grep_zero_matches`; bandit — 2 LOW (B404 `executor.py:12`, B603 `executor.py:89`) |
| R15 | `.methodology/bug_hunt_report.json` — `cli#2`, `executor#3`, `store#4` all `confirmed: false` |
| R16 | `test_kpi_submit_status_p95_under_50ms_100_iter`, `test_kpi_submit_status_p95_under_100ms_1000_tasks` — pass individually, no aggregate artifact |
| R17 | `FINAL_SIGN_OFF.md` line 41 known-limitations paragraph |

## 5. Closed Risks

| ID | Risk | Resolution |
|----|------|-----------|
| R-BH-01 | `clear_command` wrote `breaker.json` outside the breaker flock, allowing a `clear` to clobber an in-flight `record_failure` read-modify-write (bug hunt `cli#1`, MEDIUM, concurrency lens, confirmed) | Fixed. `breaker.reset(home)` now wraps the write in `_locked(home)` (`breaker.py:72`) and `cli.py:147` calls it instead of `breaker.save()`. Regression test: `test_clear_command_acquires_breaker_lock` (RED before fix, GREEN after). This was a concrete instance of R1 — it is why R1's residual likelihood is 1 rather than 2. |

## 6. Register Rules

1. A risk is only marked MITIGATED when a named test or tool output in §4 backs it.
2. Residual likelihood is never lowered below 2 on a heuristic mitigation (see R7 network-fs detection).
3. An inherent score ≥ 9 requires a formal plan in `RISK_MITIGATION_PLANS.md`, even when the residual score is already LOW — the plan then documents the standing control rather than new work.
4. Risks derived from an accepted gate finding (R10, R12, R15, R16, R17) stay OPEN — accepted; they are monitored, not scheduled. Closing them would require a refactor with no functional driver, which the project's change-scope rules forbid.
