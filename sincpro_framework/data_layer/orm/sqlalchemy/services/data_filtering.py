"""Data filters executed by SQLAlchemy, on every session of the process: each ORM statement
carries the active filters as `with_loader_criteria`, and each flush stamps and checks what it
writes.

    with DataFilters.applied(tenant_filter):
        session.execute(select(Invoice))         ->  … WHERE invoice.tenant_id IN (…)

Context: the listeners hang on `Session` once, when the ORM package is imported — before any
`Database` exists —, so no session reads around a filter: one a `Database` opens adds its own
filters (`Session.info`), any other honours the ones applied to the execution. `text()`, a Core
statement on a `Table` and an ORM `insert()` are not filtered.
"""

from typing import Any, cast

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria
from sqlalchemy.sql import visitors

from sincpro_framework.data_layer import exceptions
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import FILTERS
from sincpro_framework.data_layer.orm.sqlalchemy.services import sql_translator
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import (
    ModelIntrospection,
)
from sincpro_framework.ddd.repositories.data_filters import DataFilters

NONE = DataFilters()
"""The filters of a session no `Database` opened: only what the execution applies."""


def filters_of(session: Session) -> DataFilters:
    """The data filters a session follows."""
    return session.info.get(FILTERS) or NONE


def clause_for(filters: DataFilters, model: type) -> Any:
    """Context: a filter naming a field the model cannot answer is refused, never dropped — a
    filter silently left out reads wide."""
    expression = filters.expression_for(model)
    if expression is None:
        return None
    accepted, dropped = ModelIntrospection.describe(model).accept(expression)
    if dropped or accepted is None:
        raise exceptions.ProgrammingError(
            f"{model.__name__} cannot answer the data filters "
            f"({', '.join(one.field for one in dropped) or 'nothing survived'}); "
            "reading it wide is not an option"
        )
    return sql_translator.where_clause(model, accepted)


def _classes_in(state: ORMExecuteState) -> set[type]:
    """Context: a capped count reads the entity inside a subquery, where `all_mappers` does not
    look."""
    classes = {mapper.class_ for mapper in state.all_mappers}
    for element in visitors.iterate(cast(Any, state.statement)):
        annotations = getattr(element, "_annotations", None)
        if annotations and "parentmapper" in annotations:
            classes.add(annotations["parentmapper"].class_)
    return classes


@event.listens_for(Session, "do_orm_execute")
def _filtered_statement(state: ORMExecuteState) -> None:
    """Context: a column refreshed on a record already read is left alone — the record was
    filtered when it was read."""
    if state.is_column_load or not (state.is_select or state.is_update or state.is_delete):
        return
    filters = filters_of(state.session)
    if not filters.active():
        return
    options = [
        with_loader_criteria(model, clause, include_aliases=True)
        for model in _classes_in(state)
        if (clause := clause_for(filters, model)) is not None
    ]
    if options:
        state.statement = state.statement.options(*options)


@event.listens_for(Session, "before_flush")
def _filtered_flush(session: Session, _context: Any, _instances: Any) -> None:
    """Context: a write lands only where its reader could see it, whichever door it took."""
    filters = filters_of(session)
    if not filters.active():
        return
    filters.check_writes(list(session.new), True)
    filters.check_writes([one for one in session.dirty if session.is_modified(one)], False)
    filters.check_writes(list(session.deleted), False)
