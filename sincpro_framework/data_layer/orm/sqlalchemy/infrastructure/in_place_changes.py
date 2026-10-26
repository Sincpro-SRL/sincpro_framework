"""A value changed inside — a key of a JSON object, an item of a list, a field of a model stored in
a column — is written like a value assigned.

    invoice = unit.get(Invoice, invoice_id)
    invoice.address.city = "Sucre"               # no assignment: the same object, changed
    unit.save(invoice)                           # written, with its Updated event

**Why the framework and not the project.** SQLAlchemy writes what was *assigned*: it never looks
inside a value, so a change made in place is lost without a word — the save answers, the row still
holds the old value. Its own answer is `MutableDict`/`MutableList`, which sees the first level only, and
a model or a dataclass not at all. Every project would have to remember it, column by column.

**How.** When a record is read, a copy of each value that can change inside is stored beside it.
Before a write, each value still being the object that was read is compared with its copy, and a
difference marks the column changed, with the copy as its value before — so the change tracking
and the stamping see it as any other change. After the write, the copies are taken again.

    read      the value · its copy                       stored on the record's state
    write     same object, unlike its copy → changed     a save, a flush, a commit
    written   copies taken again                         after the flush

A value assigned is the engine's to see, not this; a value read again (`refresh`, an expired
column loaded) is copied again. A value that cannot say whether it equals another — an object
without `__eq__` — is left out: it would be "changed" at every write; assign it.

**What it costs.** Only the columns that can hold a structure are looked at — a text, a number, a
date column never is, so an entity without one pays one call per row read. A `TypedJson` value is
copied as its JSON (pydantic-core, several times faster than a deep copy) and rebuilt only when it
changed; any other value is deep-copied. At a write, a `save` compares its own records; a flush or
a commit of the whole session compares every record it holds that has a copy — the dirty checking
of Hibernate, over these records only.

**Only the sessions of a `Database`.** The copy is taken on the mappers' `load` and `refresh`,
listened to on every mapper; a session a `Database` did not open is not marked, and nothing is
copied for it.
"""

import copy
from collections.abc import Iterable
from datetime import date, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    Interval,
    LargeBinary,
    Numeric,
    String,
    Time,
    Uuid,
    event,
)
from sqlalchemy.orm import Mapper, Session, attributes
from sqlalchemy.orm.base import NO_VALUE
from sqlalchemy.orm.state import InstanceState
from sqlalchemy.types import TypeEngine

from sincpro_framework.data_layer.orm.sqlalchemy.domain.custom_fields import TypedJson
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.flushing import (
    FLUSHING,
    goes_out,
)

IN_PLACE = "sincpro_in_place"
"""Marks a session whose records are watched, and keys the copies on a record's state."""
WATCHED = "sincpro_in_place_watched"
"""The records of a session that have a copy: the only ones a write compares."""

SCALAR_COLUMNS = (
    String,
    Integer,
    Numeric,
    Float,
    Boolean,
    Date,
    DateTime,
    Time,
    Interval,
    Uuid,
    LargeBinary,
)
"""Column types whose value nothing can change inside: never looked at. `Enum` is a `String`."""
UNCHANGEABLE = (
    str,
    bytes,
    int,
    float,
    bool,
    Decimal,
    date,
    time,
    timedelta,
    UUID,
    Enum,
    tuple,
    frozenset,
)
"""Values nothing can change inside, whatever column holds them. A `datetime` is a `date`."""

STRUCTURED: dict[Mapper[Any], tuple[Any, dict[str, TypeEngine[Any]]]] = {}
"""Per mapper, its columns that can hold a structure — worked out once, and again when the mapper
gained a column (SQLAlchemy builds a new `column_attrs` then)."""


def _structured(mapper: Mapper[Any]) -> dict[str, TypeEngine[Any]]:
    """The columns of `mapper` whose type is not a plain scalar, by attribute key — the ones
    a value changed inside can live in. Cached per mapper until its `column_attrs` change.

        Invoice(id TEXT, address TypedJson, lines JSON)   ->  {"address": …, "lines": …}
    """
    columns = mapper.column_attrs
    found = STRUCTURED.get(mapper)
    if found is None or found[0] is not columns:
        found = (
            columns,
            {
                prop.key: prop.columns[0].type
                for prop in columns
                if not isinstance(prop.columns[0].type, SCALAR_COLUMNS)
            },
        )
        STRUCTURED[mapper] = found
    return found[1]


def _changeable(value: Any) -> bool:
    """Whether `value` can be changed inside: a dict, list, set or bytearray, or an object
    that compares by value (a model, a dataclass) — never `None` nor an `UNCHANGEABLE` scalar.
    """
    if value is None or isinstance(value, UNCHANGEABLE):
        return False
    return (
        isinstance(value, dict | list | set | bytearray)
        or type(value).__eq__ is not object.__eq__
    )


def _copied(column: TypeEngine[Any], value: Any) -> Any:
    """A copy to compare with later. A record may hold what was assigned to it — a `dict` of the
    shape — until it is read again: its JSON is copied all the same, without a warning."""
    if isinstance(column, TypedJson):
        return column.adapter.dump_json(value, warnings=False)
    return copy.deepcopy(value)


def _differs(column: TypeEngine[Any], value: Any, copied: Any) -> bool:
    """Whether `value` is no longer what was copied. A value its type cannot serialize any more is
    changed: the write says what is wrong with it."""
    if isinstance(column, TypedJson):
        try:
            return column.adapter.dump_json(value, warnings=False) != copied
        except ValueError:
            return True
    return value != copied


def _watched(session: Session) -> set[InstanceState[Any]]:
    """A plain set: a state holds its record weakly, as the identity map does, and the set lives
    as long as the session."""
    return session.info.setdefault(WATCHED, set())


def _copy_values(
    session: Session, state: InstanceState[Any], keys: Iterable[str] | None = None
) -> None:
    """Stores a copy of each value of the record that can change inside — of `keys`, or of every
    column that can hold one — and watches the record when it has one."""
    structured = _structured(state.mapper)
    if not structured:
        return
    copies: dict[str, tuple[Any, Any]] = (
        {} if keys is None else dict(state.info.get(IN_PLACE, {}))
    )
    for key in structured if keys is None else keys:
        column = structured.get(key)
        if column is None:
            continue
        value = state.dict.get(key, NO_VALUE)
        if _changeable(value):
            copies[key] = (value, _copied(column, value))
        else:
            copies.pop(key, None)
    if copies:
        state.info[IN_PLACE] = copies
        _watched(session).add(state)
    elif IN_PLACE in state.info:
        del state.info[IN_PLACE]


def _loaded(target: Any, context: Any) -> None:
    """The mapper's `load` hook: a record read by an `IN_PLACE` session gets its copies."""
    if context.session.info.get(IN_PLACE):
        _copy_values(context.session, attributes.instance_state(target))


def _refreshed(target: Any, context: Any, keys: Iterable[str] | None) -> None:
    """The mapper's `refresh` hook: the copies of the refreshed `keys` taken again."""
    if context.session.info.get(IN_PLACE):
        _copy_values(context.session, attributes.instance_state(target), keys)


def _attached(session: Session, record: Any) -> None:
    """A record read by another session still carries its copies, and is watched by this one."""
    state = attributes.instance_state(record)
    if session.info.get(IN_PLACE) and state.info.get(IN_PLACE):
        _watched(session).add(state)


def changed_in_place(session: Session, records: Iterable[Any] | None = None) -> None:
    """Marks changed every column whose value changed inside since it was read or written: of
    `records`, or of every record the session watches.

    1. A column assigned since is left to the engine: it already knows, and its value is no
       longer the object the copy was taken from.
    2. The value before is the copy, so the change tracking reads `(before, after)`.

        invoice read with address {"city": "La Paz"}; invoice.address["city"] = "Sucre"
        changed_in_place(session, [invoice])
            ->  None; address flagged modified, its value before {"city": "La Paz"}
        invoice.address = {"city": "Sucre"}   assigned instead: left to the engine

    A record without copies, already collected, or held by another session is skipped.
    """
    states = (
        list(_watched(session))
        if records is None
        else [attributes.instance_state(one) for one in records]
    )
    for state in states:
        copies: dict[str, tuple[Any, Any]] | None = state.info.get(IN_PLACE)
        record = state.obj()
        if not copies or record is None or state.session_id != session.hash_key:
            continue
        structured = _structured(state.mapper)
        for key, (read, copied) in copies.items():
            if state.dict.get(key, NO_VALUE) is not read or key in state.committed_state:
                continue
            column = structured[key]
            if _differs(column, read, copied):
                attributes.flag_modified(record, key)
                # The value before, from its copy: a `TypedJson` copy is JSON, read back as
                # its type.
                state.committed_state[key] = (
                    column.adapter.validate_json(copied)
                    if isinstance(column, TypedJson)
                    else copied
                )


def _before_flush(session: Session, _context: Any, _instances: Any) -> None:
    """A flush of the whole session compares what it watches. A `save` compared its own records
    before flushing them, and flushes nothing else."""
    if session.info.get(FLUSHING) is None:
        changed_in_place(session)


def _after_flush(session: Session, _context: Any) -> None:
    """What a flush wrote is what a later change is compared with."""
    for record in (*session.new, *session.dirty):
        if goes_out(session, record):
            _copy_values(session, attributes.instance_state(record))


def watch_in_place(sessions: Any) -> None:
    """Watches what the sessions of `sessions` — a `sessionmaker` — read and write.

    The mappers' `load` and `refresh` are listened to once per process, and copy only for a
    session marked `IN_PLACE`; the rest is registered on `sessions` alone.

        open_session = sessionmaker(bind=engine, info={IN_PLACE: True})
        watch_in_place(open_session)   ->  None
        with open_session() as session:
            invoice = session.get(Invoice, 1)        # tags, a JSON list column
            invoice.tags.append("urgent"); session.commit()
                ->  the tags column written, though it was never assigned

    On `sessions`: a flush compares what it watches before, takes the copies again after; a
    commit compares once more; a record attached from another session is watched too.
    """
    if not event.contains(Mapper, "load", _loaded):
        event.listen(Mapper, "load", _loaded)
        event.listen(Mapper, "refresh", _refreshed)
    event.listen(sessions, "before_flush", _before_flush)
    event.listen(sessions, "after_flush", _after_flush)
    event.listen(sessions, "before_commit", changed_in_place)
    event.listen(sessions, "detached_to_persistent", _attached)
