"""The data filters of a database, executed by SQLAlchemy: every ORM statement of its sessions —
a repository's read, a Feature called inside another's unit of work, a relation loaded later, a
plain `unit.session.execute(select(...))` — carries the filters as `with_loader_criteria`, the
recipe SQLAlchemy documents for global filters.

    database = Database(url, filters=[tenant_filter])
    session.execute(select(Invoice))         ->  … WHERE invoice.tenant_id IN (…)
    session.execute(update(Invoice)…)        ->  the same criteria on the UPDATE

Context: the criteria are read when the statement runs, so one database serves every request of
the process with each request's own values; a filter turned off by name
(`DataFilters.disabled`) adds nothing to that execution. What SQLAlchemy never sees is not
filtered — `text()` and a Core statement on a `Table` — the same edge every framework documents.
"""

from typing import Any, cast
from weakref import WeakSet

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, with_loader_criteria
from sqlalchemy.sql import visitors

from sincpro_framework.data_layer import exceptions
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.data_layer.orm.sqlalchemy.services import sql_translator
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import (
    ModelIntrospection,
)
from sincpro_framework.ddd.repositories.data_filters import DataFilters

_watched: "WeakSet[Database]" = WeakSet()
"""The databases whose sessions already carry the filters — installed once each."""


def clause_for(filters: DataFilters, model: type) -> Any:
    """The `WHERE` clause the active filters add to `model`, or `None` when none applies.

    A filter naming a field the model cannot answer is refused rather than dropped: a filter
    silently left out is the one bug filters exist to prevent.
    """
    expression = filters.expression_for(model)
    if expression is None:
        return None
    meta = ModelIntrospection.describe(model)
    accepted, dropped = meta.accept(expression)
    if dropped or accepted is None:
        raise exceptions.ProgrammingError(
            f"{model.__name__} cannot answer the data filters of its database "
            f"({', '.join(one.field for one in dropped) or 'nothing survived'}); "
            "reading it wide is not an option"
        )
    return sql_translator.where_clause(model, accepted)


def _classes_in(state: ORMExecuteState) -> set[type]:
    """Every mapped class the statement reads, at any depth — the entity it selects, and one
    inside a subquery, as a capped count builds it."""
    classes = {mapper.class_ for mapper in state.all_mappers}
    for element in visitors.iterate(cast(Any, state.statement)):
        annotations = getattr(element, "_annotations", None)
        if annotations and "parentmapper" in annotations:
            classes.add(annotations["parentmapper"].class_)
    return classes


def _filtered(filters: DataFilters, state: ORMExecuteState) -> None:
    """Adds the filters to one ORM statement of the database's sessions.

    1. Only a SELECT, an UPDATE or a DELETE; the refresh of one column of a record already
       read is left alone — the record was filtered when it was read.
    2. For every mapped class the statement touches, at any depth, the clause the filters add to it, as a
       loader criteria that also reaches its aliases and the relations loaded from it.
    """
    if not filters or state.is_column_load:
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    options = []
    for model in _classes_in(state):
        clause = clause_for(filters, model)
        if clause is not None:
            options.append(with_loader_criteria(model, clause, include_aliases=True))
    if options:
        state.statement = state.statement.options(*options)


def watch(database: Database) -> None:
    """Makes every ORM statement of `database`'s sessions carry its data filters — once per
    database, whoever builds the first repository over it."""
    if database in _watched:
        return
    _watched.add(database)
    event.listen(
        database.open_session,
        "do_orm_execute",
        lambda state: _filtered(database.filters, state),
    )
