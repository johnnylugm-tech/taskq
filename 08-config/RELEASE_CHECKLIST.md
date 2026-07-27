# RELEASE_CHECKLIST

## Pre-Release Checks
- [ ] All P1-P7 phases completed and artifacts generated.
- [ ] CI pipeline fully passed.
- [ ] Final Sign Off approved.
- [ ] Production environment provisioned.
- [ ] Rollback plan documented.

## Human Context (P8 append)

> Appended by P8 config reviewer. The framework-generated "Pre-Release Checks" section above is unmodified.
> `TBD` values have no verified source in the repo and are release blockers until filled.

### Release Identity (verified from repo state)
| Item | Value | Status |
|------|-------|--------|
| Gate 4 | PASS, score 97.2 (14 dimensions, threshold ≥ 85) | Verified — CLAUDE.md harness status block |
| Gate 1 FR coverage | FR-01..FR-05 all COMPLETE (100.0 / 93.9 / 97.5 / 98.6 / 96.1) | Verified — FR registry |
| Gate 2 / Gate 3 | 96.7 / 96.3 PASS | Verified — harness status block |
| Git commit | eefa302 | Verified — CONFIG_RECORDS.md §1 |
| Tag | harness-v4-20260727-score97-18-geefa302 | Verified — CONFIG_RECORDS.md §1 |
| quality_manifest composite_score | see `quality_manifest` artifact; harness block reports Gate 4 = 97.2 | Requires verification against manifest file at sign-off |

### Deployment Runbook
| Item | Value |
|------|-------|
| Runbook URL | TBD — no runbook URL present in repo |
| Deploy method | TBD (§5 of CONFIG_RECORDS.md is still `{{method}}`) |
| Pre-deploy gate | Gate 4 PASS + rollback SOP populated (CONFIG_RECORDS.md §7 currently templated) |

### Rollback Owner + On-Call
| Role | Name / Rotation | Escalation |
|------|-----------------|------------|
| Rollback owner (executes §7 SOP) | TBD | TBD |
| Primary on-call | TBD | TBD |
| Secondary / escalation | TBD | TBD |
| Rollback decision authority | TBD — must be reachable for the full monitoring window |

Rollback SOP body (CONFIG_RECORDS.md §7) is still a template — populate before release.

### Post-Release Monitoring
| Item | Value |
|------|-------|
| Dashboard URL | TBD |
| Watch window | 24h heightened, 7d normal |
| Focus modules | `taskq.executor`, `taskq.store` (high-risk per Phase 7) |
| Signals | task throughput / latency (NFR-01), error + retry rate (NFR-03, NFR-07, NFR-08), auth failures (NFR-02, NFR-04) |
| Alert routing | TBD — on-call rotation above |
| Abort criteria | Any Phase 7 mitigation threshold breached → rollback owner decides within 15 min |

### Customer Comms Template
- **Channel / owner**: TBD
- **Pre-release notice** (T-24h): `taskq <version> ships <date>. Scope: <FR summary>. Expected impact: none. Window: <start>-<end> <tz>.`
- **Release complete**: `taskq <version> (<tag>) is live as of <time> <tz>. Changes: <FR-01..FR-05 summary>. Report issues to <channel>.`
- **Incident / rollback**: `We identified <symptom> in taskq <version> at <time> <tz>. We rolled back to <previous tag>. Affected: <scope>. Current status: <status>. Next update: <time>.`
- **Post-incident**: `taskq <version> issue resolved at <time> <tz>. Root cause: <cause>. Prevention: <action>. Full write-up: <link>.`

Rule: no placeholder `<...>` or `TBD` may remain in a message actually sent, or in this checklist at sign-off.
