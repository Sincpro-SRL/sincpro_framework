"""A database: one engine, one session factory, and what every statement reports.

`session()` commits when the block ends and rolls back when it raises, so nothing above this
line can write a half-finished change.

Two things every database does without being asked. It stamps the write: `updated_at` on
whatever it is about to update, and `created_by` / `updated_by` when the provider said who is
writing, so those are true through every write path, including a raw session somebody opens by
hand. And it observes itself: every statement goes to the logger at DEBUG, to a span when the
process is collecting, and every failure to the error tracker; see `observability.py`.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

from sincpro_log.logger import LoggerProxy
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from sincpro_framework.ddd.entity import utc_now
from sincpro_framework.orm.sqlalchemy.observability import observe


def _stamping(actor: Callable[[], str | None] | None) -> Callable[[Session, Any, Any], None]:
    """The `before_flush` hook that writes when, and who, without a use case remembering to.

        an updated record  →  updated_at, and updated_by when an actor is configured
        a new record       →  created_by, when an actor is configured

    `is_modified` and not membership in `dirty` alone: an attribute set to the value it already
    held marks the object dirty without changing a column, and stamping that would record a
    write that never happened. The actor is read per flush and never raises: a request with no
    user behind it writes `None` rather than failing the transaction.
    """

    def who() -> str | None:
        if actor is None:
            return None
        try:
            return actor()
        except Exception:
            return None

    def stamp(session: Session, _flush_context: Any, _instances: Any) -> None:
        now = utc_now()
        acting = who()
        for record in session.dirty:
            if hasattr(record, "updated_at") and session.is_modified(record):
                record.updated_at = now
                if acting is not None and hasattr(record, "updated_by"):
                    record.updated_by = acting
        if acting is None:
            return
        for record in session.new:
            if hasattr(record, "created_by") and record.created_by is None:
                record.created_by = acting

    return stamp


class Database:

    def __init__(
        self,
        url: str,
        echo: bool = False,
        logger: LoggerProxy | None = None,
        actor: Callable[[], str | None] | None = None,
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
        `Database(url, actor=lambda: bus.context.get("user.id"))`. It is read on every flush,
        so one database serves every request of a process.
        """
        self.url = url
        self.name = make_url(url).database or ""
        self.engine: Engine = create_engine(url, echo=echo, **engine_options)
        observe(self.engine, self.name, logger)

        self.open_session = sessionmaker(
            bind=self.engine, expire_on_commit=False, autoflush=True
        )
        self.actor = actor
        event.listen(self.open_session, "before_flush", _stamping(actor))

    @contextmanager
    def session(self) -> Generator[Session]:
        """A unit of work.

            with database.session() as session:   →  commits when the block ends
            an exception inside                   →  rolls back, then re-raises

        Nothing above this line can write a half-finished change.
        """
        session = self.open_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
