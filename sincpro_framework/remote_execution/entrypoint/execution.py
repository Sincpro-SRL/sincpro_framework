"""An execution a calling service asked for, run on the context it named — what both Open Hosts
share, whatever wire the call arrived by.
"""

from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from sincpro_framework.remote_execution.domain.hosting import hosting
from sincpro_framework.remote_execution.domain.payload import (
    Readable,
    packed,
    unpack,
    unpacked,
)

if TYPE_CHECKING:
    from sincpro_framework.auth.domain import Credentials
    from sincpro_framework.use_bus import UseFramework


def execute_hosted(
    contexts: "Mapping[str, UseFramework]",
    context_name: str,
    dto: str,
    body: Readable,
    request_context: bytes | None,
    carrier: Mapping[str, str],
    credentials: "Credentials | None" = None,
) -> Iterator[bytes]:
    """The execution a caller asked for, on the context it named, answered as chunks.

    1. The context and the DTO class, by name — `LookupError` when this service has neither.
    2. The DTO rebuilt by its class from `body` as it is read, the request context unpacked.
    3. Final: executed here — whatever the configuration says of this context — inside the
       caller's request context and trace, as whoever `credentials` say the call acts for — by
       this context's `AccessControl`, never by what the request context claims; finished before the first chunk of the answer, so a
       failure is answered as one and never as a cut stream.
    """
    bus = contexts.get(context_name)
    if bus is None:
        raise LookupError(
            f"this service does not host {context_name!r} — it hosts "
            f"{', '.join(sorted(contexts)) or 'no context'}"
        )
    dto_type = bus.dto_registry.get(dto)
    if dto_type is None:
        raise LookupError(f"{context_name} does not answer {dto!r} in this service")
    from sincpro_framework.auth.transports import authenticated_as
    from sincpro_framework.events.trace import within_trace

    value = unpacked(body, dto_type)
    context: dict[str, Any] = unpack(request_context, None) if request_context else {}
    with (
        hosting(context_name),
        within_trace(carrier),
        bus.context(context),
        authenticated_as(bus, credentials),
    ):
        answer = bus(value)
    return packed(answer)
