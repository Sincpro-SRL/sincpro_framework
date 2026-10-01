"""What the engine reported, named by what happened.

    with named("one of 3 records (Invoice)"):
        session.flush()

    23505 · 1062 · UNIQUE, PRIMARYKEY                →  DuplicateAggregate     resolve the twin
    23503 · 1451, 1452 · FOREIGNKEY                  →  ConstraintViolation    a foreign key
    23502, 23514 · 1048, 3819 · NOTNULL, CHECK       →  ConstraintViolation    a rule of the table
    40001, 40P01 · 1213, 1205 · BUSY, LOCKED         →  TransactionConflict    run it again
    55P03, 57014 · 3572                              →  TimedOut               the bound it was given
    a newer version than the one written over        →  StaleAggregate         read again

Read in the order the drivers make reliable: the SQLSTATE Postgres drivers carry, the error
number MySQL ones carry, the name SQLite gives since Python 3.11, and the message last. An error
that matches none is not translated: a driver failure nobody can name is better seen as it is.
"""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm.exc import StaleDataError

from sincpro_framework.ddd.exceptions import (
    ConstraintViolation,
    DomainError,
    DuplicateAggregate,
    StaleAggregate,
    TimedOut,
    TransactionConflict,
)

Fact = tuple[type[DomainError], str]

DUPLICATE: Fact = (DuplicateAggregate, "collides with a record already stored")
FOREIGN_KEY: Fact = (ConstraintViolation, "breaks a foreign key")
RULE: Fact = (ConstraintViolation, "breaks a rule the table holds")
CONFLICT: Fact = (
    TransactionConflict,
    "lost to another transaction; run the unit of work again on a fresh read",
)
BOUND: Fact = (
    TimedOut,
    "stopped at a bound it was given — a lock it was told not to wait for, or its timeout",
)

SQLSTATES: dict[str, Fact] = {
    "23505": DUPLICATE,
    "23503": FOREIGN_KEY,
    "23502": RULE,
    "23514": RULE,
    "40001": CONFLICT,
    "40P01": CONFLICT,
    "55P03": BOUND,
    "57014": BOUND,
}
MYSQL_ERRORS: dict[int, Fact] = {
    1062: DUPLICATE,
    1451: FOREIGN_KEY,
    1452: FOREIGN_KEY,
    1048: RULE,
    3819: RULE,
    1213: CONFLICT,
    1205: CONFLICT,
    3572: BOUND,
}
SQLITE_ERRORS: dict[str, Fact] = {
    "SQLITE_CONSTRAINT_UNIQUE": DUPLICATE,
    "SQLITE_CONSTRAINT_PRIMARYKEY": DUPLICATE,
    "SQLITE_CONSTRAINT_FOREIGNKEY": FOREIGN_KEY,
    "SQLITE_CONSTRAINT_NOTNULL": RULE,
    "SQLITE_CONSTRAINT_CHECK": RULE,
    "SQLITE_BUSY": CONFLICT,
    "SQLITE_LOCKED": CONFLICT,
}
MESSAGES: dict[str, Fact] = {
    "UNIQUE constraint failed": DUPLICATE,
    "FOREIGN KEY constraint failed": FOREIGN_KEY,
    "NOT NULL constraint failed": RULE,
    "CHECK constraint failed": RULE,
    "database is locked": CONFLICT,
}


def _fact_of(orig: Any) -> Fact | None:
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate in SQLSTATES:
        return SQLSTATES[sqlstate]
    number = orig.args[0] if getattr(orig, "args", None) else None
    if isinstance(number, int) and number in MYSQL_ERRORS:
        return MYSQL_ERRORS[number]
    name = getattr(orig, "sqlite_errorname", None)
    if name in SQLITE_ERRORS:
        return SQLITE_ERRORS[name]
    message = str(orig)
    return next(
        (fact for prefix, fact in MESSAGES.items() if message.startswith(prefix)), None
    )


@contextmanager
def named(subject: str) -> Generator[None]:
    """Runs a block against the engine and re-raises what it refused as the vocabulary's own
    exception about `subject`, carrying the engine's message."""
    try:
        yield
    except StaleDataError as error:
        raise StaleAggregate(
            f"{subject} changed since it was read; read it again before writing — {error}"
        ) from error
    except DBAPIError as error:
        fact = _fact_of(error.orig)
        if fact is None:
            raise
        exception, phrase = fact
        raise exception(f"{subject} {phrase}: {error.orig}") from error
