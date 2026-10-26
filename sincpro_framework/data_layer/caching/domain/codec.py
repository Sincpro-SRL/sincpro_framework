"""`Codec`: how a value becomes the bytes a shared store holds, and back.

Context: only a shared store needs one — a `Cache` with no store holds the objects themselves.
`adapters/json_codec.py` is the one the framework ships.
"""

from typing import Protocol


class Codec[T](Protocol):
    """The port: a value to bytes and back, with a fingerprint of the shape it encodes."""

    @property
    def schema(self) -> str:
        """A fingerprint of the shape values are encoded in — part of the key, so a deploy that
        changes the shape never reads what an older one stored."""
        ...

    def encode(self, value: T) -> bytes:
        """The bytes the shared store holds for `value`."""
        ...

    def decode(self, raw: bytes) -> T:
        """Raise when `raw` is not this shape — the cache counts that as a miss."""
        ...
