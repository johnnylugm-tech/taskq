# RISK_STATUS_REPORT.md — taskq

> **Phase**: 7 — Risk Management
> **Project**: taskq
> **Report date**: 2026-07-27
> **Register**: `07-risk/RISK_REGISTER.md` · **Plans**: `07-risk/RISK_MITIGATION_PLANS.md`
> **Quality baseline**: Gate 4 composite 97.2 · `open_critical = 0` · `open_high = 0` · 5/5 FRs at Gate 1 PASS

---

## 1. Executive Summary

17 risks are tracked. 9 are seeded from `SPEC.md` §9 (R1–R9); 8 are derived from Gate 3 / Gate 4 findings, the mutation report, and the adversarial bug hunt.

| Metric | Value |
|--------|-------|
| Total risks | 17 |
| HIGH by inherent score (≥ 9) | 8 — R1, R2, R5, R6, R7, R9, R11, R14 |
| HIGH by residual score | **1 — R14** |
| MITIGATED (test-verified) | 9 — R1–R9 |
| OPEN, active work owed | 3 — R7, R11, R14 |
| OPEN, accepted / monitored | 5 — R10, R12, R15, R16, R17 |
| OPEN, awaiting human sign-off | 1 — R13 |
| CLOSED this phase | 1 — R-BH-01 (breaker flock bypass) |
| Blocking release | **0** |

Every SPEC §9 risk has a shipped mitigation with named verifying tests. The residual profile is dominated by one item — R14, where the command-injection control is a 7-character denylist whose completeness cannot be established by test. It is a design-accepted risk requiring a recorded architectural decision, not new code.

No risk in this register blocks the release certified in `FINAL_SIGN_OFF.md`.

## 2. Status by Risk

| ID | Risk | Category | Inherent | Residual | Status | Owner | Target date |
|----|------|----------|----------|----------|--------|-------|-------------|
| R14 | Injection denylist is bypassable | security | 10 | **10 HIGH** | OPEN — decision owed | ARCHITECT | 2026-08-03 |
| R7 | `flock` no-op on network filesystems | concurrency | 9 | 6 MED | OPEN — doc owed | DEVOPS | 2026-08-03 |
| R11 | Mutation survivors, `breaker.py` / `store.py` | test-effectiveness | 9 | 6 MED | OPEN — metric re-run owed | QA-LEAD | 2026-07-31 |
| R13 | `architecture` passed on DA waiver | process | 6 | 6 MED | OPEN — human sign-off | Johnny | P8 entry, 2026-07-29 |
| R1 | Concurrent writes corrupt `tasks.json` | data-integrity | 15 | 5 MED | MITIGATED — standing control | DEVOPS | review 2026-07-29 |
| R5 | Secret written to disk in output tails | security | 15 | 5 MED | MITIGATED — standing control | DEVOPS | review 2026-07-29 |
| R9 | Schema migration loses data | data-integrity | 10 | 5 MED | MITIGATED — standing control | ARCHITECT | review 2026-07-29 |
| R10 | `cli.py` 78% / `store.py` 84% branch coverage | test-coverage | 6 | 6 MED | OPEN — accepted | QA-LEAD | monitored, no date |
| R12 | MI 56.73 `cli.py` / 58.05 `executor.py` | maintainability | 6 | 6 MED | OPEN — accepted | ARCHITECT | monitored, no date |
| R16 | NFR-01 / NFR-09 p95 not aggregated | perf-observability | 6 | 6 MED | OPEN — accepted | DEVOPS | monitored, no date |
| R17 | Golden-fixture drift / missing root `tests/integration/` | ci-integrity | 4 | 4 MED | OPEN — accepted | DEVOPS | monitored, no date |
| R2 | Subprocess hang or zombie | resilience | 9 | 3 LOW | MITIGATED — standing control | DEVOPS | review 2026-07-29 |
| R6 | Fault injection reachable in production | security | 9 | 3 LOW | MITIGATED — standing control | QA-LEAD | review 2026-07-29 |
| R3 | Breaker latches OPEN | availability | 6 | 3 LOW | MITIGATED | DEVOPS | review 2026-07-29 |
| R8 | 1000-task memory budget | scalability | 6 | 3 LOW | MITIGATED | DEVOPS | review 2026-07-29 |
| R4 | Cache replays a stale result | correctness | 6 | 2 LOW | MITIGATED | DEVOPS | review 2026-07-29 |
| R15 | Diagnosability debt (3 refuted findings) | usability | 3 | 3 LOW | OPEN — accepted | DEVOPS | monitored, no date |
| R-BH-01 | `clear_command` bypassed the breaker flock | concurrency | 9 | — | **CLOSED** | DEVOPS | closed 2026-07-26 |

Rows are ordered by residual score, then by inherent score.

## 3. Action Register — What Is Actually Owed

| # | Action | Risk | Owner | Deadline | Blocking P8? |
|---|--------|------|-------|----------|--------------|
| A1 | Re-run mutmut on `breaker.py` + `store.py`; reconcile `survivor_count: 0` against the raw `Survived (14)`; triage the 14 survivors into equivalent vs. genuine | R11 | QA-LEAD | 2026-07-31 | No |
| A2 | Record a decision log naming the normative injection control ("no shell invocation" recommended as primary, 7-char blocklist as secondary) | R14 | ARCHITECT | 2026-08-03 | No |
| A3 | Document the network-filesystem limitation for operators: `$TASKQ_HOME` on a shared mount downgrades cross-process locking; multi-host concurrent use unsupported | R7 | DEVOPS | 2026-08-03 | No |
| A4 | Human confirmation that the `architecture` DA waiver is acceptable and `no_circular_dependencies` holds | R13 | Johnny | 2026-07-29 | No — but it is the one item the authoring agent cannot close |

Nothing in this table requires an FR code change, so no TDD cycle is triggered by Phase 7. A1 may produce one if triage finds genuine assertion gaps; in that case the normal `TDD-RED → GREEN → IMPROVE → GATE1` sequence applies to the affected FR.

## 4. Movement Since Gate 4

| Change | Detail |
|--------|--------|
| 1 risk closed | R-BH-01 — `breaker.reset(home)` added (`breaker.py:72`) wrapping the write in `_locked`; `cli.py:147` switched from `breaker.save()`. Regression test `test_clear_command_acquires_breaker_lock` observed RED before the fix. |
| 3 findings refuted, retained as debt | Bug hunt `cli#2`, `executor#3`, `store#4` — all `confirmed: false`. Folded into R15 rather than discarded, because each is a real diagnosability wart even though none is a defect. |
| 1 new HIGH risk raised | R11 — the mutation report's internal contradiction was not visible in any gate score, since Gate 4 does not read `mutation_survivors.json`. |
| 1 residual HIGH retained | R14 — bandit B404/B603 were accepted as "intentional" at Gate 4. That is correct for the tool finding, but the underlying denylist-completeness question is a risk item, not a lint item, so it is carried here rather than closed. |

## 5. Coverage Check Against Sources

| Source | Items | All represented? |
|--------|-------|------------------|
| `SPEC.md` §9 | R1–R9 | Yes — 9/9, one-to-one |
| Gate 3 open issues | coverage, integration coverage, assertion quality, bandit ×2 | Yes — R10, R14; assertion-quality noted under R11 |
| Gate 4 open issues | coverage ×2, readability ×2, bandit ×2, assertion quality, DA waiver | Yes — R10, R12, R13, R14 |
| `mutation_survivors.json` | 14 survivors + count contradiction | Yes — R11 |
| `bug_hunt_report.json` | 1 confirmed + 3 refuted | Yes — R-BH-01 (closed), R15 |
| `FINAL_SIGN_OFF.md` limitations | 5 items | Yes — R14, R13, R17 (×2), R16 |
| `.methodology/deferred_fixes.md` | file absent | N/A — no deferred fixes exist for this project |
| `.sessi-work/issue_registry.json` | file absent | N/A — issue state read from `gate3_result.json` / `gate4_result.json`, both of which report 0 critical / 0 high |

Both absent files are recorded as absent. Nothing was inferred to fill them.

## 6. Assessment Confidence

**Where this assessment is most likely wrong:**

1. **R11's severity may be overstated.** Judging the 14 survivors HIGH rests on the raw mutmut transcript being accurate and the mutants being semantically meaningful. If most are equivalent mutants (a plausible outcome on `store.py` lines 2/8/11, which are low in the file and likely imports or constants), the real severity is MEDIUM or lower. This is exactly why A1 is a triage action rather than a fix action.
2. **R14's residual score may be too pessimistic.** With `shell=True` absent everywhere and execution going through an argv list, shell metacharacters are inert. The scenario that keeps residual impact at 5 requires a future change to reintroduce shell invocation. Scored on today's code alone, residual would be closer to 4. It is held at HIGH deliberately, because the risk lives in the control's structure rather than in current behaviour.
3. **The likelihood axis is judgement, not measurement.** No production telemetry exists for this project — it has never run outside its own test suite. Every likelihood value is an engineering estimate over the SPEC's operating assumptions.

**Unverified assumptions:**

- That `os.replace` is atomic on the deployment filesystem. Verified for local POSIX filesystems; unverified for the network-filesystem case that R7 covers.
- That the mutmut raw transcript in `mutation_survivors.json` reflects the current HEAD. `generated_at` is 2026-07-26T02:54Z, which predates the R-BH-01 fix, so the `breaker.py` survivor lines may already be stale.
- That the four action owners (DEVOPS / ARCHITECT / QA-LEAD) map to real people. They are the SAD §3183 P7 RACI roles; in a single-operator project all four resolve to Johnny.

**Confidence: Medium-High.** High on the register's completeness and on the mitigation-to-test mapping — every claim in §4 of the register cites a test that exists in `03-development/tests/`. Medium on the numeric scores, for the reasons above. The one item that cannot be resolved by the authoring agent is A4 (R13), the architecture waiver.

## 7. Phase 7 Exit Position

- All 3 P7 deliverables present: `RISK_REGISTER.md`, `RISK_MITIGATION_PLANS.md`, `RISK_STATUS_REPORT.md`.
- 0 risks block release; the Gate 4 certification in `FINAL_SIGN_OFF.md` stands.
- 4 actions are owed, none on the critical path to P8; the earliest deadline is A1 on 2026-07-31.
- No FR code was modified in Phase 7, so the per-FR `GATE1-DELTA` loop has nothing to re-evaluate.
