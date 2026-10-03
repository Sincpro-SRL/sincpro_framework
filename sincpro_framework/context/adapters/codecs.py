"""The codecs a context is written with.

    PlainCodec()                       {"tenant_id": "acme", "tenant_ids": ["acme", "beta"]}
    TypedCodec(SIATContext)            SIAT_ENV comes back a SIATEnvironment, not an int
    TypedCodec(SIATContext, keep_secrets=True)   a Secret written as its value, for a trusted store
    PickleCodec()                      any picklable value, the same codebase on both ends

**Plain** is for anything outside this codebase — another language, the frontend, a broker anyone
reads: only the travelling keys (text, numbers, booleans, lists of them).
**Typed** is for the same codebase: the schema a project already declares — a `TypedDict` or a DTO —
validates what comes back and gives each value its type; keys the schema does not name are left out.
A `Secret` is written masked unless `keep_secrets` says the store may hold it.
**Pickle** carries anything picklable; it runs code when it loads, so it never reads bytes that came
from outside the deployment.
"""

import json
import pickle
from collections.abc import Mapping
from typing import Any

from pydantic import Secret, TypeAdapter

from sincpro_framework.context.domain.codec import ContextCodec
from sincpro_framework.context.domain.keys import standardized, travelling


class PlainCodec(ContextCodec):
    def dumps(self, context: Mapping[str, Any]) -> bytes:
        return json.dumps(travelling(context), separators=(",", ":")).encode()

    def loads(self, data: bytes) -> dict[str, Any]:
        found = json.loads(data)
        return standardized(found) if isinstance(found, dict) else {}


class TypedCodec(ContextCodec):
    def __init__(self, schema: type, keep_secrets: bool = False) -> None:
        self.schema = schema
        self.keep_secrets = keep_secrets
        self._adapter: TypeAdapter[Any] = TypeAdapter(schema)

    def dumps(self, context: Mapping[str, Any]) -> bytes:
        named = {key: value for key, value in context.items() if isinstance(key, str)}
        validated = self._adapter.validate_python(named)
        if not self.keep_secrets:
            return self._adapter.dump_json(validated)
        written = self._adapter.dump_python(validated, mode="json")
        fields = validated if isinstance(validated, Mapping) else validated.__dict__
        for key, value in fields.items():
            if isinstance(value, Secret):
                written[key] = value.get_secret_value()
        return json.dumps(written).encode()

    def loads(self, data: bytes) -> dict[str, Any]:
        loaded = self._adapter.validate_json(data)
        return dict(loaded) if isinstance(loaded, Mapping) else dict(loaded.__dict__)


class PickleCodec(ContextCodec):
    def dumps(self, context: Mapping[str, Any]) -> bytes:
        return pickle.dumps(dict(context), protocol=pickle.HIGHEST_PROTOCOL)

    def loads(self, data: bytes) -> dict[str, Any]:
        found = pickle.loads(
            data
        )  # noqa: S301 - the same deployment wrote it; see the module
        return dict(found) if isinstance(found, Mapping) else {}
