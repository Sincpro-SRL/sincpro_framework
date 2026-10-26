"""How a project maps its models: aggregates to their tables (`map_aggregates`), a context's events
to its event table (`map_events`), and how another engine cuts a date (`register_grain_translator`).
The machinery each one stands on is in `services/`."""

from collections.abc import Collection, Mapping
from typing import Any

import sqlalchemy
from sqlalchemy import Table, event
from sqlalchemy.orm import registry

from sincpro_framework.data_layer import exceptions
from sincpro_framework.data_layer.orm.sqlalchemy.domain.consolidation import Consolidated
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.mapping_registry import (
    mapping_registry,
)
from sincpro_framework.data_layer.orm.sqlalchemy.services import (
    extension_mapping,
    sql_translator,
)
from sincpro_framework.data_layer.orm.sqlalchemy.services.data_mapper import (
    computed_properties,
    inferred_foreign_keys,
    install,
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
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import (
    ModelIntrospection,
)
from sincpro_framework.data_layer.orm.sqlalchemy.services.sql_translator import (
    GrainTranslator,
    PartTranslator,
)
from sincpro_framework.ddd.entity import Entity, extensions, relations
from sincpro_framework.ddd.events import DomainEvent


def map_aggregates(
    mapper_registry: registry,
    tables: Mapping[type, Table | Consolidated],
    properties: dict[type, dict[str, Any]] | None = None,
    relations: dict[type, dict[str, relations.Relation]] | None = None,
    strict: Collection[type] = (),
    delegated: Collection[type] = (),
    uses: Mapping[type, Collection[str]] | None = None,
    requires: Mapping[type, str] | None = None,
    overlay: Mapping[type, Mapping[str, Mapping[str, Any]]] | None = None,
) -> None:
    """Maps each class to its table, once, switching on the version check for an `Entity`, and
    installs the relations on the classes: the declared ones, then the ones the foreign keys
    already say.

        in      registry, {Note: note_table, Dataset: dataset_table}
        out     both mapped; calling it again changes nothing

    `properties` is per class and is what `map_imperatively` takes: how to map an attribute
    onto a column with another name, say `{"id": dataset_table.c.dataset_id}` for a table
    that predates the `Entity` convention. For a class that is already mapped it adds the
    attributes it does not have yet, which is how a module maps a key of the JSON slot of an
    entity it does not own.

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

    An answer writes the fields an extension adds flat, beside the entity's own. `delegated` lists
    the classes whose answers nest them instead, each under the extension's key
    (`{"number": …, "delivery": {"route": …}}`).

    A class mapped to `Consolidated(Core, ExtensionA, ExtensionB)` is one flat record of all of
    them, read through an outer join and written to each table, the core's first: the place where
    sibling extensions become one object, declared by the module that knows them all.

    A module that adds to an entity it does not own says what it reads and what it was written
    against. `uses={Move: ("total",)}` is checked once the call has mapped everything: a core that
    renamed `total` stops the application where the module is mapped, naming the field.
    `requires={Move: "2.1"}` is checked against the `CONTRACT` the core publishes on the entity
    (`CONTRACT = "2.3"`): the same major and at least that minor, or the application does not start.

    `overlay={Move: {"total": {"label": {"default": "Amount"}, "readonly": True}}}` changes how a
    module shows a field of the core — its label, its help, read-only, required — and nothing else:
    the field itself is the core's.

    An extension may also live in its **parent's own table** (`{Move: move, MoveDelivery: move}`):
    the columns it owns are columns of that table, and a row has the extension when any of them
    holds a value. Its migration adds columns to a table that is not its own, which is why the
    migrations compare each chain only with the columns it declares.

        map_aggregates(registry, {Author: author_table, Work: work_table})   ->  None
        sqlalchemy.inspect(Work).local_table   ->  work_table
        mapping_registry.relations_of(Work)["author"]
            ->  Relation.foreign_key(Author, identified_by="author_id"), from work.author_id

    1. The classes the registry already maps are noted; `delegated` is recorded as the classes
       whose answers nest their extensions.
    2. Each class of `tables`, parents first (fewest bases first), unless already mapped:
       2.1 to a `Consolidated`: mapped as one flat record over the core and its extensions;
       2.2 otherwise its computed fields and its `properties` are its properties, and
           2.2.1 a root — no mapped parent — takes the version check when it is an `Entity`
                 with a `version` column, and the options that let it answer its extensions;
           2.2.2 an extension takes its own table, joined to its parent's, or its columns of
                 the parent's table;
           2.2.3 mapped imperatively, its mapper finished, the table recorded as the class's;
       2.3 a `load` listener gives a loaded record the defaults no column holds.
    3. `properties` of a class mapped before this call: the attributes it lacks are added.
    4. `overlay` applied field by field, and every entity's cached description cleared.
    5. `uses` and `requires` checked — a field the core lacks, a contract out of range, stop
       the start here.
    6. The declared `relations` installed.
    Final: the relations the foreign keys say installed on every mapped class — this call's
    and earlier ones' — never over a declared one.
    """
    already = {mapper.class_ for mapper in mapper_registry.mappers}
    mapped_before = set(already)
    extensions.DELEGATED.update(delegated)
    for entity, table in sorted(tables.items(), key=lambda item: len(item[0].__mro__)):
        if entity in already:
            continue

        own = (properties or {}).get(entity, {})
        if isinstance(table, Consolidated):
            mapper = extension_mapping.map_consolidated(mapper_registry, entity, table, own)
        else:
            options: dict[str, Any] = {}
            computed = computed_properties(entity, table)
            if computed or own:
                options["properties"] = {**computed, **own}
            parent = next((base for base in entity.__mro__[1:] if base in already), None)
            if parent is None:
                if issubclass(entity, Entity) and "version" in table.c:
                    options["version_id_col"] = table.c.version
                local, extra = table, (
                    extension_mapping.root_options(entity, table, strict)
                    if isinstance(table, Table)
                    else {}
                )
            else:
                local, extra = extension_mapping.extension_options(
                    mapper_registry, entity, table, parent, strict
                )
            options = {
                **options,
                **extra,
                "properties": {
                    **options.get("properties", {}),
                    **extra.get("properties", {}),
                },
            }
            mapper = mapper_registry.map_imperatively(entity, local, **options)
            extension_mapping.finished(mapper, entity, parent)
            mapping_registry.record_mapping(table, entity)
        event.listen(
            mapper, "load", with_transient_defaults(entity, set(mapper.columns.keys()))
        )
        already.add(entity)

    for entity, added in (properties or {}).items():
        if entity in mapped_before:
            # an attribute a later module maps on an entity that is already mapped: a key of the
            # JSON slot as a field, say
            mapper = sqlalchemy.inspect(entity)
            for name, prop in added.items():
                if name not in mapper.attrs:
                    mapper.add_property(name, prop)
    for entity, per_field in (overlay or {}).items():
        for field, changes in per_field.items():
            extensions.overlay(entity, field, dict(changes))
    ModelIntrospection.describe.cache_clear()  # what an entity is told about has changed with what extends it
    for entity, names in (uses or {}).items():
        extension_mapping.refuse_a_field_the_core_lacks(entity, names)
    for entity, wanted in (requires or {}).items():
        extension_mapping.refuse_a_contract_out_of_range(entity, wanted)
    for aggregate, declared in (relations or {}).items():
        install(aggregate, declared)
    # Every mapped class, not only this call's: a foreign key between a class mapped earlier
    # and one mapped now is a relation on both, and both are seen on the second call.
    for mapper in mapper_registry.mappers:
        install(mapper.class_, inferred_foreign_keys(mapper_registry, mapper.class_))


def map_events(mapper_registry: registry, base: type[DomainEvent], table: Table) -> None:
    """Maps `base` — the bounded context's event class — and every subclass of it to `table`.

    A base per context, so two contexts in one process have two tables; mapping it again is a
    no-op.

        map_events(registry, BillingDomainEvent, event_table("billing_events", metadata))
            ->  None; InvoicePaid(invoice_id="F-1") saved is one row named "InvoicePaid"
        map_events(registry, Invoice, table)   ->  ProgrammingError, not a DomainEvent class

    1. Refused when `base` is not a `DomainEvent` class; nothing done when it is mapped.
    2. Every class of the tree — the base and each subclass, depth by depth — claims its
       `name` in `table` first, so a name two classes share refuses before anything maps.
    3. The base mapped to `table`, the `name` column as discriminator and `payload` as what
       subclasses add.
    4. Listeners on the whole tree: the payload packed before an insert, unpacked on load.
    5. The base's `__new__` maps a subclass declared later before its first instance.
    Final: the base recorded with its registry and table, and every subclass already declared
    mapped under it.
    """
    if not (isinstance(base, type) and issubclass(base, DomainEvent)):
        raise exceptions.ProgrammingError(
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
