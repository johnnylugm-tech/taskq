# BASELINE.md - taskq

> Phase 5 verification baseline snapshot. Records the system state at Gate 3
> pass + per-FR Gate 1 certification so that Gate 4 (Phase 6) and any future
> regression delta has a deterministic reference point.

## 1. Baseline Overview
- Project: `taskq` (local task queue CLI, Python 3.11 stdlib only)
- Canonical spec: `SPEC.md` v4.0.0 (2026-07-11) — 5 FR / 10 NFR / 8 env vars
- Phase: **5 — Verification**
- Last Gate: **Gate 3** (composite 96.3) + per-FR Gate 1 PASS for all 5 FRs
- Last FR completed: **FR-03** (Gate 1 score 97.5)
- Author: P5 Verification Author (claude-code sub-agent)
- Reviewer: Johnny (project owner)
- session_id: harness-methodology v2.9 Phase 5 verification run
- Date: 2026-07-27 (UTC)
- Methodology state: `.methodology/state.json` `state=RUNNING, current_phase=5, last_gate=1, last_fr=FR-03, last_update=2026-07-27T02:22:17Z`
- Source of truth: `quality_manifest.json` (Gate 1 FR scores) + `gate3_result.json` (14-dim score) + `04-testing/TEST_RESULTS.md` + `04-testing/COVERAGE_REPORT.md`

## 2. Functional Baseline (maps to SRS FR, 100% complete)

| FR ID | Feature Description | Baseline Status | Gate 1 Score | Owner Module | Notes |
|-------|--------------------|-----------------|--------------|--------------|-------|
| FR-01 | 任務提交與驗證 (submit validation: empty / length / injection / name-unique) | PASS | 100.0 | `taskq.cli` | Highest score; full coverage |
| FR-02 | 任務執行器 (subprocess.run + ThreadPoolExecutor `--all` + thread-safe store) | PASS | 97.73 | `taskq.executor` + `taskq.store` | Both modules are framework-classified high-risk |
| FR-03 | 重試與斷路器 (exponential backoff + OPEN/HALF_OPEN/CLOSED state machine) | PASS | 97.46 | `taskq.breaker` + `taskq.executor` | Last FR certified in this run (commit 0daa418) |
| FR-04 | 結果 TTL 快取 (sha256(command) cache, atomic + thread-safe write) | PASS | 98.58 | `taskq.cache` | Highest scoring non-trivial FR |
| FR-05 | CLI 整合 (argparse subcommands + --json flag + 5 exit codes) | PASS | 97.72 | `taskq.cli` | All 5 exit codes (0/1/2/3/4) exercised |

All 5 FRs reach the PASS certification precedence (Gate 1 has no FAIL / Conditional PASS / UNKNOWN entries).

## 3. Quality Baseline

| Metric | Threshold | Actual | Status |
|--------|-----------|--------|--------|
| Gate 3 composite score (14-dim) | ≥ 80 | 96.3 | PASS |
| Coverage (`03-development/src/`) | ≥ 80% | 100% (467/467 statements, 0 missed) | PASS |
| Logic correctness (Gate 3) | ≥ 90 | 100 (see `gate3_result.json`) | PASS |
| Linting (Gate 3) | ≥ 90 | 100 | PASS |
| Type safety (Gate 3) | ≥ 85 | 100 (mypy clean) | PASS |
| Security (Gate 3) | ≥ 80 | 98 (informational B404/B603 from `executor.py` only) | PASS |
| Secrets scanning (Gate 3) | 100 | 100 (gitleaks re-run: `no leaks found`) | PASS |
| License compliance (Gate 3) | 100 | 100 | PASS |
| Test cases (live pytest) | 100% pass | 6,175 pass / 6,176 collected (1 harness golden mismatch) | PARTIAL — see §5 |
| Integration tests (`tests/integration/`) | n/a | `collected 0 items` (no integration test dir at repo root) | SKIPPED — see §5 |

Evidence sources:
- `04-testing/COVERAGE_REPORT.md` (total=100, statements=467, missed=0)
- `04-testing/TEST_RESULTS.md` (6,176 collected, 6,175 passed, 1 failed)
- `.methodology/gate3_result.json` (`overall_score=96.3, meets_target=true, quality_complete=true, open_critical_count=0, open_high_count=0`)
- `bandit -r 03-development/src/ -ll` — `No issues identified.` at MEDIUM/HIGH

## 4. Performance Baseline (A/B monitoring)

| Metric | Baseline Value | Source / Spec |
|--------|---------------|---------------|
| Test-suite wall time (full pytest discovery) | 109.60 s | `04-testing/TEST_RESULTS.md` |
| Total lines of code (`03-development/src/taskq/`) | 745 (bandit LOC) | bandit scan 2026-07-27 |
| Source statements (under coverage) | 467 | `coverage report --format=total` ⇒ 100 |
| Source size (per-module LoC) | `__init__.py`=5, `__main__.py`=9, `breaker.py`=141, `cache.py`=114, `cli.py`=234, `config.py`=48, `executor.py`=300, `store.py`=151 (total 1002) | `wc -l` |
| NFR-01 (`submit` + `status` p95 < 50 ms) | not separately captured in `TEST_RESULTS.md` | see §5 |
| NFR-09 (1000-task p95 < 100 ms; 0 loss over 100 tasks) | not separately captured in `TEST_RESULTS.md` | see §5 |
| Mutation score (per-FR Gate 1) | not re-run in P5 (per scope rules) | reference Gate 1 artifacts on demand |

> Performance telemetry for NFR-01 / NFR-09 is documented at the acceptance-criterion
> level inside `03-development/tests/test_fr01.py::test_submit_json_output` and
> `03-development/tests/test_fr02.py::test_run_all_concurrent`; aggregate
> p95 / throughput numbers are **not** surfaced in `TEST_RESULTS.md` and
> are flagged as a known baseline gap in §5 below.

## 5. Known Issues
| Severity | Count | Description |
|----------|-------|-------------|
| HIGH | 0 | none |
| MEDIUM | 0 | none |
| LOW (test) | 1 | `harness/tests/test_workflowgen_golden.py::test_generated_output_matches_golden[4]` fails — harness golden-fixture drift (additional permitted HUNT-RESOLVE instructions in generated output). Disposition: deferred (golden update requires an authorized harness change; out of scope for P5). Does not touch `03-development/src`. |
| LOW (security) | 2 | Bandit B404 (`subprocess` import in `executor.py`) and B603 (`subprocess` call of validated task commands) — both informational; required by design of FR-02. Disposition: acknowledged false positives; no action. |
| LOW (coverage baseline gap) | 1 | NFR-01 / NFR-09 p95 latency not aggregated into `TEST_RESULTS.md`. Disposition: AC-level only; aggregate metric deliberately owned by Gate 1 per-FR (FR-01 score 100.0, FR-02 score 97.73) and not re-run in P5. |
| LOW (integration scope) | 1 | `tests/integration/` does not exist at repo root. End-to-end coverage lives at `03-development/tests/integration/test_cli_end_to_end.py` (discovered by pytest rootdir). Re-run from this directory in P5 returned `collected 0 items`, no failures. Disposition: skip documented as graceful. |

HIGH severity count = 0, satisfying the baseline sign-off precondition.

## 6. Change Log

| Date | Change | Commit / Ref |
|------|--------|--------------|
| 2026-07-27 | feat(FR-03): Gate1 PASS — score=97.5 | `0daa418` |
| 2026-07-27 | feat(FR-05): Gate1 PASS — score=97.7 | `8342048` |
| 2026-07-27 | feat(FR-02): Gate1 PASS — score=97.7 | `c218690` |
| 2026-07-27 | feat(FR-04): Gate1 PASS — score=98.6 | `2418b9a` |
| 2026-07-27 | feat(FR-01): Gate1 PASS — score=100.0 | `4be382f` |
| 2026-07-27 | chore: bump harness submodule to 37adc43 (Round 24: env-check classification rule fix) | `4d5d2c8` |
| 2026-07-27 | chore: bump harness submodule to e2b98b6 (Round 23: env-check bash-timeout-aware background poll) | `7bf9ecc` |
| 2026-07-26 | fix(breaker): delete two no-op except-passthrough wrappers | `4eba939` |
| 2026-07-26 | chore: bump harness submodule to 4aa6ff2 (Round 22: advance-phase pytest scope + pragma allowlist fix) | `310e84c` |
| 2026-07-26 | chore: phase 4 clean-up | `3b1d61d` |

## 7. Acceptance Sign-off
- P5 Verification Author: harness-methodology P5 sub-agent — session P5-verification-2026-07-27 — 2026-07-27
- Quality owner: Johnny (project owner) — pending review — 2026-07-27
- Approver: Johnny (project owner) — pending sign-off after BASELINE review — 2026-07-27
- Companion deliverable: `05-verification/VERIFICATION_REPORT.md` (generated by `harness_cli.py generate-verification-report`)
