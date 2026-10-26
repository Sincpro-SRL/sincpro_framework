"""`MemoryNumbering`: numbers without gaps held in the process — the `INumbering` of tests and of a
single process."""

from threading import Lock

from sincpro_framework.ddd.repositories.numbering import INumbering, refuse_no_count
from sincpro_framework.ddd.repositories.repository import refuse_writing_in_preview


class MemoryNumbering(INumbering):
    """Numbers held in the process: for a test, where there is no rollback to give them back."""

    def __init__(self) -> None:
        """No series yet: each starts at 1 the first time it is taken."""
        self._last: dict[tuple[str, str], int] = {}
        self._lock = Lock()

    def take(self, series: str, count: int = 1, scope: str = "") -> range:
        """Take `count` numbers of `series` within `scope` from a counter held under a lock.

        What this one does differently: the counter lives in a dict, not in a row, so nothing
        a unit of work rolls back gives the numbers back — a test that needs gapless numbers
        across a rollback runs against the database's numbering.

            numbering.take("F", count=3)   ->  range(1, 4)
            numbering.take("F")            ->  range(4, 5)
        """
        refuse_writing_in_preview("take")
        refuse_no_count(count)
        with self._lock:
            last = self._last.get((series, scope), 0) + count
            self._last[(series, scope)] = last
        return range(last - count + 1, last + 1)
