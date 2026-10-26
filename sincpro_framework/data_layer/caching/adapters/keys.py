"""What two equal parameters always hash to — the key of anything cached or stored.

    KeyEncoder.key_of("tenant", token)          →  'a3f0…'  (32 hex characters)
    KeyEncoder.key_of(QueryBalance(customer_id="c1"), {"tenant_id": "acme"})

Context: a value is written canonically before hashing — a DTO by the fields it sets apart from
their defaults, a decimal by its value, a date in ISO, a mapping by its sorted keys, a set sorted —
so `Decimal("1.0")` and `Decimal("1")` are one key, dict ordering never makes two, and a field
added later with a default leaves the keys already written unchanged. Always hashed: a token, an email or an id used as a key is
never readable in the store's key names.
"""

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel


class KeyEncoder:
    """Writes parameters canonically and hashes them — what every key of caching and
    idempotency is made of.

        KeyEncoder.key_of("rates", "USD")       ->  "9f2c…" (32 hex)
        KeyEncoder.canonical(Decimal("1.50"))   ->  "1.5"
    """

    length = 32
    """Hex characters of a key — 128 bits of sha256, so two different parameters never
    collide."""

    @classmethod
    def canonical(cls, value: Any) -> Any:
        """A value written the one way two equal values are always written.

            canonical({"b": Decimal("1.50"), "a": {3, 1, 2}, "d": date(2026, 1, 31),
                       "t": (1, "x")})
                ->  {"a": [1, 2, 3], "b": "1.5", "d": "2026-01-31", "t": [1, "x"]}
            canonical(QueryBalance(customer_id="c1"))   ->  {"customer_id": "c1"}  # defaults out

        Recursive — a DTO by the fields it sets apart from their defaults, an Enum by its value;
        anything else it does not know is answered as it is, for `json.dumps` to write."""
        match value:
            case BaseModel():
                return cls.canonical(value.model_dump(exclude_defaults=True))
            case Decimal():
                return str(value.normalize())
            case datetime() | date():
                return value.isoformat()
            case Enum():
                return cls.canonical(value.value)
            case Mapping():
                return {str(key): cls.canonical(item) for key, item in sorted(value.items())}
            case set() | frozenset():
                return sorted((cls.canonical(item) for item in value), key=json.dumps)
            case list() | tuple():
                return [cls.canonical(item) for item in value]
        return value

    @classmethod
    def key_of(cls, *parts: Any) -> str:
        """The 32-hex key `parts` always hash to — equal parameters, one key, in any process.

            KeyEncoder.key_of("rates", "USD")                       ->  "9f2c…" (32 hex)
            KeyEncoder.key_of("rates", Decimal("1.0")) == KeyEncoder.key_of("rates", Decimal("1"))
                ->  True

        1. The parts written canonically, as one list, to JSON with sorted keys — what JSON
           does not know is written by `str`.
        Final: the first `length` hex characters of its sha256.
        """
        written = json.dumps(cls.canonical(list(parts)), sort_keys=True, default=str)
        return hashlib.sha256(written.encode()).hexdigest()[: cls.length]
