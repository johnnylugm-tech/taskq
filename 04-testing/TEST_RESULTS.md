# Test Results — Phase 4 (Testing)

**Run date:** 2026-07-26  
**Python:** `/Users/johnny/projects/taskq/.venv/bin/python` (CPython 3.11.15)  
**Raw output:** `/Users/johnny/projects/taskq/04-testing/coverage_raw.txt`

## Test execution

The complete pytest discovery run was executed from the repository root with coverage collection enabled for `03-development/src`:

```text
/Users/johnny/projects/taskq/.venv/bin/python -m pytest --cov=03-development/src --cov-report=term-missing -q | tee /Users/johnny/projects/taskq/04-testing/coverage_raw.txt
```

| Metric | Result |
|---|---:|
| Test cases run | 6,176 |
| Passed | 6,175 |
| Failed | 1 |
| Errors | 0 |
| Skipped | 0 |
| Warnings | 5 |
| Duration | 109.60 s (1 m 49 s) |

**Suite result: FAIL** because one test failed. The coverage collection completed and reported the source tree at 100% line coverage; coverage status is reported separately in `COVERAGE_REPORT.md`.

## Failure requiring follow-up

```text
harness/tests/test_workflowgen_golden.py::test_generated_output_matches_golden[4]
```

The phase-4 workflow generator output differs from the checked-in golden file. Pytest reports an assertion failure in `harness/tests/test_workflowgen_golden.py:38`; the diff includes additional permitted HUNT-RESOLVE instructions in the generated output. This is a harness golden-fixture synchronization issue, not a `taskq` source test failure.

**Disposition:** Deferred. Updating or regenerating the harness golden fixture requires a separate authorized harness change and was intentionally not performed in this deliverable.

## Warnings

Pytest emitted five warnings, all from harness self-tests: an invalid constitution fixture, an unknown constitution check type, a malformed coverage configuration fixture, a policy-disable deprecation, and the deprecated `stage_pass_generator` import. No warning originated from `03-development/src`.

## Scope and deferred issues

- The run used pytest's full repository discovery, so the 6,176 cases include the project tests and harness self-tests discovered by pytest.
- No test was skipped or errored in the reported run.
- One harness golden-output issue remains deferred as documented above.
- No additional deferred `taskq` coverage issue was identified by the live term-missing report; all source modules had zero missed statements.
