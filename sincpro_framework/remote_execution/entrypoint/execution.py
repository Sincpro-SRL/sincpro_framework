"""An execution a calling service asked for, run on the context it named — what both Open Hosts
share, whatever wire the call arrived by.
"""

from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING

from pydantic import ValidationError

from sincpro_framework.context.domain.level import EntrypointKind
from sincpro_framework.exceptions import UnknownDTOToExecute
from sincpro_framework.remote_execution.domain.errors import DTODoesNotFit
from sincpro_framework.remote_execution.domain.payload import (
    Readable,
    packed,
    unpack_context,
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

    1. The context, by name — `LookupError` when this service does not host it.
    2. The DTO class, by its registered name — `UnknownDTOToExecute` when the context answers no
       DTO of that name here.
    3. The DTO rebuilt by its class from `body` as it is read — `DTODoesNotFit` naming the fields
       when the caller's values do not fit it; the request context unpacked.
    4. Final: executed here, inside the caller's request context and trace, as whoever
       `credentials` say the call acts for — by this context's `AccessControl`, never by what
       the request context claims; finished before the first chunk of the answer, so a failure
       is answered as one and never as a cut stream.
    """
    bus = contexts.get(context_name)
    if bus is None:
        raise LookupError(
            f"this service does not host {context_name!r} — it hosts "
            f"{', '.join(sorted(contexts)) or 'no context'}"
        )
    dto_type = bus.dto_registry.get(dto)
    if dto_type is None:
        raise UnknownDTOToExecute(f"{context_name} does not answer {dto!r} in this service")
    from sincpro_framework.auth.transports import authenticated_as
    from sincpro_framework.event_driven.infrastructure.trace import within_trace

    try:
        value = unpacked(body, dto_type)
    except ValidationError as error:
        raise DTODoesNotFit(f"{dto} sent to {context_name}", error) from None
    context = unpack_context(request_context) if request_context else {}
    with (
        within_trace(carrier),
        bus.context(context, kind=EntrypointKind.REMOTE),
        authenticated_as(bus, credentials),
    ):
        answer = bus(value)
    return packed(answer)
