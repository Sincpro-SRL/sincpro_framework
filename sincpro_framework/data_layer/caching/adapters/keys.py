"""What two equal parameters always hash to — the key of anything kept.

    key_of("tenant", token)                     →  'a3f0…'  (32 hex characters)
    key_of(QueryBalance(customer_id="c1"), {"tenant_id": "acme"})

Context: a value is written canonically before hashing — a DTO by the fields it sets apart from
their defaults, a decimal by its value, a date in ISO, a mapping by its sorted keys, a set sorted —
so `Decimal("1.0")` and `Decimal("1")` are one key, dict ordering never makes two, and a field
added later with a default keeps the keys already written. Always hashed: a token, an email or an id used as a key is
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

KEY_LENGTH = 32
"""Hex characters of a key — 128 bits of sha256, so two different parameters never collide."""


def canonical(value: Any) -> Any:
    """A value written the one way two equal values are always written."""
    match value:
        case BaseModel():
            return canonical(value.model_dump(exclude_defaults=True))
        case Decimal():
            return str(value.normalize())
        case datetime() | date():
            return value.isoformat()
        case Enum():
            return canonical(value.value)
        case Mapping():
            return {str(key): canonical(item) for key, item in sorted(value.items())}
        case set() | frozenset():
            return sorted((canonical(item) for item in value), key=json.dumps)
        case list() | tuple():
            return [canonical(item) for item in value]
    return value


def key_of(*parts: Any) -> str:
    written = json.dumps(canonical(list(parts)), sort_keys=True, default=str)
    return hashlib.sha256(written.encode()).hexdigest()[:KEY_LENGTH]
