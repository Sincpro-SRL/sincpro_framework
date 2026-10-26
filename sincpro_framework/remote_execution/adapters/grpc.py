"""The gRPC transport of hosted contexts, the caller's side: `/sincpro.Contexts/Execute`, chunks of
bytes both ways.

    GrpcTransport(HostedAt(Scheme.GRPC, "b:50051")).execute("billing", dto, request)

Context: one generic method rather than one per DTO, because both ends run the same code — the
context's name rides as metadata, the message (`sincpro_framework.remote_execution.domain.payload`: the DTO's identity,
its values, the request context) as a stream of 1 MiB chunks each way, so no gRPC message comes
near its per-message limit. This module imports gRPC — it is imported only when an address names
`grpc://`.
"""

from collections.abc import Iterator, Mapping
from typing import Any

from sincpro_framework.auth import security
from sincpro_framework.common.naming import registered_name
from sincpro_framework.common.transport.grpc import grpc
from sincpro_framework.observability.tracing.propagation import trace_carrier
from sincpro_framework.remote_execution.domain import exceptions
from sincpro_framework.remote_execution.domain.address import HostedAt
from sincpro_framework.remote_execution.domain.failure_codec import FailureCodec
from sincpro_framework.remote_execution.domain.payload import Payload

SERVICE = "sincpro.Contexts"
METHOD = "Execute"
PATH = f"/{SERVICE}/{METHOD}"
CONTEXT_HEADER = "sp-bounded-context"
ACCEPTED_HEADER = "sp-accepted"
"""What the host answers first, before it reads the DTO — a call dropped after it may have run."""
ERROR_MODULE = "sp-error-module"
ERROR_KIND = "sp-error-kind"
ERROR_DETAILS = "sp-error-details-bin"
CHUNK_SIZE = 1024 * 1024
"""Bytes per gRPC message — far below its per-message limit."""


def chunks_of(data: bytes) -> Iterator[bytes]:
    """`data` cut in `CHUNK_SIZE` pieces — one empty piece when there is nothing."""
    view = memoryview(data)
    yield bytes(view[:CHUNK_SIZE])
    for start in range(CHUNK_SIZE, len(view), CHUNK_SIZE):
        yield bytes(view[start : start + CHUNK_SIZE])


def _raised(error: Any, context: str, hosted_at: HostedAt) -> Exception:
    """What the caller raises for a failed call.

    1. No answer in time: `ContextTimeout` — it may have run.
    2. The connection lost: `ContextOutcomeUnknown` once the host accepted the call,
       `ContextUnavailable` before. Nobody hosting it there: `ContextUnavailable`. Each
       `unreached` — raised here, what an address's circuit counts.
    3. Final: the remote exception as itself — see `FailureCodec.rebuilt`.
    """
    code, details = error.code(), error.details() or ""
    where = f"{context} at {hosted_at.address}"
    if code == grpc.StatusCode.DEADLINE_EXCEEDED:
        return exceptions.ContextTimeout(
            f"{where} did not answer within {hosted_at.timeout:g}s", unreached=True
        )
    if code == grpc.StatusCode.UNAVAILABLE:
        if ACCEPTED_HEADER in dict(error.initial_metadata() or ()):
            return exceptions.ContextOutcomeUnknown(
                f"{where} took the call and was lost: {details}", unreached=True
            )
        return exceptions.ContextUnavailable(
            f"{where} is not answering: {details}", unreached=True
        )
    if code in (grpc.StatusCode.UNIMPLEMENTED, grpc.StatusCode.NOT_FOUND):
        return exceptions.ContextUnavailable(f"{where}: {details}", unreached=True)
    trailing = dict(error.trailing_metadata() or ())
    return FailureCodec.rebuilt(
        trailing.get(ERROR_MODULE),
        trailing.get(ERROR_KIND),
        details,
        trailing.get(ERROR_DETAILS),
    )


class GrpcTransport:
    """The caller's side for one address: one channel, shared by every context and thread."""

    def __init__(self, hosted_at: HostedAt) -> None:
        """The channel opened lazily by gRPC — nothing connects until the first call, so an
        address that is down fails that call, not the build. Plaintext: TLS is not offered."""
        self.hosted_at = hosted_at
        self._channel = grpc.insecure_channel(hosted_at.address)
        self._call = self._channel.stream_stream(PATH)

    def execute(self, context: str, dto: Any, request: Mapping[str, Any]) -> Payload:
        """One execution on the hosting service, its answer read back whole.

            transport.execute("billing", CommandPay(amount=10), {"correlation_id": "c1"})
            ->  Payload(type="billing.ResponsePay", data={"paid": True}, ...)
            <the host raised InvoiceNotFound>   ->  InvoiceNotFound raised here, as itself

        1. The message as a stream of chunks — the DTO by its identity, its values, the
           request context; the context, the trace and who the call acts for as metadata.
        2. One streaming call, with the address's deadline.
        3. Final: the answer's message, or the failure raised as `_raised` says.
        """
        metadata = (
            (CONTEXT_HEADER, context),
            *trace_carrier().items(),
            *security.identity_headers(context).items(),
        )
        body = Payload.of(registered_name(type(dto), context), dto, request).as_json()
        try:
            answers = self._call(
                chunks_of(body), metadata=metadata, timeout=self.hosted_at.timeout
            )
            return Payload.from_json(b"".join(answers))
        except grpc.RpcError as error:
            raise _raised(error, context, self.hosted_at) from None

    def close(self) -> None:
        """The channel closed — a call made after it fails."""
        self._channel.close()
