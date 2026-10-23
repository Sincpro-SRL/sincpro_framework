"""Mapping what extends an entity: how the mapper is told a class adds to another, where its
columns live, and what a module checks before it is allowed to start.

    root         the entity a module extends: its reads answer the extension each row has
    extension    a subclass with a table of its own, or with columns in its parent's table
    consolidated one flat class over the outer join of an entity and its extensions

`map_aggregates` calls these for each class it maps and holds none of this itself. The registry
they fill is `ddd/entity/extensions.py` (whose a field is), and `extension_typing.py` (how a row is
recognised, where an extension is stored).
"""

import dataclasses
from collections.abc import Collection
from typing import Any

import sqlalchemy
from sqlalchemy import Column, Join, Table, event, or_
from sqlalchemy.orm import Mapper, column_property, registry

from sincpro_framework.data_layer import exceptions
from sincpro_framework.data_layer.orm.sqlalchemy.domain.consolidation import Consolidated
from sincpro_framework.data_layer.orm.sqlalchemy.services import (
    extension_typing,
    extension_writing,
)
from sincpro_framework.data_layer.orm.sqlalchemy.services.data_mapper import (
    parent_table,
    refuse_a_table_that_does_not_extend,
)
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import describe
from sincpro_framework.ddd.entity import extensions
from sincpro_framework.ddd.entity.extensions import identity_of

SHARED_COLUMNS = frozenset({"id", "created_at", "updated_at", "version"})
"""What an entity and each of its extensions all have: written to each table, read from the core's."""


def stored_type_of_the_row(_mapper: Any, _connection: Any, record: Any) -> None:
    """A core whose table stores the type of its rows writes it when a row is inserted: the class
    the record is, and nothing to work out when it is read."""
    setattr(record, extension_typing.STORED_ATTRIBUTE, identity_of(type(record)))


def fields_added(entity: type, parent: type, table: Table) -> tuple[str, ...]:
    """The fields an extension adds to its parent: its own, that are columns of its table."""
    if not dataclasses.is_dataclass(entity):
        return ()
    inherited = (
        {field.name for field in dataclasses.fields(parent)}
        if dataclasses.is_dataclass(parent)
        else set()
    )
    return tuple(
        field.name
        for field in dataclasses.fields(entity)
        if field.name not in inherited and field.name in table.c
    )


def refuse_a_column_the_parents_have(entity: type, table: Table, parent: type) -> None:
    """Context: SQLAlchemy joins two same-named columns of an inheritance chain under one
    attribute with a warning, and a save then writes the extension's value to the parent's
    column too. A core release that adds a field an extension already has would silently
    overwrite the core's own data; it is refused where the extension is mapped, naming both.
    """
    held = {
        column.name: other.name
        for other in sqlalchemy.inspect(parent).tables
        for column in other.c
        if not column.primary_key
    }
    clashing = [
        f"{column.name} (in {held[column.name]} and {table.name})"
        for column in table.c
        if not column.primary_key and column.name in held
    ]
    if clashing:
        raise exceptions.ProgrammingError(
            f"{entity.__name__} extends {parent.__name__} but both have a column of the same "
            f"name: {', '.join(clashing)}. Rename the extension's column, or mean the parent's "
            "by not declaring it again"
        )


def refuse_a_field_the_core_lacks(entity: type, names: Collection[str]) -> None:
    """Context: a module that reads `Move.total` says so (`uses`), and the framework checks it when
    the module is mapped. A core release that renamed or removed the field is found where the
    module starts, naming the field, and not by a query that returns nothing at the first request.
    """
    own = {name for name, field in describe(entity).fields.items() if field.owner is None}
    missing = sorted(set(names) - own)
    if missing:
        raise exceptions.ProgrammingError(
            f"a module reads {entity.__name__}.{', '.join(missing)}, which {entity.__name__} "
            f"does not have: it has {', '.join(sorted(own))}"
        )


def refuse_a_contract_out_of_range(entity: type, wanted: str) -> None:
    """Context: the core publishes a contract version on an entity (`CONTRACT = "2.3"`: a major
    that breaks, a minor that adds) and a module declares the one it was written against. The
    same major and at least that minor is a core it works with; anything else stops the
    application from starting, because a half-working ERP is worse than one that does not start.
    """
    have = getattr(entity, "CONTRACT", None)
    if have is None:
        raise exceptions.ProgrammingError(
            f"a module requires {entity.__name__} contract {wanted}, and {entity.__name__} "
            "publishes no CONTRACT version"
        )
    want_major, want_minor = (int(part) for part in wanted.split(".")[:2])
    have_major, have_minor = (int(part) for part in str(have).split(".")[:2])
    if have_major != want_major or have_minor < want_minor:
        raise exceptions.ProgrammingError(
            f"a module requires {entity.__name__} contract {wanted} (the same major, at least "
            f"that minor) and the core publishes {have}"
        )


def consolidated_columns(
    mapper_registry: registry, entity: type, consolidation: Consolidated
) -> tuple[Join, dict[str, Any], set[str], Table]:
    """The join a consolidated class is mapped against, how each of its fields maps, the columns
    it leaves out, and the core's table (its key identifies the record, its version guards the
    write).

        in      MoveConsolidated, Consolidated(Move, MoveDelivery, MoveFiscal)
        out     move LEFT JOIN move_delivery LEFT JOIN move_fiscal,
                {id: [move.id, move_delivery.id, move_fiscal.id], route: move_delivery.route, …}

    The extensions are outer-joined: a record without a row of one reads it as empty. A field two
    tables both have is refused, naming both, unless it is one the entity itself owns in every
    table (`id`, `created_at`, `updated_at`, `version`), which is written to each.
    """
    tables = [
        parent_table(mapper_registry, one)
        for one in (consolidation.core, *consolidation.extensions)
    ]
    core_table, *extension_tables = tables
    key = list(core_table.primary_key.columns)[0]
    joined: Join | Table = core_table
    for table in extension_tables:
        joined = joined.outerjoin(table, list(table.primary_key.columns)[0] == key)
    wanted = (
        {one.name for one in dataclasses.fields(entity)}
        if dataclasses.is_dataclass(entity)
        else set()
    )
    owners: dict[str, list[Column]] = {}
    for table in tables:
        for column in table.c:
            owners.setdefault(column.name, []).append(column)
    properties: dict[str, Any] = {}
    unread = {name for name in owners if name not in wanted}
    for name, columns in owners.items():
        if name in unread:
            continue
        if len(columns) == 1:
            properties[name] = columns[0]
        elif name in SHARED_COLUMNS:
            properties[name] = columns
        else:
            holders = ", ".join(column.table.name for column in columns)
            raise exceptions.ProgrammingError(
                f"{entity.__name__} would read {name} from {holders}: a field of an entity, "
                "an extension and another extension cannot share a name; rename one"
            )
    return joined, properties, unread, core_table  # type: ignore[return-value]


def map_consolidated(
    mapper_registry: registry,
    entity: type,
    consolidation: Consolidated,
    properties: dict[str, Any],
) -> Mapper:
    """Maps one flat class over the outer join of an entity and its extensions, and notes which
    table holds which of its fields, so a save can give a record the row it lacks."""
    joined, columns, unread, core_table = consolidated_columns(
        mapper_registry, entity, consolidation
    )
    mapper = mapper_registry.map_imperatively(
        entity,
        joined,
        properties={**columns, **properties},
        exclude_properties=unread,
        primary_key=list(core_table.primary_key.columns),
        version_id_col=core_table.c.version,
        confirm_deleted_rows=False,  # an extension the record never had has no row to delete
    )
    extension_typing.CONSOLIDATED[entity] = [
        (
            table,
            list(table.primary_key.columns)[0],
            tuple(
                name
                for name, column in columns.items()
                if isinstance(column, Column) and column.table is table
            ),
            level,
        )
        for level, table in (
            (level, parent_table(mapper_registry, level))
            for level in consolidation.extensions
        )
    ]
    if consolidation.core in extension_typing.STORED_TYPE:
        identity_name = core_table.primary_key.columns.values()[0].key

        def typed_after_insert(_mapper: Any, connection: Any, record: Any) -> None:
            extension_writing.retype(
                connection, consolidation.core, getattr(record, identity_name)
            )

        event.listen(mapper, "after_insert", typed_after_insert)
    return mapper


def root_options(entity: type, table: Table, strict: Collection[type]) -> dict[str, Any]:
    """What the mapper needs of an entity so that what extends it later can be read as itself: the
    type of a row as its discriminator, and the identity of the root."""
    options: dict[str, Any] = {
        "polymorphic_on": extension_typing.TYPE_ATTRIBUTE,
        "polymorphic_identity": identity_of(entity),
        "properties": {
            extension_typing.TYPE_ATTRIBUTE: column_property(
                extension_typing.ExtensionType(entity)
            )
        },
    }
    extension_typing.ROOT_OF[entity] = entity
    if entity in strict:
        extension_typing.STRICT.add(entity)
    if extension_typing.STORED_ATTRIBUTE in table.c:
        extension_typing.STORED_TYPE[entity] = table.c[extension_typing.STORED_ATTRIBUTE]
    return options


def extension_options(
    mapper_registry: registry,
    entity: type,
    table: Table,
    parent: type,
    strict: Collection[type],
) -> tuple[Table | None, dict[str, Any]]:
    """What the mapper needs of a class that extends another: the table it names — none when its
    columns are the parent's own — and how a row is recognised as it."""
    above = parent_table(mapper_registry, parent)
    root = extension_typing.ROOT_OF[parent]
    shared = table is above
    options: dict[str, Any] = {
        "inherits": parent,
        "polymorphic_identity": identity_of(entity),
        "polymorphic_load": "selectin",
    }
    if shared:
        own = fields_added(entity, parent, table)
        if not own:
            raise exceptions.ProgrammingError(
                f"{entity.__name__} shares {table.name} with {parent.__name__} but none of its "
                "own fields is a column of it: add the columns to the table"
            )
        if root in strict or root in extension_typing.STRICT:
            raise exceptions.ProgrammingError(
                f"{entity.__name__} keeps its columns in {table.name} and {root.__name__} "
                "answers only itself: a row would never be read as the extension"
            )
        proof = or_(*[table.c[name].isnot(None) for name in own])
    else:
        refuse_a_table_that_does_not_extend(entity, table, parent, above)
        refuse_a_column_the_parents_have(entity, table, parent)
        proof = extension_typing.has_the_row_of(parent_table(mapper_registry, root), table)
        if root not in strict:
            options["properties"] = {
                extension_typing.TYPE_ATTRIBUTE: column_property(
                    extension_typing.ExtensionType(root, within=entity)
                )
            }
    extension_typing.ROOT_OF[entity] = root
    extension_typing.register(
        entity, proof, table, None if shared else list(table.primary_key.columns)[0]
    )
    extensions.register(root, entity, fields_added(entity, parent, table))
    # a subclass given the very table of its parent is single-table only if it names none
    return (None if shared else table), options


def finished(mapper: Mapper, entity: type, parent: type | None) -> None:
    """What is left to say once the mapper exists: a core that stores the type writes it on
    insert, and an extension reads the discriminator of its root."""
    if parent is None:
        if entity in extension_typing.STORED_TYPE:
            event.listen(mapper, "before_insert", stored_type_of_the_row, propagate=True)
        return
    if (
        mapper.polymorphic_on is None
        and extension_typing.ROOT_OF[entity] not in extension_typing.STRICT
    ):
        # SQLAlchemy hands a subclass the parent's discriminator only when it is a column of
        # the subclass's own selectable; ours is an expression, so a read that starts at an
        # extension would never go further down its chain.
        mapper.polymorphic_on = mapper.get_property(extension_typing.TYPE_ATTRIBUTE).columns[
            0
        ]
