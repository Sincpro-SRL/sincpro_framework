"""Opening a unit of work with what it was asked for: before any statement runs, the only time
an isolation level can still be chosen."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session

from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.orm.sqlalchemy.domain.transaction import (
    ISOLATION_LEVELS,
    Isolation,
    Transaction,
)
from sincpro_framework.sincpro_logger import logger


def began(session: Session, transaction: Transaction) -> None:
    """Opens the transaction with what it was asked for, before any statement runs — the
    only moment an isolation level can still be chosen.

    1. Isolation and the engine's own options go on the connection, as SQLAlchemy takes
       them; a level the dialect does not have is refused.
    2. Read-only is also asked of Postgres; every other engine gets the framework's own
       refusal of a write, which holds everywhere.
    3. Final: a timeout is a `SET LOCAL` on Postgres; elsewhere it is warned about and the
       statements run unbounded.
    """
    options: dict[str, Any] = dict(transaction.engine)
    dialect = session.get_bind().dialect.name
    if transaction.isolation is not None:
        # SQLite only serializes, and serializable honours any weaker level asked of it.
        level = Isolation.SERIALIZABLE if dialect == "sqlite" else transaction.isolation
        options["isolation_level"] = ISOLATION_LEVELS[level]
    if transaction.read_only and dialect == "postgresql":
        options["postgresql_readonly"] = True
    if options:
        try:
            session.connection(execution_options=options)
        except ArgumentError as error:
            raise ContractViolation(
                f"{dialect} cannot open this unit of work as asked: {error}"
            ) from error
    if transaction.timeout is None:
        return
    if dialect == "postgresql":
        milliseconds = int(transaction.timeout * 1000)
        session.execute(text(f"SET LOCAL statement_timeout = {milliseconds}"))
    else:
        logger.warning(
            f"timeout={transaction.timeout} is not honoured on {dialect}: "
            "the statements of this unit of work run unbounded"
        )
