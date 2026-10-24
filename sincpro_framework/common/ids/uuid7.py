"""The ids the framework mints: UUID v7, written as 32 hex characters — every entity's, every
event's and every execution's.

    new_entity_id()            '01a0fb9a7e0174f8b13fa739ccbdcd53'
    moment_of(that)            datetime(2026, 10, 2, …, tzinfo=UTC)

**Sortable, because its leading 48 bits are a millisecond timestamp** — ordering by id *is*
ordering by time, and the moment something began is read off its id instead of being kept twice.
Unique across machines with no coordination.

Python 3.12 and 3.13 have no `uuid.uuid7`; `uuid7()` below is RFC 9562 by hand and defers to the
standard library where it exists. Only the standard library, so the bus mints ids without
loading the domain layer.
"""

import os
import threading
import time
import uuid
from datetime import UTC, datetime

_MINTING = threading.Lock()
_last_millisecond = 0
_counter = 0


def uuid7() -> uuid.UUID:
    """A time-ordered UUID (RFC 9562 version 7).

        layout  48 bits unix milliseconds · 4 bits version · 12 bits counter within the ms
                2 bits variant · 62 bits random

        uuid7()   ->  UUID('01a0fb9a-7e01-74f8-b13f-a739ccbdcd53')
        uuid7()   ->  UUID('01a0fb9a-7e01-74f9-…')   # same millisecond: the counter + 1

    >>> uuid7().version
    7

    1. Python's own `uuid.uuid7`, where it exists, is the answer.
    2. The current unix milliseconds, and 80 random bits — 62 of them the random tail.
    3. Under a lock, the 12-bit counter:
       3.1 the same millisecond as the last id, the counter not full: the counter + 1;
       3.2 else the millisecond is remembered and the counter starts at a random point of
           its lower 11 bits, so it has room to count up.
    Final: milliseconds, version 7, counter, variant `10` and random tail packed in a UUID.
    """
    native = getattr(uuid, "uuid7", None)
    if native is not None:
        return native()

    milliseconds = time.time_ns() // 1_000_000
    entropy = int.from_bytes(os.urandom(10), "big")
    rand_b = entropy & ((1 << 62) - 1)
    with _MINTING:
        # RFC 9562 method 1: within one millisecond the 12 bits are a counter, so ids minted
        # in order sort in order; a new millisecond starts the counter at a random point.
        global _last_millisecond, _counter
        if milliseconds == _last_millisecond and _counter < 0xFFF:
            _counter += 1
        else:
            _last_millisecond = milliseconds
            _counter = (entropy >> 62) & 0x7FF
        rand_a = _counter
    value = (milliseconds << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def new_entity_id() -> str:
    """A fresh UUID v7 as 32 hex characters — what an entity, an event or an execution gets when
    nobody passes one.

    >>> len(new_entity_id())
    32
    """
    return uuid7().hex


def moment_of(identity: str) -> datetime | None:
    """When a UUID v7 was minted, read off its leading 48 bits; `None` for an id that is not one
    — a project's own format, a UUID v4.

        moment_of("01a0fb9a7e0174f8b13fa739ccbdcd53")
            ->  datetime(2026, 10, 2, 7, 53, 21, 153000, tzinfo=UTC)
        moment_of(uuid.uuid4().hex)   ->  None    # version 4
        moment_of("ds_01")            ->  None    # not a UUID

    >>> moment_of(new_entity_id()).year >= 2026
    True
    >>> moment_of("ds_01") is None
    True

    The moment is UTC, to the millisecond the id was minted at.
    """
    try:
        parsed = uuid.UUID(identity)
    except (ValueError, TypeError, AttributeError):
        return None
    if parsed.version != 7:
        return None
    return datetime.fromtimestamp((parsed.int >> 80) / 1000, UTC)
