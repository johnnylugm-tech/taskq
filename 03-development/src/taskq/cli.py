"""Command-line interface for taskq.

[FR-01, FR-02]
Citations: SPEC.md lines 55-72, 102-115, 161-165 (FR-01); FR-02 subcommand.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

from . import config, executor, store

# SPEC.md §3 FR-01: seven injection characters must never appear in a
# submitted command string.
_INJECTION_CHARACTERS = frozenset(";|&$><`")

# Exit code returned for any validation failure (FR-01 invariant).
_EXIT_VALIDATION_ERROR = 2

# Exit code returned for a single-task run that produced a timeout (FR-02 AC-2.5).
_EXIT_TIMEOUT = 4


def _validation_error(message: str) -> int:
    """Surface a validation rejection on stderr and return the FR-01 exit code."""

    print(message, file=sys.stderr)
    return _EXIT_VALIDATION_ERROR


def submit_command(args: argparse.Namespace, cfg: config.Config) -> int:
    """Validate and persist one submitted command. [FR-01]

    Citations: SPEC.md lines 55-72.
    """

    command = args.command
    if not command.strip():
        return _validation_error("command must not be empty")
    if len(command) > 1000:
        return _validation_error("command must not exceed 1000 characters")
    if any(character in command for character in _INJECTION_CHARACTERS):
        return _validation_error("command contains a forbidden injection character")

    if args.name is not None:
        state = store.read_state(cfg.home)
        collision = any(
            task.get("name") == args.name
            and task.get("status") in {"pending", "running"}
            for task in state["tasks"].values()
        )
        if collision:
            return _validation_error(f"task name already active: {args.name}")

    record = store.add_task(cfg.home, command, args.name)
    if args.json_output:
        sys.stdout.write(
            json.dumps(
                {"id": record["id"], "status": "pending"},
                separators=(",", ":"),
            )
        )
    else:
        print(record["id"])
    return 0


def run_command(args: argparse.Namespace, cfg: config.Config) -> int:
    """Run one task by id or drain pending tasks with --all. [FR-02]

    Citations: SPEC.md lines FR-02 AC-2.4, AC-2.5.
    """

    if args.all:
        results = executor.run_all(cfg=cfg)
        if any(task.get("status") == "timeout" for task in results):
            return _EXIT_TIMEOUT
        return 0
    if not args.id:
        print("run requires a task id or --all", file=sys.stderr)
        return _EXIT_VALIDATION_ERROR
    task = executor.run_task(args.id, cfg=cfg)
    if task.get("status") == "timeout":
        return _EXIT_TIMEOUT
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taskq")
    parser.add_argument("--json", action="store_true", dest="json_output")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    submit = subparsers.add_parser("submit")
    submit.add_argument("command")
    submit.add_argument("--name")
    submit.add_argument("--json", action="store_true", dest="json_output")
    submit.set_defaults(handler=submit_command)
    run = subparsers.add_parser("run")
    run.add_argument("id", nargs="?")
    run.add_argument("--all", action="store_true")
    run.set_defaults(handler=run_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch the selected taskq command. [FR-01]

    Citations: SPEC.md lines 102-115, 161-165.
    """

    arguments = list(argv) if argv is not None else sys.argv[1:]
    fault_arguments = [arg for arg in arguments if arg.startswith("--inject-fault=")]
    arguments = [arg for arg in arguments if not arg.startswith("--inject-fault=")]
    if fault_arguments:
        if os.environ.get("TASKQ_INJECT_FAULT_OK") != "1":
            print("inject-fault rejected in production", file=sys.stderr)
            return _EXIT_VALIDATION_ERROR
        print(f"fault injection triggered: {fault_arguments[-1].split('=', 1)[1]}", file=sys.stderr)
        return 1

    args = _build_parser().parse_args(arguments)
    return args.handler(args, config.load())
