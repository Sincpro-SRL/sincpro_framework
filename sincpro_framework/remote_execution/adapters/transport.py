"""What every transport answers: one execution, answered as a message the calling bus rebuilds.

    Transport.execute("billing", CommandIssueInvoice(...), request_context)  →  Payload

Context: the contract `transport_for` holds the transports by. Only that facade picks one; the
bus calls what it hands back and never names this `Protocol`, so it lives with the adapters
that implement it, not in `domain/`.
"""

from collections.abc import Mapping
from datetime import timedelta
from functools import lru_cache
from typing import Any, Protocol

from sincpro_framework.circuits import Circuit
from sincpro_framework.common.transport.addresses import HostedAt, Wire
from sincpro_framework.remote_execution.domain.exceptions import is_unreached
from sincpro_framework.remote_execution.domain.payload import Payload

PROBE_FOR_DEADLINES = 2
"""How many of the address's deadlines the call let through half-open keeps the others out — it
outlives the call, so a probe still waiting never lets a second one in."""


class Transport(Protocol):
    """The caller's side of one wire for one address — built once per address, shared by
    every thread and context that calls there."""

    def execute(self, context: str, dto: Any, request: Mapping[str, Any]) -> Payload:
        """Run `dto` on the context hosted at the address, with the caller's `request`
        context; the answer as the message the calling bus rebuilds — or the failure, raised
        as the remote exception itself or as a `ContextUnavailable`/`ContextTimeout`/
        `ContextOutcomeUnknown` marked unreached when the call itself failed."""
        ...


@lru_cache(maxsize=None)
def circuit_for(hosted_at: HostedAt) -> Circuit | None:
    """The circuit of an address that asks for one (`circuit=on`) — one per address, its state
    where the calling context keeps its leases, so every replica sharing that store stops calling
    a service that does not answer. `None` when the address asks for none, as by default.

    It counts only what this process's transport raised (`is_unreached`): the context not
    reached, or no answer back. Whatever the hosting service raised — even its own
    `CircuitOpen`, an outcome it does not know, a context it could not reach — is its answer:
    it is up, and an outage behind it never cuts its callers off.
    """
    if not hosted_at.circuit:
        return None
    open_for = timedelta(seconds=hosted_at.circuit_open_for)
    return Circuit(
        f"remote:{hosted_at.wire}://{hosted_at.address}",
        failures=hosted_at.circuit_failures,
        open_for=open_for,
        counts=is_unreached,
        probe_for=max(open_for, timedelta(seconds=hosted_at.timeout) * PROBE_FOR_DEADLINES),
    )


@lru_cache(maxsize=None)
def transport_for(hosted_at: HostedAt) -> Transport:
    """The transport of an address — one per address for the process, cached, so its channel
    or its per-thread connections are reused by every call there. The gRPC module, and gRPC
    with it, is imported only for a `grpc://` address.

        transport_for(HostedAt(Wire.GRPC, "billing:50051"))       ->  GrpcTransport
        transport_for(HostedAt(Wire.HTTPS, "billing.internal:443")) ->  HttpTransport
    """
    match hosted_at.wire:
        case Wire.GRPC:
            from sincpro_framework.remote_execution.adapters.grpc import (
                GrpcTransport,
            )

            return GrpcTransport(hosted_at)
        case Wire.HTTP | Wire.HTTPS:
            from sincpro_framework.remote_execution.adapters.http import (
                HttpTransport,
            )

            return HttpTransport(hosted_at)
