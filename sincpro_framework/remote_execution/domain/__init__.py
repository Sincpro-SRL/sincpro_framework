"""The vocabulary of executing a bounded context in another service: the published language it
travels in (`payload`), what a failed call raises (`errors`) and the port a transport implements
(`transport`) — beside where it is hosted, `sincpro_framework.transport.addresses`, which the
settings declare too. Standard library and pydantic only.
"""

from sincpro_framework.remote_execution.domain.errors import (
    ContextFailed,
    ContextOutcomeUnknown,
    ContextTimeout,
    ContextUnavailable,
    DTODoesNotFit,
    error_details,
    raised_as_itself,
)
from sincpro_framework.remote_execution.domain.payload import (
    CHUNK_SIZE,
    CannotTravel,
    ChunkReader,
    pack,
    pack_context,
    packed,
    unpack,
    unpack_context,
    unpacked,
)
from sincpro_framework.remote_execution.domain.transport import Transport

__all__ = [
    "CHUNK_SIZE",
    "CannotTravel",
    "ChunkReader",
    "ContextFailed",
    "ContextOutcomeUnknown",
    "ContextTimeout",
    "ContextUnavailable",
    "DTODoesNotFit",
    "Transport",
    "error_details",
    "pack",
    "pack_context",
    "packed",
    "raised_as_itself",
    "unpack",
    "unpack_context",
    "unpacked",
]
