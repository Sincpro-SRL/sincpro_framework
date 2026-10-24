"""The unit of work of `MemoryRepository`: what a block read and wrote, put back when it raises, and
the rows it claims until it ends.

    with repository.context() as unit:
        invoice = unit.get(Invoice, invoice_id, for_update=True)    claimed until the block ends
        invoice.pay()
        unit.save(invoice)
        unit.after_commit(lambda: notify(invoice))
    an exception inside                →  every record it touched as it was before the block

It keeps the promises of the database's unit of work that a Feature can see, so a Feature tested
here behaves as it does on Postgres:

- **Everything or nothing.** Each record is copied the first time the block is handed it or writes
  it; a block that raises puts every one back, and what it added is gone.
- **What is written is what was saved.** A record changed in the block but never saved is put back
  when the block ends, as the engine leaves it unwritten.
- **A row claimed is held.** `for_update` takes the record until the block ends — another block,
  on another thread, waits; `skip_locked` passes over it; `nowait` refuses at once (`TimedOut`).
- **Nesting joins.** A `context()` inside another is the same unit.

What it does not promise is isolation: one block sees what another wrote before it commits — the
store is one dict. A test of a race claims its rows, as production does.
"""

import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from sincpro_framework.data_layer import exceptions
from sincpro_framework.sincpro_logger import logger

ABSENT = object()
"""What a record was before the block when the block added it."""
WAIT_FOR_A_ROW = 30.0
"""Seconds a claim waits for another block's row before it gives up — a test that hangs on a
lock nobody releases fails instead."""

type Key = tuple[type, Any]


@dataclass
class MemoryUnit:
    """One block of `context()`: the records as they were before it, the ones it saved, the rows it
    holds and what waits for its end."""

    before: dict[Key, Any] = field(default_factory=dict)
    saved: set[Key] = field(default_factory=set)
    claimed: set[Key] = field(default_factory=set)
    after_commit: list[Callable[[], Any]] = field(default_factory=list)
    after_rollback: list[Callable[[], Any]] = field(default_factory=list)

    def remember(self, key: Key, held: Any) -> None:
        """`held` as it is now, the first time the block reaches it."""
        if key not in self.before:
            self.before[key] = ABSENT if held is None else deepcopy(held)


def restored(stored: dict[type, dict[Any, Any]], key: Key, before: Any) -> None:
    """The record under `key` as it was: the object the store holds keeps its identity and takes
    the values it had, so whoever holds it sees them too; one the block added is gone."""
    kind, identity = key
    held = stored.setdefault(kind, {})
    if before is ABSENT:
        held.pop(identity, None)
        return
    current = held.get(identity)
    if current is not None and type(current) is type(before) and hasattr(current, "__dict__"):
        current.__dict__.clear()
        current.__dict__.update(before.__dict__)
        return
    held[identity] = before


def ran(callbacks: list[Callable[[], Any]], moment: str) -> None:
    """Each callback in turn; one that raises is logged and the others still run — what ended
    stands."""
    for callback in callbacks:
        try:
            callback()
        except Exception:  # noqa: BLE001 - the unit already ended
            logger.exception(f"an {moment} callback failed")


class RowLocks:
    """The rows each block holds, across the threads of the process."""

    def __init__(self) -> None:
        self._held: dict[Key, MemoryUnit] = {}
        self._changed = threading.Condition()

    def claim(self, unit: MemoryUnit, key: Key, skip_locked: bool, nowait: bool) -> bool:
        """Whether `unit` holds the row now: it waits for another block's, unless told to pass
        over it (`False`) or to refuse at once."""
        with self._changed:
            waited = 0.0
            while self._held.get(key) not in (None, unit):
                if skip_locked:
                    return False
                if nowait or waited >= WAIT_FOR_A_ROW:
                    raise exceptions.TimedOut(
                        f"{key[0].__name__} {key[1]} is held by another unit of work"
                    )
                self._changed.wait(0.05)
                waited += 0.05
            self._held[key] = unit
            unit.claimed.add(key)
            return True

    def release(self, unit: MemoryUnit) -> None:
        with self._changed:
            for key in unit.claimed:
                if self._held.get(key) is unit:
                    del self._held[key]
            self._changed.notify_all()
