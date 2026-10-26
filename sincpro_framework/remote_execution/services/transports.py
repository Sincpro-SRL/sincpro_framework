"""The transport and the circuit of each address — one of each per address for the process.

    transport_for(HostedAt(Scheme.GRPC, "10.0.0.5:50051")).execute("billing", dto, {})  → Payload
    circuit_breaker_for(HostedAt.parse("grpc://b:50051?circuit_breaker=on"))  → CircuitBreaker

Context: one transport per address, shared by every context and thread that reaches it. `http`
is the standard library's; `grpc` is imported only when an address names it, so the core needs no
extra.
"""

from datetime import timedelta
from functools import lru_cache

from sincpro_framework.circuit_breaker import CircuitBreaker
from sincpro_framework.remote_execution.domain import exceptions
from sincpro_framework.remote_execution.domain.address import HostedAt, Scheme
from sincpro_framework.remote_execution.domain.transport import Transport

PROBE_FOR_DEADLINES = 2
"""How many of the address's deadlines the call let through half-open holds the others out — it
outlives the call, so a probe still waiting never lets a second one in."""


@lru_cache(maxsize=None)
def circuit_breaker_for(hosted_at: HostedAt) -> CircuitBreaker | None:
    """The circuit of an address that asks for one (`circuit_breaker=on`) — one per address, its
    state where the calling context stores its leases, so every replica sharing that store stops
    calling a service that does not answer. `None` when the address asks for none, as by default.

    It counts only what this process's transport raised (`error.unreached`): the context not
    reached, or no answer back. Whatever the hosting service raised — even its own
    `CircuitOpen`, an outcome it does not know, a context it could not reach — is its answer:
    it is up, and an outage behind it never cuts its callers off.
    """
    if not hosted_at.circuit_breaker:
        return None
    open_for = timedelta(seconds=hosted_at.circuit_breaker_open_for)
    return CircuitBreaker(
        f"remote:{hosted_at.scheme}://{hosted_at.address}",
        failures=hosted_at.circuit_breaker_failures,
        open_for=open_for,
        counts=lambda error: isinstance(error, exceptions.Unreachable) and error.unreached,
        probe_for=max(open_for, timedelta(seconds=hosted_at.timeout) * PROBE_FOR_DEADLINES),
    )


@lru_cache(maxsize=None)
def transport_for(hosted_at: HostedAt) -> Transport:
    """The transport of an address — one per address for the process, cached, so its channel
    or its per-thread connections are reused by every call there. Each transport module is
    imported only for an address that names it: the gRPC one brings the `[grpc]` extra, the
    HTTP one brings auth — a bus that reaches no other service loads neither.

        transport_for(HostedAt(Scheme.GRPC, "billing:50051"))       ->  GrpcTransport
        transport_for(HostedAt(Scheme.HTTPS, "billing.internal:443")) ->  HttpTransport
    """
    match hosted_at.scheme:
        case Scheme.GRPC:
            from sincpro_framework.remote_execution.adapters.grpc import (
                GrpcTransport,
            )

            return GrpcTransport(hosted_at)
        case Scheme.HTTP | Scheme.HTTPS:
            from sincpro_framework.remote_execution.adapters.http import (
                HttpTransport,
            )

            return HttpTransport(hosted_at)
