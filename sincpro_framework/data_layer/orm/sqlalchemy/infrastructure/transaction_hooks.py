"""What runs once a unit of work has committed, or once it was undone — that one, not every
session the process opens.

    with repository.context() as unit:
        unit.save(invoice)
        unit.after_commit(lambda: publisher.publish_all(invoice.pull_events()))

    a savepoint that ends well   →  what it registered moves to the level around it
    a savepoint that raises      →  its after_rollback runs; its after_commit is dropped
    the commit                   →  every after_commit held, in the order registered
    the rollback                 →  every after_rollback held, in the order registered

Kept as a stack of levels on the session: the unit of work at the bottom, one level per
`savepoint()` open above it. The bottom level answers to SQLAlchemy's `after_commit` and to the
outermost transaction's `after_soft_rollback`; `Repository.savepoint` opens and closes the levels
above it.

A callback that raises is logged and the rest still run: the commit is final, and an exception
here would tell the caller it was not. The session can no longer write when one runs — what it
does with a database goes through another unit of work.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from sincpro_framework.sincpro_logger import logger

LEVELS = "sincpro_after_transaction"

Callback = Callable[[], Any]


@dataclass
class Level:
    after_commit: list[Callback] = field(default_factory=list)
    after_rollback: list[Callback] = field(default_factory=list)


def _levels(session: Session) -> list[Level]:
    return session.info.setdefault(LEVELS, [Level()])


def _run(callbacks: list[Callback], moment: str) -> None:
    for callback in callbacks:
        try:
            callback()
        except Exception:
            logger.exception(f"an {moment} callback raised; the rest still run")


def _committed(session: Session) -> None:
    held = session.info.pop(LEVELS, [Level()])
    _run([one for level in held for one in level.after_commit], "after_commit")


def _rolled_back(session: Session, previous: SessionTransaction) -> None:
    """Context: SQLAlchemy reports a savepoint's rollback here too; that one is
    `Repository.savepoint`'s to close, so only the outermost transaction's is taken."""
    if previous.parent is not None or previous.nested:
        return
    held = session.info.pop(LEVELS, [Level()])
    _run([one for level in held for one in level.after_rollback], "after_rollback")


def install(sessions: sessionmaker) -> None:
    """Listens on every session this factory opens. Once per `Database`."""
    event.listen(sessions, "after_commit", _committed)
    event.listen(sessions, "after_soft_rollback", _rolled_back)


def register(session: Session, moment: str, callback: Callback) -> None:
    """Holds `callback` on the innermost level open: the savepoint in play, or the unit of work.
    The transaction is begun here if nothing has yet, so its end is one SQLAlchemy reports."""
    session.connection()
    getattr(_levels(session)[-1], moment).append(callback)


def opened_savepoint(session: Session) -> None:
    _levels(session).append(Level())


def closed_savepoint(session: Session, undone: bool) -> None:
    """A savepoint that ended well hands what it held to the level around it; one that was
    undone runs its after_rollback and forgets its after_commit."""
    levels = _levels(session)
    level = levels.pop() if len(levels) > 1 else Level()
    if undone:
        _run(level.after_rollback, "after_rollback")
        return
    levels[-1].after_commit.extend(level.after_commit)
    levels[-1].after_rollback.extend(level.after_rollback)
