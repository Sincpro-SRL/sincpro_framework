"""`MemoryNumbering`: numbers without gaps held in the process — the `INumbering` of tests and of a
single process."""

from threading import Lock

from sincpro_framework.ddd.repositories.numbering import INumbering, refuse_no_count
from sincpro_framework.ddd.repositories.repository import refuse_writing_in_preview


class MemoryNumbering(INumbering):
    """Numbers kept in the process: for a test, where there is no rollback to give them back."""

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], int] = {}
        self._lock = Lock()

    def take(self, series: str, count: int = 1, scope: str = "") -> range:
        refuse_writing_in_preview("take")
        refuse_no_count(count)
        with self._lock:
            last = self._last.get((series, scope), 0) + count
            self._last[(series, scope)] = last
        return range(last - count + 1, last + 1)
