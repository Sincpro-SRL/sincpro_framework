"""Every record a criteria matches, out as Parquet, an Arrow stream or JSON Lines — a page at a
time, never all of them in memory — for whoever needs the whole set: a data-science notebook,
a nightly dump to a lake, a spreadsheet.

    with open("entries.parquet", "wb") as sink:
        write_parquet(sink, repository, Entry, posted)               # 2 million rows, 5 000 at a time

    exported(repository, Entry, posted, "parquet")                   # an answer a wire streams:
        ->  Streamed(media_type="application/vnd.apache.parquet", filename="entry.parquet", …)

Context: a screen reads pages of 40 and a page of 40 over HTTP is the wrong tool for two million
rows; Stripe points its exports at another door for the same reason. This is that door, built
from what the framework already has: `repository.stream` walks the cursor (keyset, so a row
inserted mid-walk neither repeats nor hides another), each page is turned into an Arrow record
batch, and the batch is written — a Parquet row group, an Arrow IPC message, lines of JSON — and
let go of. The repository's own scope applies (the tenant, `narrowed`), because it is the
repository that reads.

**The schema comes from the model, not from the values.** Fast readers lose types — ADBC answers
a PostgreSQL NUMERIC as text, a pandas frame turns it into a float — and a schema guessed from the
first page changes with the page. Here each column is typed by its field: a day is `date32`, an
instant `timestamp(us, UTC)`, an integer `int64`, a list of texts `list<string>`, a key what it
holds, and an exact number `decimal128(38, scale)` at the scale the store declares
(`Numeric(12, 2)`) — or text, exactly as the wire sends it, when none is declared. A value object
or a translated text travels as its JSON. Relations are left out: a relation is another export.

**One snapshot.** `exported` reads every page inside one unit of work opened at
`REPEATABLE_READ`, read only, when the repository offers one: the file is the set as it stood
when the export began, whatever is written meanwhile. The price is a transaction open for as
long as the download lasts — on PostgreSQL that holds back the vacuum, and a replica cancels a
query that outlives `max_standby_streaming_delay` —, so an export of hours belongs in a job that
writes a file, not in a request.

Parquet and Arrow need the `[data-analysis]` extra, pyarrow, imported when asked for; JSON
Lines needs nothing beyond the framework.
"""

import contextvars
import inspect
import io
import json
from collections.abc import Callable, Generator, Iterator, Mapping
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, BinaryIO

from pydantic import TypeAdapter
from pydantic_core import to_jsonable_python

from sincpro_framework.ddd.criteria import CountMode, Criteria, Offset
from sincpro_framework.ddd.entity.entity_collection import Count, EntityCollection
from sincpro_framework.ddd.entity.entity_meta import FieldMeta, FieldType, Meta
from sincpro_framework.ddd.query import Streamed, _record, _version_of, writing_out
from sincpro_framework.exceptions import ProgrammingError

PYARROW_MISSING = (
    "pyarrow is not installed — Parquet and Arrow need it. Install with: "
    "pip install sincpro-framework[data-analysis]"
)


def _pyarrow() -> Any:
    """pyarrow, imported when Parquet or Arrow is asked for — JSON Lines never needs it."""
    try:
        import pyarrow
        import pyarrow.ipc
        import pyarrow.parquet
    except ImportError as error:  # pragma: no cover - tests/core/test_core_without_extras.py
        raise ImportError(PYARROW_MISSING) from error
    return pyarrow


FORMATS = {
    "parquet": "application/vnd.apache.parquet",
    "arrow": "application/vnd.apache.arrow.stream",
    "jsonl": "application/jsonl",
}
"""The formats an export is written in, by name, with the media type each one travels as —
both Arrow types are registered with IANA; `application/jsonl` is what FastAPI streams."""

EXTENSIONS = {"parquet": "parquet", "arrow": "arrows", "jsonl": "jsonl"}

EXPORT_BATCH = 5_000
"""How many records each page of an export reads, and each batch writes: what the export holds
in memory at once."""


def _scalar(kind: FieldType) -> Any:
    """The Arrow type of a field the model types plainly; text for anything else."""
    pyarrow = _pyarrow()
    return {
        FieldType.TEXT: pyarrow.string(),
        FieldType.INTEGER: pyarrow.int64(),
        FieldType.BOOLEAN: pyarrow.bool_(),
        FieldType.DATE: pyarrow.date32(),
        FieldType.DATETIME: pyarrow.timestamp("us", tz="UTC"),
        FieldType.TEXT_LIST: pyarrow.list_(pyarrow.string()),
        FieldType.UUID: pyarrow.string(),
    }.get(kind, pyarrow.string())


_PLAIN_TYPES = (
    FieldType.TEXT,
    FieldType.INTEGER,
    FieldType.BOOLEAN,
    FieldType.DATE,
    FieldType.DATETIME,
    FieldType.TEXT_LIST,
    FieldType.UUID,
)
"""The types a column holds as themselves; any other is written as its JSON."""


def arrow_type(field: FieldMeta, scale: int | None = None) -> Any:
    """The Arrow type of one field, from what the model says it is.

    an exact number, scale 2       ->  decimal128(38, 2)
    an exact number, no scale      ->  string        exactly the text the wire sends
    a number that is not exact     ->  float64
    a value object, a translation  ->  string        its JSON
    """
    pyarrow = _pyarrow()
    if field.type is FieldType.NUMBER:
        if not field.exact:
            return pyarrow.float64()
        return pyarrow.decimal128(38, scale) if scale is not None else pyarrow.string()
    return _scalar(field.type)


def exported_fields(meta: Meta, criteria: Criteria | None = None) -> dict[str, FieldMeta]:
    """The fields an export writes: every one that is not a relation — the identity first —, or
    those the criteria's specification names."""
    named = set(criteria.specification.named) if criteria and criteria.specification else None
    found = {
        name: field
        for name, field in meta.fields.items()
        if not field.type.is_relational
        and (named is None or name in named or name == meta.identity)
    }
    first = {meta.identity: found.pop(meta.identity)} if meta.identity in found else {}
    return {**first, **found}


def arrow_schema(
    meta: Meta,
    scales: Mapping[str, int] | None = None,
    criteria: Criteria | None = None,
) -> Any:
    """The Arrow schema of an export of `meta`'s records: one column per field it writes
    (`exported_fields`), typed by the model (`arrow_type`), nullable as the field is.

        arrow_schema(describe(Entry), {"amount": 2})
            ->  id: string not null, posted_on: date32, amount: decimal128(38, 2), …
    """
    scales = scales or {}
    pyarrow = _pyarrow()
    return pyarrow.schema(
        [
            pyarrow.field(name, arrow_type(field, scales.get(name)), nullable=field.nullable)
            for name, field in exported_fields(meta, criteria).items()
        ]
    )


def scales_of(target: type) -> dict[str, int]:
    """The decimal places each exact number of `target` is stored with, as its store declares
    them — `Numeric(12, 2)` on a mapped table —; nothing for a store that declares none."""
    try:
        from sqlalchemy import Numeric
        from sqlalchemy import inspect as inspected
        from sqlalchemy.exc import NoInspectionAvailable
    except ImportError:  # pragma: no cover - SQLAlchemy is a core dependency
        return {}
    try:
        mapper = inspected(target)
    except NoInspectionAvailable:
        return {}
    return {
        column.key: column.type.scale
        for column in mapper.columns
        if isinstance(column.type, Numeric) and isinstance(column.type.scale, int)
    }


def _cell(field: FieldMeta, kind: Any, value: Any) -> Any:
    """One value as its column holds it."""
    pyarrow = _pyarrow()
    if value is None:
        return None
    if isinstance(value, Enum):
        value = value.value
    if pyarrow.types.is_decimal(kind):
        return value if isinstance(value, Decimal) else Decimal(str(value))
    if pyarrow.types.is_timestamp(kind) and isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if pyarrow.types.is_list(kind):
        return [str(one) for one in value]
    if pyarrow.types.is_string(kind) and not isinstance(value, str):
        if field.type is FieldType.NUMBER:
            return str(value)
        plain = to_jsonable_python(value)
        return plain if isinstance(plain, str) else json.dumps(plain, ensure_ascii=False)
    return value


def record_batch(records: EntityCollection | list[Any], schema: Any, meta: Meta) -> Any:
    """One page of records as one Arrow record batch of `schema`."""
    pyarrow = _pyarrow()
    fields = meta.fields
    columns = [
        [
            _cell(fields[one.name], one.type, getattr(record, one.name, None))
            for record in records
        ]
        for one in schema
    ]
    return pyarrow.RecordBatch.from_arrays(
        [pyarrow.array(column, type=one.type) for column, one in zip(columns, schema)],
        schema=schema,
    )


def _following(asked: Criteria, page: EntityCollection) -> Criteria | None:
    """The criteria of the page after `page`: from its cursor; under an offset, past the rows it
    brought when it came full; none when it was the last."""
    if page.cursor is not None:
        return asked.resuming_from(page.cursor)
    strategy = asked.pagination.strategy
    if isinstance(strategy, Offset) and page.items and len(page.items) >= asked.limit:
        moved = Offset(rows=strategy.rows + len(page.items))
        return asked.model_copy(
            update={"pagination": asked.pagination.model_copy(update={"strategy": moved})}
        )
    return None


def pages(
    repository: Any, target: type, criteria: Criteria | None = None, batch: int = EXPORT_BATCH
) -> Iterator[EntityCollection]:
    """Every page of what `criteria` matches, `batch` records each, uncounted — a count per
    page is a query nobody reads here —, from where the criteria stands (its cursor, its offset),
    following the cursor or moving the offset."""
    asked: Criteria | None = criteria or Criteria()
    asked = asked.model_copy(
        update={
            "pagination": asked.pagination.model_copy(update={"limit": batch}),
            "count": CountMode.NONE,
        }
    )
    while asked is not None:
        page = repository.search(target, asked)
        yield page
        asked = _following(asked, page)


def _described(page: EntityCollection, target: type) -> Meta:
    if page.meta is not None:
        return page.meta
    from sincpro_framework.ddd.metamodel import description_of

    return description_of(target)


class _Drain(io.RawIOBase):
    """A sink that keeps what was written until it is drained — how a writer that wants a file
    hands its bytes on as it writes them."""

    def __init__(self) -> None:
        super().__init__()
        self._held: list[bytes] = []
        self._at = 0

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        chunk = bytes(data)
        self._held.append(chunk)
        self._at += len(chunk)
        return len(chunk)

    def tell(self) -> int:
        return self._at

    def drained(self) -> bytes:
        out, self._held = b"".join(self._held), []
        return out


def arrow_batches(
    repository: Any,
    target: type,
    criteria: Criteria | None = None,
    batch: int = EXPORT_BATCH,
    scales: Mapping[str, int] | None = None,
) -> tuple[Any, Iterator[Any]]:
    """The schema of the export, and its record batches as the pages are read — the first page
    is read here, so the schema is the store's description of what it holds."""
    walk = pages(repository, target, criteria, batch)
    first = next(walk, None) or EntityCollection()
    meta = _described(first, target)
    schema = arrow_schema(meta, scales if scales is not None else scales_of(target), criteria)

    def batches() -> Iterator[Any]:
        yield record_batch(first, schema, meta)
        for page in walk:
            yield record_batch(page, schema, meta)

    return schema, batches()


def parquet_chunks(
    repository: Any,
    target: type,
    criteria: Criteria | None = None,
    batch: int = EXPORT_BATCH,
    compression: str = "zstd",
) -> Iterator[bytes]:
    """A Parquet file, written a row group per page and handed on as it is written."""
    schema, batches = arrow_batches(repository, target, criteria, batch)
    sink = _Drain()
    pyarrow = _pyarrow()
    with pyarrow.parquet.ParquetWriter(sink, schema, compression=compression) as writer:
        for one in batches:
            writer.write_batch(one)
            yield sink.drained()
    yield sink.drained()


def arrow_chunks(
    repository: Any, target: type, criteria: Criteria | None = None, batch: int = EXPORT_BATCH
) -> Iterator[bytes]:
    """An Arrow IPC stream, a message per page — what a client that appends batch after batch
    reads without waiting for the end."""
    schema, batches = arrow_batches(repository, target, criteria, batch)
    sink = _Drain()
    pyarrow = _pyarrow()
    with pyarrow.ipc.new_stream(sink, schema) as writer:
        for one in batches:
            writer.write_batch(one)
            yield sink.drained()
    yield sink.drained()


def jsonl_chunks(
    repository: Any, target: type, criteria: Criteria | None = None, batch: int = EXPORT_BATCH
) -> Iterator[bytes]:
    """JSON Lines, one record per line, each written exactly as an answer writes it — decimals
    as text, a day a day — a page of lines at a time."""
    specification = criteria.specification if criteria else None
    for page in pages(repository, target, criteria, batch):
        with writing_out():
            lines = [
                json.dumps(
                    _record(record, specification, page.meta, "json"), ensure_ascii=False
                )
                for record in page
            ]
        if lines:
            yield ("\n".join(lines) + "\n").encode()


WRITERS: dict[str, Callable[..., Iterator[bytes]]] = {
    "parquet": parquet_chunks,
    "arrow": arrow_chunks,
    "jsonl": jsonl_chunks,
}


def _unit_in_play(database: Any) -> bool:
    """Whether a unit of work is already open on `database` in this context."""
    if database is None:
        return False
    from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.unit_in_play import (
        in_play,
    )

    return in_play(database) is not None


def _snapshot(repository: Any) -> AbstractContextManager[Any]:
    """One unit of work at `REPEATABLE_READ`, read only, when the repository opens one — or the
    unit already in play, joined as it is, when the export runs inside another use case's: its
    transaction began with its own options, and a second one cannot be opened inside it."""
    context = getattr(repository, "context", None)
    if context is None:
        return nullcontext(repository)
    accepted = inspect.signature(context).parameters
    database = getattr(repository, "database", None)
    if "isolation" in accepted and "read_only" in accepted and not _unit_in_play(database):
        from sincpro_framework.data_layer.orm.sqlalchemy.domain.transaction import Isolation

        return context(isolation=Isolation.REPEATABLE_READ, read_only=True)
    return context()


def _in_snapshot(
    repository: Any, write: Callable[[Any], Iterator[bytes]]
) -> Generator[bytes]:
    with _snapshot(repository) as unit:
        yield from write(unit)


def _carried(chunks: Generator[bytes], context: contextvars.Context) -> Iterator[bytes]:
    """The chunks, each produced inside the context the export was asked in — the identity and
    the scope a use case runs with — whichever thread a wire pulls them from."""
    try:
        while True:
            try:
                chunk = context.run(next, chunks)
            except StopIteration:
                return
            if chunk:
                yield chunk
    finally:
        # A client that leaves mid-stream: the snapshot closes now, in the context it opened in.
        context.run(chunks.close)


def exported(
    repository: Any,
    target: type,
    criteria: Criteria | None = None,
    format: str = "parquet",
    batch: int = EXPORT_BATCH,
    filename: str = "",
) -> Streamed:
    """An export as an answer a wire streams: nothing is read until the first chunk is asked
    for, every page then comes from one snapshot (`_snapshot`), and each chunk is produced in
    the context the use case ran in.

        @bus.feature(QueryExportEntries)
        class ExportEntries(Feature):
            def execute(self, dto: QueryExportEntries) -> Streamed:
                return exported(self.repository, Entry, dto.criteria, dto.format)

    A format it does not write is refused by name, before anything is read.
    """
    if format not in WRITERS:
        raise ProgrammingError(
            f"an export is written as {', '.join(WRITERS)}, not {format!r}"
        )
    write = WRITERS[format]
    chunks = _in_snapshot(repository, lambda unit: write(unit, target, criteria, batch))
    return Streamed(
        media_type=FORMATS[format],
        filename=filename or f"{target.__name__.lower()}.{EXTENSIONS[format]}",
        chunks=_carried(chunks, contextvars.copy_context()),
    )


def write_parquet(
    sink: BinaryIO,
    repository: Any,
    target: type,
    criteria: Criteria | None = None,
    batch: int = EXPORT_BATCH,
    compression: str = "zstd",
) -> int:
    """Every record `criteria` matches written to `sink` as one Parquet file, a row group per
    page, from one snapshot; how many were written."""
    written = 0
    pyarrow = _pyarrow()
    with _snapshot(repository) as unit:
        schema, batches = arrow_batches(unit, target, criteria, batch)
        with pyarrow.parquet.ParquetWriter(sink, schema, compression=compression) as writer:
            for one in batches:
                writer.write_batch(one)
                written += one.num_rows
    return written


ENVELOPE = b"sincpro"
"""The key of a Parquet answer's footer metadata holding what JSON carries beside the records:
`{"records": "<field>", "cursor", "count", "model_version", "dropped"}`."""


def _page_walk(
    repository: Any, target: type, criteria: Criteria, batch: int
) -> Iterator[EntityCollection]:
    """The pages of one asked page of `criteria.limit` records, `batch` at a time — the last
    one cut to what is left —, so a page of two million is never held at once. The last page
    read carries the cursor the asked page answers."""
    left = criteria.limit
    asked: Criteria | None = criteria.model_copy(update={"count": CountMode.NONE})
    while asked is not None and left > 0:
        size = min(batch, left)
        sized = asked.model_copy(
            update={"pagination": asked.pagination.model_copy(update={"limit": size})}
        )
        page = repository.search(target, sized)
        yield page
        left -= len(page.items)
        asked = _following(sized, page) if page.items else None


def _json_columns(meta: Meta, criteria: Criteria) -> list[str]:
    """The columns written as JSON text — a value object, a text in several languages, what
    the model does not type plainly —, so a reader turns them back into what JSON would have
    sent."""
    plain = {*_PLAIN_TYPES, FieldType.NUMBER}
    return [
        name
        for name, field in exported_fields(meta, criteria).items()
        if field.type not in plain
    ]


def _counted(
    repository: Any, target: type, criteria: Criteria, held: int, cursor: str | None
) -> dict[str, Any] | None:
    """The count a Parquet page carries, as its criteria asked for it: none; all there is when
    the page came back whole; otherwise the store's count, never a floor below what it holds.
    """
    if criteria.count is CountMode.NONE:
        return None
    from_the_start = criteria.cursor is None and criteria.pagination.skipped() == 0
    if cursor is None and from_the_start and held < criteria.limit:
        return {"value": held, "exact": True}
    counted = repository.count(target, criteria)
    if counted.exact or counted.value > held:
        return counted.model_dump()
    return {"value": held, "exact": False}


def searched_as_parquet(
    repository: Any,
    target: type,
    criteria: Criteria,
    response: type,
    batch: int = EXPORT_BATCH,
) -> Streamed:
    """A search's page as a Parquet file instead of JSON: the same records — `criteria.limit`
    of them, read `batch` at a time from one snapshot —, typed by the model, with what JSON
    carries beside them in the file's footer (`ENVELOPE`): the response's records field, the
    cursor to go on, the count, the model's version, what was dropped.

        QueryListEntries(criteria=<limit 2 000 000>, format="parquet")
            ->  Streamed("application/vnd.apache.parquet", "entries.parquet")
                footer: {"records": "entries", "cursor": "eyJ…", "count": {"value": 10000,
                         "exact": false}, "model_version": "3f9c…", "dropped": []}

    Read back as the collection it is with `collection_from_parquet`, or by any Parquet reader:
    pandas, polars, DuckDB, a browser.
    """
    records = response.records_field()
    declared = response.model_fields[records].annotation
    version = _version_of(declared)

    def write(unit: Any) -> Iterator[bytes]:
        pyarrow = _pyarrow()
        walk = _page_walk(unit, target, criteria, batch)
        first = next(walk, None) or EntityCollection()
        meta = _described(first, target)
        schema = arrow_schema(meta, scales_of(target), criteria)
        sink = _Drain()
        held, last = 0, first
        # snappy: every Parquet reader decodes it with nothing else installed — a browser's too.
        with pyarrow.parquet.ParquetWriter(sink, schema, compression="snappy") as writer:
            for page in (first, *walk):
                writer.write_batch(record_batch(page, schema, meta))
                held += len(page.items)
                last = page
                yield sink.drained()
            cursor = last.cursor if held >= criteria.limit else None
            writer.add_key_value_metadata(
                {
                    ENVELOPE: json.dumps(
                        {
                            "records": records,
                            "cursor": cursor,
                            "count": _counted(unit, target, criteria, held, cursor),
                            "model_version": version,
                            "dropped": [one.model_dump() for one in first.dropped],
                            "json": _json_columns(meta, criteria),
                        }
                    ).encode()
                }
            )
        yield sink.drained()

    chunks = _in_snapshot(repository, write)
    return Streamed(
        media_type=FORMATS["parquet"],
        filename=f"{records}.parquet",
        chunks=_carried(chunks, contextvars.copy_context()),
    )


def collection_from_parquet(data: bytes, target: type) -> EntityCollection[Any]:
    """A Parquet answer read back as the collection it is: each row the record it was, typed by
    `target`, with the cursor and the count its footer carries — what a Python client holds,
    the same as from JSON.

        collection_from_parquet(body, Entry)   ->  EntityCollection(2000000 records of 10000+, more)
    """
    pyarrow = _pyarrow()
    table = pyarrow.parquet.read_table(io.BytesIO(data))
    footer = pyarrow.parquet.ParquetFile(io.BytesIO(data)).metadata.metadata or {}
    said = footer.get(ENVELOPE)
    envelope = json.loads(said) if said else {}
    adapter = TypeAdapter(target)
    rows = [adapter.validate_python(row) for row in table.to_pylist()]
    count = envelope.get("count")
    return EntityCollection(
        items=tuple(rows),
        cursor=envelope.get("cursor"),
        count=Count(**count) if count else None,
    )
