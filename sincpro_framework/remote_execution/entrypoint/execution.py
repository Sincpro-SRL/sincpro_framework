"""An execution a calling service asked for, run on the context it named — what both Open Hosts
share, whatever wire the call arrived by.
"""

import dataclasses
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from sincpro_framework.common.naming import registered_name
from sincpro_framework.context.domain.level import EntrypointKind
from sincpro_framework.exceptions import UnknownDTOToExecute
from sincpro_framework.remote_execution.domain.errors import DTODoesNotFit
from sincpro_framework.remote_execution.domain.payload import Payload

if TYPE_CHECKING:
    from sincpro_framework.auth.domain.identity import Credentials
    from sincpro_framework.use_bus import UseFramework


def _answer_type(answer: Any, context: str) -> str:
    """The identity of what answered — a DTO or a dataclass; nothing for a plain value."""
    if isinstance(answer, BaseModel) or (
        dataclasses.is_dataclass(answer) and not isinstance(answer, type)
    ):
        return registered_name(type(answer), context)
    return ""


def execute_hosted(
    contexts: "Mapping[str, UseFramework]",
    context_name: str,
    body: bytes,
    carrier: Mapping[str, str],
    credentials: "Credentials | None" = None,
) -> bytes:
    """The execution a caller asked for, on the context it named, answered as a message.

    1. The context, by name — `LookupError` when this service does not host it.
    2. The message read; the DTO by its identity in that context — `UnknownDTOToExecute` when
       the context answers none of it here — rebuilt from its values: `DTODoesNotFit` naming
       the fields when they do not fit it.
    3. Final: executed here, inside the caller's request context and trace, as whoever
       `credentials` say the call acts for — by this context's `AccessControl`, never by what
       the request context claims; the answer written as a message.
    """
    bus = contexts.get(context_name)
    if bus is None:
        raise LookupError(
            f"this service does not host {context_name!r} — it hosts "
            f"{', '.join(sorted(contexts)) or 'no context'}"
        )
    from sincpro_framework.auth.entrypoint.transports import authenticated_as
    from sincpro_framework.observability.tracing.propagation import within_trace

    message = Payload.from_json(body)
    try:
        value = bus.map_to_dto_or_event(message.type, message.data)
    except UnknownDTOToExecute:
        raise UnknownDTOToExecute(
            f"{context_name} does not answer {message.type!r} in this service"
        ) from None
    except ValidationError as error:
        raise DTODoesNotFit(f"{message.type} sent to {context_name}", error) from None
    with (
        within_trace(carrier),
        bus.context(message.full_context(), kind=EntrypointKind.REMOTE),
        authenticated_as(bus, credentials),
    ):
        answer = bus(value)
    return Payload(type=_answer_type(answer, context_name), data=answer).as_json()
