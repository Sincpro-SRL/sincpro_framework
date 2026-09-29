"""The gRPC transport of hosted contexts, the caller's side: `/sincpro.Contexts/Execute`, chunks of
bytes both ways.

    GrpcTransport(HostedAt("grpc", "b:50051")).execute("billing", dto, Response, request)

Context: one generic method rather than one per DTO, because both ends run the same code — the
context's name, the DTO's registered name and the request context ride as metadata, the DTO's
values as a stream of 1 MiB chunks each way (`remote_execution.domain.payload`), so no message comes
near gRPC's per-message limit and a payload of several GiB is never one buffer. This module imports
gRPC — it is imported only when an address names `grpc://`.
"""

from collections.abc import Mapping
from typing import Any

from sincpro_framework.entrypoints.grpc.wire import grpc
from sincpro_framework.events.trace import trace_carrier
from sincpro_framework.remote_execution.domain.address import HostedAt
from sincpro_framework.remote_execution.domain.errors import (
    ContextTimeout,
    ContextUnavailable,
    raised_as_itself,
)
from sincpro_framework.remote_execution.domain.hosting import dto_name
from sincpro_framework.remote_execution.domain.payload import (
    ChunkReader,
    pack,
    packed,
    unpacked,
)

SERVICE = "sincpro.Contexts"
METHOD = "Execute"
PATH = f"/{SERVICE}/{METHOD}"
CONTEXT_HEADER = "sp-bounded-context"
DTO_HEADER = "sp-dto"
REQUEST_CONTEXT_HEADER = "sp-request-context-bin"
ERROR_MODULE = "sp-error-module"
ERROR_KIND = "sp-error-kind"


def _raised(error: Any, context: str, hosted_at: HostedAt) -> Exception:
    """What the caller raises for a failed call.

    1. No answer in time: `ContextTimeout`. Nobody there, or not hosting it: `ContextUnavailable`.
    2. Final: the remote exception as itself, when this process can raise it — see
       `raised_as_itself`.
    """
    code, details = error.code(), error.details() or ""
    where = f"{context} at {hosted_at.address}"
    if code == grpc.StatusCode.DEADLINE_EXCEEDED:
        return ContextTimeout(f"{where} did not answer within {hosted_at.timeout:g}s")
    if code in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.UNIMPLEMENTED):
        return ContextUnavailable(f"{where} is not answering: {details}")
    if code == grpc.StatusCode.NOT_FOUND:
        return ContextUnavailable(f"{where}: {details}")
    trailing = dict(error.trailing_metadata() or ())
    return raised_as_itself(trailing.get(ERROR_MODULE), trailing.get(ERROR_KIND), details)


class GrpcTransport:
    """The caller's side for one address: one channel, shared by every context and thread."""

    def __init__(self, hosted_at: HostedAt) -> None:
        self.hosted_at = hosted_at
        self._channel = grpc.insecure_channel(hosted_at.address)
        self._call = self._channel.stream_stream(PATH)

    def execute(
        self, context: str, dto: Any, response: Any, request: Mapping[str, Any]
    ) -> Any:
        """1. The DTO's values as a stream of chunks; the context, the DTO's name, the request
           context, the trace and who the call acts for as metadata.
        2. One streaming call, with the address's deadline.
        3. Final: the answer rebuilt as `response` as its chunks arrive, or the failure raised as
           `_raised` says.
        """
        from sincpro_framework.auth.transports import identity_headers

        metadata = (
            (CONTEXT_HEADER, context),
            (DTO_HEADER, dto_name(type(dto))),
            (REQUEST_CONTEXT_HEADER, pack(dict(request))),
            *trace_carrier().items(),
            *identity_headers(context).items(),
        )
        try:
            answers = self._call(
                packed(dto), metadata=metadata, timeout=self.hosted_at.timeout
            )
            return unpacked(ChunkReader(answers), response)
        except grpc.RpcError as error:
            raise _raised(error, context, self.hosted_at) from None

    def close(self) -> None:
        self._channel.close()
