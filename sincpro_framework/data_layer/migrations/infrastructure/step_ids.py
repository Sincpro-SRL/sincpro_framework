"""`StepIds`: the id a new step takes — a UUIDv7 in hex, so ids sort by when they were made."""

import os
import time
from threading import Lock


class StepIds:
    """Makes step ids, each after the last one this generator made — even within one
    millisecond, or when the clock steps back.

        ids = StepIds()
        ids.new()   ->  "01a16ec29864702f9f105f09cfbae1a5"
        ids.new()   ->  "01a16ec29864702f9f105f09cfbae1a6"   the same millisecond, +1

    Context: the last millisecond and counter it handed out are its own state, guarded by a
    lock, so one generator orders ids across threads; `Migrations` owns one.
    """

    def __init__(self) -> None:
        """No id made yet: the first one starts a fresh counter."""
        self._last = (0, 0)
        self._lock = Lock()

    def new(self) -> str:
        """A UUIDv7 in hex: 48 bits of Unix milliseconds, the version, 74 bits of counter, the
        variant — RFC 9562 §6.2, method 1.

        1. Read the clock in milliseconds.
        2. Under the lock: within the last millisecond (or when the clock stepped back) take the
           last millisecond and its counter + 1; otherwise start the counter at a random value
           with room to grow. Store the pair as the last one.
        Final: the bits laid out as a UUIDv7 — 32 hex digits, the 13th the version `7`; ids
        made later sort after, as text too.
        """
        milliseconds = time.time_ns() // 1_000_000
        with self._lock:
            last_milliseconds, last_counter = self._last
            if milliseconds <= last_milliseconds:
                milliseconds, counter = last_milliseconds, last_counter + 1
            else:
                counter = int.from_bytes(os.urandom(9)) >> 2
            self._last = (milliseconds, counter)
        value = (
            milliseconds << 80
            | 0x7 << 76
            | (counter >> 62) << 64
            | 0x2 << 62
            | counter & ((1 << 62) - 1)
        )
        return f"{value:032x}"
