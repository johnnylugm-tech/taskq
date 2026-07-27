# CONFIG_RECORDS.md - taskq

> On-demand Lazy Load template.

## 1. Version Information
- Version: vharness-v4-20260727-score97-18-geefa302
- Git Commit: eefa302
- Release Date: 2026-07-27

## 2. Runtime Configuration
| Environment | Config |
|-------------|--------|
| Development | {{config}} |
| Production | {{config}} |

## 3. Dependency List
```
{{pip freeze / npm lock output}}
```

## 4. Environment Variables
| Variable | Type | Description |
|----------|------|-------------|
| {{VAR}} | secret | {{description}} |

## 5. Deployment Log
| Date | Version | Method | Executor |
|------|---------|--------|----------|
| 2026-07-27 | harness-v4-20260727-score97-18-geefa302 | {{method}} | {{name}} |

## 6. Configuration Change Log
| Phase | Change | Rationale |
|-------|--------|----------|
| Phase 8 | {{change}} | {{reason}} |

## 7. Rollback SOP
**Trigger Condition**: {{condition}}
**Commands**:
```bash
{{rollback commands}}
```

## 8. Configuration Compliance
- [ ] Phase 7 risk mitigations implemented
- [ ] Monitoring thresholds configured
- [ ] Circuit breaker enabled

## Human Context (P8 append)

> Appended by P8 config reviewer. Framework-generated sections 1-8 above are unmodified.
> Values marked `TBD` have no verified source in the repo and MUST be filled by the owner before release sign-off.

### 9.1 Ownership per Config Item
| Config Item | Source of Truth | Owner (role) | Change Approval |
|-------------|-----------------|--------------|-----------------|
| Runtime config (§2) | `taskq` config module | TBD — service owner | TBD |
| Dependency lockfile (§3) | repo lockfile | TBD — maintainer | PR review |
| Environment variables / secrets (§4) | secret manager (not in repo) | TBD — platform/secops | TBD |
| Deployment method (§5) | CI pipeline definition | TBD — release manager | TBD |
| Rollback SOP (§7) | this document | TBD — on-call owner | TBD |
| High-risk module tuning (`taskq.executor`, `taskq.store`) | source modules | TBD — module owner | Gate 4 re-run |

Rule: no config item ships without a named owner; `TBD` is a release blocker, not a default.

### 9.2 Secret Rotation Cadence
| Secret Class | Cadence | Trigger-based Rotation | Executor |
|--------------|---------|------------------------|----------|
| Application credentials / API tokens | 90 days | On suspected leak, on owner offboarding | TBD |
| Datastore credentials (`taskq.store`) | 90 days | On leak, on access-scope change | TBD |
| CI / deploy credentials | 180 days | On pipeline owner change | TBD |
| Break-glass / emergency credentials | 30 days, or immediately after each use | Any use | TBD |

Notes:
- Rotation must be zero-downtime (overlap old+new before revoke); a rotation that requires an outage is a design defect, not an operational one.
- Every rotation appends a row to §6 Configuration Change Log.
- Relates to NFR-02 / NFR-04 (security dimension).

### 9.3 Access Audit Log Reference
| Item | Value |
|------|-------|
| Audit log location | TBD — central log sink (not in repo) |
| Retention | TBD (recommend ≥ 365 days) |
| Events captured | secret read, secret rotate, config change, deploy, rollback |
| Review cadence | Quarterly, plus after every rollback |
| Reviewer | TBD — security reviewer (must differ from config owner) |
| In-repo cross-refs | §5 Deployment Log, §6 Configuration Change Log |

Separation of duties: the config owner in §9.1 must not be the sole audit reviewer.
