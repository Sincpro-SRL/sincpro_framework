"""Write a service's client contract without serving anything — for a build or a CI step:

    python -m sincpro_framework.entrypoints export-client-contract app.http:gateways web/contracts

`<module>:<name>` names a gateway, or a list of them — one per wire —; each bounded context's
contract is written to `<out_dir>/<context>.contract.json` (`export_client_contract`). A project
wraps it in its Makefile:

    contract:
        poetry run python -m sincpro_framework.entrypoints export-client-contract \\
            app.http:gateways web/src/contracts
"""

import argparse
import importlib
import sys
from typing import Any

from sincpro_framework.entrypoints.services.client_contract import export_client_contract


def _loaded(target: str) -> Any:
    """What `module:name` names, its module imported: `app.http:gateways` is `gateways` of
    `app.http`."""
    module, _, name = target.partition(":")
    if not module or not name:
        raise SystemExit(f"{target!r} names no gateway: write it `module:name`")
    return getattr(importlib.import_module(module), name)


def main(argv: list[str] | None = None) -> int:
    """Run the command `argv` names, and say what it wrote.

    1. `export-client-contract <module:gateways> <out_dir>`: each context's contract written.
    Final: one line per file written, `<context>: <path>`.
    """
    parser = argparse.ArgumentParser(prog="python -m sincpro_framework.entrypoints")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser(
        "export-client-contract",
        help="write each bounded context's client contract to <out_dir>/<context>.contract.json",
    )
    export.add_argument("gateways", help="module:name of a gateway, or of a list of gateways")
    export.add_argument("out_dir", help="the directory the contracts are written to")
    arguments = parser.parse_args(argv)
    written = export_client_contract(_loaded(arguments.gateways), arguments.out_dir)
    for context, path in written.items():
        print(f"{context}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
