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

from sincpro_framework.common.transport.grpc import metadata_from_context
from sincpro_framework.entrypoints.adapters.grpc import proto, wire
from sincpro_framework.entrypoints.adapters.grpc.wire import Struct, grpc
from sincpro_framework.entrypoints.domain.layers import Scalar


class GrpcClient:
    """One channel to a sincpro gRPC gateway.

    `call("/qr.v1.QrService/ChargePayment", {"amount": 10})` — the path is what
    `GrpcGateway.methods()` and `Describe` publish. Context travels as metadata,
    never inside the payload.
    """

    def __init__(
        self,
        target: str,
        credentials: Any | None = None,
        options: Sequence[tuple[str, Any]] | None = None,
    ):
        """Open the channel to `target` — plaintext with no `credentials`, TLS with them.

        `options` are gRPC channel arguments, passed through as they are:

            GrpcClient("localhost:50051")
            GrpcClient("qr.internal:443", grpc.ssl_channel_credentials(),
                       options=[("grpc.max_receive_message_length", 8_000_000)])
        """
        self.channel = (
            grpc.insecure_channel(target, options=list(options or ()))
            if credentials is None
            else grpc.secure_channel(target, credentials, options=list(options or ()))
        )

    def __enter__(self) -> Self:
        """The client itself — `with GrpcClient(target) as client:` closes it on the way
        out."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the channel when the `with` block ends, whether it raised or not."""
        self.close()

    def close(self) -> None:
        """Close the channel — every call in flight on it is cancelled."""
        self.channel.close()

    def call(
        self,
        path: str,
        payload: Scalar | None = None,
        context: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Scalar:
        """Execute one method and answer its result as a Scalar.

        Context: the payload is a plain dict — the `Struct` the gateway reads — so no stub is
        generated; `context` (tenant, user, trace) goes out as metadata, the way the gateway
        opens its flow. A failure raises `grpc.RpcError` with the gateway's status and
        details.

            client.call("/qr.v1.QrService/ChargePayment", {"amount": 10}, {"tenant_id": "a"})
            ->  {"payment_id": "P-1", "status": "charged"}

        1. A unary-unary callable for `path`, (de)serializing `Struct`.
        2. The payload packed as a `Struct` (`{}` when none); the context as metadata.
        Final: the answered `Struct` unpacked back into a Scalar.
        """
        callable_ = self.channel.unary_unary(
            path,
            request_serializer=Struct.SerializeToString,
            response_deserializer=Struct.FromString,
        )
        response = callable_(
            wire.scalar_to_struct(payload or {}),
            metadata=metadata_from_context(context) or None,
            timeout=timeout,
        )
        return wire.struct_to_scalar(response)

    def describe(self, timeout: float | None = None) -> Scalar:
        """The served catalog: services, methods and each input's JSON Schema."""
        return self.call(proto.DESCRIBE_PATH, timeout=timeout)
