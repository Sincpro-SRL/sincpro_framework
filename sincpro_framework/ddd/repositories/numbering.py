"""`Numbering`: numbers without gaps, per series and scope — a fiscal invoice's correlative.

    with self.repository.context() as unit:
        numbers = self.numbering.take("F", count=len(invoices), scope="branch-1/2026")
        for invoice, number in zip(invoices, numbers):
            invoice.number = number
        unit.save(invoices)

**Taken inside the transaction that uses them, and given back by its rollback.** A database
sequence is fast and never gapless: `nextval` is not undone by a rollback, so a failed invoice
leaves a hole a tax authority asks about. A counter row is gapless because it is written in the
same transaction as what carries the numbers — committed with them, or undone with them.

**What it costs.** Two transactions taking from one series wait for each other: the counter's
row is held until the first commits. Take the numbers last, keep that transaction short, and give
each point of sale or branch its own series — `scope` — so two tills never wait on one row.
Several numbers at once are one write, not several.
"""

from abc import ABC, abstractmethod
from threading import Lock

from sincpro_framework.ddd.exceptions import ContractViolation


def refuse_no_count(count: int) -> None:
    if count < 1:
        raise ContractViolation(f"take at least one number, not {count}")


class Numbering(ABC):
    """Gapless numbers per series and scope."""

    @abstractmethod
    def take(self, series: str, count: int = 1, scope: str = "") -> range:
        """The next `count` numbers of this series in this scope, in order — the first ever
        taken is 1."""

    def next_number(self, series: str, scope: str = "") -> int:
        """The next number of this series in this scope."""
        return self.take(series, 1, scope).start


class MemoryNumbering(Numbering):
    """Numbers kept in the process: for a test, where there is no rollback to give them back."""

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], int] = {}
        self._lock = Lock()

    def take(self, series: str, count: int = 1, scope: str = "") -> range:
        refuse_no_count(count)
        with self._lock:
            last = self._last.get((series, scope), 0) + count
            self._last[(series, scope)] = last
        return range(last - count + 1, last + 1)
