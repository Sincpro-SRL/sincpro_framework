"""Which records a flush writes: everything the session holds, or only the ones a `save` named.

Context: under `Writes.SAVED` a `save` flushes its own records and nothing else, so a record
changed beside it is not written in passing. The stamping and the change tracking run on every
flush; they read this to touch only what goes out.
"""

from collections.abc import Generator, Iterable
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

FLUSHING = "sincpro_flushing"


@contextmanager
def only(session: Session, records: Iterable[Any]) -> Generator[None]:
    session.info[FLUSHING] = {id(one) for one in records}
    try:
        yield
    finally:
        session.info.pop(FLUSHING, None)


def goes_out(session: Session, record: Any) -> bool:
    """Whether this flush writes the record."""
    named = session.info.get(FLUSHING)
    return named is None or id(record) in named
