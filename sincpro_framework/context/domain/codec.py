"""`ContextCodec`: a context as bytes and back — what a store keeps and a message carries.

    PlainCodec()              text, numbers, booleans, lists — any language, the frontend
    TypedCodec(SIATContext)   the same codebase: enums, datetimes, DTOs come back as themselves
    PickleCodec()             the same codebase, any picklable value — never for bytes from outside

The implementations are `sincpro_framework.context.adapters.codecs`.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any


class ContextCodec(ABC):
    @abstractmethod
    def dumps(self, context: Mapping[str, Any]) -> bytes: ...

    @abstractmethod
    def loads(self, data: bytes) -> dict[str, Any]: ...
