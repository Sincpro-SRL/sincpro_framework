"""The published language: how a DTO travels between two services that run the same code.

    packed(CommandIssueInvoice(total=Decimal("10"), pdf=b"%PDF…"))  →  chunks of bytes, 1 MiB each
    unpacked(ChunkReader(chunks), CommandIssueInvoice)               →  CommandIssueInvoice(...)
    pack(value) / unpack(data, of)                                   the same, whole, for small values

Context: the DTO is dumped in pydantic's python mode — its values as Python keeps them, `bytes`
included — and packed with `pickle`; the receiver reads it through an allow-list of value types
and rebuilds the DTO with its own class, so it is validated there as it would be here. Only
values travel, never an object of the project, and a payload naming any class outside the
allow-list is refused while it is read — before anything is built. JSON was not chosen because
pydantic writes `bytes` as UTF-8, so a PDF fails, and base64 cannot be told from text on the way
back.

**Chunked, so size has no cap but memory.** `pickle` hands a large `bytes` value to its writer as
the object itself, so packing keeps references and cuts them into chunks one at a time; reading
fills each value with `readinto` straight from the chunks. The peak is the DTO itself plus a chunk
— several GiB travel as long as they fit in memory on both ends.
"""

import dataclasses
import io
import pickle
import sys
from collections.abc import Iterable, Iterator
from enum import Enum
from functools import lru_cache
from typing import Any, Protocol

from pydantic import BaseModel, TypeAdapter

ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        ("builtins", "set"),
        ("builtins", "frozenset"),
        ("builtins", "bytearray"),
        ("builtins", "complex"),
        ("datetime", "date"),
        ("datetime", "datetime"),
        ("datetime", "time"),
        ("datetime", "timedelta"),
        ("datetime", "timezone"),
        ("decimal", "Decimal"),
        ("uuid", "UUID"),
        ("zoneinfo", "ZoneInfo._unpickle"),
    }
)
"""The value types a payload may hold beyond what `pickle` writes natively — and enum members of
modules already imported here."""


class CannotTravel(Exception):
    """A payload named a class that does not travel between bounded contexts."""


class _Values(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Any:
        if (module, name) in ALLOWED:
            return super().find_class(module, name)
        found = getattr(sys.modules.get(module), name, None)
        if isinstance(found, type) and issubclass(found, Enum):
            return found
        raise CannotTravel(
            f"{module}.{name} cannot travel to another bounded context: only values do — "
            "builtins, bytes, dates, Decimal, UUID and enum members"
        )


@lru_cache(maxsize=512)
def _adapter(of: Any) -> TypeAdapter[Any]:
    return TypeAdapter(of)


CHUNK_SIZE = 1024 * 1024
"""Bytes per chunk on the wire — far below any transport's per-message limit."""


class Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...

    def readline(self, size: int = -1, /) -> bytes: ...


class _Pieces:
    """What `pickle` writes, kept as the objects it hands over — a large `bytes` value arrives as
    itself, never copied."""

    def __init__(self) -> None:
        self.pieces: list[Any] = []

    def write(self, data: Any) -> int:
        self.pieces.append(data)
        return len(data)


def _chunks(pieces: Iterable[Any]) -> Iterator[bytes]:
    """`pieces` cut and joined into chunks of `CHUNK_SIZE` bytes — the last one shorter, and one
    empty chunk when there is nothing at all."""
    pending = bytearray()
    sent = False
    for piece in pieces:
        view = memoryview(piece).cast("B")
        while len(view):
            room = CHUNK_SIZE - len(pending)
            pending += view[:room]
            view = view[room:]
            if len(pending) == CHUNK_SIZE:
                yield bytes(pending)
                pending.clear()
                sent = True
    if pending or not sent:
        yield bytes(pending)


def packed(value: Any) -> Iterator[bytes]:
    """`value` as chunks of bytes — a DTO by its values, as its own class would dump them."""
    python = (
        None if value is None else _adapter(type(value)).dump_python(value, mode="python")
    )
    written = _Pieces()
    pickle.Pickler(written, protocol=pickle.HIGHEST_PROTOCOL).dump(python)
    return _chunks(written.pieces)


def pack(value: Any) -> bytes:
    """`value` as bytes, whole — for what is small, like a request context."""
    return b"".join(packed(value))


class ChunkReader:
    """Chunks read as a file — what the unpickler reads from, filling each value in place."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = iter(chunks)
        self._current = memoryview(b"")

    def _filled(self) -> bool:
        while not len(self._current):
            following = next(self._chunks, None)
            if following is None:
                return False
            self._current = memoryview(following).cast("B")
        return True

    def readinto(self, buffer: Any) -> int:
        target = memoryview(buffer).cast("B")
        written = 0
        while written < len(target) and self._filled():
            taken = min(len(target) - written, len(self._current))
            target[written : written + taken] = self._current[:taken]
            self._current = self._current[taken:]
            written += taken
        return written

    def read(self, size: int = -1, /) -> bytes:
        if size < 0:
            rest = [bytes(self._current), *self._chunks]
            self._current = memoryview(b"")
            return b"".join(rest)
        into = bytearray(size)
        return bytes(into[: self.readinto(into)])

    def readline(self, size: int = -1, /) -> bytes:
        line = bytearray()
        while (size < 0 or len(line) < size) and self._filled():
            byte = self._current[:1].tobytes()
            self._current = self._current[1:]
            line += byte
            if byte == b"\n":
                break
        return bytes(line)


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


def unpacked(source: Readable, of: Any) -> Any:
    """What `source` holds — rebuilt as `of` when given, the plain values when not.

    1. The values read, refusing any class outside the allow-list (`CannotTravel`).
    2. Validated by `of`, as a value of it would be here.
    3. Final: every dataclass in it built through its constructor (see `_constructed`).
    """
    values = _Values(source).load()
    return values if of is None else _constructed(_adapter(of).validate_python(values))


def unpack(data: bytes, of: Any) -> Any:
    """`data`, whole, read back as `unpacked` reads a stream."""
    return unpacked(io.BytesIO(data), of)
