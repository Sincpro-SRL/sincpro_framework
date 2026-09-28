"""A hosting process: `python -m sincpro_framework.remote_execution.entrypoint.host_process <address> <module:attr>…`.

What `serve_contexts(..., Attach.PROCESS)` launches. It imports the contexts by path — never the
parent's `__main__`, so a script needs no `if __name__ == "__main__"` guard — starts their host,
and says where it listens on stdout. It stops when its parent writes the grace to stdin, or when
stdin closes because the parent is gone.
"""

import json
import sys
from collections.abc import Sequence

READY = "sincpro-host-ready "
"""The line a hosting process writes once it listens — the rest of its stdout is its logs."""
DEFAULT_GRACE = 5.0


def main(argv: Sequence[str]) -> int:
    """1. Import the contexts, bind the port; say where it listens, or why it could not.
    2. Wait for the parent: a grace on stdin, or stdin closing with it.
    3. Final: drain the calls in progress, then exit.
    """
    from sincpro_framework.remote_execution.entrypoint.hosts import (
        _bound,
        _gateway,
        _imported,
    )

    address, paths = argv[0], argv[1:]
    try:
        server = _gateway([_imported(one) for one in paths]).server()
        bound = _bound(address, server.add_insecure_port(address))
        server.start()
    except Exception as error:
        print(READY + json.dumps({"failed": f"{type(error).__name__}: {error}"}), flush=True)
        return 1
    print(READY + json.dumps({"listening": bound}), flush=True)
    line = sys.stdin.readline()
    grace = float(line) if line.strip() else DEFAULT_GRACE
    server.stop(grace).wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
