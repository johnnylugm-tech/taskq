# Coverage Report — Phase 4 (Testing)

**Run date:** 2026-07-26  
**Python:** `/Users/johnny/projects/taskq/.venv/bin/python` (CPython 3.11.15)  
**Coverage scope:** `03-development/src`

## Commands and evidence

Coverage was collected by the required live pytest command:

```text
/Users/johnny/projects/taskq/.venv/bin/python -m pytest --cov=03-development/src --cov-report=term-missing -q | tee /Users/johnny/projects/taskq/04-testing/coverage_raw.txt
```

The raw terminal output is preserved at [`coverage_raw.txt`](coverage_raw.txt). The independent total check was then run with:

```text
/Users/johnny/projects/taskq/.venv/bin/python -m coverage report --format=total
```

Its output was `100`.

## Overall coverage

**Line coverage: 100%**

| Metric | Value |
|---|---:|
| Statements | 467 |
| Missed statements | 0 |
| Total coverage | 100% |
| Gate 3 minimum | 80% |

The measured coverage is 20 percentage points above the Gate 3 minimum. The live pytest run itself had one unrelated harness golden-output failure (6,175 passed and 1 failed); that failure did not produce any missed lines in the requested `03-development/src` coverage scope.

## Per-module breakdown

| Module | Statements | Missed | Coverage | Uncovered lines |
|---|---:|---:|---:|---|
| `taskq/__init__.py` | 0 | 0 | 100% | — |
| `taskq/__main__.py` | 2 | 0 | 100% | — |
| `taskq/breaker.py` | 62 | 0 | 100% | — |
| `taskq/cache.py` | 61 | 0 | 100% | — |
| `taskq/cli.py` | 134 | 0 | 100% | — |
| `taskq/config.py` | 16 | 0 | 100% | — |
| `taskq/executor.py` | 120 | 0 | 100% | — |
| `taskq/store.py` | 72 | 0 | 100% | — |
| **TOTAL** | **467** | **0** | **100%** | **—** |

## Uncovered lines

**None.** The live `term-missing` report lists zero missed statements for every module under `03-development/src/taskq/`, so there are no uncovered line numbers to report.

## Verification

- The `TOTAL` row in the live pytest coverage output is `467` statements, `0` missed, `100%`.
- The independent `coverage report --format=total` command returned `100`.
- `coverage_raw.txt` is the unabridged pytest output used for these values.
