"""A dict-in/dict-out gRPC client, for callers with no generated stubs.

A Python service calling another Sincpro bus should not have to run `protoc` to
send a DTO: the wire is `Struct`, so the payload is the same Scalar the Feature
already speaks. Go/TypeScript clients generate from the exported `.proto`
(`GrpcGateway.write_proto_files`) — this is the Python shortcut, and what the
tests dial.
"""

from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any, Self

from sincpro_framework.entrypoints.const import Scalar
from sincpro_framework.entrypoints.grpc import proto, wire
from sincpro_framework.entrypoints.grpc.wire import Struct, grpc


class GrpcClient:
    """One channel to a sincpro gRPC gateway.

    `call("/qr.Features/ChargePayment", {"amount": 10})` — the path is what
    `GrpcGateway.methods()` and `Describe` publish. Context travels as metadata,
    never inside the payload.
    """

    def __init__(
        self,
        target: str,
        credentials: Any | None = None,
        options: Sequence[tuple[str, Any]] | None = None,
    ):
        self.channel = (
            grpc.insecure_channel(target, options=list(options or ()))
            if credentials is None
            else grpc.secure_channel(target, credentials, options=list(options or ()))
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.channel.close()

    def call(
        self,
        path: str,
        payload: Scalar | None = None,
        context: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Scalar:
        """Execute one method. Raises `grpc.RpcError` with the gateway's status."""
        callable_ = self.channel.unary_unary(
            path,
            request_serializer=Struct.SerializeToString,
            response_deserializer=Struct.FromString,
        )
        response = callable_(
            wire.scalar_to_struct(payload or {}),
            metadata=wire.metadata_from_context(context) or None,
            timeout=timeout,
        )
        return wire.struct_to_scalar(response)

    def describe(self, timeout: float | None = None) -> Scalar:
        """The served catalog: services, methods and each input's JSON Schema."""
        return self.call(proto.DESCRIBE_PATH, timeout=timeout)
