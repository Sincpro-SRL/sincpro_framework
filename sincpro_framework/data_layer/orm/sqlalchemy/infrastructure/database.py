"""A database: one engine, one session factory, and what every statement reports.

`session()` commits when the block ends and rolls back when it raises, so nothing above this
line can write a half-finished change.

Two things every database does without being asked. It stamps the write: `updated_at` on
whatever it is about to update, and `created_by` / `updated_by` when the provider said who is
writing, so those are true through every write path, including a raw session somebody opens by
hand. And it observes itself: every statement goes to the logger at DEBUG, to a span when the
process is collecting, and every failure to the error tracker; see `observability.py`.
"""

import re
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

from sincpro_log.logger import LoggerProxy
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.elements import TextClause

from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.change_tracking import (
    _tracking,
)
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.engine_errors import named
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.flushing import goes_out
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.observability import observe
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.read_tracking import (
    note_statement_reads,
)
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.transaction_hooks import (
    install as hooks_per_transaction,
)
from sincpro_framework.ddd.editing.preview import is_previewing
from sincpro_framework.ddd.entity import utc_now
from sincpro_framework.ddd.repositories.repository import refuse_writing_in_preview


def _stamping(actor: Callable[[], str | None] | None) -> Callable[[Session], None]:
    """The `before_flush` hook that writes when, and who, without a use case remembering to.

        an updated record  →  updated_at, and updated_by when an actor is configured
        a new record       →  updated_at = created_at, and created_by when an actor is configured

    An insert is the first write, so `updated_at` is when it was last written from the start and
    "untouched since" is one column. `is_modified` and not membership in `dirty` alone: an
    attribute set to the value it already held marks the object dirty without changing a column,
    and stamping that would record a write that never happened. The actor is read per flush and
    never raises: a request with no user behind it writes `None` rather than failing the
    transaction.
    """

    def who() -> str | None:
        if actor is None:
            return None
        try:
            return actor()
        except Exception:
            return None

    def stamp(session: Session) -> None:
        now = utc_now()
        acting = who()
        for record in session.dirty:
            if not goes_out(session, record):
                continue
            if hasattr(record, "updated_at") and session.is_modified(record):
                record.updated_at = now
                if acting is not None and hasattr(record, "updated_by"):
                    record.updated_by = acting
        for record in session.new:
            if not goes_out(session, record):
                continue
            if hasattr(record, "updated_at") and record.updated_at is None:
                record.updated_at = getattr(record, "created_at", now)
            if (
                acting is not None
                and hasattr(record, "created_by")
                and record.created_by is None
            ):
                record.created_by = acting

    return stamp


def _enforcing_foreign_keys(connection: Any, _record: Any) -> None:
    """SQLite leaves foreign keys unenforced unless each connection asks — so a test on SQLite
    would write the orphan Postgres refuses in production. Asked on every new connection, as
    Django and Rails do."""
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Database:

    def __init__(
        self,
        url: str,
        echo: bool = False,
        logger: LoggerProxy | None = None,
        actor: Callable[[], str | None] | None = None,
        enforce_foreign_keys: bool = False,
        **engine_options: Any,
    ):
        """One engine, one connection pool, one session factory.

            Database("sqlite:///catalog.sqlite3")
            Database("postgresql+psycopg://…", pool_size=10, pool_pre_ping=True)
            Database(url, logger=bus.logger)      →  the queries land in that bus's log

        `engine_options` go straight to `create_engine`: the provider decides the pool, the
        driver arguments and everything else about how this process reaches its database.
        The framework picks no driver and no pool.

        `logger` is where every statement is reported at DEBUG; without one, the layer logs
        as `sincpro_framework.sql`. Tracing and error reporting need no argument: they follow
        what the process already configured.

        `actor` answers who is writing, for an `AuditedMixin` aggregate:
        `Database(url, actor=lambda: bus.current_context().get("user_id"))`. It is read on
        every flush, so one database serves every request of a process.

        `enforce_foreign_keys` makes SQLite refuse an orphan, as Postgres does — off by default
        while one flush can still insert a child before its parent of another aggregate type
        (see the review log); on, a test on SQLite fails where production would.
        """
        self.url = url
        self.name = make_url(url).database or ""
        self.engine: Engine = create_engine(url, echo=echo, **engine_options)
        observe(self.engine, self.name, logger)
        if enforce_foreign_keys and self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", _enforcing_foreign_keys)

        self.open_session = sessionmaker(
            bind=self.engine, expire_on_commit=False, autoflush=True
        )
        self.actor = actor
        # The two things the framework guarantees about a write, through the same door anybody
        # else uses. On the moment that sees every write, rather than on the repository, because
        # a use case holding a plain session must not be able to step around them.
        self.before_flush(_stamping(actor))
        self.before_flush(_tracking)
        # A preview stores nothing, whichever door a write takes: a repository call, a unit of
        # work committing what it tracks, a plain session.
        self.before_flush(_refused_in_preview)
        event.listen(self.open_session, "do_orm_execute", _statement_refused_in_preview)
        event.listen(self.open_session, "do_orm_execute", note_statement_reads)
        hooks_per_transaction(self.open_session)

    def before_flush(self, run: "Callable[[Session], None]") -> "Database":
        """Runs before this database writes anything, for every session it ever opens.

            database.before_flush(lambda session: ...)

        The moment that sees **every** write — through a repository, through a plain session, a
        script, a migration. A rule on a repository runs for whoever goes through that
        repository; this runs for whoever touches this database, which is why the stamping and
        the change tracking are registered here and not there.

        The session is what the block is handed: `session.new`, `session.dirty` and
        `session.deleted` are what is about to happen, and the records can still be changed —
        which is what makes this the moment to stamp one.

        **Not the place to publish.** The transaction has not committed and can still be undone.

        Answers the database, so several read as one wiring.
        """
        event.listen(
            self.open_session,
            "before_flush",
            lambda session, _context, _instances: run(session),
        )
        return self

    def after_flush(self, run: "Callable[[Session], None]") -> "Database":
        """Runs once the statements have gone out, before the transaction commits.

            database.after_flush(lambda session: ...)

        What a record looks like now that the database has seen it — a generated id, a default
        the engine filled. Still inside the transaction, so this is not where the world is told
        either; a rollback after this leaves nothing behind.
        """
        event.listen(self.open_session, "after_flush", lambda session, _context: run(session))
        return self

    def after_commit(self, run: "Callable[[Session], None]") -> "Database":
        """Runs once the transaction committed: what was written is final.

            database.after_commit(lambda session: ...)

        The moment to let go of what a write made stale — a held read, a cached answer. At
        `after_flush` it is a race: a read between the flush and the commit holds the rows the
        commit is about to change. The session can no longer write here.
        """
        event.listen(self.open_session, "after_commit", run)
        return self

    def after_rollback(self, run: "Callable[[Session], None]") -> "Database":
        """Runs once the transaction was undone: nothing it flushed was written.

            database.after_rollback(lambda session: ...)

        What was noted at a flush to act on at the commit is forgotten here.
        """
        event.listen(self.open_session, "after_rollback", run)
        return self

    @contextmanager
    def session(self) -> Generator[Session]:
        """A unit of work.

            with database.session() as session:   →  commits when the block ends
            an exception inside                   →  rolls back, then re-raises

        Nothing above this line can write a half-finished change. What the engine refuses — a
        lock it would not wait for, a serialization failure at the commit — leaves named by
        what happened (`engine_errors.py`).
        """
        session = self.open_session()
        try:
            with named("the unit of work"):
                yield session
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _refused_in_preview(session: Session) -> None:
    """A flush with something to write, inside `previewing()`, is refused: `record_changes`,
    the commit of a unit of work tracking changes, a record added to the session by hand.

    Outside the guard: a statement sent on a raw connection, and a thread started without the
    framework's context (`ContextExecutor`), which does not see the preview at all.
    """
    if session.new or session.dirty or session.deleted:
        refuse_writing_in_preview("a flush")


def _statement_refused_in_preview(state: Any) -> None:
    """A statement sent through a session inside `previewing()` that writes or locks is refused:
    an INSERT, UPDATE or DELETE, a text statement that is not a read, a read that locks rows.

        UPDATE … / text("DELETE …")             →  WriteInPreview
        SELECT … FOR UPDATE                      →  WriteInPreview
        SELECT … / text("SELECT …")             →  runs, without an autoflush
    """
    if not is_previewing():
        return
    if state.is_select:
        # A read inside a preview never flushes what the unit of work holds pending: that
        # flush would be a write, refused, and the read would fail for nothing. The preview
        # sees what is stored, not the unit of work's unflushed changes.
        state.update_execution_options(autoflush=False)
    if state.is_insert or state.is_update or state.is_delete:
        refuse_writing_in_preview("a statement that writes")
    statement = state.statement
    if isinstance(statement, TextClause) and not _reads_only(statement.text):
        refuse_writing_in_preview("a text statement that is not a read")
    if getattr(statement, "_for_update_arg", None) is not None:
        refuse_writing_in_preview("a read that locks rows")


LEADING_NOISE = re.compile(r"\A(?:\s+|--[^\n]*(?:\n|\Z)|/\*.*?\*/|\()*", re.DOTALL)
"""Whitespace, comments and opening parentheses before a statement's first word."""
LOCKING = re.compile(r"\bFOR\s+(?:NO\s+KEY\s+)?(?:UPDATE|SHARE|KEY\s+SHARE)\b", re.IGNORECASE)
WRITING = re.compile(r"\b(?:INSERT|UPDATE|DELETE|MERGE)\b", re.IGNORECASE)


def _reads_only(sql: str) -> bool:
    """Whether a text statement is one read and nothing else.

        SELECT … / WITH … SELECT … / -- a note\nSELECT …          →  True
        WITH … UPDATE … / SELECT … FOR UPDATE / SELECT 1; DELETE …  →  False

    Conservative on purpose: a single statement, starting with SELECT, or with WITH and no word
    that writes anywhere in it, and no row lock. A rare read that trips it — a column named
    `update` inside a WITH — is refused inside a preview; outside one nothing is asked.
    """
    body = LEADING_NOISE.sub("", sql).rstrip().rstrip(";")
    if ";" in body or LOCKING.search(body):
        return False
    first = body.split(None, 1)[0].upper() if body else ""
    if first == "SELECT":
        return True
    return first == "WITH" and WRITING.search(body) is None
