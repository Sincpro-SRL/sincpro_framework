"""Writing what extends an entity: the rows an extension holds, apart from the entity's own.

    give_rows             a stored record gets the rows of an extension it does not have
    type_as               a core that stores the type of its rows says what a record is now
    complete_consolidated a consolidated record gets the row of an extension it changed and lacks
    remove_rows           a record removed as the entity takes the rows of its extensions with it
    reclassify_rows       the stored type of every row, worked out from the extension rows

`Writing` opens the unit of work and refuses what must not be written; these write inside it.
"""

import dataclasses
from typing import Any

import sqlalchemy
from sqlalchemy import delete, insert, literal, select, update
from sqlalchemy.orm import Session

from sincpro_framework.data_layer import exceptions
from sincpro_framework.data_layer.orm.sqlalchemy.services import extension_typing
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import (
    ModelIntrospection,
)
from sincpro_framework.ddd.entity import extensions
from sincpro_framework.ddd.entity.extensions import identity_of


def type_as(session: Session, extension: type, identity: Any) -> None:
    """A core whose table stores the type of its rows says the record is now `extension`."""
    stored = extension_typing.STORED_TYPE.get(
        extension_typing.ROOT_OF.get(extension, extension)
    )
    if stored is None:
        return
    session.execute(
        update(stored.table)
        .where(list(stored.table.primary_key.columns)[0] == identity)
        .values({stored.name: identity_of(extension)})
    )


def retype(connection: Any, root: type, identity: Any) -> None:
    """A core whose table stores the type of its rows works it out again for one record, from the
    extension rows it has now: after a consolidated record wrote several of them at once."""
    stored = extension_typing.STORED_TYPE.get(root)
    if stored is None:
        return
    connection.execute(
        update(stored.table)
        .where(list(stored.table.primary_key.columns)[0] == identity)
        .values({stored.name: extension_typing.derived(root)})
    )


def defaults_of(level: type, names: tuple[str, ...]) -> dict[str, Any]:
    """The default of each of these fields of a class: what a new row of the extension starts with."""
    return {
        one.name: (
            one.default_factory()
            if one.default_factory is not dataclasses.MISSING
            else None if one.default is dataclasses.MISSING else one.default
        )
        for one in dataclasses.fields(level)
        if one.name in names
    }


def give_rows(session: Session, record: Any, extension: type) -> None:
    """Gives a stored record the rows of an extension it does not have: one per level between its
    class and `extension`, the shallowest first, each with the defaults of its fields.

        give_rows(session, move, MoveDelivery)    # the Move `get` returned, no delivery
            ->  INSERT INTO move_delivery (id, route) VALUES (move.id, NULL)
        give_rows(session, move, MoveTracking)    # MoveTracking extends MoveDelivery
            ->  a move_delivery row, then a move_tracking row; the move itself not touched

    An extension that stores its columns in the entity's own table has nothing to insert. A
    core that stores the type of its rows is told the record is `extension` now.
    """
    missing = sorted(
        (
            level
            for level in extension.__mro__
            if level in extension_typing.STORED_IN and not isinstance(record, level)
        ),
        key=lambda level: len(level.__mro__),
    )
    identity = getattr(record, ModelIntrospection.describe(type(record)).identity)
    for level in missing:
        table, key = extension_typing.STORED_IN[level]
        if key is None:
            continue
        added = next(
            one.fields for one in extensions.under(level.__mro__[1]) if one.entity is level
        )
        session.execute(
            insert(table).values({key.name: identity, **defaults_of(level, added)})
        )
    type_as(session, extension, identity)


def complete_consolidated(session: Session, records: list[Any]) -> None:
    """A stored consolidated record that changed a field of an extension it has no row of gets the
    row first, holding what the record says: the update that follows finds it.

        consolidated (move with no move_fiscal row), record.cufd = "A1"
        complete_consolidated(session, [record])
            ->  INSERT INTO move_fiscal (id, cufd) VALUES (record.id, 'A1'); type redone
        record.route changed and its move_delivery row exists   ->  nothing inserted

    1. Each record that is a stored consolidated class — anything else is skipped.
    2. Per extension it spans: skipped when none of the fields that table holds changed, or
       when the table already has the record's row.
    3. Otherwise a row under the record's identity, with the record's values of those fields.
    Final: the root's stored type of the record worked out again, when it stores one.
    """
    for record in records:
        parts = extension_typing.CONSOLIDATED.get(type(record))
        state = sqlalchemy.inspect(record, raiseerr=False)
        if not parts or state is None or not state.has_identity:
            continue
        identity = getattr(record, ModelIntrospection.describe(type(record)).identity)
        for table, key, names, level in parts:
            if not any(state.attrs[name].history.has_changes() for name in names):
                continue
            if session.execute(
                select(literal(1)).where(key == identity).select_from(table)
            ).first():
                continue
            values = {name: getattr(record, name) for name in names}
            session.execute(insert(table).values({key.name: identity, **values}))
            retype(session, extension_typing.ROOT_OF.get(level, level), identity)


def remove_rows(session: Session, records: list[Any]) -> None:
    """A record removed as the entity, while rows of an extension still hold its key, takes them
    with it: the extension's rows are the entity's, and nothing else points at them. The deepest
    go first, because each points up the chain.

        remove_rows(session, [move])    # read as Move; rows in move_delivery, move_tracking
            ->  DELETE FROM move_tracking WHERE id = :id; DELETE FROM move_delivery WHERE …
        remove_rows(session, [tracking_move])   ->  nothing: it is the deepest class

    An extension that stores its columns in the entity's table goes with the entity's row; an
    unmapped record is skipped.
    """
    for record in records:
        if sqlalchemy.inspect(type(record), raiseerr=False) is None:
            continue
        identity = getattr(record, ModelIntrospection.describe(type(record)).identity)
        below = sorted(
            (
                one
                for registered in extensions.EXTENSIONS.values()
                for one in registered
                if not isinstance(record, one.entity) and issubclass(one.entity, type(record))
            ),
            key=lambda one: -one.depth,
        )
        for one in below:
            table, key = extension_typing.STORED_IN[one.entity]
            if key is not None:
                session.execute(delete(table).where(key == identity))


def reclassify_rows(session: Session, entity: type) -> int:
    """Writes the stored type of every row of a core that stores it, from the extension rows that
    exist. Answers how many rows it set.

        reclassify_rows(session, Move)          ->  1200, every row of move
        reclassify_rows(session, MoveDelivery)  ->  1200 as well: its root's rows are written
        reclassify_rows(session, Partner)       ->  ProgrammingError, no extension_type column

    One `UPDATE` of the whole table, the type worked out per row by `derived`.
    """
    root = extension_typing.ROOT_OF.get(entity, entity)
    stored = extension_typing.STORED_TYPE.get(root)
    if stored is None:
        raise exceptions.ProgrammingError(
            f"{root.__name__} stores no type for its rows: its table has no "
            f"{extension_typing.STORED_ATTRIBUTE} column"
        )
    result = session.execute(
        update(stored.table).values({stored.name: extension_typing.derived(root)})
    )
    return result.rowcount  # type: ignore[attr-defined]
