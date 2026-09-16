"""What this layer reports about every statement it runs, through what the framework already
has: its logger, its tracer, and its error tracker.

Attached to an engine once, when the `Database` is built. Three outputs, each one optional on
its own:

    logger.debug   →  every statement, its table, its duration and its row count — the way to
                      see the queries a Feature really ran. The provider hands the logger of
                      the bus that owns the database, so the queries land beside that bus's
                      lines; without one, the layer logs as `sincpro_framework.sql`.
    tracer         →  one span per statement, only when the process is actually collecting.
                      The check is `process.status.active` and not `tracer()`: with the SDK
                      installed but no collector, `tracer()` hands back a no-op tracer that
                      builds a span per statement and throws it away.
    record_error   →  every database failure, to GlitchTip when a DSN is configured.

**No parameter value ever reaches a log, a span or an error report.** SQLAlchemy binds values
separately from the statement, so `SELECT … WHERE phn_enc = ?` travels without the identifier
it was looking for, and that is the only shape allowed here.
"""

from time import perf_counter
from typing import Any

from sincpro_log.logger import LoggerProxy, create_logger
from sqlalchemy import Engine, event

from sincpro_framework.observability.api import process

SQL_LOGGER = "sincpro_framework.sql"

# Where a connection keeps what its open statements need: the spans, and the moment each one
# started. Lists and not single values because SQLAlchemy can run a nested statement on the
# same connection.
OPEN_SPANS = "sincpro_open_spans"
STARTED_AT = "sincpro_started_at"

# What a span is named after: the first word of the statement, which is the operation, and the
# table the compiler already resolved. Never the statement itself.
UNKNOWN_OPERATION = "SQL"

# How SQLAlchemy spells a name it made up for an unnamed subquery: `%(140234 anon)s`.
ANONYMOUS = "%("


def operation_of(statement: str) -> str:
    """The verb a statement opens with.

    in  "SELECT dataset.name FROM dataset WHERE …"  →  out  'SELECT'
    in  ""                                          →  out  'SQL'
    """
    head = statement.lstrip().split(" ", 1)[0].upper()
    return head or UNKNOWN_OPERATION


def table_of(context: Any) -> str:
    """The table a statement touches, read off what the compiler already resolved.

    in  an execution context for `SELECT … FROM dataset`  →  out  'dataset'
    in  one for `INSERT INTO dataset …`                    →  out  'dataset'
    in  a count over a subquery, whose FROM is anonymous  →  out  ''
    in  a raw statement the ORM did not compile           →  out  ''

    An anonymous name is dropped rather than passed through: SQLAlchemy builds it from the
    object's id, so it differs per process, and a span named after it is a new name on every
    deploy.
    """
    try:
        statement = context.compiled.statement
        target = getattr(statement, "table", None)
        if target is None:
            target = statement.get_final_froms()[0]
        name = getattr(target, "name", "")
    except Exception:
        return ""
    return "" if not isinstance(name, str) or ANONYMOUS in name else name


def _elapsed_ms(connection: Any) -> float:
    started = connection.info.get(STARTED_AT) if connection is not None else None
    if not started:
        return 0.0
    return round((perf_counter() - started.pop()) * 1000, 2)


def _close_span(connection: Any, error: BaseException | None = None) -> None:
    """Ends the newest open span on this connection, recording the failure when there is one."""
    spans = connection.info.get(OPEN_SPANS) if connection is not None else None
    if not spans:
        return

    span = spans.pop()
    if error is not None:
        span.record_exception(error)
        span.set_status(_error_status(error))
    span.end()


def _error_status(error: BaseException) -> Any:
    from opentelemetry.trace import Status, StatusCode

    return Status(StatusCode.ERROR, type(error).__name__)


def observe(engine: Engine, database: str, logger: LoggerProxy | None = None) -> None:
    """Attaches logging, tracing and error reporting to one engine.

        always                        →  one DEBUG line per statement, one ERROR per failure
        with a collector configured   →  one span per statement, `SELECT dataset`
        with a Sentry DSN configured  →  every database failure reported under `database`

    1. Take the logger the provider handed in, or the layer's own, bound to this database.
    2. Ask whether tracing is actually on; a no-op tracer is not, see the module docstring.
    3. Before each statement note the moment and open the span; after it, log and close.
    4. Final: on a failure, log it, close the span with the error and hand it to the tracker.
    """
    log = (logger or create_logger(SQL_LOGGER)).bind(database=database)
    tracer = process.tracer("sincpro.orm") if process.status.active else None

    @event.listens_for(engine, "before_cursor_execute")
    def _opened(connection, cursor, statement, parameters, context, executemany) -> None:
        connection.info.setdefault(STARTED_AT, []).append(perf_counter())
        if tracer is None:
            return
        table = table_of(context)
        span = tracer.start_span(
            f"{operation_of(statement)} {table}".strip(),
            attributes={
                "db.system": engine.dialect.name,
                "db.name": database,
                "db.operation": operation_of(statement),
                "db.sql.table": table,
                # The statement without its values; see the module docstring.
                "db.statement": statement,
            },
        )
        connection.info.setdefault(OPEN_SPANS, []).append(span)

    @event.listens_for(engine, "after_cursor_execute")
    def _closed(connection, cursor, statement, parameters, context, executemany) -> None:
        log.debug(
            "sql",
            operation=operation_of(statement),
            table=table_of(context),
            statement=statement,
            duration_ms=_elapsed_ms(connection),
            rows=cursor.rowcount,
        )
        _close_span(connection)

    @event.listens_for(engine, "handle_error")
    def _failed(context) -> None:
        """Context: `after_cursor_execute` never runs for a statement that raised, so this is
        the only place a failing statement is logged and its span closed."""
        error = context.original_exception
        log.error(
            "sql failed",
            statement=context.statement or "",
            duration_ms=_elapsed_ms(context.connection),
            error=type(error).__name__,
        )
        _close_span(context.connection, error)
        process.record_error(error, layer="database")
