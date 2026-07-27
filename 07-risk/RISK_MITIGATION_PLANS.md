# RISK_MITIGATION_PLANS.md — taskq

> **Phase**: 7 — Risk Management
> **Project**: taskq
> **Date**: 2026-07-27
> **Scope**: formal plans for every risk with an inherent score (likelihood × impact) ≥ 9
> **Register**: `07-risk/RISK_REGISTER.md`

---

## Plan Coverage

| ID | Risk | Inherent | Residual | Plan type | Owner | Deadline |
|----|------|----------|----------|-----------|-------|----------|
| R1 | Concurrent writes corrupt `tasks.json` | 15 | 5 | Standing control | DEVOPS | Control active — review at P8 entry (2026-07-29) |
| R5 | Secret written to disk in output tails | 15 | 5 | Standing control | DEVOPS | Control active — review at P8 entry (2026-07-29) |
| R9 | Schema migration loses data | 10 | 5 | Standing control | ARCHITECT | Control active — review at P8 entry (2026-07-29) |
| R14 | Injection denylist is bypassable | 10 | 10 | **Active work** | ARCHITECT | Decision recorded by 2026-08-03 |
| R2 | Subprocess hang / zombie | 9 | 3 | Standing control | DEVOPS | Control active — review at P8 entry (2026-07-29) |
| R6 | Fault-injection reachable in production | 9 | 3 | Standing control | QA-LEAD | Control active — review at P8 entry (2026-07-29) |
| R7 | `flock` no-op on network filesystems | 9 | 6 | **Active work** | DEVOPS | Documented limitation by 2026-08-03 |
| R11 | Mutation survivors on `breaker.py` / `store.py` | 9 | 6 | **Active work** | QA-LEAD | Metric re-run by 2026-07-31 |

Owner roles follow the P7 RACI in `harness/SAD.md` §3183 — DEVOPS authors risk assessments, ARCHITECT reviews them. QA-LEAD is used for test-effectiveness items.

**Standing control** = the mitigation is already implemented and test-verified; the plan documents the control, its owner, and the condition that would reopen it.
**Active work** = residual risk is still MEDIUM or HIGH and a concrete next action is owed.

---

## R1 — Concurrent writes corrupt `tasks.json`

- **Inherent** 3 × 5 = 15 (HIGH) · **Residual** 1 × 5 = 5 (MEDIUM)
- **Owner**: DEVOPS · **Reviewer**: ARCHITECT
- **Deadline**: control active; re-verify at P8 entry, 2026-07-29

**Control in force**
1. Every write to `tasks.json`, `breaker.json`, `cache.json` goes through a temp-file write followed by `os.replace` (atomic on POSIX) — NFR-03.
2. Writers take an exclusive `fcntl.flock`; readers take a shared lock — NFR-08.
3. Atomic write is the primary defence; the lock is a serialisation optimisation layered on top. If the lock is unavailable the file is still never left half-written.

**Verification** — `test_cross_process_no_corruption`, `test_four_process_concurrent`, `test_posix_flock`, `test_atomic_write_three_files`, `test_atomic_add_task`, `test_run_all_concurrent`, `test_kill_mid_write`, `test_corrupt_mid_write`, `test_mid_write_crash`, `test_write_unlocked_cleans_up_temp_file_on_failure`.

**Why residual impact stays at 5** — the impact of a corrupted task file is unchanged by the mitigation; only its likelihood moved. Residual impact is never discounted for a data-integrity risk.

**Reopen condition** — any new producer of one of the three data files that does not route through the locked write helper. This exact failure already occurred once (see R-BH-01: `clear_command` bypassed `breaker._locked`), which is why the reopen condition is stated as a code-shape rule rather than a test.

**Monitoring** — `preflight_reliability_lint` at P4+ plus the bug-hunt concurrency lens. A grep for direct `_write_unlocked` callers outside the store module is the cheapest recurring check.

---

## R5 — Secret written to disk in `stdout_tail` / `stderr_tail`

- **Inherent** 3 × 5 = 15 (HIGH) · **Residual** 1 × 5 = 5 (MEDIUM)
- **Owner**: DEVOPS · **Reviewer**: ARCHITECT
- **Deadline**: control active; re-verify at P8 entry, 2026-07-29

**Control in force**
1. Any line matching `(sk-[A-Za-z0-9_-]{8,}|token=\S+)` is replaced in full by `[REDACTED]` — NFR-04.
2. Redaction runs **before** truncation, so a secret cannot survive by sitting on a truncation boundary.
3. Both streams are covered, not just stdout.

**Verification** — `test_sk_token_redacted`, `test_token_equals_redacted`, `test_sk_token_redacted_stderr`, `test_token_equals_redacted_stderr`, `test_secret_redaction_before_truncation`; gitleaks over 83 commits reports 0 leaks; `secrets_scanning` dimension 100.0 at both Gate 3 and Gate 4.

**Known limitation (accepted, not waived)** — the redactor is pattern-based. A credential shaped unlike `sk-…` or `token=…` (for example a bare 40-char hex string, or `Authorization: Bearer …`) is not redacted. This is the same denylist-versus-allowlist weakness as R14 and is accepted for the same reason: the spec fixes the two patterns.

**Reopen condition** — a new credential format appears in task output, or the redaction call is moved after truncation.

---

## R9 — Schema migration loses data

- **Inherent** 2 × 5 = 10 (HIGH) · **Residual** 1 × 5 = 5 (MEDIUM)
- **Owner**: ARCHITECT · **Reviewer**: DEVOPS
- **Deadline**: control active; re-verify at P8 entry, 2026-07-29

**Control in force**
1. All three data files carry a root `version` field (currently 1) — NFR-10.
2. `version < 1` is upgraded in place and written back; the original is first copied to `<file>.v<n>.bak`.
3. A failed migration keeps the backup and exits 1 fail-fast — no silent rebuild.
4. `version > 1` is refused rather than best-effort parsed.

**Verification** — `test_v0_migrate_with_backup`, `test_migration_fail_fast`, `test_v2_refuses`, `test_add_task_on_v0_migrates`, `test_version_field_invariant`, `test_read_unlocked_rejects_unsupported_schema`.

**Residual gap** — no v1 → v2 migration exists yet, so the forward path is proven only for v0 → v1. The first real v2 schema change must ship with its own migration test; the harness cannot pre-verify it.

**Reopen condition** — introduction of schema v2.

---

## R14 — Command-injection denylist is bypassable — **ACTIVE**

- **Inherent** 2 × 5 = 10 (HIGH) · **Residual** 2 × 5 = 10 (**HIGH — the only residual HIGH in the register**)
- **Owner**: ARCHITECT · **Reviewer**: DEVOPS
- **Deadline**: architectural decision recorded by **2026-08-03**

**Current control**
1. `submit` rejects any command containing one of 7 characters: `;` `|` `&` `$` `>` `<` `` ` `` — NFR-02.
2. `shell=True` appears nowhere in the codebase, asserted at source level.
3. Execution goes through `shlex.split()` + `subprocess.run` with an argument list.

**Verification** — one negative test per blocked character (`test_submit_injection_semicolon_rejected` … `test_submit_injection_backtick_rejected`), `test_submit_rejects_injection_chars`, `test_no_shell_true_in_source`, `test_shell_true_grep_zero_matches`. Bandit reports exactly 2 LOW findings, both intentional: B404 (`import subprocess`, `executor.py:12`) and B603 (`subprocess.run` without `shell=True`, `executor.py:89`).

**Why residual is not lowered** — a denylist enumerates what is forbidden, so its completeness cannot be demonstrated by testing the 7 enumerated cases. Newline, `\r`, `$(`-free command substitution via `\n`, and quoting tricks are not in the blocklist. The genuine safety property here is *not* the blocklist — it is the absence of `shell=True`: with no shell, metacharacters are inert argv bytes. The blocklist is defence in depth, and treating it as the primary control would be the actual risk.

**Plan**
1. **Decide and record** (owner ARCHITECT, by 2026-08-03) which of the two controls is normative. Recommendation: declare "no shell invocation" the primary control and the 7-character blocklist an input-hygiene secondary. Record it as a decision log under `.methodology/decision_logs/`.
2. **Do not** widen the blocklist speculatively. Adding characters neither closes the class nor is traceable to a requirement; NFR-02 fixes the set at 7.
3. **Do not** convert to an allowlist without a spec change — that would alter FR-01's accepted-input contract and require a new TDD cycle.
4. If step 1 concludes the blocklist is normative, the risk must be escalated to human review because that conclusion is not defensible by test.

**Escalation** — if no decision is recorded by 2026-08-03, escalate to Johnny with this section attached.

---

## R2 — Subprocess hang or zombie

- **Inherent** 3 × 3 = 9 (HIGH) · **Residual** 1 × 3 = 3 (LOW)
- **Owner**: DEVOPS · **Reviewer**: ARCHITECT
- **Deadline**: control active; re-verify at P8 entry, 2026-07-29

**Control in force**
1. Every `subprocess.run` carries an explicit `timeout=` — FR-02.
2. A timeout kills the child and cleans up orphans rather than leaking them.
3. Timeout surfaces as exit code 4, distinct from failure (1) and unknown-task (2).
4. `preflight_reliability_lint` blocks any timeout-less `subprocess.run`/`Popen` from P4 onward, so the control is enforced mechanically and not by review discipline.

**Verification** — `test_timeout_exit_code_4`, `test_run_subprocess_orphan_cleanup_on_timeout`, `test_run_all_with_timeout_returns_4`, `test_run_all_timeout_reported_via_exit_code_4`, `test_run_timeout_status_returns_4`, `test_run_all_no_timeout_returns_0`.

**Residual gap** — a grandchild process spawned by the task command is not tracked; killing the direct child does not reap it. Out of scope for FR-02 (single-process task model) and recorded here so the gap is not rediscovered as a defect.

**Reopen condition** — task commands begin spawning process trees, or the reliability lint is disabled.

---

## R6 — Fault-injection hooks reachable on the production path

- **Inherent** 3 × 3 = 9 (HIGH) · **Residual** 1 × 3 = 3 (LOW)
- **Owner**: QA-LEAD · **Reviewer**: ARCHITECT
- **Deadline**: control active; re-verify at P8 entry, 2026-07-29

**Control in force**
1. Injection is triggered only by the explicit `--inject-fault=<scenario>` CLI flag or a unit-test monkeypatch — NFR-07.
2. Deliberately **not** env-var driven; SPEC §5.3 keeps the 8 `TASKQ_*` variables free of any test hook, so no ambient configuration can turn injection on.
3. The production path rejects the flag rather than ignoring it — a misuse fails loudly.

**Verification** — `test_inject_fault_rejected_in_production`, `test_inject_fault_rejected_on_prod`, `test_main_inject_fault_rejected_without_env`, `test_inject_fault_triggered_when_opted_in`, `test_main_inject_fault_triggered_with_env`, `test_fault_injection_fails_fast_or_recovers`.

**Reopen condition** — any attempt to move the trigger to an environment variable, which would make injection reachable without an argv change.

---

## R7 — `flock` is a no-op on NFS / network filesystems — **ACTIVE**

- **Inherent** 3 × 3 = 9 (HIGH) · **Residual** 2 × 3 = 6 (MEDIUM)
- **Owner**: DEVOPS · **Reviewer**: ARCHITECT
- **Deadline**: limitation documented for operators by **2026-08-03**

**Control in force**
1. flock is specified as a best-effort enhancement, not the safety property — NFR-08.
2. On a detected network filesystem the code degrades to atomic-write-only and emits a `WARNING`.
3. Atomic write (NFR-03) continues to hold on any filesystem with a POSIX-compliant `rename`.

**Verification** — `test_network_fs_warning`, `test_posix_flock`.

**Why residual likelihood stays at 2** — network-fs detection is a heuristic. An exotic mount (FUSE overlay, SMB with unusual `st_dev` reporting, a container bind-mount over NFS) may be classified as local, in which case flock is silently ineffective and no warning is emitted. The consequence is degraded serialisation, not corruption, because atomic write still applies — hence impact 3, not 5.

**Plan**
1. **Document** in operator-facing notes (owner DEVOPS, by 2026-08-03): "`$TASKQ_HOME` on a network filesystem downgrades cross-process locking; concurrent multi-host use is unsupported." Documentation, not code — the code behaviour is already correct.
2. **Do not** attempt to enumerate every network filesystem. That is an unbounded denylist with the same structural weakness as R14.
3. **Do not** add an override flag. It would be a new configuration surface with no requirement behind it.

**Reopen condition** — a deployment places `$TASKQ_HOME` on a shared mount with writers on more than one host.

---

## R11 — Mutation survivors on `breaker.py` and `store.py` — **ACTIVE**

- **Inherent** 3 × 3 = 9 (HIGH) · **Residual** 2 × 3 = 6 (MEDIUM)
- **Owner**: QA-LEAD · **Reviewer**: DEVOPS
- **Deadline**: metric integrity resolved by **2026-07-31**

**Finding** — `.methodology/mutation_survivors.json` contains two contradictory statements:

- `survivor_count: 0` with `survivors: []`
- a `raw` mutmut transcript reading `Survived 🙁 (14)` — `breaker.py` lines 57, 59-60, 76 (4) and `store.py` lines 2, 8, 11, 23-26, 32, 35, 45 (10), plus `Untested/skipped (376)`

Both named modules are the project's declared high-risk modules (`taskq.executor`, `taskq.store` per `CLAUDE.md`; `breaker.py` carries the FR-03 state machine).

**Why this is HIGH inherent** — surviving mutants on a write path (`store.py`) and a state machine (`breaker.py`) mean the tests execute those lines without constraining their behaviour. That is precisely the failure mode that lets an R1- or R3-class defect ship while coverage reads 97%. The 376 untested/skipped mutants make the picture worse, not better.

**Plan**
1. **Re-run** mutmut on `breaker.py` and `store.py` and reconcile `survivor_count` with the raw transcript (owner QA-LEAD, by 2026-07-31). Until reconciled, treat the *raw* figure as authoritative — the conservative reading.
2. **Triage** each of the 14 survivors into: (a) equivalent mutant, no action; (b) genuine assertion gap. Record the split; do not assume all 14 are real.
3. For category (b) only, strengthen the specific assertion through the normal per-FR TDD cycle (`run-fr-step --step TDD-RED` first — the strengthened assertion must fail against the mutant before it is accepted).
4. **Do not** add tests for category (a). Killing equivalent mutants inflates the score without adding signal.

**Related metric caveat** — `test_assertion_quality` scored 91.6 with the note that 10 tests contain no `ast.Assert` node because they rely on `pytest.raises` alone. That is an AST-walker artifact, not a real gap, and is not part of this plan.

**Escalation** — if the re-run confirms genuine assertion gaps on `store.py` write paths, that raises R1's residual likelihood and both entries must be re-scored together.

---

## Plans Not Required

R3 (6), R4 (6), R8 (6), R10 (6), R12 (6), R13 (6), R15 (3), R16 (6), R17 (4) are below the inherent-score-9 threshold. They are tracked in the register and reported in `RISK_STATUS_REPORT.md` without a formal plan.

One of them warrants an explicit note: **R13** (`architecture` dimension passed on a devil's-advocate waiver rather than tool evidence) is a process risk, not a code risk. It cannot be closed by the authoring agent — it needs human confirmation that `no_circular_dependencies` holds. It is listed in the status report as awaiting Johnny's sign-off.
