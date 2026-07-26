"""Command-line interface for taskq.

[FR-01, FR-02, FR-05]
Citations: SPEC.md lines 55-72, 102-115, 161-165 (FR-01); FR-02 subcommand;
SPEC.md §3 FR-05 (AC-5.1..5.8), §7 (exit code map, error phrasing).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

from . import breaker, cache, config, executor, store

# SPEC.md §3 FR-01: seven injection characters must never appear in a
# submitted command string.
_INJECTION_CHARACTERS = frozenset(";|&$><`")

# Exit code returned for any validation failure (FR-01 invariant).
_EXIT_VALIDATION_ERROR = 2

# Exit code returned for a single-task run that produced a timeout (FR-02 AC-2.5).
_EXIT_TIMEOUT = 4

# Exit code returned when the breaker is OPEN and refuses admission (FR-03 AC-3.3).
_EXIT_BREAKER_OPEN = 3


def _validation_error(message: str) -> int:
    """Surface a validation rejection on stderr and return the FR-01 exit code."""

    print(message, file=sys.stderr)
    return _EXIT_VALIDATION_ERROR


def _print_json(payload: object) -> None:
    """Write `payload` to stdout as single-line JSON (FR-05 `--json` contract)."""

    sys.stdout.write(json.dumps(payload, separators=(",", ":")))


def _add_json_flag(subparser: argparse.ArgumentParser) -> None:
    """Register the per-subcommand `--json` override used by all FR-05 subcommands."""

    subparser.add_argument("--json", action="store_true", dest="json_output", default=argparse.SUPPRESS)


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
        _print_json({"id": record["id"], "status": "pending"})
    else:
        print(record["id"])
    return 0


def run_command(args: argparse.Namespace, cfg: config.Config) -> int:
    """Run one task by id or drain pending tasks with --all. [FR-02, FR-03, FR-04, FR-05]

    Maps a breaker-OPEN refusal from `executor` to exit 3 + stderr
    `breaker open`, without ever spawning the underlying subprocess.
    Forwards `--cached` to `executor.run_task` for a single-id run. Rejects
    the undocumented `<id> --all` combination and an unknown task id with
    exit 2 (AC-5.2, AC-5.6).

    Citations: SPEC.md lines FR-02 AC-2.4, AC-2.5; FR-03 AC-3.3; FR-04
    AC-4.2; FR-05 AC-5.2, AC-5.4, AC-5.6.
    """

    if args.id and args.all:
        print("run does not accept both <id> and --all", file=sys.stderr)
        return _EXIT_VALIDATION_ERROR

    try:
        if args.all:
            results = executor.run_all(cfg=cfg)
            if any(task.get("status") == "timeout" for task in results):
                return _EXIT_TIMEOUT
            return 0
        if not args.id:
            print("run requires a task id or --all", file=sys.stderr)
            return _EXIT_VALIDATION_ERROR
        try:
            task = executor.run_task(args.id, cfg=cfg, use_cache=args.cached)
        except KeyError:
            print(f"unknown task: {args.id}", file=sys.stderr)
            return _EXIT_VALIDATION_ERROR
        if task.get("status") == "timeout":
            return _EXIT_TIMEOUT
        return 0
    except breaker.BreakerOpenError:
        print("breaker open", file=sys.stderr)
        return _EXIT_BREAKER_OPEN


def status_command(args: argparse.Namespace, cfg: config.Config) -> int:
    """Print one task's full record, or exit 2 for an unknown id. [FR-05]

    Citations: SPEC.md §3 FR-05 subcommand table (AC-5.1, AC-5.5); §7
    (AC-5.6).
    """

    state = store.read_state(cfg.home)
    task = state["tasks"].get(args.id)
    if task is None:
        print(f"unknown task: {args.id}", file=sys.stderr)
        return _EXIT_VALIDATION_ERROR
    if args.json_output:
        _print_json(task)
    else:
        print(task)
    return 0


def list_command(args: argparse.Namespace, cfg: config.Config) -> int:
    """Print every task, optionally filtered by `--status`. [FR-05]

    Citations: SPEC.md §3 FR-05 subcommand table (AC-5.1, AC-5.5).
    """

    state = store.read_state(cfg.home)
    tasks = list(state["tasks"].values())
    if args.status is not None:
        tasks = [task for task in tasks if task.get("status") == args.status]
    if args.json_output:
        _print_json(tasks)
    else:
        for task in tasks:
            print(task)
    return 0


def clear_command(args: argparse.Namespace, cfg: config.Config) -> int:
    """Empty `$TASKQ_HOME`'s task store, breaker state, and cache. [FR-05]

    Citations: SPEC.md §3 FR-05 subcommand table (AC-5.1, AC-5.5).
    """

    store.write_state(cfg.home, store._empty_state())
    breaker.save(cfg.home, breaker._default_state())
    cache.clear(cfg.home)
    if args.json_output:
        _print_json({"cleared": True})
    else:
        print("cleared")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taskq")
    parser.add_argument("--json", action="store_true", dest="json_output")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    submit = subparsers.add_parser("submit")
    submit.add_argument("command")
    submit.add_argument("--name")
    _add_json_flag(submit)
    submit.set_defaults(handler=submit_command)
    run = subparsers.add_parser("run")
    run.add_argument("id", nargs="?")
    run.add_argument("--all", action="store_true")
    run.add_argument("--cached", action="store_true")
    _add_json_flag(run)
    run.set_defaults(handler=run_command)
    status = subparsers.add_parser("status")
    status.add_argument("id")
    _add_json_flag(status)
    status.set_defaults(handler=status_command)
    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--status", dest="status")
    _add_json_flag(list_parser)
    list_parser.set_defaults(handler=list_command)
    clear = subparsers.add_parser("clear")
    _add_json_flag(clear)
    clear.set_defaults(handler=clear_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch the selected taskq command. [FR-01, FR-05]

    Wraps the handler dispatch so a corrupted `tasks.json` maps to exit 1
    + stderr `store corrupted` (AC-5.7), and any other unexpected
    exception raised by a handler maps to exit 1 rather than leaking a
    raw traceback (AC-5.4, AC-5.8).

    Citations: SPEC.md lines 102-115, 161-165; §3 FR-05 AC-5.4, AC-5.7,
    AC-5.8.
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
    try:
        return args.handler(args, config.load())
    except store.StoreCorruptedError:
        print("store corrupted", file=sys.stderr)
        return 1
    except Exception as exc:  # AC-5.4/AC-5.8: unexpected handler error -> exit 1
        print(f"internal error: {exc}", file=sys.stderr)
        return 1
