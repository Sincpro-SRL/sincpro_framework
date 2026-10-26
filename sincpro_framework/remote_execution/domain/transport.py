"""What every transport answers: one execution, answered as a message the calling bus rebuilds.

    Transport.execute("billing", CommandIssueInvoice(...), request_context)  →  Payload

Context: the wires — gRPC and HTTP, `remote_execution.adapters` — each satisfy it, and
`remote_execution.services.transports` hands back one per address by it.
"""

from collections.abc import Mapping
from typing import Any, Protocol

from sincpro_framework.remote_execution.domain.payload import Payload


class Transport(Protocol):
    """The caller's side of one wire for one address — built once per address, shared by
    every thread and context that calls there."""

    def execute(self, context: str, dto: Any, request: Mapping[str, Any]) -> Payload:
        """Run `dto` on the context hosted at the address, with the caller's `request`
        context; the answer as the message the calling bus rebuilds — or the failure, raised
        as the remote exception itself or as a `ContextUnavailable`/`ContextTimeout`/
        `ContextOutcomeUnknown` that says `unreached` when the call itself failed."""
        ...
