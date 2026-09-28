"""The vocabulary of executing a bounded context in another service: where it is hosted
(`address`), the published language it travels in (`payload`), what a failed call raises
(`errors`), the port a transport implements (`transport`), and the contexts this execution hosts
(`hosting`). Standard library and pydantic only.
"""

from sincpro_framework.remote_execution.domain.address import (
    DEFAULT_TIMEOUT,
    HostedAt,
    context_map_of,
    parse_address,
    parse_context_map,
)
from sincpro_framework.remote_execution.domain.errors import (
    ContextFailed,
    ContextTimeout,
    ContextUnavailable,
    raised_as_itself,
)
from sincpro_framework.remote_execution.domain.hosting import dto_name, hosted_here, hosting
from sincpro_framework.remote_execution.domain.payload import (
    CHUNK_SIZE,
    CannotTravel,
    ChunkReader,
    pack,
    packed,
    unpack,
    unpacked,
)
from sincpro_framework.remote_execution.domain.transport import Transport

__all__ = [
    "CHUNK_SIZE",
    "CannotTravel",
    "ChunkReader",
    "ContextFailed",
    "ContextTimeout",
    "ContextUnavailable",
    "DEFAULT_TIMEOUT",
    "HostedAt",
    "Transport",
    "context_map_of",
    "dto_name",
    "hosted_here",
    "hosting",
    "pack",
    "packed",
    "parse_address",
    "parse_context_map",
    "raised_as_itself",
    "unpack",
    "unpacked",
]
