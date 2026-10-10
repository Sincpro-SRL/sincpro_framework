"""How a project maps its models: aggregates to their tables (`map_aggregates`), a context's events
to its event table (`map_events`), and how another engine cuts a date (`register_grain_translator`).
The machinery each one stands on is in `services/`."""

from typing import Any

from sqlalchemy import Table, event
from sqlalchemy.orm import registry

from sincpro_framework.data_layer.orm.sqlalchemy.domain.registry import record_mapping
from sincpro_framework.data_layer.orm.sqlalchemy.services import sql_translator
from sincpro_framework.data_layer.orm.sqlalchemy.services.data_mapper import (
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
)
from sincpro_framework.ddd.entity import Entity, relations
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.exceptions import ProgrammingError


def map_aggregates(
    mapper_registry: registry,
    tables: dict[type, Table],
    properties: dict[type, dict[str, Any]] | None = None,
    relations: dict[type, dict[str, relations.Relation]] | None = None,
) -> None:
    """Maps each class to its table, once, switching on the version check for an `Entity`, and
    installs the relations on the classes: the declared ones, then the ones the foreign keys
    already say.

        in      registry, {Note: note_table, Dataset: dataset_table}
        out     both mapped; calling it again changes nothing

    `properties` is per class and is what `map_imperatively` takes: how to map an attribute
    onto a column with another name, say `{"id": dataset_table.c.dataset_id}` for a table
    that predates the `Entity` convention.

    `relations` is what the tables cannot say on their own — a kind that needs a bus, a
    function or a scope. Whatever is declared here wins over what a foreign key infers.

    A class that extends another mapped aggregate is mapped as its extension: the fields it
    inherits stay in the parent's table, its table holds only its own columns and references
    the parent's key. Parents are mapped first, whatever the order of `tables`.
    """
    already = {mapper.class_ for mapper in mapper_registry.mappers}
    for entity, table in sorted(tables.items(), key=lambda item: len(item[0].__mro__)):
        if entity in already:
            continue

        options: dict[str, Any] = {}
        if properties and entity in properties:
            options["properties"] = properties[entity]
        parent = next((base for base in entity.__mro__[1:] if base in already), None)
        if parent is not None:
            refuse_a_table_that_does_not_extend(
                entity, table, parent, parent_table(mapper_registry, parent)
            )
            options["inherits"] = parent
        elif issubclass(entity, Entity) and "version" in table.c:
            options["version_id_col"] = table.c.version

        mapper = mapper_registry.map_imperatively(entity, table, **options)
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
