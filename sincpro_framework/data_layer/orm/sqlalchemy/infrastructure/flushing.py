"""Which records a flush writes: everything the session holds, or only the ones a `save` named.

Context: under `Writes.SAVED` a `save` flushes its own records and nothing else, so a record
changed beside it is not written in passing. The stamping and the change tracking run on every
flush; they read this to touch only what goes out.

SQLAlchemy 2.1 deprecates `Session.flush(objects=...)` — the one way it has to flush some records
and not others — with nothing in its place. `flush_only` keeps calling it and silences that one
warning, nothing else; the day a release removes it, this is the one place to change.
"""

import warnings
from collections.abc import Generator, Iterable, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

FLUSHING = "sincpro_flushing"


@contextmanager
def only(session: Session, records: Iterable[Any]) -> Generator[None]:
    """While the block runs, `goes_out` answers true only for `records` — by identity, so
    an unhashable record is fine.

        with only(session, [invoice]):
            flush_only(session, [invoice])     the stamping touches `invoice` alone
    """
    session.info[FLUSHING] = {id(one) for one in records}
    try:
        yield
    finally:
        session.info.pop(FLUSHING, None)


def goes_out(session: Session, record: Any) -> bool:
    """Whether this flush writes the record."""
    named = session.info.get(FLUSHING)
    return named is None or id(record) in named


def flush_only(session: Session, records: Sequence[Any]) -> None:
    """Flush `records` and nothing else the session holds."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="The `objects` parameter of `Session.flush` is deprecated"
        )
        session.flush(objects=records)
