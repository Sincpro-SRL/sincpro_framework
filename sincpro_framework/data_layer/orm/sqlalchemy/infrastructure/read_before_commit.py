"""A record handed back after its unit of work answers every column it maps — a `computed(sql=…)`
field and a field stored in an extension's slot included.

    with store.context() as unit:
        unit.save(bill)
    bill.balance            ->  Decimal("60"), read before the commit

**Why.** A flush expires what it cannot know without asking the database: an expression mapped
as a column, and a value the engine generated without handing it back. SQLAlchemy reads such a
column again on its next access, through the record's session; once the session closed there is
none, and the access raises `DetachedInstanceError`. A use case answers what it saved after its
unit of work closed, so an `Edit` of an entity with a computed field failed at its answer.

**How.** Each flush notes what it wrote. Before each commit, what is still pending is flushed —
the commit's own flush would expire it again — and the columns a flush left expired on what was
written are read in one statement per mapper and per thousand records, by primary key: the
values the transaction is about to commit, read while the session can still read. An
`after_commit` callback reads them too, where the session can no longer run a statement.

**What it costs.** No statement for a record a flush left nothing expired on — one that maps no
expression and whose columns the engine handed back. One SELECT per mapper per thousand written
records otherwise; a hundred thousand `Bill` inserted and read back, measured on SQLite, spend
under two seconds here.
"""

from itertools import batched
from typing import Any

from sqlalchemy import event, select, tuple_
from sqlalchemy.orm import InstanceState, Mapper, Session, attributes, load_only, sessionmaker

WRITTEN = "sincpro_written"
"""The key of `session.info` under which the flushes of a transaction note what they wrote."""

CHUNK = 1000
"""Records read per statement: under the bound parameters every engine accepts."""


def _noted(session: Session, _context: Any) -> None:
    """The `after_flush` hook: what this flush wrote, noted for the commit. A record the flush
    left alone is noted too — nothing expired on it, so the commit reads nothing for it."""
    written: set[InstanceState] = session.info.setdefault(WRITTEN, set())
    written.update(attributes.instance_state(one) for one in (*session.new, *session.dirty))


def _expired_by_mapper(
    session: Session,
) -> dict[Mapper, tuple[set[str], list[tuple[Any, ...]]]]:
    """The noted records still held by `session` with a column left to read: per mapper, the
    columns expired on any of them and the primary key of each.

        a new Bill and an edited one, balance expired   ->  {Bill: ({"balance"}, [(id1,), (id2,)])}

    A record deleted, detached or collected since its flush is skipped: nobody reads it here.
    """
    found: dict[Mapper, tuple[set[str], list[tuple[Any, ...]]]] = {}
    for state in session.info.pop(WRITTEN, ()):
        identity = state.identity
        if (
            identity is None
            or not state.expired_attributes
            or state.session_id != session.hash_key
            or state.deleted
            or state.obj() is None
        ):
            continue
        expired, identities = found.setdefault(state.mapper, (set(), []))
        expired.update(state.expired_attributes)
        identities.append(identity)
    return found


def _read_again(
    session: Session, mapper: Mapper, expired: set[str], identities: list[tuple[Any, ...]]
) -> None:
    """The `expired` columns of the records of `mapper` named by `identities`, read by primary
    key into the records the session holds.

        Bill ×1 500, balance expired   ->  SELECT bill.total - bill.paid, bill.id … WHERE id IN (…1 000)
                                           SELECT … WHERE id IN (…500)

    A record already in the session is filled where a column of it is unloaded, and nothing it
    holds is overwritten: the loading SQLAlchemy does for any row it already has.
    """
    keys = [prop.key for prop in mapper.column_attrs if prop.key in expired]
    if not keys:
        return
    primary = [
        mapper.get_property_by_column(column).class_attribute for column in mapper.primary_key
    ]
    single = len(primary) == 1
    key = primary[0] if single else tuple_(*primary)
    only = load_only(*(mapper.attrs[name].class_attribute for name in keys))
    for chunk in batched(identities, CHUNK):
        ids = [identity[0] for identity in chunk] if single else list(chunk)
        session.execute(select(mapper.class_).where(key.in_(ids)).options(only)).all()


def _read_before_commit(session: Session) -> None:
    """The `before_commit` hook: the last flush, then what the flushes left expired read again.

    1. What is pending flushed now — nothing when the session is clean — so the commit's own
       flush has nothing left to expire.
    Final: each mapper's expired columns read in chunks (`_read_again`); nothing noted is no
    statement.
    """
    session.flush()
    for mapper, (expired, identities) in _expired_by_mapper(session).items():
        _read_again(session, mapper, expired, identities)


def read_before_commit(sessions: sessionmaker) -> None:
    """Every session of `sessions` reads, before it commits, what its flushes left expired.

        open_session = sessionmaker(bind=engine)
        read_before_commit(open_session)
        with open_session() as session:
            session.add(bill); session.commit()
        bill.balance        ->  answered, the session closed

    Registered after the in-place watch: its `before_commit` marks a value changed inside, and
    the flush here writes it.
    """
    event.listen(sessions, "after_flush", _noted)
    event.listen(sessions, "before_commit", _read_before_commit)
