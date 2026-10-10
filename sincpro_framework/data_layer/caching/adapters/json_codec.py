"""`JsonCodec`: a value as JSON, validated on the way back.

    JsonCodec(ResponseBalance)          a DTO
    JsonCodec(dict[str, int])           anything pydantic can validate

Context: decoding validates — a value kept by an older deploy, in a shape this one no longer
reads, fails loudly instead of reaching the caller half-right.
"""

import hashlib
import json
from typing import Any

from pydantic import TypeAdapter


class JsonCodec[T]:
    def __init__(self, shape: type[T] | Any) -> None:
        self._adapter: TypeAdapter[T] = TypeAdapter(shape)
        written = json.dumps(self._adapter.json_schema(), sort_keys=True)
        self._schema = hashlib.sha256(written.encode()).hexdigest()[:12]

    @property
    def schema(self) -> str:
        return self._schema

    def encode(self, value: T) -> bytes:
        return self._adapter.dump_json(value)

    def decode(self, raw: bytes) -> T:
        return self._adapter.validate_json(raw)
