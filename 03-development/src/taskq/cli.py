"""Command-line interface for taskq.

[FR-01]
Citations: SPEC.md lines 55-72, 102-115, 161-165.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

from . import config, store

_INJECTION_CHARACTERS = frozenset(";|&$><`")


def submit_command(args: argparse.Namespace, cfg: config.Config) -> int:
    """Validate and persist one submitted command. [FR-01]

    Citations: SPEC.md lines 55-72.
    """

    command = args.command
    if not command.strip():
        print("command must not be empty", file=sys.stderr)
        return 2
    if len(command) > 1000:
        print("command must not exceed 1000 characters", file=sys.stderr)
        return 2
    if any(character in command for character in _INJECTION_CHARACTERS):
        print("command contains a forbidden injection character", file=sys.stderr)
        return 2

    if args.name is not None:
        state = store.read_state(cfg.home)
        collision = any(
            task.get("name") == args.name
            and task.get("status") in {"pending", "running"}
            for task in state["tasks"].values()
        )
        if collision:
            print(f"task name already active: {args.name}", file=sys.stderr)
            return 2

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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="taskq")
    parser.add_argument("--json", action="store_true", dest="json_output")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    submit = subparsers.add_parser("submit")
    submit.add_argument("command")
    submit.add_argument("--name")
    submit.add_argument("--json", action="store_true", dest="json_output")
    submit.set_defaults(handler=submit_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch the selected taskq command. [FR-01]

    Citations: SPEC.md lines 102-115, 161-165.
    """

    arguments = list(argv) if argv is not None else sys.argv[1:]
    fault_arguments = [arg for arg in arguments if arg.startswith("--inject-fault=")]
    if fault_arguments:
        arguments = [arg for arg in arguments if not arg.startswith("--inject-fault=")]
        if os.environ.get("TASKQ_INJECT_FAULT_OK") != "1":
            print("inject-fault rejected in production", file=sys.stderr)
            return 2
        print(f"fault injection triggered: {fault_arguments[-1].split('=', 1)[1]}", file=sys.stderr)
        return 1

    args = _build_parser().parse_args(arguments)
    return args.handler(args, config.load())
