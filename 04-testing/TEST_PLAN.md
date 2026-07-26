# TEST_PLAN.md — taskq (Phase 4 Testing)

> Source of truth: `01-requirements/SRS.md` (FR-01..FR-05 ACs, NFR-01..NFR-10 ACs)
> + `.methodology/quality_manifest.json` (FR list, NFR→module traceability).
> Canonical spec: `SPEC.md v4.0.0`. Generated: 2026-07-26.
> Scope: master test-case catalogue authored once, ahead of per-FR TDD.
> Coverage classes per requirement: **P**ositive / **N**egative / **B**oundary / **E**dge.

## 0. Conventions

- **Test ID**: `TC-<FR|NFR>-<nn>[-<class>]`; class ∈ {P,N,B,E}.
- **Priority**: P0 (acceptance-gating) / P1 (core) / P2 (defensive).
- **Runner**: `pytest` (+ `pytest-benchmark` for NFR-01/09). Entry: `python -m taskq`.
- **Exit code map** (AC-5.4): `0` ok / `1` internal / `2` input+unknown-id / `3` breaker open / `4` single-task timeout.
- **8 env vars**: `TASKQ_HOME, TASKQ_TASK_TIMEOUT, TASKQ_MAX_WORKERS, TASKQ_RETRY_LIMIT, TASKQ_BACKOFF_BASE, TASKQ_BREAKER_THRESHOLD, TASKQ_BREAKER_COOLDOWN, TASKQ_CACHE_TTL`.
- Each row cites the AC it binds to.

---

## 1. FR-01 — Task submission and validation

Surface: `taskq submit "<command>" [--name NAME]`. Fail → exit 2 + stderr, no store write (AC-1.1..1.6).

| Test ID | Class | Description | Input | Expected output | Prio | AC |
|---------|-------|-------------|-------|-----------------|------|----|
| TC-FR01-01-P | P | Valid submit returns 8-hex id, status pending | `submit "echo hi"` | stdout = 8-hex id; task recorded `pending` w/ command,name,created_at; tasks.json atomically written | P0 | 1.1,1.5 |
| TC-FR01-02-P | P | `--json` single-line output | `submit "echo hi" --json` | stdout = `{"id": "...", "status": "pending"}` single line | P0 | 1.6 |
| TC-FR01-03-P | P | `--name` recorded when unique | `submit "echo hi" --name build` | pending task with name=build | P1 | 1.4,1.5 |
| TC-FR01-04-N | N | Empty command rejected | `submit ""` | exit 2 + stderr; no store write | P0 | 1.1 |
| TC-FR01-05-N | N | Whitespace-only command rejected | `submit "   "` | exit 2 (non-empty after strip) | P0 | 1.1 |
| TC-FR01-06-N | N | Duplicate `--name` vs pending/running collides | pre-existing pending `build`; `submit "x" --name build` | exit 2 + stderr; no write | P1 | 1.4 |
| TC-FR01-07-B | B | Length exactly 1000 accepted | `submit "<1000-char cmd>"` | success, pending | P1 | 1.2 |
| TC-FR01-08-B | B | Length 1001 rejected | `submit "<1001-char cmd>"` | exit 2 | P0 | 1.2 |
| TC-FR01-09-E | E | id is first 8 hex chars of uuid4 (format `^[0-9a-f]{8}$`) | `submit "echo hi"` | id matches regex | P1 | 1.5 |
| TC-FR01-10-E | E | Name uniqueness ignores done/failed states | done task `old`; `submit "x" --name old` | success (only pending/running block) | P2 | 1.4 |

Injection-character negatives are enumerated under **NFR-02** (7 chars) to avoid duplication; they also assert AC-1.3.

---

## 2. FR-02 — Task executor

Surface: `taskq run <id>` / `run --all`. No `shell=True` anywhere (AC-2.1..2.5).

| Test ID | Class | Description | Input | Expected output | Prio | AC |
|---------|-------|-------------|-------|-----------------|------|----|
| TC-FR02-01-P | P | Exit 0 → done | run id of `echo hi` | status `done`, exit_code 0 | P0 | 2.2 |
| TC-FR02-02-P | P | Uses `shlex.split`, capture_output, text, timeout (no shell) | run `echo a b` | argv split; stdout captured; no shell metachar expansion | P0 | 2.1 |
| TC-FR02-03-P | P | Result record fields present | run `echo hi` | record has exit_code, stdout_tail, stderr_tail, duration_ms, finished_at | P0 | 2.3 |
| TC-FR02-04-N | N | Non-zero exit → failed | run `python -c "import sys;sys.exit(1)"` | status `failed`, exit_code non-zero | P0 | 2.2 |
| TC-FR02-05-N | N | Timeout → timeout status, single-run exit 4 | `TASKQ_TASK_TIMEOUT=1` run `sleep 5` | status `timeout`; process exit 4 | P0 | 2.2,2.5 |
| TC-FR02-06-B | B | stdout_tail truncated to last 2000 chars | run cmd emitting 3000 chars | stdout_tail len == 2000 (tail) | P1 | 2.3 |
| TC-FR02-07-B | B | stderr_tail truncated to last 2000 chars | run cmd emitting 3000 stderr chars | stderr_tail len == 2000 (tail) | P1 | 2.3 |
| TC-FR02-08-P | P | `run --all` concurrent via ThreadPoolExecutor(max_workers) | N pending tasks; `run --all` | all processed; thread-safe store writes via shared Lock | P0 | 2.4 |
| TC-FR02-09-E | E | State machine only pending→running→terminal | run already-done id | no illegal re-transition (no-op / rejected) | P2 | 2.2 |
| TC-FR02-10-E | E | `run --all` timeout does NOT force exit 4 (single-only) | mixed batch w/ one timeout via `--all` | exit ≠ 4 (batch mode); task status `timeout` recorded | P1 | 2.5 |

---

## 3. FR-03 — Retry and circuit breaker

Global cross-task/cross-process breaker; injectable sleep (AC-3.1..3.5).

| Test ID | Class | Description | Input | Expected output | Prio | AC |
|---------|-------|-------------|-------|-----------------|------|----|
| TC-FR03-01-P | P | Retry up to RETRY_LIMIT on failed/timeout | failing cmd, `TASKQ_RETRY_LIMIT=3` | ≤3 retries then final failed | P0 | 3.1 |
| TC-FR03-02-P | P | Backoff = BACKOFF_BASE × 2^n via injected sleep | failing cmd, injected sleep spy | sleep args = base·2^1, base·2^2, ... | P0 | 3.1 |
| TC-FR03-03-B | B | Threshold reached → OPEN | THRESHOLD consecutive final-failures | breaker state OPEN | P0 | 3.2 |
| TC-FR03-04-B | B | One below threshold stays CLOSED | THRESHOLD-1 final-failures | breaker CLOSED | P1 | 3.2 |
| TC-FR03-05-N | N | OPEN refuses run → exit 3, no subprocess | breaker OPEN; `run <id>` | exit 3 + stderr `breaker open`; subprocess not spawned | P0 | 3.3 |
| TC-FR03-06-P | P | After cooldown → HALF_OPEN, one probe allowed; success → CLOSED+reset | OPEN + wait COOLDOWN; probe succeeds | state CLOSED, counter reset | P0 | 3.4 |
| TC-FR03-07-N | N | HALF_OPEN probe fails → back to OPEN | HALF_OPEN; probe fails | state OPEN again | P1 | 3.4 |
| TC-FR03-08-E | E | Breaker persisted atomically to breaker.json | trigger OPEN | breaker.json valid JSON w/ state; tmp+os.replace | P1 | 3.5 |
| TC-FR03-09-E | E | Retry-exhausted-but-still-failing increments consecutive count | repeated exhaustion | count increments toward threshold | P2 | 3.2 |

---

## 4. FR-04 — Result TTL cache

Signature `sha256(command)`; `--cached` replays recent `done` within TTL (AC-4.1..4.4).

| Test ID | Class | Description | Input | Expected output | Prio | AC |
|---------|-------|-------------|-------|-----------------|------|----|
| TC-FR04-01-P | P | Signature = sha256(command) | put/get for `echo hi` | key == sha256 hex of command | P0 | 4.1 |
| TC-FR04-02-P | P | Cache hit replays exit_code+stdout_tail, no subprocess, cached:true | prior done; `run <id> --cached` within TTL | replayed result; no subprocess; status done, `cached: true` | P0 | 4.2 |
| TC-FR04-03-N | N | Cache miss executes normally then writes cache | new signature `run <id> --cached` | subprocess runs; on done, cache.json updated | P0 | 4.3 |
| TC-FR04-04-B | B | Entry at exactly TTL boundary | result aged == TASKQ_CACHE_TTL | defined boundary behavior (expired → re-exec) asserted | P1 | 4.2 |
| TC-FR04-05-B | B | Entry just past TTL → miss/re-exec | aged TTL+1s; `--cached` | subprocess runs (expiry) | P0 | 4.2,4.3 |
| TC-FR04-06-N | N | Only `done` results are cached | failed/timeout result | not written to cache.json | P1 | 4.3 |
| TC-FR04-07-E | E | Cache read/write atomic + thread-safe under `run --all` | concurrent cached runs | cache.json valid JSON, no corruption | P1 | 4.4 |
| TC-FR04-08-E | E | Different command → different signature (no cross-hit) | `echo a` cached; `run` of `echo b --cached` | miss (distinct sha256) | P2 | 4.1,4.2 |

---

## 5. FR-05 — CLI integration

argparse subcommands; global `--json`; entry `python -m taskq` (AC-5.1..5.8).

| Test ID | Class | Description | Input | Expected output | Prio | AC |
|---------|-------|-------------|-------|-----------------|------|----|
| TC-FR05-01-P | P | Subcommands wired: submit/run/status/list/clear | each subcommand invoked | documented behaviour, exit 0 on valid | P0 | 5.1 |
| TC-FR05-02-P | P | `run` accepts `<id>`, `--cached`, `--all` combinations | `run <id>`, `run <id> --cached`, `run --all` | parsed per table | P0 | 5.2 |
| TC-FR05-03-P | P | Global `--json` → single-line JSON | `status <id> --json` | machine-readable single-line JSON | P1 | 5.3 |
| TC-FR05-04-P | P | `status <id>` full record; `list [--status S]`; `clear` empties HOME | respective invocations | correct listing/filter; clear empties $TASKQ_HOME | P0 | 5.5 |
| TC-FR05-05-N | N | Exit-code map — validation error | `submit ""` | exit 2 | P0 | 5.4 |
| TC-FR05-06-N | N | Exit-code map — breaker open | OPEN; `run <id>` | exit 3 | P0 | 5.4 |
| TC-FR05-07-N | N | Exit-code map — single-task timeout | `TASKQ_TASK_TIMEOUT=1` run `sleep 5` | exit 4 | P0 | 5.4 |
| TC-FR05-08-N | N | Exit-code map — internal error | forced internal error | exit 1 | P1 | 5.4 |
| TC-FR05-09-N | N | Unknown id at status/run/clear → exit 2 + verbatim stderr | `status deadbeef` | exit 2 + stderr `unknown task: deadbeef` | P0 | 5.6 |
| TC-FR05-10-N | N | Corrupt tasks.json at startup → exit 1, not rebuilt | non-parseable tasks.json; any cmd | exit 1 + stderr `store corrupted`; file untouched | P0 | 5.7 |
| TC-FR05-11-E | E | No bare `except:` / broad swallow (static + behavior) | grep AST scan of src | zero bare/broad swallow; unexpected → exit 1 | P1 | 5.8 |
| TC-FR05-12-B | B | `list --status` filter matches only given status | mixed statuses | only matching rows returned | P2 | 5.5 |

---

## 6. Non-Functional Requirements

### NFR-01 — Performance (perf/benchmark)
| Test ID | Class | Description | Input | Expected output | Prio | AC |
|---------|-------|-------------|-------|-----------------|------|----|
| TC-NFR01-01-P | P | submit+status p95 < 50ms / 100 iter | pytest-benchmark 100 iterations (no subprocess) | p95 < 50 ms | P0 | NFR01.1 |

### NFR-02 — Security: shell + injection blacklist
| Test ID | Class | Description | Input | Expected output | Prio | AC |
|---------|-------|-------------|-------|-----------------|------|----|
| TC-NFR02-01-N | N | Repo-wide grep `shell=True` → 0 in production code | source scan | zero matches | P0 | NFR02.1 |
| TC-NFR02-02-N | N | Injection char `;` rejected | `submit "echo a; rm x"` | exit 2 | P0 | NFR02.2,1.3 |
| TC-NFR02-03-N | N | Injection char `\|` rejected | `submit "echo a \| b"` | exit 2 | P0 | NFR02.2,1.3 |
| TC-NFR02-04-N | N | Injection char `&` rejected | `submit "a & b"` | exit 2 | P0 | NFR02.2,1.3 |
| TC-NFR02-05-N | N | Injection char `$` rejected | `submit "echo $X"` | exit 2 | P0 | NFR02.2,1.3 |
| TC-NFR02-06-N | N | Injection char `>` rejected | `submit "echo a > f"` | exit 2 | P0 | NFR02.2,1.3 |
| TC-NFR02-07-N | N | Injection char `<` rejected | `submit "cat < f"` | exit 2 | P0 | NFR02.2,1.3 |
| TC-NFR02-08-N | N | Injection char `` ` `` rejected | ``submit "echo `id`"`` | exit 2 | P0 | NFR02.2,1.3 |

### NFR-03 — Reliability: atomic write + breaker recovery
| Test ID | Class | Description | Input | Expected output | Prio | AC |
|---------|-------|-------------|-------|-----------------|------|----|
| TC-NFR03-01-P | P | 3 files written tmp+os.replace, parseable | write each file | each parseable JSON post-write | P0 | NFR03.1 |
| TC-NFR03-02-E | E | Mid-write crash → valid JSON OR fail-fast (no silent rewrite) | simulate kill during write | old file valid JSON, or explicit stderr+non-zero | P0 | NFR03.2 |
| TC-NFR03-03-B | B | Breaker OPEN→CLOSED recovery ≤ cooldown+1s | integration timing | recovery time within bound | P1 | NFR03.3 |

### NFR-04 — Security: secret redaction
| Test ID | Class | Description | Input | Expected output | Prio | AC |
|---------|-------|-------------|-------|-----------------|------|----|
| TC-NFR04-01-N | N | `sk-` token line redacted before persist | stdout line `sk-abcdefghijklmnop` | stored as `[REDACTED]` | P0 | NFR04.1 |
| TC-NFR04-02-N | N | `token=` line redacted before persist | stderr line `token=secretvalue` | stored as `[REDACTED]` | P0 | NFR04.2 |
| TC-NFR04-03-E | E | Non-secret line untouched | ordinary output | persisted verbatim | P2 | NFR04.1,4.2 |

### NFR-05 — Maintainability: docstring [FR-XX] tags
| Test ID | Class | Description | Input | Expected output | Prio | AC |
|---------|-------|-------------|-------|-----------------|------|----|
| TC-NFR05-01-P | P | Every public fn/class docstring has ≥1 `[FR-XX]` tag | AST scan of src/taskq | all public symbols tagged (`[NFR-XX]` alone insufficient) | P0 | NFR05.1 |

### NFR-06 — Deployability: env config
| Test ID | Class | Description | Input | Expected output | Prio | AC |
|---------|-------|-------------|-------|-----------------|------|----|
| TC-NFR06-01-P | P | config.py exposes 8 TASKQ_* readers w/ defaults | import config | all 8 present w/ documented defaults | P0 | NFR06.1 |
| TC-NFR06-02-P | P | .env.example declares all 8 vars w/ comment | parse .env.example | 8 vars each commented | P0 | NFR06.2 |

### NFR-07 — Resilience: fault injection
| Test ID | Class | Description | Input | Expected output | Prio | AC |
|---------|-------|-------------|-------|-----------------|------|----|
| TC-NFR07-01-E | E | corrupt-mid-write → restore or fail-fast | `--inject-fault=corrupt-mid-write` then startup | backup-restore OR explicit stderr+non-zero; no silent rewrite | P0 | NFR07.1 |
| TC-NFR07-02-E | E | oserror-on-write → recovery/fail-fast | `--inject-fault=oserror-on-write` | matches 07.1 behavior | P0 | NFR07.2 |
| TC-NFR07-03-E | E | disk-full → recovery/fail-fast | `--inject-fault=disk-full` | matches 07.1 behavior | P0 | NFR07.3 |
| TC-NFR07-04-E | E | kill-mid-write → recovery/fail-fast | `--inject-fault=kill-mid-write` | matches 07.1 behavior | P0 | NFR07.4 |
| TC-NFR07-05-N | N | `--inject-fault` rejected on production CLI | normal `submit --inject-fault=...` | flag rejected (not accepted in normal run) | P1 | NFR07.5 |

### NFR-08 — Concurrency: cross-process safety
| Test ID | Class | Description | Input | Expected output | Prio | AC |
|---------|-------|-------------|-------|-----------------|------|----|
| TC-NFR08-01-P | P | POSIX flock (write-excl/read-shared); Windows msvcrt.locking | lock acquisition | correct primitive per platform | P1 | NFR08.1 |
| TC-NFR08-02-N | N | NFS/network fs → flock disabled + WARNING | simulate network fs | flock off, WARNING emitted, atomic write retained | P1 | NFR08.2 |
| TC-NFR08-03-E | E | 4 concurrent processes → 3 valid JSON files, no corruption | 4-proc concurrent writes on same $TASKQ_HOME | all 3 files valid JSON, no loss | P0 | NFR08.3 |

### NFR-09 — Scalability
| Test ID | Class | Description | Input | Expected output | Prio | AC |
|---------|-------|-------------|-------|-----------------|------|----|
| TC-NFR09-01-P | P | 1000-task submit+status p95 < 100ms | pytest-benchmark 1000 tasks | p95 < 100 ms | P0 | NFR09.1 |
| TC-NFR09-02-E | E | run --all 100 tasks → valid JSON, no loss | run --all over 100 tasks | tasks.json valid, all 100 records present | P0 | NFR09.2 |
| TC-NFR09-03-B | B | Memory < 100MB peak via streaming iterator | 1000-task op mem probe | peak < 100 MB; no full in-memory load | P1 | NFR09.3 |

### NFR-10 — Evolvability: schema migration
| Test ID | Class | Description | Input | Expected output | Prio | AC |
|---------|-------|-------------|-------|-----------------|------|----|
| TC-NFR10-01-P | P | All 3 files root `version == 1` | read files | version field == 1 | P0 | NFR10.1 |
| TC-NFR10-02-P | P | version<1 → auto-migrate v1 + backup `<file>.v<n>.bak` | v0 file read | migrated & written back; backup preserved | P0 | NFR10.2 |
| TC-NFR10-03-N | N | version>1 → refuse + upgrade prompt, no in-place migrate | v2 file read | refusal + upgrade prompt | P0 | NFR10.3 |
| TC-NFR10-04-E | E | Migration failure → backup retained, exit 1 | force migration error | `.v<n>.bak` retained; exit 1 fail-fast | P1 | NFR10.4 |

---

## 7. Coverage Traceability

| Req | Manifest? | Test IDs | Classes covered |
|-----|-----------|----------|-----------------|
| FR-01 | ✅ | TC-FR01-01..10 (+NFR02 injection) | P/N/B/E |
| FR-02 | ✅ | TC-FR02-01..10 | P/N/B/E |
| FR-03 | ✅ | TC-FR03-01..09 | P/N/B/E |
| FR-04 | ✅ | TC-FR04-01..08 | P/N/B/E |
| FR-05 | ✅ | TC-FR05-01..12 | P/N/B/E |
| NFR-01 | ✅ | TC-NFR01-01 | P |
| NFR-02 | ✅ | TC-NFR02-01..08 | N |
| NFR-03 | ✅ | TC-NFR03-01..03 | P/B/E |
| NFR-04 | ✅ | TC-NFR04-01..03 | N/E |
| NFR-05 | ✅ | TC-NFR05-01 | P |
| NFR-06 | ✅ | TC-NFR06-01..02 | P |
| NFR-07 | ✅ | TC-NFR07-01..05 | N/E |
| NFR-08 | ✅ | TC-NFR08-01..03 | P/N/E |
| NFR-09 | ✅ | TC-NFR09-01..03 | P/B/E |
| NFR-10 | ✅ | TC-NFR10-01..04 | P/N/E |

All 5 FRs from `quality_manifest.json` (`fr_ids`) covered; all 10 NFRs covered.
