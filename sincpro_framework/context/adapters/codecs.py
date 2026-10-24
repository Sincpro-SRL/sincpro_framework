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
from sincpro_framework.context.domain.keys import travelling
from sincpro_framework.sincpro_logger import logger


class PlainCodec(ContextCodec):
    """JSON of the travelling keys — what any reader understands, at the cost of types: a
    datetime goes as text and comes back as text."""

    def dumps(self, context: Mapping[str, Any]) -> bytes:
        """Compact JSON of `travelling(context)` — an object, a connection or a callable is
        left out (warned once per key), a `Secret` written as the value it holds.

            PlainCodec().dumps({"tenant_id": "acme", "db": engine})
            ->  b'{"tenant_id":"acme"}'
        """
        return json.dumps(travelling(context), separators=(",", ":")).encode()

    def loads(self, data: bytes) -> dict[str, Any]:
        """The JSON object `data` holds — anything other than an object (a list, a number)
        reads as an empty context rather than failing the reader."""
        found = json.loads(data)
        return found if isinstance(found, dict) else {}


class TypedCodec(ContextCodec):
    """JSON validated by the project's context schema both ways — each value comes back with
    its type: an enum as the enum, a datetime as a datetime, a `Secret` as a `Secret`."""

    def __init__(self, schema: type) -> None:
        """Hold `schema` — a `TypedDict` or a DTO — and the pydantic adapter built from it
        once, not per message."""
        self.schema = schema
        self._adapter: TypeAdapter[Any] = TypeAdapter(schema)

    def dumps(self, context: Mapping[str, Any]) -> bytes:
        """The context validated by the schema, written as JSON.

        Context: pydantic dumps a `Secret` masked (`"**********"`); a store must keep what the
        context holds, so each `Secret` is written back as its value — and `loads` turns it
        into a `Secret` again because the schema says so.

        1. Only the keys that are text are offered to the schema.
        2. The schema validates them and dumps them in JSON mode.
        3. Each field the schema typed as a `Secret` is written as the value it holds.
        Final: the JSON bytes.

            TypedCodec(Ctx).dumps({"since": date(2026, 1, 2), "token": Secret("t")})
            ->  b'{"since": "2026-01-02", "token": "t"}'      (Ctx types since and token)
        """
        named = {key: value for key, value in context.items() if isinstance(key, str)}
        validated = self._adapter.validate_python(named)
        written = self._adapter.dump_python(validated, mode="json")
        fields = validated if isinstance(validated, Mapping) else validated.__dict__
        for key, value in fields.items():
            if isinstance(value, Secret):
                written[key] = value.get_secret_value()
        return json.dumps(written).encode()

    def loads(self, data: bytes) -> dict[str, Any]:
        """The JSON validated by the schema into typed values — a DTO's fields read as a plain
        mapping, so every codec answers the same shape."""
        loaded = self._adapter.validate_json(data)
        return dict(loaded) if isinstance(loaded, Mapping) else dict(loaded.__dict__)


PICKLED_BY_KEY = "sincpro.pickled-by-key"
"""What marks a context `PickleCodec` wrote one value at a time."""


class PickleCodec(ContextCodec):
    """Pickle, one value at a time — any picklable value round-trips as itself between
    processes of the same deployment; one value that cannot be written or read costs only its
    own key.
    """

    def dumps(self, context: Mapping[str, Any]) -> bytes:
        """Each value pickled on its own — one that cannot be (a lock, a connection) stays in this
        process, said in a warning.

            PickleCodec().dumps({"tenant_id": "acme", "lock": threading.Lock()})
                ->  pickled ("sincpro.pickled-by-key", {"tenant_id": b"\\x80\\x05…"})
                    and a warning: "context key 'lock' cannot be pickled and stays here: …"

        The envelope is what `loads` recognises; every value inside is bytes of its own."""
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
        out, as if it never existed, said in a warning naming the key and what is missing.

        1. Unpickle the envelope.
        2. Not the `PICKLED_BY_KEY` envelope — bytes pickled whole — is read as the mapping it
           holds, or as an empty context.
        3. Each value unpickled on its own; one that fails is warned about and skipped.
        Final: the values that could be read.

            codec.loads(codec.dumps({"tenant_id": "acme", "invoice": Invoice(...)}))
                ->  {"tenant_id": "acme", "invoice": Invoice(...)}
                ->  {"tenant_id": "acme"}, in a process without `Invoice`'s module

        Only bytes this deployment wrote are read: unpickling runs the code they name.
        """
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
