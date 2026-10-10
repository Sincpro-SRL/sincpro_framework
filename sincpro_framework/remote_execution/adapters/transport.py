"""What every transport answers: one execution, answered as a message the calling bus rebuilds.

    Transport.execute("billing", CommandIssueInvoice(...), request_context)  →  Payload

Context: the contract `transport_for` holds the transports by. Only that facade picks one; the
bus calls what it hands back and never names this `Protocol`, so it lives with the adapters
that implement it, not in `domain/`.
"""

from collections.abc import Mapping
from functools import lru_cache
from typing import Any, Protocol

from sincpro_framework.common.transport.addresses import HostedAt, Wire
from sincpro_framework.remote_execution.domain.payload import Payload


class Transport(Protocol):
    def execute(self, context: str, dto: Any, request: Mapping[str, Any]) -> Payload: ...


@lru_cache(maxsize=None)
def transport_for(hosted_at: HostedAt) -> Transport:
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
