"""The ids the framework mints: UUID v7, written as 32 hex characters — every entity's, every
event's and every execution's.

    new_entity_id()            '01a0fb9a7e0174f8b13fa739ccbdcd53'
    moment_of(that)            datetime(2026, 10, 2, …, tzinfo=UTC)

**Sortable, because its leading 48 bits are a millisecond timestamp** — ordering by id *is*
ordering by time, and the moment something began is read off its id instead of being stored twice.
Unique across machines with no coordination.

**Python 3.14 mints them; before it, `rfc9562_uuid7` does.** `uuid7` is the standard library's
where it has one and this module's otherwise — chosen once, by version, as a backport is
(`tomllib`/`tomli`): the global `uuid` module is never patched, so a library that asks it for
`uuid7` hears the truth. Only the standard library, so the bus mints ids without loading the
domain layer.

**Two counters, both RFC 9562, both monotonic within a process.** This module counts in the 12
bits after the version (method 1 of RFC 9562 §6.2), Python 3.14 in 42; each borrows the next
millisecond when its counter is full and never counts back when the clock does. Across processes
no UUID v7 orders two ids of the same millisecond — the bits after the timestamp are a counter
or random —, so ids of two Python versions mixed lose no guarantee they had.

**32 hex, not the 36 of the standard's text.** RFC 9562 writes a UUID with dashes; the framework
stores and sends it without them, in every table, draft, event and client (`new_entity_id`). A
key that takes the standard's spellings — a `uuid.UUID`, dashes, capitals — reads them into this
one (`EntityID`); a context's key that keeps the standard's own is `EntityKey[E, UUID]`.
"""

import os
import sys
import threading
import time
import uuid
from datetime import UTC, datetime

_MINTING = threading.Lock()
_last_millisecond = 0
_counter = 0

COUNTER_FULL = 0xFFF
"""The last value the 12 bits after the version hold."""


def rfc9562_uuid7() -> uuid.UUID:
    """A time-ordered UUID (RFC 9562 version 7), by hand — what `uuid7` is before Python 3.14.

        layout  48 bits unix milliseconds · 4 bits version · 12 bits counter within the ms
                2 bits variant · 62 bits random

        rfc9562_uuid7()   ->  UUID('01a0fb9a-7e01-74f8-b13f-a739ccbdcd53')
        rfc9562_uuid7()   ->  UUID('01a0fb9a-7e01-74f9-…')   # same millisecond: the counter + 1

    >>> rfc9562_uuid7().version
    7

    1. The current unix milliseconds, and 80 random bits — 62 of them the random tail.
    2. Under a lock, the millisecond and the 12-bit counter (RFC 9562 §6.2, method 1):
       2.1 a millisecond later than the last id's: remembered, and the counter starts at a
           random point of its lower 11 bits, so it has room to count up;
       2.2 the same millisecond, or an earlier one — a clock moved back —: the last id's
           millisecond is kept and the counter + 1;
       2.3 the counter full: the next millisecond is borrowed and the counter starts again, so
           the id is still greater than the last.
    Final: milliseconds, version 7, counter, variant `10` and random tail packed in a UUID.
    """
    milliseconds = time.time_ns() // 1_000_000
    entropy = int.from_bytes(os.urandom(10), "big")
    rand_b = entropy & ((1 << 62) - 1)
    start = (entropy >> 62) & 0x7FF
    with _MINTING:
        global _last_millisecond, _counter
        if milliseconds > _last_millisecond:
            _last_millisecond, _counter = milliseconds, start
        elif _counter < COUNTER_FULL:
            _counter += 1
        else:
            _last_millisecond, _counter = _last_millisecond + 1, start
        milliseconds, rand_a = _last_millisecond, _counter
    value = (milliseconds << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


if sys.version_info >= (3, 14):
    from uuid import uuid7
else:
    # The backport, under the standard library's name: on this Python it is `uuid7`.
    uuid7 = rfc9562_uuid7


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

    The moment is UTC, to the millisecond the id was minted at — the same 48 bits whichever
    counter minted it.
    """
    try:
        parsed = uuid.UUID(identity)
    except (ValueError, TypeError, AttributeError):
        return None
    if parsed.version != 7:
        return None
    return datetime.fromtimestamp((parsed.int >> 80) / 1000, UTC)
