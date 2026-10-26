"""The Open Host Service: a bounded context hosted for other services, however this process runs.

    billing.serve("0.0.0.0:50051")                                  its own deployment: blocks
    host = billing.serve("0.0.0.0:50051", Attach.THREAD)            beside a REST API, one process
    host = serve_contexts(["my_erp.billing:billing"], "0.0.0.0:50051", Attach.PROCESS)
    host.stop()                                                     drains the calls in progress

Context: a service that already runs a REST API should not need a second deployment, or an
orchestrator, to host its contexts. `THREAD` serves from this process's own gRPC server threads —
a FastAPI lifespan starts it and stops it. `PROCESS` gives the contexts their own interpreter —
their own CPU, GIL and crash — tied to this one: it stops with `stop()`, and on its own when this
process dies (its stdin closes). It is launched as its own module, never re-importing this
process's `__main__`, so it imports what it hosts by path; a context object is turned into one by
finding the module that holds it. The
transport is gRPC, on a server of its own that serves the open host and its health only — never
the contexts' public catalog; HTTP hosting is a route on the service's own app instead —
`sincpro_framework.remote_execution.entrypoint.http`.
"""

import importlib
import json
import os
import queue
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, overload

if TYPE_CHECKING:
    from sincpro_framework.use_bus import UseFramework

READY = "sincpro-host-ready "
"""The line a hosting process writes once it listens — the rest of its stdout is its logs."""
READY_WITHIN = 60.0
"""Seconds a hosting process may take to import its contexts and bind its port."""


class Attach(StrEnum):
    """How a host runs beside the rest of this process."""

    FOREGROUND = "foreground"
    """This process is the host: `serve` blocks until it is told to stop (SIGTERM, SIGINT)."""
    THREAD = "thread"
    """Served from this process's gRPC threads; `serve` answers the `OpenHost` at once."""
    PROCESS = "process"
    """Served from a subprocess of its own, tied to this one; `serve` answers when it listens."""


class OpenHost(ABC):
    """A host serving contexts for calling services. `address` is where it listens — its bound
    port when `:0` was asked for; `pid` is the process that answers."""

    address: str
    pid: int

    @abstractmethod
    def stop(self, grace: float = 5.0) -> None:
        """Stop taking calls, let the ones in progress finish for up to `grace` seconds."""


class _ServerHost(OpenHost):
    """A host served on this process's own gRPC threads (`Attach.THREAD`)."""

    def __init__(self, server: Any, address: str) -> None:
        """A server already started, at the address it bound."""
        self._server = server
        self.address = address
        self.pid = os.getpid()

    def stop(self, grace: float = 5.0) -> None:
        """The server stopped — new calls refused, the ones in progress given `grace` seconds
        — and waited for until it has."""
        self._server.stop(grace).wait()


class _ProcessHost(OpenHost):
    """A host served by a subprocess (`Attach.PROCESS`) — `pid` is the subprocess's."""

    def __init__(self, process: "subprocess.Popen[str]", address: str) -> None:
        """A subprocess already listening, at the address it said."""
        self._process = process
        self.address = address
        self.pid = process.pid

    def stop(self, grace: float = 5.0) -> None:
        """The subprocess asked to stop, waited for, and killed when it will not.

        1. Still running: `grace` written to its stdin and stdin closed — what `host_process`
           waits on; a pipe already gone is ignored.
        2. Waited for `grace` plus ten seconds, to drain and exit.
        Final: still alive after that — killed, and reaped.
        """
        if self._process.poll() is None and self._process.stdin is not None:
            try:
                self._process.stdin.write(f"{grace}\n")
                self._process.stdin.close()
            except OSError:
                pass
        try:
            self._process.wait(grace + 10)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()


def _internal_server(contexts: Sequence["UseFramework"]) -> Any:
    """A gRPC server hosting `contexts` for calling services — the open host and its health,
    and nothing a public client could discover."""
    from sincpro_framework.remote_execution.entrypoint.grpc import open_host

    return open_host(contexts).server()


def _bound(address: str, port: int) -> str:
    """`address` with the port the server actually bound — what `:0` turns into.

    _bound("127.0.0.1:0", 50731)   ->  "127.0.0.1:50731"
    """
    host, _, _asked = address.rpartition(":")
    return f"{host}:{port}"


def _import_path(context: "UseFramework") -> str:
    """Where `context` can be imported from — `module:attribute` of the first loaded module that
    holds it, so a fresh process can import it."""
    for name, module in sorted(sys.modules.items()):
        if name == "__main__" or module is None:
            continue
        for attribute, value in vars(module).items():
            if value is context:
                return f"{name}:{attribute}"
    raise ValueError(
        f"{context.name} is not held by any importable module, and a hosting process imports "
        "what it hosts — write serve_contexts(['module:attribute'], address, Attach.PROCESS)"
    )


def _imported(path: str) -> "UseFramework":
    """The context a `module:attribute` path names, its module imported.

    _imported("billing.framework:billing")   ->  the `billing` UseFramework
    """
    module, _, attribute = path.partition(":")
    return getattr(importlib.import_module(module), attribute)


def _forwarded(lines: Iterator[str], ready: "queue.Queue[str]") -> None:
    """A hosting process's stdout: its ready line handed over, everything else — its logs —
    forwarded to this process's stdout, so its pipe never fills."""
    for line in lines:
        if line.startswith(READY):
            ready.put(line[len(READY) :])
        else:
            sys.stdout.write(line)


def _started(paths: Sequence[str], address: str) -> OpenHost:
    """A hosting process for `paths`, answered once it listens.

    1. Launch `host_process` with this process's import path, so it imports what this one can.
    2. Wait for its ready line, forwarding the rest of what it writes.
    3. Final: the host where it listens — refused, saying why, when it could not start.
    """
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(one for one in sys.path if one),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "sincpro_framework.remote_execution.entrypoint.host_process",
            address,
            *paths,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=environment,
    )
    ready: queue.Queue[str] = queue.Queue()
    if process.stdout is not None:
        threading.Thread(target=_forwarded, args=(process.stdout, ready), daemon=True).start()
    try:
        said = json.loads(ready.get(timeout=READY_WITHIN))
    except queue.Empty:
        process.kill()
        raise RuntimeError(
            f"the host of {', '.join(paths)} did not listen within {READY_WITHIN:g}s"
        ) from None
    if "listening" not in said:
        process.wait(5)
        raise RuntimeError(
            f"the host of {', '.join(paths)} could not start: {said['failed']}"
        )
    return _ProcessHost(process, said["listening"])


@overload
def serve_contexts(contexts: "Sequence[UseFramework | str]", address: str) -> None: ...


@overload
def serve_contexts(
    contexts: "Sequence[UseFramework | str]",
    address: str,
    attach: Literal[Attach.FOREGROUND],
) -> None: ...


@overload
def serve_contexts(
    contexts: "Sequence[UseFramework | str]",
    address: str,
    attach: Literal[Attach.THREAD, Attach.PROCESS],
) -> OpenHost: ...


def serve_contexts(
    contexts: "Sequence[UseFramework | str]",
    address: str,
    attach: Attach = Attach.FOREGROUND,
) -> OpenHost | None:
    """Host `contexts` for calling services at `address` — `host:port`, `:0` for any free port.

        serve_contexts([billing], "0.0.0.0:50061")         ->  None, once SIGTERM drained it
        serve_contexts([billing], "127.0.0.1:0", Attach.THREAD)
        ->  OpenHost listening at "127.0.0.1:50731"
        serve_contexts(["billing.framework:billing"], "127.0.0.1:0", Attach.PROCESS)
        ->  OpenHost of a subprocess, once it says it listens

    1. `FOREGROUND`: this process serves until SIGTERM/SIGINT, draining the calls in progress.
    2. `THREAD`: started on this process's gRPC threads; the host answers at once.
    3. Final — `PROCESS`: each context by its import path, served by a subprocess tied to this
       one; the host answers once the subprocess listens.
    """
    if attach == Attach.PROCESS:
        return _started(
            [one if isinstance(one, str) else _import_path(one) for one in contexts], address
        )
    served = [_imported(one) if isinstance(one, str) else one for one in contexts]
    if attach == Attach.FOREGROUND:
        from sincpro_framework.common.transport.grpc import serve_until_terminated

        server = _internal_server(served)
        server.add_insecure_port(address)
        serve_until_terminated(server, ", ".join(one.name for one in served))
        return None
    server = _internal_server(served)
    bound = _bound(address, server.add_insecure_port(address))
    server.start()
    return _ServerHost(server, bound)
