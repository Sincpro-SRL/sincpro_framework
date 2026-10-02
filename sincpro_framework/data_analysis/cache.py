"""`QueryCache`: what was read is never read again.

    cache = QueryCache(max_rows=2_000_000)
    first = cache.fetch(repository, InvoiceLine, posted)            # one page read
    more = cache.fetch(repository, InvoiceLine, posted, pages=3)    # only pages 2 and 3 read
    everything = cache.fetch_all(repository, InvoiceLine, posted)   # the rest, then complete
    cache.invalidate(InvoiceLine)                                   # after a write, when it matters

Context: a read is kept under the repository's fingerprint of it — the filter, the order, the
mask and the scope, never the page — so the same question with another page continues what is
held, and a narrower one is answered by `DataFrame.narrow` on a complete frame. Pages are read
with the repository's cursor and without a count, and appended in order. What is held is not
refreshed on its own: the process that writes knows when to `invalidate`. Past `max_rows`, the
reads used longest ago are let go. One cache serves many threads; two that miss the same page at
once may both read it.
"""

import threading
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sincpro_framework.data_analysis.frame import DataFrame
from sincpro_framework.ddd.criteria import CountMode, Criteria
from sincpro_framework.ddd.entity.entity_collection import identity_name, model_and_collection
from sincpro_framework.ddd.entity.model_meta import (
    FieldType,
    annotations_of,
    describe_class,
    logical_type,
    without_optional,
)
from sincpro_framework.ddd.repositories import IRepository

FRAME_TYPES = {
    FieldType.TEXT: "string",
    FieldType.INTEGER: "integer",
    FieldType.NUMBER: "number",
    FieldType.BOOLEAN: "boolean",
    FieldType.DATE: "date",
    FieldType.DATETIME: "datetime",
}


@dataclass
class _Held:
    model: type
    frame: DataFrame
    pages: int


def _column_type(annotation: Any) -> str:
    """Context: the model says `number` for a decimal too; a frame keeps a decimal exact."""
    if without_optional(annotation) is Decimal:
        return "decimal"
    return FRAME_TYPES.get(logical_type(annotation), "string")


def _no_rows(model: type, criteria: Criteria, where: dict | None) -> DataFrame:
    """A read before its first row, with the columns a row of it has — the identity and what the
    specification keeps, or every field the model describes — typed by the model, so a read that
    answers nothing still joins, and goes to Arrow and Parquet, with its columns."""
    described = describe_class(model).fields
    annotations = annotations_of(model)
    specification = criteria.specification
    columns = (
        tuple(described)
        if specification is None
        else tuple(dict.fromkeys([identity_name(model), *specification.named]))
    )
    types = tuple(
        _column_type(annotations[name]) if name in described else "string" for name in columns
    )
    return DataFrame(columns, types, ((),) * len(columns), where=where, complete=False)


def _page(
    repository: IRepository,
    target: type,
    criteria: Criteria,
    cursor: str | None,
    where: dict | None,
) -> DataFrame:
    """One page, from `cursor` — from the first row when there is none, whatever page the
    `Criteria` was asked from: the page is not part of the read."""
    asked = criteria.model_copy(update={"count": CountMode.NONE, "meta": False})
    collection = repository.search(target, asked.resuming_from(cursor))
    rows = collection.to_records(criteria.specification)
    return DataFrame.from_rows(
        rows, where=where, complete=collection.cursor is None, cursor=collection.cursor
    )


class QueryCache:
    def __init__(self, max_rows: int | None = None) -> None:
        self.max_rows = max_rows
        self._held: OrderedDict[str, _Held] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._held)

    def _let_go_past_the_limit(self, kept: str) -> None:
        if self.max_rows is None:
            return
        while sum(len(held.frame) for held in self._held.values()) > self.max_rows:
            oldest = next(iter(self._held))
            if oldest == kept:
                return
            del self._held[oldest]

    def get(
        self, repository: IRepository, target: type, criteria: Criteria
    ) -> DataFrame | None:
        """What is held for this read, which counts as using it — or nothing, and no read."""
        key = repository.fingerprint(target, criteria)
        with self._lock:
            held = self._held.get(key)
            if held is None:
                return None
            self._held.move_to_end(key)
            return held.frame

    def _read(
        self, repository: IRepository, target: type, criteria: Criteria, pages: int | None
    ) -> DataFrame:
        """Up to `pages` pages of this read held — every page when `None` — only the ones not
        held read.

        1. What is held for this read, or one with its columns and no row yet.
        2. Each missing page, from where the held ones stopped — outside the lock, so a read
           never waits on another one.
        3. The pages read merged into what is held, once.
        4. Final: kept as the read used last; older reads let go past `max_rows`.
        """
        key = repository.fingerprint(target, criteria)
        model, _ = model_and_collection(target)
        where = criteria.model_dump(mode="json")["where"]
        with self._lock:
            held = self._held.get(key)
        if held is None:
            held = _Held(model, _no_rows(model, criteria, where), 0)
        read: list[DataFrame] = []
        cursor, complete, count = held.frame.cursor, held.frame.complete, held.pages
        while not complete and (pages is None or count < pages):
            page = _page(repository, target, criteria, cursor, where)
            read.append(page)
            cursor, complete, count = page.cursor, page.complete, count + 1
        if read:
            rows = [row for page in read for row in page.rows()]
            pages_read = DataFrame.from_rows(rows, held.frame.key, where, complete, cursor)
            held = _Held(model, held.frame.append(pages_read), count)
        with self._lock:
            self._held[key] = held
            self._held.move_to_end(key)
            self._let_go_past_the_limit(key)
        return held.frame

    def fetch(
        self, repository: IRepository, target: type, criteria: Criteria, pages: int = 1
    ) -> DataFrame:
        """This read with at least `pages` pages held — the total, not how many more — only
        the ones not held read."""
        return self._read(repository, target, criteria, pages)

    def fetch_all(
        self, repository: IRepository, target: type, criteria: Criteria
    ) -> DataFrame:
        """Every row of this read — the pages not held yet read now — so the frame is complete
        and can answer a narrower filter by itself."""
        return self._read(repository, target, criteria, None)

    def invalidate(self, target: type | None = None) -> None:
        """Let go of what is held of `target` — of everything when not given."""
        with self._lock:
            if target is None:
                self._held.clear()
                return
            model, _ = model_and_collection(target)
            for key in [key for key, held in self._held.items() if held.model is model]:
                del self._held[key]
