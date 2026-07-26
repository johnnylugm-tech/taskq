# taskq Phase 4 Test Plan

## 1. Scope and sources

This plan verifies all functional requirements in `.methodology/quality_manifest.json` (`FR-01` through `FR-05`) and all non-functional requirements in `01-requirements/SRS.md` (`NFR-01` through `NFR-10`). Acceptance-criterion references below use the identifiers in the SRS.

Out of scope for this plan: implementation changes, TDD execution, gate execution, bug hunts, and phase advancement.

## 2. Test approach

- **Executable:** `/Users/johnny/projects/taskq/.venv/bin/python`
- **CLI surface:** `<python> -m taskq`
- **Isolation:** Give every test a fresh temporary `TASKQ_HOME`; never share state unless the case explicitly tests threads or processes.
- **Determinism:** Inject clocks and sleep functions for retry, breaker, and TTL tests. Use real elapsed time only for the NFR latency/recovery acceptance tests.
- **Persistence checks:** Parse every affected JSON file after operations and verify both its schema and retained records. For failure cases, compare file bytes/state before and after to detect forbidden writes or silent rebuilds.
- **Subprocess checks:** Spy on or monkeypatch `subprocess.run` for unit/integration cases; use real child processes for CLI, timeout, and cross-process cases.
- **Fault injection:** Trigger NFR-07 scenarios through test monkeypatching (or an explicitly test-gated interface if one exists). The production CLI must reject `--inject-fault` as required by AC-NFR07.5.
- **Performance:** Warm up before measurement, use the same machine/run for comparisons, report raw samples and p95, and exclude task subprocess execution as specified.

### Categories

| Category | Meaning |
|---|---|
| Positive | Valid input and normal successful behavior |
| Negative | Invalid input, denied operation, or explicit failure behavior |
| Boundary | Exact limits, thresholds, and transition instants |
| Edge | Concurrency, persistence, unusual state, or cross-feature interaction |

### Priorities

| Priority | Meaning |
|---|---|
| P0 | Release-blocking safety, security, persistence, or primary CLI behavior |
| P1 | Required behavior with substantial functional or operational impact |
| P2 | Lower-risk compatibility, audit, or diagnostic behavior |

## 3. Functional test cases

### FR-01 — Task submission and validation

| Test case ID | Category | AC | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|---|
| TC-FR01-POS-001 | Positive | AC-1.1, AC-1.5 | Submit a valid named command and persist a new pending task. | Fresh home; run `submit "echo hi" --name greeting`. | Exit `0`; stdout is an 8-character lowercase hexadecimal task ID derived from UUID4; `tasks.json` contains that ID with `status=pending`, exact command/name, and a populated `created_at`; the write is atomic. | P0 |
| TC-FR01-NEG-001 | Negative | AC-1.1 | Reject empty and whitespace-only commands without touching storage. | Parameterize command as `""`, `" "`, and `"\t\n"`; snapshot home first. | Exit `2`; explanatory stderr; no task is added and no data file is created or changed. | P0 |
| TC-FR01-NEG-002 | Negative | AC-1.3 | Reject every prohibited injection character. | Parameterize `echo x{char}y` over `;`, `|`, `&`, `$`, `>`, `<`, and backtick. | Every invocation exits `2` with stderr; stdout has no ID; storage remains unchanged. | P0 |
| TC-FR01-BND-001 | Boundary | AC-1.2 | Verify the inclusive 1000-character command limit. | Submit a safe command string of exactly 1000 characters, then one of 1001 characters. | Length 1000 succeeds and is stored exactly; length 1001 exits `2` and causes no write. | P0 |
| TC-FR01-EDGE-001 | Edge | AC-1.4 | Enforce name uniqueness only for active tasks. | Seed tasks sharing candidate name `same` in turn as `pending`, `running`, `done`, `failed`, and `timeout`; submit a new task with `--name same`. | Existing `pending` or `running` causes exit `2` and no write; each terminal state permits the new submission. | P1 |
| TC-FR01-EDGE-002 | Edge | AC-1.6 | Emit the documented machine-readable submission response. | Run global `--json` with a valid submit in a fresh home. | Exit `0`; stdout is exactly one parseable JSON line with keys `id` and `status`; `id` matches the stored task and `status` is `pending`; no human text contaminates stdout. | P1 |

### FR-02 — Task executor

| Test case ID | Category | AC | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|---|
| TC-FR02-POS-001 | Positive | AC-2.1, AC-2.2, AC-2.3 | Execute a pending task that exits successfully. | Submit a safe Python command that writes to stdout and exits `0`; run its ID. | Observed transition is `pending → running → done`; execution uses tokenized arguments without a shell; exit `0`; result stores `exit_code=0`, output tails, non-negative `duration_ms`, and `finished_at`. | P0 |
| TC-FR02-NEG-001 | Negative | AC-2.2, AC-2.3 | Record a non-zero child exit as a final failed result. | Pending command writes stderr and exits `7`; retries disabled for this case. | Task transitions `pending → running → failed`; stored `exit_code=7`, stderr tail, duration, and finish time are correct; CLI reports the failure without corrupting state. | P0 |
| TC-FR02-BND-001 | Boundary | AC-2.3 | Retain exactly the final 2000 characters of each output stream. | Child emits distinguishable stdout/stderr payloads of lengths `1999`, `2000`, and `2001`. | For 1999/2000, complete content is stored; for 2001, exactly the last 2000 characters are stored independently for each stream. | P1 |
| TC-FR02-BND-002 | Boundary | AC-2.2, AC-2.5 | Enforce the configured single-task timeout. | Set `TASKQ_TASK_TIMEOUT` to a short positive value; run a command that exceeds it. | Task ends as `timeout`; single-task CLI exits `4`; timing/result fields are persisted and no child remains running. | P0 |
| TC-FR02-EDGE-001 | Edge | AC-2.4 | Run all and process only pending tasks at bounded concurrency. | Set `TASKQ_MAX_WORKERS=2`; seed at least five pending tasks plus terminal/running tasks; invoke `run --all` with concurrency instrumentation. | Every initially pending task executes once, non-pending tasks do not execute, active workers never exceed `2`, and all state writes remain valid with no lost task. | P0 |
| TC-FR02-EDGE-002 | Edge | AC-2.1, AC-2.4 | Preserve argument boundaries for commands containing quoted spaces during concurrent execution. | Submit safe commands whose quoted arguments contain spaces; execute via `run --all`. | Each child receives the expected `shlex.split` argument vector; no shell expansion occurs; stored outputs map to the correct task IDs. | P1 |

### FR-03 — Retry and circuit breaker

| Test case ID | Category | AC | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|---|
| TC-FR03-POS-001 | Positive | AC-3.1 | Retry transient failure and stop after eventual success. | `TASKQ_RETRY_LIMIT=2`, `TASKQ_BACKOFF_BASE=0.1`; injected executor fails twice then succeeds; injected sleeper records calls. | Three total attempts occur; waits are `0.1` then `0.2` seconds (`base × 2^n`, n starting at 0); final task is `done`; no further retry occurs. | P0 |
| TC-FR03-NEG-001 | Negative | AC-3.3 | Refuse all execution while the breaker is open. | Persist a non-expired `OPEN` breaker; spy on subprocess; invoke `run <id>`. | Immediate exit `3`; stderr contains verbatim `breaker open`; subprocess call count is zero; task remains unexecuted. | P0 |
| TC-FR03-BND-001 | Boundary | AC-3.1, AC-3.2 | Verify exact retry and final-failure thresholds. | Set retry limit `2` and breaker threshold `3`; make every attempt fail across three tasks, then request a fourth run. | Each failing task has one initial attempt plus exactly two retries; breaker remains closed after final failures 1 and 2, opens on failure 3, and refuses task 4 with exit `3`. | P0 |
| TC-FR03-BND-002 | Boundary | AC-3.4 | Test the cooldown transition immediately before and at expiry. | Fake clock at `opened_at + cooldown - ε`, then at `opened_at + cooldown`. | Before expiry, run is refused with exit `3`; at expiry, state becomes `HALF_OPEN` and exactly one probe is admitted. | P0 |
| TC-FR03-EDGE-001 | Edge | AC-3.4 | Resolve both HALF_OPEN probe outcomes and admit only one concurrent probe. | At cooldown expiry issue two simultaneous runs; parameterize admitted probe as success or final failure. | Only one probe executes. Success closes breaker and resets count to zero; failure reopens it with a refreshed open time; the competing run is refused. | P0 |
| TC-FR03-EDGE-002 | Edge | AC-3.5 | Persist breaker state across process boundaries. | Process A opens the breaker and exits; process B uses the same home before cooldown. | `breaker.json` is valid atomic JSON; process B reads the persisted state, exits `3`, prints `breaker open`, and spawns no task process. | P0 |

### FR-04 — Result TTL cache

| Test case ID | Category | AC | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|---|
| TC-FR04-POS-001 | Positive | AC-4.1, AC-4.2 | Replay the newest valid done result for the same command. | Seed a successful cached result under `sha256(command)` with age less than TTL; submit the same command under a new ID; run with `--cached`; spy on subprocess. | No subprocess is called; task becomes `done` with `cached=true`; cached `exit_code` and `stdout_tail` are replayed; signature equals the hexadecimal SHA-256 of the exact command. | P0 |
| TC-FR04-NEG-001 | Negative | AC-4.3 | Execute normally when no eligible cache entry exists. | Parameterize absent entry, different-command signature, cached non-`done` result, and invocation without `--cached`. | Subprocess executes normally; successful result is stored under the correct signature in `cache.json`; result is not falsely marked cached. | P0 |
| TC-FR04-BND-001 | Boundary | AC-4.2, AC-4.3 | Verify the precise TTL boundary. | With fake time, set cache age to `TTL - ε`, exactly `TTL`, and `TTL + ε`. | `TTL - ε` replays; exactly TTL and older are expired because they are not within TTL, so normal execution occurs and refreshes cache on success. | P0 |
| TC-FR04-EDGE-001 | Edge | AC-4.1, AC-4.2 | Select the most recent done result without normalizing the command. | Seed multiple done results for the exact command and a visually similar command with different whitespace. | Exact command hashes independently from the whitespace variant; the newest eligible exact-signature result is replayed. | P1 |
| TC-FR04-EDGE-002 | Edge | AC-4.4 | Keep cache valid under concurrent hits and misses. | Use `run --all` with tasks causing simultaneous reads and successful writes to shared cache. | Every task receives its own correct result; `cache.json` remains parseable and complete; no entry is lost or partially written. | P0 |

### FR-05 — CLI integration

| Test case ID | Category | AC | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|---|
| TC-FR05-POS-001 | Positive | AC-5.1, AC-5.2 | Verify the module entry point and documented argparse command matrix. | Invoke `<python> -m taskq` for `submit`, `run <id>`, `run <id> --cached`, `run --all`, `status <id>`, `list`, `list --status pending`, and `clear`. | Each form parses and dispatches to its documented behavior; valid operations exit `0`; `run` options act on the intended task set. | P0 |
| TC-FR05-POS-002 | Positive | AC-5.5 | Verify status, filtered list, and full clear behavior end to end. | Seed tasks in multiple statuses and all three data files; query one task, list with/without filters, then run `clear`. | Status returns the complete matching record; list returns all or exactly the selected status; clear empties `$TASKQ_HOME` data while leaving the tool usable. | P0 |
| TC-FR05-NEG-001 | Negative | AC-5.4, AC-5.6 | Return the canonical unknown-ID error wherever an ID is accepted. | Invoke `status <missing>`, `run <missing>`, and the AC-5.6 clear form with `<missing>` against a valid store. | Each exits `2`; stderr contains verbatim `unknown task: <id>`; no data changes and no subprocess starts. | P0 |
| TC-FR05-NEG-002 | Negative | AC-5.2, AC-5.4 | Reject malformed CLI combinations. | Invoke no subcommand, unknown subcommand, `run` with neither ID nor `--all`, and `run <id> --all`. | argparse rejects each input with exit `2` and useful stderr; no store mutation occurs. | P1 |
| TC-FR05-BND-001 | Boundary | AC-5.3 | Apply global JSON mode consistently at the command boundary. | Run each successful subcommand with global `--json`, including empty and non-empty list results. | Each stdout response is one parseable JSON line with no human prefix/suffix; represented records preserve their full typed values. | P1 |
| TC-FR05-BND-002 | Boundary | AC-5.4 | Exercise every canonical exit-code class. | Parameterize success, validation/unknown ID, open breaker, single-task timeout, and injected internal error. | CLI exits exactly `0`, `2`, `3`, `4`, and `1`, respectively; no other code is substituted. | P0 |
| TC-FR05-EDGE-001 | Edge | AC-5.7 | Fail fast on a corrupted task store without rebuilding it. | Write non-JSON bytes to `tasks.json`, snapshot them, then invoke a CLI command that loads the store. | Exit `1`; stderr contains `store corrupted`; original bytes remain unchanged; no fresh empty store is written. | P0 |
| TC-FR05-EDGE-002 | Edge | AC-5.8 | Surface unexpected internal errors and prohibit broad exception swallowing. | Inject a representative unexpected I/O error at the CLI boundary; statically inspect exception handlers in production code. | CLI exits `1` with an error diagnostic; no bare `except:` or broad `except Exception:` silently consumes the error. | P0 |

## 4. Non-functional test cases

### NFR-01 — Performance

| Test case ID | Category | AC | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|---|
| TC-NFR01-POS-001 | Positive | AC-NFR01.1 | Measure valid submit-plus-status latency over 100 iterations. | Warm environment; 100 isolated valid `submit` + matching `status` operations; no task execution. | Reported p95 is strictly `< 50 ms`; all operations are correct and no samples are discarded without disclosure. | P1 |
| TC-NFR01-NEG-001 | Negative | AC-NFR01.1 | Confirm the performance gate rejects a non-compliant sample set. | Feed the metric assertion a controlled sample set whose p95 is `> 50 ms`. | The test fails with measured p95 and threshold in the diagnostic rather than passing by rounding. | P2 |
| TC-NFR01-BND-001 | Boundary | AC-NFR01.1 | Enforce the strict threshold comparison. | Calibrate metric-assertion samples to p95 immediately below and exactly at `50 ms`. | Below 50 passes; exactly 50 fails. | P2 |
| TC-NFR01-EDGE-001 | Edge | AC-NFR01.1 | Measure the valid maximum-length submission path. | Repeat the 100-iteration measurement using safe 1000-character commands and immediate status reads. | Correctness is retained and p95 remains `< 50 ms`. | P1 |

### NFR-02 — Security

| Test case ID | Category | AC | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|---|
| TC-NFR02-POS-001 | Positive | AC-NFR02.1 | Verify safe commands execute without a shell. | Submit/run a command with quoted arguments; inspect subprocess invocation and production Python AST/source. | Correct argv reaches the child; no production call passes `shell=True`; repository production scan has zero matches. | P0 |
| TC-NFR02-NEG-001 | Negative | AC-NFR02.2 | Cover every blacklist character individually. | Seven parameterized submissions containing `;`, `|`, `&`, `$`, `>`, `<`, or backtick. | All seven exit `2`, emit stderr, and cause zero writes/executions. | P0 |
| TC-NFR02-BND-001 | Boundary | AC-NFR02.2 | Reject a prohibited character at every command boundary. | For each of the seven characters, place it first, last, and between otherwise safe characters. | Every placement is rejected with exit `2`; surrounding whitespace does not hide it. | P0 |
| TC-NFR02-EDGE-001 | Edge | AC-NFR02.1 | Detect syntactic variations of a forbidden shell argument. | Static scan covers multiline calls and whitespace variants, not only the literal text `shell=True`. | Any fixture containing a truthy `shell` keyword is flagged; production contains none. | P1 |

### NFR-03 — Reliability and atomicity

| Test case ID | Category | AC | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|---|
| TC-NFR03-POS-001 | Positive | AC-NFR03.1 | Verify atomic writes for all three data files. | Trigger writes to `tasks.json`, `breaker.json`, and `cache.json` while spying on filesystem operations. | Each write uses a temp file followed by `os.replace`; each completed target parses as JSON and retains expected records. | P0 |
| TC-NFR03-NEG-001 | Negative | AC-NFR03.2 | Interrupt each file type during a write. | Parameterize the three files and terminate/fail between temp-file write and replace. | On startup, the previous target remains valid, recovery restores a valid backup, or the CLI fails fast with explicit stderr/non-zero exit; it never silently rebuilds. | P0 |
| TC-NFR03-BND-001 | Boundary | AC-NFR03.3 | Measure maximum breaker recovery time. | Open breaker, use a successful half-open probe, and measure from `opened_at` through close. | `OPEN → CLOSED` completes no later than `TASKQ_BREAKER_COOLDOWN + 1 s`; measured duration is reported. | P0 |
| TC-NFR03-EDGE-001 | Edge | AC-NFR03.1, AC-NFR03.2 | Start with an orphan temp file beside a valid target. | Leave a partial temp file from an interrupted write and restart using the same home. | Canonical target remains valid and authoritative (or startup fails explicitly); no partial temp content silently replaces it. | P1 |

### NFR-04 — Secret redaction

| Test case ID | Category | AC | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|---|
| TC-NFR04-POS-001 | Positive | AC-NFR04.1, AC-NFR04.2 | Redact both secret patterns from both persisted streams. | Parameterize stdout/stderr lines containing `sk-abcdefghijklmnop` and `token=secret-value`. | Every matching line is stored exactly as `[REDACTED]`; the raw secret appears nowhere in `tasks.json` or `cache.json`. | P0 |
| TC-NFR04-NEG-001 | Negative | AC-NFR04.1, AC-NFR04.2 | Preserve non-matching near-secret lines. | Emit `sk-1234567`, `token=`, and ordinary text. | Non-matching lines remain unchanged; redaction does not erase benign output. | P1 |
| TC-NFR04-BND-001 | Boundary | AC-NFR04.1 | Enforce the minimum `sk-` token length. | Emit otherwise valid tokens with suffix lengths 7 and 8, including allowed `_` and `-`. | Length 7 is retained; length 8 is redacted wholesale. | P0 |
| TC-NFR04-EDGE-001 | Edge | AC-NFR04.1, AC-NFR04.2 | Redact the whole matching line in multiline and mixed-content output. | Emit benign line, line with prefix/suffix around a secret, then another benign line; include both patterns across stdout/stderr. | Only matching lines become exactly `[REDACTED]`; neighboring lines and line order remain intact; truncation cannot expose a matched secret. | P0 |

### NFR-05 — Maintainability

| Test case ID | Category | AC | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|---|
| TC-NFR05-POS-001 | Positive | AC-NFR05.1 | Audit every public function and class for an FR tag. | Parse all Python modules under `src/taskq`; enumerate public functions/classes and inspect docstrings. | Every enumerated symbol has a non-empty docstring containing at least one `[FR-XX]` tag. | P1 |
| TC-NFR05-NEG-001 | Negative | AC-NFR05.1 | Ensure an NFR-only or missing tag does not satisfy the audit. | Run the audit against fixtures with no docstring, an untagged docstring, and `[NFR-01]` only. | Each fixture is reported as non-compliant with its symbol and file location. | P2 |
| TC-NFR05-BND-001 | Boundary | AC-NFR05.1 | Distinguish public from private symbols. | Audit fixtures containing `public_fn`/`PublicClass` and `_private_fn`/`_PrivateClass`. | Public symbols are required and checked; private symbols are excluded from this specific requirement. | P2 |
| TC-NFR05-EDGE-001 | Edge | AC-NFR05.1 | Include public methods and exported symbols consistently. | Enumerate public module functions/classes and public methods exposed by those classes. | No publicly exposed callable/class is skipped due to decorators, imports, or class nesting rules used by the project. | P2 |

### NFR-06 — Deployability

| Test case ID | Category | AC | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|---|
| TC-NFR06-POS-001 | Positive | AC-NFR06.1 | Verify all documented default configuration values. | Clear all `TASKQ_*` variables and load config. | Values are `TASKQ_HOME=.taskq`, `TASKQ_MAX_WORKERS=4`, `TASKQ_TASK_TIMEOUT=10.0`, `TASKQ_RETRY_LIMIT=2`, `TASKQ_BACKOFF_BASE=0.1`, `TASKQ_BREAKER_THRESHOLD=3`, `TASKQ_BREAKER_COOLDOWN=5.0`, and `TASKQ_CACHE_TTL=3600`. | P1 |
| TC-NFR06-NEG-001 | Negative | AC-NFR06.2 | Make omissions in the environment template detectable. | Run the inventory validator against fixtures missing one variable or its comment. | The validator fails and names every missing declaration/comment; the real `.env.example` has no omissions. | P1 |
| TC-NFR06-BND-001 | Boundary | AC-NFR06.1, AC-NFR06.2 | Enforce the exact eight-variable inventory. | Compare `config.py`, `.env.example`, and the documented eight-name set. | All eight names appear in both locations with defaults/comments; no documented name is duplicated or missing. | P1 |
| TC-NFR06-EDGE-001 | Edge | AC-NFR06.1 | Verify each environment override is read through centralized config. | Set all eight variables to distinguishable valid non-default values, reload config in an isolated process, then clear them. | Loaded values match overrides with correct types; a fresh process after clearing returns to defaults without stale values. | P1 |

### NFR-07 — Fault-injection resilience

| Test case ID | Category | AC | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|---|
| TC-NFR07-POS-001 | Positive | AC-NFR07.1–AC-NFR07.4 | Recover safely from every specified injected write fault when a valid backup is available. | For each of three data files, parameterize `corrupt-mid-write`, `oserror-on-write`, `disk-full`, and `kill-mid-write` through test monkeypatch/test-only activation. | Next startup detects the fault and restores a parseable, semantically valid backup; recovery is explicit and no records are silently fabricated. | P0 |
| TC-NFR07-NEG-001 | Negative | AC-NFR07.1–AC-NFR07.4 | Fail fast when recovery is impossible. | Repeat every fault with no usable backup; snapshot damaged/original state. | Explicit stderr identifies failure, exit is non-zero, and no empty/default file silently replaces the data. | P0 |
| TC-NFR07-BND-001 | Boundary | AC-NFR07.1–AC-NFR07.4 | Inject immediately before and after atomic replacement. | Place the fault at the last write step before `os.replace` and immediately after successful replace for each file. | Before replace, old target stays valid or startup fails explicitly; after replace, new target is complete and valid; no partial target is accepted. | P0 |
| TC-NFR07-EDGE-001 | Edge | AC-NFR07.5 | Keep fault activation out of production CLI paths. | Invoke normal `<python> -m taskq ... --inject-fault=<scenario>` for every scenario without a test gate. | argparse rejects the flag with exit `2`; no fault executes and existing data remains unchanged. | P0 |

### NFR-08 — Cross-process safety

| Test case ID | Category | AC | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|---|
| TC-NFR08-POS-001 | Positive | AC-NFR08.1 | Use platform-appropriate shared and exclusive file locks. | Instrument reads/writes on POSIX; exercise a mocked/Windows-specific path for `msvcrt.locking`. | Reads request shared locks; writes request exclusive locks; POSIX uses `fcntl.flock` and Windows uses `msvcrt.locking`. | P0 |
| TC-NFR08-NEG-001 | Negative | AC-NFR08.2 | Degrade explicitly on a detected network filesystem. | Simulate NFS/network-FS detection and perform reads/writes. | Flock is skipped, a `WARNING` is emitted, and atomic temp-plus-replace writes remain active; degradation is not silent. | P0 |
| TC-NFR08-BND-001 | Boundary | AC-NFR08.3 | Run the required four-process concurrent-write test. | Start exactly four independent CLI processes against one home, coordinating simultaneous task, breaker, and cache updates. | All processes finish without lost acknowledged writes; all three files parse as JSON and satisfy their schemas. | P0 |
| TC-NFR08-EDGE-001 | Edge | AC-NFR08.1–AC-NFR08.3 | Interleave readers, submitters, runners, and cache writers across processes. | Four processes repeatedly mix `submit`, `status`, and run/cache operations against one home. | No process observes partial JSON; no task/cache/breaker record is cross-assigned or silently lost; final files remain valid. | P0 |

### NFR-09 — Scalability

| Test case ID | Category | AC | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|---|
| TC-NFR09-POS-001 | Positive | AC-NFR09.1 | Measure submit-plus-status at 1000-task scale. | Prepopulate/submit to 1000 tasks, then measure valid combined operations without subprocess execution. | Reported p95 is strictly `< 100 ms`; queried records are correct. | P1 |
| TC-NFR09-NEG-001 | Negative | AC-NFR09.2 | Preserve records when the 100-task batch contains mixed child outcomes. | Run `--all` on 100 pending tasks whose deterministic outcomes include done, failed, and timeout. | `tasks.json` remains valid and contains all original 100 IDs with their correct final states; failures do not drop records. | P0 |
| TC-NFR09-BND-001 | Boundary | AC-NFR09.1, AC-NFR09.3 | Enforce strict latency and memory ceilings at the required scale. | Measure 1000-task workload p95 and peak resident/traced memory with calibrated threshold assertions. | p95 must be `< 100 ms` and peak memory must be `< 100 MB`; equality at either limit fails rather than passing by rounding. | P1 |
| TC-NFR09-EDGE-001 | Edge | AC-NFR09.2, AC-NFR09.3 | Execute exactly 100 tasks concurrently without full-load behavior or loss. | Queue 100 uniquely identifiable tasks; run `--all`; monitor peak memory and parse final file. | All 100 IDs remain, output maps to the right IDs, JSON is valid, and peak memory stays below 100 MB using streaming iteration. | P0 |

### NFR-10 — Schema evolution

| Test case ID | Category | AC | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|---|
| TC-NFR10-POS-001 | Positive | AC-NFR10.1 | Persist current schema version on every root object. | Create/use tasks, breaker, and cache data through public operations. | Each of `tasks.json`, `breaker.json`, and `cache.json` has root `version: 1` and remains readable. | P0 |
| TC-NFR10-NEG-001 | Negative | AC-NFR10.3 | Reject a future schema version without modifying it. | Parameterize each file with valid JSON carrying `version: 2`; snapshot bytes; start the CLI. | Read is refused; stderr prompts use of an upgrade tool; process exits non-zero/`1`; bytes remain unchanged and no in-place migration occurs. | P0 |
| TC-NFR10-BND-001 | Boundary | AC-NFR10.2 | Migrate the immediately previous version to v1 with a backup. | Parameterize each valid v0 data file; start an operation that reads it. | File is migrated and written back with `version: 1`; original is retained exactly as `<file>.v0.bak`; records are preserved. | P0 |
| TC-NFR10-EDGE-001 | Edge | AC-NFR10.4 | Retain the backup and fail fast if migration cannot complete. | Force migration/write failure after the v0 backup is created for each file. | Process exits `1`; explicit stderr is emitted; `<file>.v0.bak` remains byte-for-byte intact; no partial v1 file is accepted. | P0 |

## 5. Manifest quality and architecture checks

| Test case ID | Source | Description | Input / precondition | Expected output / state | Priority |
|---|---|---|---|---|---|
| TC-QUAL-001 | `quality_targets.min_coverage` | Measure automated-test coverage. | Run the approved coverage collector over the complete test suite. | Coverage is at least `80%`; report uncovered production lines. | P1 |
| TC-QUAL-002 | `quality_targets.max_complexity` | Audit production-code complexity. | Analyze all production functions using the approved complexity metric. | No function exceeds complexity `15`; violations identify file and symbol. | P2 |
| TC-QUAL-003 | `quality_targets.max_coupling` | Audit module coupling. | Analyze dependencies among production modules. | Coupling is at most `0.3`; report the measured value and offending edges if exceeded. | P2 |
| TC-ARCH-001 | `architecture_constraints` | Detect circular dependencies. | Build/import-analyze the `taskq` module dependency graph. | No circular dependency is present. | P1 |

## 6. Requirement coverage matrix

| Requirement | Covered test cases | Status |
|---|---|---|
| FR-01 | TC-FR01-POS-001, TC-FR01-NEG-001, TC-FR01-NEG-002, TC-FR01-BND-001, TC-FR01-EDGE-001, TC-FR01-EDGE-002 | Planned |
| FR-02 | TC-FR02-POS-001, TC-FR02-NEG-001, TC-FR02-BND-001, TC-FR02-BND-002, TC-FR02-EDGE-001, TC-FR02-EDGE-002 | Planned |
| FR-03 | TC-FR03-POS-001, TC-FR03-NEG-001, TC-FR03-BND-001, TC-FR03-BND-002, TC-FR03-EDGE-001, TC-FR03-EDGE-002 | Planned |
| FR-04 | TC-FR04-POS-001, TC-FR04-NEG-001, TC-FR04-BND-001, TC-FR04-EDGE-001, TC-FR04-EDGE-002 | Planned |
| FR-05 | TC-FR05-POS-001, TC-FR05-POS-002, TC-FR05-NEG-001, TC-FR05-NEG-002, TC-FR05-BND-001, TC-FR05-BND-002, TC-FR05-EDGE-001, TC-FR05-EDGE-002 | Planned |
| NFR-01 | TC-NFR01-POS-001, TC-NFR01-NEG-001, TC-NFR01-BND-001, TC-NFR01-EDGE-001 | Planned |
| NFR-02 | TC-NFR02-POS-001, TC-NFR02-NEG-001, TC-NFR02-BND-001, TC-NFR02-EDGE-001 | Planned |
| NFR-03 | TC-NFR03-POS-001, TC-NFR03-NEG-001, TC-NFR03-BND-001, TC-NFR03-EDGE-001 | Planned |
| NFR-04 | TC-NFR04-POS-001, TC-NFR04-NEG-001, TC-NFR04-BND-001, TC-NFR04-EDGE-001 | Planned |
| NFR-05 | TC-NFR05-POS-001, TC-NFR05-NEG-001, TC-NFR05-BND-001, TC-NFR05-EDGE-001 | Planned |
| NFR-06 | TC-NFR06-POS-001, TC-NFR06-NEG-001, TC-NFR06-BND-001, TC-NFR06-EDGE-001 | Planned |
| NFR-07 | TC-NFR07-POS-001, TC-NFR07-NEG-001, TC-NFR07-BND-001, TC-NFR07-EDGE-001 | Planned |
| NFR-08 | TC-NFR08-POS-001, TC-NFR08-NEG-001, TC-NFR08-BND-001, TC-NFR08-EDGE-001 | Planned |
| NFR-09 | TC-NFR09-POS-001, TC-NFR09-NEG-001, TC-NFR09-BND-001, TC-NFR09-EDGE-001 | Planned |
| NFR-10 | TC-NFR10-POS-001, TC-NFR10-NEG-001, TC-NFR10-BND-001, TC-NFR10-EDGE-001 | Planned |

## 7. Entry and exit criteria

### Entry criteria

- Python 3.11 virtual environment and test dependencies are available.
- The CLI is importable through `python -m taskq`.
- Each test can create an isolated writable `TASKQ_HOME`.
- Timing-sensitive cases have a documented machine/load baseline.

### Exit criteria

- Every manifest FR (`FR-01` through `FR-05`) has executed positive, negative, boundary, and edge coverage.
- Every SRS NFR (`NFR-01` through `NFR-10`) has an executed case and traceable evidence.
- All P0 cases pass; any P1/P2 failure is recorded with requirement impact and disposition.
- All three persisted files remain parseable and schema-valid after relevant normal, concurrent, and fault cases.
- Performance, coverage, complexity, coupling, and architecture thresholds are reported without rounding a failing boundary into a pass.

## 8. TC quick index

The detailed test tables above use composite IDs of the form `TC-FR##-<CAT>-<NNN>` (e.g. `TC-FR01-POS-001`). The numeric-only handles below are kept for harness-side tooling that scans for `TC-\d+` patterns:

- TC-1 — FR-01 positive path (valid submit + atomic persistence)
- TC-2 — FR-02 executor happy path (pending → done, exit_code=0)
- TC-3 — FR-03 retry transient failure (eventual success, base × 2^n backoff)
