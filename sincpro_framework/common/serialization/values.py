"""Any value as JSON values and back — what every message the framework sends is written with.

**One JSON document.** A value is written as its class dumps it, `bytes` as `{"$bytes": "<base64>"}`
so binary is never mistaken for text. It is read back with the receiver's own class: fields by name,
a field it does not know ignored, a missing one defaulted, a missing required one a
`ValidationError` naming it.
"""

import base64
import dataclasses
import json
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Secret, SecretBytes, SecretStr, TypeAdapter
from pydantic_core import to_jsonable_python

BYTES = "$bytes"


@lru_cache(maxsize=512)
def _adapter(of: Any) -> TypeAdapter[Any]:
    return TypeAdapter(of)


def values_of(value: Any) -> Any:
    """`value` as JSON values: a DTO or dataclass as its class dumps it, `bytes` tagged, a secret
    as what it holds, a `Decimal`, a date, a `UUID` or an enum member as JSON writes it.
    Anything else that JSON cannot write raises."""
    if isinstance(value, bytes | bytearray | memoryview):
        return {BYTES: base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, Secret | SecretStr | SecretBytes):
        return values_of(value.get_secret_value())
    if isinstance(value, BaseModel) or (
        dataclasses.is_dataclass(value) and not isinstance(value, type)
    ):
        return values_of(_adapter(type(value)).dump_python(value, mode="python"))
    if isinstance(value, Mapping):
        return {str(key): values_of(one) for key, one in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [values_of(one) for one in value]
    return to_jsonable_python(value)


def _restored(written: dict[str, Any]) -> Any:
    if len(written) == 1 and isinstance(written.get(BYTES), str):
        return base64.b64decode(written[BYTES])
    return written


def read_values(written: Any) -> Any:
    """What `values_of` wrote, from JSON text or already parsed — each tagged `bytes` back as
    bytes."""
    if isinstance(written, str | bytes | bytearray):
        return json.loads(written, object_hook=_restored)
    return _walked(written)


def _walked(written: Any) -> Any:
    if isinstance(written, Mapping):
        return _restored({str(key): _walked(one) for key, one in written.items()})
    if isinstance(written, list):
        return [_walked(one) for one in written]
    return written


def _held(instance: Any) -> dict[str, Any]:
    """A dataclass instance's field values, read without its class's descriptors — an instance
    built outside its constructor answers them from `__dict__` alone."""
    held = getattr(instance, "__dict__", None)
    if held is not None:
        return held
    return {one.name: getattr(instance, one.name) for one in dataclasses.fields(instance)}


def _constructed(value: Any) -> Any:
    """`value` with every dataclass in it built through its own constructor.

    Context: pydantic fills a dataclass's `__dict__` without calling `__init__`, so a class whose
    `__init__` does work — SQLAlchemy instruments a mapped aggregate's to give it instance state,
    change tracking takes its baseline in `__post_init__` — came back half built. Rebuilt through
    the constructor, it is what the class makes of those values here.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        held, fields = _held(value), dataclasses.fields(value)
        built = type(value)(
            **{
                one.name: _constructed(held[one.name])
                for one in fields
                if one.init and one.name in held
            }
        )
        for one in fields:
            if not one.init and one.name in held:
                object.__setattr__(built, one.name, _constructed(held[one.name]))
        return built
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            if name in value.__dict__:
                object.__setattr__(value, name, _constructed(value.__dict__[name]))
        return value
    if isinstance(value, list):
        return [_constructed(one) for one in value]
    if isinstance(value, tuple):
        return tuple(_constructed(one) for one in value)
    if isinstance(value, dict):
        return {key: _constructed(one) for key, one in value.items()}
    return value


def rebuilt(values: Any, of: Any) -> Any:
    """`values` rebuilt as `of` — as they are when `of` is `None`."""
    if of is None:
        return values
    return _constructed(_adapter(of).validate_python(values))


def pack(value: Any) -> bytes:
    """`value` as JSON — see `values_of`."""
    return json.dumps(values_of(value), separators=(",", ":")).encode()


def unpack(data: bytes | str, of: Any) -> Any:
    """What `pack` wrote, rebuilt as `of` — see `rebuilt`."""
    return rebuilt(read_values(data), of)
