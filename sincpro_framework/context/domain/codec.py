"""`ContextCodec`: a context as bytes and back — what a store saves and a message carries.

    PlainCodec()              text, numbers, booleans, lists — any language, the frontend
    TypedCodec(SIATContext)   the same codebase: enums, datetimes, DTOs come back as themselves
    PickleCodec()             the same codebase, any picklable value — never for bytes from outside

The implementations are `sincpro_framework.context.adapters.codecs`.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any


class ContextCodec(ABC):
    """How a context becomes bytes and comes back — the one choice between reaching any reader
    (`PlainCodec`) and getting the same values back as themselves (`TypedCodec`,
    `PickleCodec`)."""

    @abstractmethod
    def dumps(self, context: Mapping[str, Any]) -> bytes:
        """`context` as bytes a store saves or a message carries — what `loads` reads
        back.

            PlainCodec().dumps({"tenant_id": "acme"})  ->  b'{"tenant_id":"acme"}'
        """

    @abstractmethod
    def loads(self, data: bytes) -> dict[str, Any]:
        """The context `data` was written from — as typed as this codec writes it: plain JSON
        values, or the values themselves.

            PlainCodec().loads(b'{"tenant_id":"acme"}')  ->  {"tenant_id": "acme"}
        """
