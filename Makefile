# System verification target (SAD.md §1.1 / Gate 2 requirement).
# Uses the project venv directly by absolute path so `make verify-system`
# behaves the same regardless of the caller's activated PATH.
PYTHON := $(CURDIR)/.venv/bin/python

.PHONY: verify-system
verify-system:
	PYTHONPATH=$(CURDIR)/03-development/src $(PYTHON) -m taskq --help >/dev/null
	$(PYTHON) -m pytest 03-development/tests/integration -q
