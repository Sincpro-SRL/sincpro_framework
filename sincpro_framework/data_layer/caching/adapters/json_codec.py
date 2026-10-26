"""`JsonCodec`: a value as JSON, validated on the way back.

    JsonCodec(ResponseBalance)          a DTO
    JsonCodec(dict[str, int])           anything pydantic can validate

Context: decoding validates — a value stored by an older deploy, in a shape this one no longer
reads, fails loudly instead of reaching the caller half-right.
"""

import hashlib
import json
from typing import Any

from pydantic import TypeAdapter


class JsonCodec[T]:
    """The `Codec` on pydantic's `TypeAdapter` — JSON bytes out, validated objects back."""

    def __init__(self, shape: type[T] | Any) -> None:
        """`shape` is any type pydantic validates; its JSON schema is hashed into `schema`."""
        self._adapter: TypeAdapter[T] = TypeAdapter(shape)
        written = json.dumps(self._adapter.json_schema(), sort_keys=True)
        self._schema = hashlib.sha256(written.encode()).hexdigest()[:12]

    @property
    def schema(self) -> str:
        """The first 12 hex characters of the sha256 of the shape's JSON schema — a field
        added, removed or retyped changes it, and with it every key.

            JsonCodec(dict[str, int]).schema  ->  "5b1e…" (12 hex characters)
        """
        return self._schema

    def encode(self, value: T) -> bytes:
        """`value` as JSON bytes, the way pydantic dumps the shape."""
        return self._adapter.dump_json(value)

    def decode(self, raw: bytes) -> T:
        """`raw` parsed and validated against the shape — a `ValidationError` when it is not
        this shape, which the cache counts as a miss."""
        return self._adapter.validate_json(raw)
