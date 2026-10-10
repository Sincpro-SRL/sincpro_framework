"""The command line a project exposes from its composition root, for its Makefile:

    # entrypoints/migrations.py
    if __name__ == "__main__":
        raise SystemExit(command_line(migrations))

    migrate:      python -m myapp.entrypoints.migrations upgrade
    db-plan:      python -m myapp.entrypoints.migrations upgrade --plan
    db-status:    python -m myapp.entrypoints.migrations status
    db-revision:  python -m myapp.entrypoints.migrations revision $(ctx) $(store) -m "$(m)"
    db-down:      python -m myapp.entrypoints.migrations downgrade --to $(to)
    db-check:     python -m myapp.entrypoints.migrations check
    db-adopt:     python -m myapp.entrypoints.migrations adopt $(ctx) $(store)

Context: exit code 0 when it did what was asked, 1 when it refused, a step failed or `check`
found a problem — so `make` stops there. What it did goes to stdout; why it stopped, to stderr.
"""

import argparse
import sys
from collections.abc import Sequence

from sincpro_framework.data_layer.migrations.domain.step import (
    MigrationFailed,
    MigrationRefused,
    Step,
)
from sincpro_framework.data_layer.migrations.entrypoint.orchestrator import Migrations


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate every context and store, in order.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="where every store stands, and the timeline")
    upgrade = commands.add_parser("upgrade", help="apply pending steps, in timeline order")
    upgrade.add_argument("--to", help="stop after this step id")
    upgrade.add_argument(
        "--plan", action="store_true", help="say what would run; run nothing"
    )
    downgrade = commands.add_parser("downgrade", help="put the whole system back to a step")
    downgrade.add_argument("--to", required=True, help="a step id, or 'base' for nothing")
    downgrade.add_argument(
        "--plan", action="store_true", help="say what would run; run nothing"
    )
    revision = commands.add_parser("revision", help="a new step at the end of a chain")
    revision.add_argument("context")
    revision.add_argument("store")
    revision.add_argument("-m", "--message", required=True)
    revision.add_argument(
        "--requires", nargs="*", default=[], help="context/store/id of each"
    )
    revision.add_argument("--irreversible", action="store_true")
    commands.add_parser("check", help="for CI: sums, linear chains, requires, drift")
    commands.add_parser(
        "hash", help="checksum every step body again, after reviewing an edit"
    )
    resolve = commands.add_parser("resolve", help="record where a dirty store really stands")
    resolve.add_argument("context")
    resolve.add_argument("store")
    resolve.add_argument("--at", help="the step it stands on; left out for none")
    adopt = commands.add_parser(
        "adopt", help="record a store that already has its tables on its last step"
    )
    adopt.add_argument("context")
    adopt.add_argument("store")
    return parser


def _print_status(migrations: Migrations) -> None:
    status = migrations.status()
    for key, chain in status.chains.items():
        print(f"{key:<32} {chain.state.value:<11} head={chain.position.head or '-'}")
    for step, applied in status.timeline:
        mark = "✔" if applied else "·"
        print(f"  {mark} {step.id}  {step.chain_key:<24} {step.message}")


def _print_plan(verb: str, steps: Sequence[Step]) -> None:
    print(f"would {verb} {len(steps)} step(s)")
    for step in steps:
        print(f"  {step.id}  {step.chain_key:<24} {step.message}")


def _dispatch(migrations: Migrations, arguments: argparse.Namespace) -> int:
    if arguments.command == "status":
        _print_status(migrations)
    elif arguments.command == "upgrade" and arguments.plan:
        _print_plan("apply", migrations.upgrade_plan(to=arguments.to))
    elif arguments.command == "upgrade":
        applied = migrations.upgrade(to=arguments.to)
        print(f"applied {len(applied)} step(s)")
    elif arguments.command == "downgrade" and arguments.plan:
        _print_plan("revert", migrations.downgrade_plan(to=arguments.to))
    elif arguments.command == "downgrade":
        reverted = migrations.downgrade(to=arguments.to)
        print(f"reverted {len(reverted)} step(s)")
    elif arguments.command == "revision":
        step = migrations.revision(
            arguments.context,
            arguments.store,
            arguments.message,
            arguments.requires,
            arguments.irreversible,
        )
        print(f"{step.key}  {step.file}")
    elif arguments.command == "check":
        problems = migrations.check()
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    elif arguments.command == "hash":
        changed = migrations.hash()
        for key in changed:
            print(f"hashed {key}")
        if not changed:
            print("every checksum already matched")
    elif arguments.command == "resolve":
        migrations.resolve(arguments.context, arguments.store, arguments.at)
    elif arguments.command == "adopt":
        step = migrations.adopt(arguments.context, arguments.store)
        print(f"adopted {step.key} ({step.message})")
    return 0


def command_line(migrations: Migrations, argv: Sequence[str] | None = None) -> int:
    """Run one command — `status`, `upgrade`, `downgrade`, `revision`, `check`, `hash`,
    `resolve`, `adopt` — and answer the exit code. `argv` defaults to the process's arguments.
    """
    arguments = _parser().parse_args(argv)
    try:
        return _dispatch(migrations, arguments)
    except (MigrationRefused, MigrationFailed) as error:
        print(error, file=sys.stderr)
        return 1
