"""How a project maps its models: aggregates to their tables (`map_aggregates`), a context's events
to its event table (`map_events`), and how another engine cuts a date (`register_grain_translator`).
The machinery each one stands on is in `services/`."""

from collections.abc import Collection, Mapping
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from typing import Any

from sqlalchemy import Table, event
from sqlalchemy.orm import column_property, registry

from sincpro_framework.data_layer.orm.sqlalchemy.domain.consolidation import Consolidated
from sincpro_framework.data_layer.orm.sqlalchemy.domain.registry import record_mapping
from sincpro_framework.data_layer.orm.sqlalchemy.services import (
    extension_typing,
    sql_translator,
)
from sincpro_framework.data_layer.orm.sqlalchemy.services.data_mapper import (
    computed_properties,
    consolidated_mapping,
    inferred_foreign_keys,
    install,
    parent_table,
    refuse_a_table_that_does_not_extend,
    with_transient_defaults,
)
from sincpro_framework.data_layer.orm.sqlalchemy.services.event_mapping import (
    bases,
    claim,
    map_new_event_classes,
    mapped_before_built,
    packed,
    unpacked,
)
from sincpro_framework.data_layer.orm.sqlalchemy.services.sql_translator import (
    GrainTranslator,
    PartTranslator,
)
from sincpro_framework.ddd.entity import Entity, relations
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.exceptions import ProgrammingError


def own_columns(entity: type, parent: type, table: Table) -> list[str]:
    """The columns of a shared table that an extension adds: its fields the parent does not have."""
    if not is_dataclass(entity):
        raise ProgrammingError(
            f"{entity.__name__} must be a dataclass to share its parent's table"
        )
    inherited = (
        {field.name for field in dataclass_fields(parent)} if is_dataclass(parent) else set()
    )
    found = [
        field.name
        for field in dataclass_fields(entity)
        if field.name not in inherited and field.name in table.c
    ]
    if not found:
        raise ProgrammingError(
            f"{entity.__name__} shares {table.name} with {parent.__name__} but none of its own "
            f"fields is a column of it: add the columns to the table"
        )
    return found


def map_aggregates(
    mapper_registry: registry,
    tables: Mapping[type, Table | Consolidated],
    properties: dict[type, dict[str, Any]] | None = None,
    relations: dict[type, dict[str, relations.Relation]] | None = None,
    strict: Collection[type] = (),
) -> None:
    """Maps each class to its table, once, switching on the version check for an `Entity`, and
    installs the relations on the classes: the declared ones, then the ones the foreign keys
    already say.

        in      registry, {Note: note_table, Dataset: dataset_table}
        out     both mapped; calling it again changes nothing

    `properties` is per class and is what `map_imperatively` takes: how to map an attribute
    onto a column with another name, say `{"id": dataset_table.c.dataset_id}` for a table
    that predates the `Entity` convention.

    A field declared with `computed(sql=…)` is mapped as an expression over the table
    (`column_property`): it is read with the row, and filtered, ordered and grouped by like a
    column, and it is never written. A `properties` entry for the same name wins.

    `relations` is what the tables cannot say on their own — a kind that needs a bus, a
    function or a scope. Whatever is declared here wins over what a foreign key infers.

    A class that extends another mapped aggregate is mapped as its extension: the fields it
    inherits stay in the parent's table, its table holds only its own columns and references
    the parent's key. Parents are mapped first, whatever the order of `tables`.

    **An extension answers downstream.** Reading a class returns each row as the most derived
    extension it has — `Move` for a row with nothing added, `MoveDelivery` for one with a
    delivery row — and the columns of the extension are read in one more statement per type
    found, not a join per extension. `strict` lists the classes that answer only themselves.

    A class mapped to `Consolidated(Core, ExtensionA, ExtensionB)` is one flat record of all of
    them, read through an outer join and written to each table, the core's first: the place where
    sibling extensions become one object, declared by the module that knows them all.

    An extension may also live in its **parent's own table** (`{Move: move, MoveDelivery: move}`):
    the columns it owns are columns of that table, and a row has the extension when any of them
    holds a value. Its migration adds columns to a table that is not its own, which is why the
    migrations compare each chain only with the columns it declares.
    """
    already = {mapper.class_ for mapper in mapper_registry.mappers}
    for entity, table in sorted(tables.items(), key=lambda item: len(item[0].__mro__)):
        if entity in already:
            continue

        if isinstance(table, Consolidated):
            joined, columns, unread, core_table = consolidated_mapping(
                mapper_registry, entity, table
            )
            mapper = mapper_registry.map_imperatively(
                entity,
                joined,
                properties={**columns, **(properties or {}).get(entity, {})},
                exclude_properties=unread,
                primary_key=list(core_table.primary_key.columns),
                version_id_col=core_table.c.version,
            )
            event.listen(
                mapper, "load", with_transient_defaults(entity, set(mapper.columns.keys()))
            )
            already.add(entity)
            continue

        options: dict[str, Any] = {}
        computed = computed_properties(entity, table)
        if computed or (properties and entity in properties):
            options["properties"] = {**computed, **(properties or {}).get(entity, {})}
        parent = next((base for base in entity.__mro__[1:] if base in already), None)
        if parent is not None:
            above = parent_table(mapper_registry, parent)
            shared = table is above
            root = extension_typing.ROOT_OF[parent]
            if shared:
                own = own_columns(entity, parent, table)
                if root in strict or root in extension_typing.STRICT:
                    raise ProgrammingError(
                        f"{entity.__name__} keeps its columns in {table.name} and {root.__name__} "
                        "answers only itself: a row would never be read as the extension"
                    )
                proof = extension_typing.has_a_column_set(table, own)
            else:
                refuse_a_table_that_does_not_extend(entity, table, parent, above)
                proof = extension_typing.has_the_row_of(
                    parent_table(mapper_registry, root), table
                )
            options["polymorphic_load"] = "selectin"
            if not shared and root not in strict:
                options["properties"] = {
                    **options.get("properties", {}),
                    extension_typing.TYPE_ATTRIBUTE: column_property(
                        extension_typing.ExtensionType(root, within=entity)
                    ),
                }
            options["inherits"] = parent
            options["polymorphic_identity"] = extension_typing.identity_of(entity)
            extension_typing.ROOT_OF[entity] = root
            extension_typing.register(root, entity, len(entity.__mro__), proof)
        else:
            if issubclass(entity, Entity) and "version" in table.c:
                options["version_id_col"] = table.c.version
            if isinstance(table, Table):
                options["polymorphic_on"] = extension_typing.TYPE_ATTRIBUTE
                options["properties"] = {
                    **options.get("properties", {}),
                    extension_typing.TYPE_ATTRIBUTE: column_property(
                        extension_typing.ExtensionType(entity)
                    ),
                }
                options["polymorphic_identity"] = extension_typing.identity_of(entity)
                extension_typing.ROOT_OF[entity] = entity
                if entity in strict:
                    extension_typing.STRICT.add(entity)

        # a subclass given the very table of its parent is single-table only if it names none
        mapper = mapper_registry.map_imperatively(
            entity,
            (
                None
                if options.get("inherits")
                and table is parent_table(mapper_registry, options["inherits"])
                else table
            ),
            **options,
        )
        if (
            parent is not None
            and mapper.polymorphic_on is None
            and extension_typing.ROOT_OF[entity] not in extension_typing.STRICT
        ):
            # SQLAlchemy hands a subclass the parent's discriminator only when it is a column of
            # the subclass's own selectable; ours is an expression, so a read that starts at an
            # extension would never go further down its chain.
            mapper.polymorphic_on = mapper.get_property(
                extension_typing.TYPE_ATTRIBUTE
            ).columns[0]
        record_mapping(table, entity)
        event.listen(
            mapper, "load", with_transient_defaults(entity, set(mapper.columns.keys()))
        )
        already.add(entity)

    for aggregate, declared in (relations or {}).items():
        install(aggregate, declared)
    # Every mapped class, not only this call's: a foreign key between a class mapped earlier
    # and one mapped now is a relation on both, and both are seen on the second call.
    for mapper in mapper_registry.mappers:
        install(mapper.class_, inferred_foreign_keys(mapper_registry, mapper.class_))


def map_events(mapper_registry: registry, base: type[DomainEvent], table: Table) -> None:
    """Maps `base` — the bounded context's event class — and every subclass of it to `table`.

    A base per context, so two contexts in one process keep two tables; mapping it again is a
    no-op.
    """
    if not (isinstance(base, type) and issubclass(base, DomainEvent)):
        raise ProgrammingError(
            f"{base!r} is not a DomainEvent class: there is nothing to map"
        )
    if base in bases:
        return
    pending = [base]
    while pending:  # every name refused before anything is mapped, so nothing is half mapped
        cls = pending.pop(0)
        claim(table, cls)
        pending.extend(cls.__subclasses__())
    mapper_registry.map_imperatively(
        base,
        table,
        polymorphic_on=table.c.name,
        polymorphic_identity=base.name,
        properties={
            "wire_name": table.c.name,
            "payload_of_class": table.c.payload,
        },
    )
    event.listen(base, "before_insert", packed, propagate=True)
    event.listen(base, "load", unpacked, propagate=True)
    setattr(base, "__new__", staticmethod(mapped_before_built))
    bases[base] = (mapper_registry, table)
    map_new_event_classes()


def register_grain_translator(dialect: str, translator: GrainTranslator) -> None:
    """Teaches the translator how another engine cuts a date.

        register_grain_translator("mysql", lambda column, grain: func.date_format(column, …))

    The translator receives the column and one of `GRAINS`, and must answer the same text
    the built-in ones do for that grain — `2026-09` for a month — so buckets open the same
    way everywhere.
    """
    sql_translator.GRAIN_TRANSLATORS[dialect] = translator


def register_part_translator(dialect: str, translator: PartTranslator) -> None:
    """Teaches the translator how another engine extracts a number from a date.

        register_part_translator("mysql", lambda column, part: func.extract(…))

    The translator receives the column and one of `Part`'s values, and must answer the same
    integer the built-in ones do — the month as 1 to 12, the weekday as 1 (Monday) to 7 — so a
    group of a part reads the same everywhere.
    """
    sql_translator.PART_TRANSLATORS[dialect] = translator
