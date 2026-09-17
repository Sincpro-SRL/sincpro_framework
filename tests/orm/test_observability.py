"""That a statement lands in the log, on a trace and, when it fails, on the tracker, and that
tracing costs nothing when nobody is collecting.

The assertion that matters most: **no parameter value reaches a log line or a span.** A trace
or a log carrying `WHERE secret = '2137…'` would put the data a repository refuses to log in a
collector nobody audited.

A real tracer provider with an in-memory exporter, because a fake would only prove that the
code calls the methods a fake was given. The logger is a stand-in that keeps what it was told,
because what matters is the fields, not structlog's rendering.
"""

from typing import Any, cast

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from sqlalchemy import column, create_engine, insert, table, text

from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.observability import (
    OPEN_SPANS,
    _close_span,
    observe,
    operation_of,
    table_of,
)

SECRET = "213720070017"


class Collecting:
    """Observability as it looks when a collector and a tracker are both configured.

    A stand-in for the framework's `process`, because its `status` is a read-only property and
    the global tracer provider can only be set once per interpreter.
    """

    status = type("Status", (), {"active": True})()

    def __init__(self, provider: TracerProvider, exporter: InMemorySpanExporter) -> None:
        self._provider = provider
        self.exporter = exporter
        self.reported: list[tuple[str, str]] = []

    def tracer(self, name: str):
        return self._provider.get_tracer(name)

    def record_error(self, error: Exception, layer: str = "process") -> None:
        self.reported.append((type(error).__name__, layer))


class Idle:
    """Observability with the SDK installed and nothing configured: a tracer is handed out,
    and the status says nobody is collecting."""

    status = type("Status", (), {"active": False})()

    def tracer(self, name: str):
        return object()

    def record_error(self, error: Exception, layer: str = "process") -> None:
        pass


class RecordingLogger:
    """Keeps every line as `(level, event, fields)`; `bind` answers itself so the fields it
    was bound with are visible too."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []
        self.bound: dict[str, Any] = {}

    def bind(self, **fields: Any) -> "RecordingLogger":
        self.bound.update(fields)
        return self

    def debug(self, event: str, **fields: Any) -> None:
        self.lines.append(("debug", event, fields))

    def error(self, event: str, **fields: Any) -> None:
        self.lines.append(("error", event, fields))


@pytest.fixture
def collecting(monkeypatch) -> Collecting:
    """A real provider whose spans land in memory instead of on a collector."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    active = Collecting(provider, exporter)
    monkeypatch.setattr("sincpro_framework.orm.sqlalchemy.observability.process", active)
    return active


@pytest.fixture
def log() -> RecordingLogger:
    return RecordingLogger()


@pytest.fixture
def database(collecting, log, tmp_path) -> Database:
    """A database with one table and one secret in it, observed from birth; the statements
    that built it are cleared so each test sees only its own."""
    database = Database(f"sqlite:///{tmp_path}/ledger.sqlite3", logger=cast(Any, log))
    with database.session() as session:
        session.execute(text("CREATE TABLE note (note_id TEXT, secret TEXT)"))
        session.execute(text(f"INSERT INTO note VALUES ('n_1', '{SECRET}')"))
    collecting.exporter.clear()
    log.lines.clear()
    return database


def test_no_span_listener_is_attached_when_nobody_is_collecting(monkeypatch, log):
    """The check is the status and not the tracer: with the SDK installed but no collector,
    `get_tracer` still answers a no-op tracer that would build a span per statement."""
    monkeypatch.setattr("sincpro_framework.orm.sqlalchemy.observability.process", Idle())
    engine = create_engine("sqlite://")

    observe(engine, "ledger", cast(Any, log))
    with engine.connect() as connection:
        connection.execute(text("SELECT 1")).all()
        opened = connection.info.get(OPEN_SPANS)

    assert [line[1] for line in log.lines] == ["sql"]
    assert not opened


def test_a_database_is_named_after_its_url(tmp_path):
    assert Database("sqlite://").name == ""
    assert Database(f"sqlite:///{tmp_path}/ledger.sqlite3").name.endswith("ledger.sqlite3")


def test_a_write_names_its_table(database, log):
    """An INSERT has no FROM; its table is read off the statement itself."""
    note = table("note", column("note_id"), column("secret"))

    with database.session() as session:
        session.execute(insert(note).values(note_id="n_2", secret="nothing"))

    assert log.lines[0][2]["table"] == "note"
    assert log.lines[0][2]["rows"] == 1


def test_a_statement_becomes_one_log_line(database, log):
    with database.session() as session:
        session.execute(text("SELECT note_id FROM note")).all()

    level, event, fields = log.lines[0]
    assert (level, event) == ("debug", "sql")
    assert fields["operation"] == "SELECT"
    assert fields["statement"] == "SELECT note_id FROM note"
    assert fields["duration_ms"] >= 0
    assert log.bound["database"].endswith("ledger.sqlite3")


def test_a_statement_becomes_one_span(database, collecting):
    with database.session() as session:
        session.execute(text("SELECT note_id FROM note")).all()

    spans = collecting.exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes["db.system"] == "sqlite"
    assert spans[0].attributes["db.name"].endswith("ledger.sqlite3")
    assert spans[0].attributes["db.operation"] == "SELECT"


def test_no_parameter_value_ever_reaches_a_span_or_a_log_line(database, collecting, log):
    """`SELECT … WHERE secret = ?` travels without what it was looking for."""
    with database.session() as session:
        session.execute(
            text("SELECT note_id FROM note WHERE secret = :secret"), {"secret": SECRET}
        ).all()

    on_spans = " ".join(
        str(span.attributes) for span in collecting.exporter.get_finished_spans()
    )
    in_log = " ".join(str(fields) for _, _, fields in log.lines)
    assert SECRET not in on_spans and SECRET not in in_log
    assert (
        "secret" in on_spans and "secret" in in_log
    )  # the column is named; its value is not


def test_a_failing_statement_is_logged_traced_and_reported(database, collecting, log):
    """Context: `after_cursor_execute` never runs for a statement that raised, so a span left
    open here would stay open for the life of the connection."""
    with pytest.raises(Exception):
        with database.session() as session:
            session.execute(text("SELECT * FROM no_such_table"))

    level, event, fields = log.lines[-1]
    assert (level, event) == ("error", "sql failed")
    assert fields["error"] == "OperationalError"
    spans = collecting.exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code is StatusCode.ERROR
    assert [event.name for event in spans[0].events] == ["exception"]
    assert collecting.reported == [("OperationalError", "database")]


def test_an_anonymous_subquery_name_never_becomes_a_span_name(database, collecting):
    """SQLAlchemy builds it from an object id, so it differs per process; a span named after
    one is a new name on every deploy, which is cardinality a collector pays for."""
    with database.session() as session:
        session.execute(text("SELECT count(*) FROM (SELECT 1 FROM note LIMIT 10) AS x")).all()

    assert all("%(" not in span.name for span in collecting.exporter.get_finished_spans())


def test_the_pieces_a_span_name_is_built_from():
    assert operation_of("SELECT note_id FROM note") == "SELECT"
    assert operation_of("  insert into note …") == "INSERT"
    assert operation_of("") == "SQL"
    assert table_of(None) == ""


def test_a_from_that_is_not_a_plain_name_is_dropped():
    """The two shapes that must not reach a span name: an anonymous subquery, and anything
    that is not a string at all."""

    def context_whose_from_is(name):
        statement = type("S", (), {"get_final_froms": lambda self: [name]})()
        compiled = type("Compiled", (), {"statement": statement})
        return type("Context", (), {"compiled": compiled()})()

    named = type("From", (), {"name": "dataset"})()
    anonymous = type("From", (), {"name": "%(4488012736 anon)s"})()
    nameless = type("From", (), {"name": None})()

    assert table_of(context_whose_from_is(named)) == "dataset"
    assert table_of(context_whose_from_is(anonymous)) == ""
    assert table_of(context_whose_from_is(nameless)) == ""


def test_a_statement_the_orm_did_not_compile_still_gets_a_span(database, collecting):
    """Raw SQL has no compiler resolution, so the span is opened without a table to name."""
    with database.session() as session:
        session.execute(text("PRAGMA user_version")).all()

    spans = collecting.exporter.get_finished_spans()
    assert spans and spans[0].attributes["db.sql.table"] == ""


def test_a_failure_with_no_connection_closes_nothing():
    """SQLAlchemy raises before a connection exists when the URL itself is wrong, so
    `handle_error` arrives with nothing to close."""
    _close_span(None)
