"""The codecs a context is written with.

    PlainCodec()                       {"tenant_id": "acme", "tenant_ids": ["acme", "beta"]}
    TypedCodec(SIATContext)            SIAT_ENV comes back a SIATEnvironment, TOKEN a Secret
    PickleCodec()                      any picklable value, the same codebase on both ends

**Plain** is for anything outside this codebase — another language, the frontend, a broker anyone
reads: only the travelling keys (text, numbers, booleans, lists of them).
**Typed** is for the same codebase: the schema a project already declares — a `TypedDict` or a DTO —
validates what comes back and gives each value its type; keys the schema does not name are left out.
A `Secret` is written as its value and comes back a `Secret` — what the context holds is the
project's decision, so a store keeps it as it is.
**Pickle** carries anything picklable, each value on its own: a reader that has the value's class
gets the object; one that lacks it reads the context without that key — as if it never existed, said
in a warning — never a context that fails whole. It runs code when it loads, so it never reads bytes
that came from outside the deployment.
"""

import json
import pickle
from collections.abc import Mapping
from typing import Any

from pydantic import Secret, TypeAdapter

from sincpro_framework.context.domain.codec import ContextCodec
from sincpro_framework.context.domain.keys import standardized, travelling
from sincpro_framework.sincpro_logger import logger


class PlainCodec(ContextCodec):
    def dumps(self, context: Mapping[str, Any]) -> bytes:
        return json.dumps(travelling(context), separators=(",", ":")).encode()

    def loads(self, data: bytes) -> dict[str, Any]:
        found = json.loads(data)
        return standardized(found) if isinstance(found, dict) else {}


class TypedCodec(ContextCodec):
    def __init__(self, schema: type) -> None:
        self.schema = schema
        self._adapter: TypeAdapter[Any] = TypeAdapter(schema)

    def dumps(self, context: Mapping[str, Any]) -> bytes:
        named = {key: value for key, value in context.items() if isinstance(key, str)}
        validated = self._adapter.validate_python(named)
        written = self._adapter.dump_python(validated, mode="json")
        fields = validated if isinstance(validated, Mapping) else validated.__dict__
        for key, value in fields.items():
            if isinstance(value, Secret):
                written[key] = value.get_secret_value()
        return json.dumps(written).encode()

    def loads(self, data: bytes) -> dict[str, Any]:
        loaded = self._adapter.validate_json(data)
        return dict(loaded) if isinstance(loaded, Mapping) else dict(loaded.__dict__)


PICKLED_BY_KEY = "sincpro.pickled-by-key"
"""What marks a context `PickleCodec` wrote one value at a time."""


class PickleCodec(ContextCodec):
    def dumps(self, context: Mapping[str, Any]) -> bytes:
        """Each value pickled on its own — one that cannot be (a lock, a connection) stays in this
        process, said in a warning."""
        written: dict[str, bytes] = {}
        for key, value in context.items():
            try:
                written[key] = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
            except Exception as error:
                logger.warning(
                    f"context key {key!r} cannot be pickled and stays here: {error}"
                )
        return pickle.dumps((PICKLED_BY_KEY, written), protocol=pickle.HIGHEST_PROTOCOL)

    def loads(self, data: bytes) -> dict[str, Any]:
        """Each value as its class rebuilds it — a value whose class this process lacks is left
        out, as if it never existed, said in a warning naming the key and what is missing."""
        found = pickle.loads(
            data
        )  # noqa: S301 - the same deployment wrote it; see the module
        if not (isinstance(found, tuple) and len(found) == 2 and found[0] == PICKLED_BY_KEY):
            return dict(found) if isinstance(found, Mapping) else {}
        read: dict[str, Any] = {}
        for key, value in found[1].items():
            try:
                read[key] = pickle.loads(value)  # noqa: S301 - see the module
            except Exception as error:
                logger.warning(
                    f"context key {key!r} could not be read here and is left out — "
                    f"{type(error).__name__}: {error}"
                )
        return read
