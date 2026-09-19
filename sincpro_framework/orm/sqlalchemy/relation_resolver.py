"""Resolves the relations a specification names: once per node, for a whole page, never per row.

    page of 20 datasets, specification {"runs": C}
    →  one statement over Run: WHERE dataset_id IN (20 ids) AND C.where,
       row_number() OVER (PARTITION BY dataset_id ORDER BY C.order) <= C.limit
    →  dataset.runs = EntityCollection[Run](items, count, cursor) on each parent

The kinds only a database has live here, partitioned in SQL with a window function: a foreign
key, a table in between, a list of ids held in a JSON column. Anything brought from elsewhere,
a bus, a function, goes through its `Resolver` in `sincpro_framework.ddd.entity.relations`, one call
per node, cut per parent in memory. Same shape either way, chosen by what the data mapper
declared.

No ceiling anywhere, only defaults: a to-many node without a page gets 40 per parent, and a
node that asks for ten million gets ten million.
"""

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from sincpro_framework.ddd.criteria import Criteria, Specification
from sincpro_framework.ddd.entity.entity_collection import Count, Dropped, EntityCollection
from sincpro_framework.ddd.entity.model_meta import FieldType, Meta
from sincpro_framework.ddd.entity.relations import Groups
from sincpro_framework.ddd.entity.relations import Relation as DeclaredRelation
from sincpro_framework.ddd.entity.relations import cut, limit_of, resolve_elsewhere
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.orm.sqlalchemy import sql_translator as sql
from sincpro_framework.orm.sqlalchemy.data_mapper import RESOLVED, Relation, relations_of
from sincpro_framework.orm.sqlalchemy.model_introspection import describe

PARENT_KEY = "_sincpro_parent_key"
POSITION, TOTAL = sql.POSITION, sql.TOTAL
"""What every kind answers: the related records per parent identity."""


def _partitioned(
    session: Session,
    parent_key: Any,
    related: type,
    base: Any,
    clause: Any,
    sorts: tuple,
    limit: int | None,
) -> list[tuple[Any, Any, int]]:
    """The rows of one node as `(related record, parent key, total)`, ordered as the node
    asked, at most `limit` per parent, in one statement over a window."""
    order = sql.order_clauses(related, sorts)
    position = func.row_number().over(partition_by=parent_key, order_by=order).label(POSITION)
    total = func.count().over(partition_by=parent_key).label(TOTAL)
    inner = base.add_columns(parent_key.label(PARENT_KEY), position, total)
    if clause is not None:
        inner = inner.where(clause)
    sub = inner.subquery()
    alias = aliased(related, sub)
    statement = select(alias, sub.c[PARENT_KEY], sub.c[TOTAL]).order_by(
        sub.c[PARENT_KEY], sub.c[POSITION]
    )
    if limit is not None:
        statement = statement.where(sub.c[POSITION] <= limit)
    return [(row[0], row[1], row[2]) for row in session.execute(statement).all()]


def _grouped(
    rows: list[tuple[Any, Any, int]], node: Criteria, sorts: tuple, limit: int | None
) -> Groups:
    """Partitioned rows folded into one collection per parent, each with its exact total."""
    groups: dict[Any, list[Any]] = {}
    totals: dict[Any, int] = {}
    for record, parent_key, total in rows:
        groups.setdefault(parent_key, []).append(record)
        totals[parent_key] = total
    return {
        key: EntityCollection(
            items=tuple(items),
            count=Count(value=totals[key], exact=True),
            cursor=(
                node.pagination.next_from(items[-1], sorts)
                if limit is not None and totals[key] > limit
                else None
            ),
        )
        for key, items in groups.items()
    }


def _nested(
    repository: Any,
    session: Session,
    related: type,
    records: Sequence[Any],
    node: Criteria,
    definition: Meta,
    dropped: list[Dropped],
) -> Meta:
    """The node's own specification over all the related records at once; the related
    aggregate's definition back, cut by that specification."""
    resolved = resolve_relations(
        repository, session, related, list(records), node.specification, definition, dropped
    )
    return resolved.only(node.specification)


def _by_foreign_key_on_related(
    repository, session, meta, parents, relation, node, limit, dropped
):
    """A to-many: the related aggregate holds this one's identity."""
    related = relation.related
    clause, sorts, definition, node_dropped = repository.prepare(related, node)
    dropped.extend(node_dropped)
    ids = [getattr(parent, meta.identity) for parent in parents]
    key = getattr(related, relation.identified_by)
    rows = _partitioned(
        session, key, related, select(related).where(key.in_(ids)), clause, sorts, limit
    )
    groups = _grouped(rows, node, sorts, limit)
    return groups, _nested(
        repository, session, related, [r[0] for r in rows], node, definition, dropped
    )


def _by_foreign_key_here(repository, session, meta, parents, relation, node, dropped):
    """A to-one: this aggregate holds the related one's identity."""
    related = relation.related
    clause, sorts, definition, node_dropped = repository.prepare(related, node)
    dropped.extend(node_dropped)
    wanted = {getattr(parent, relation.identified_by) for parent in parents} - {None}
    if not wanted:
        return {}, definition.only(node.specification)
    identity = getattr(related, definition.identity)
    statement = select(related).where(identity.in_(wanted))
    if clause is not None:
        statement = statement.where(clause)
    found = {getattr(r, definition.identity): r for r in session.scalars(statement)}
    groups = {
        getattr(parent, meta.identity): EntityCollection(items=(found[key],))
        for parent in parents
        if (key := getattr(parent, relation.identified_by)) in found
    }
    return groups, _nested(
        repository, session, related, list(found.values()), node, definition, dropped
    )


def _through_table(repository, session, meta, parents, relation, node, limit, dropped):
    """A many-to-many: the pairs live in a table in between."""
    related = relation.related
    clause, sorts, definition, node_dropped = repository.prepare(related, node)
    dropped.extend(node_dropped)
    ids = [getattr(parent, meta.identity) for parent in parents]
    table = relation.through
    if table is None or relation.related_key is None:
        raise ContractViolation(
            f"{relation.related.__name__}: a many-to-many needs the table in between and "
            "the key on it pointing at the related aggregate"
        )
    this_key = table.c[relation.identified_by]
    related_key = table.c[relation.related_key]
    base = (
        select(related)
        .join(table, related_key == getattr(related, definition.identity))
        .where(this_key.in_(ids))
    )
    rows = _partitioned(session, this_key, related, base, clause, sorts, limit)
    groups = _grouped(rows, node, sorts, limit)
    return groups, _nested(
        repository, session, related, [r[0] for r in rows], node, definition, dropped
    )


def _by_id_list(repository, session, meta, parents, relation, node, limit, dropped):
    """A to-many whose ids this aggregate holds in a column; the cut per parent is in memory,
    because the related aggregate has no column pointing back."""
    related = relation.related
    clause, sorts, definition, node_dropped = repository.prepare(related, node)
    dropped.extend(node_dropped)
    wanted: set[Any] = set()
    for parent in parents:
        wanted.update(getattr(parent, relation.identified_by) or [])
    if not wanted:
        return {}, definition.only(node.specification)
    identity = getattr(related, definition.identity)
    statement = (
        select(related)
        .where(identity.in_(wanted))
        .order_by(*sql.order_clauses(related, sorts))
    )
    if clause is not None:
        statement = statement.where(clause)
    fetched = list(session.scalars(statement))
    groups: Groups = {}
    for parent in parents:
        mine = set(getattr(parent, relation.identified_by) or [])
        items = [r for r in fetched if getattr(r, definition.identity) in mine]
        groups[getattr(parent, meta.identity)] = cut(items, node, sorts, limit, exact=True)
    return groups, _nested(repository, session, related, fetched, node, definition, dropped)


def _resolve(
    repository: Any,
    session: Session,
    meta: Meta,
    parents: Sequence[Any],
    declared: DeclaredRelation,
    node: Criteria,
    many: bool,
    dropped: list[Dropped],
    whole: bool,
) -> tuple[Groups, Meta | None]:
    """One node, by the kind the data mapper declared."""
    limit = limit_of(node, whole)
    if declared.kind == "resolver":
        return resolve_elsewhere(meta, parents, declared, node, many, limit)
    relation = cast(Relation, declared)
    match relation.kind:
        case "foreign_key" if many:
            return _by_foreign_key_on_related(
                repository, session, meta, parents, relation, node, limit, dropped
            )
        case "foreign_key":
            return _by_foreign_key_here(
                repository, session, meta, parents, relation, node, dropped
            )
        case "many_to_many":
            return _through_table(
                repository, session, meta, parents, relation, node, limit, dropped
            )
        case "id_list":
            return _by_id_list(
                repository, session, meta, parents, relation, node, limit, dropped
            )
        case "resolver":
            return resolve_elsewhere(meta, parents, relation, node, many, limit)
    raise ValueError(f"unknown relation kind {relation.kind!r}")


# ---------------------------------------------------------------------------- the two doors


def resolve_relations(
    repository: Any,
    session: Session,
    model: type,
    records: Sequence[Any],
    specification: Specification | None,
    meta: Meta,
    dropped: list[Dropped],
    whole: bool = False,
) -> Meta:
    """Every relation the specification names, resolved onto the records; the definition back
    with each expanded relation's own definition, cut by its node.

    1. A scalar with children is `not_expandable`; an unknown name was already reported.
    2. A relation nobody declared how to resolve is `not_expandable` too.
    3. Per named relation: one resolution for all the records, grouped by parent, assigned;
       then the node's own specification, recursively, over all the related records at once.
    """
    if specification is None or not records:
        return meta
    fields = dict(meta.fields)
    for name, node in specification.root.items():
        field = meta.fields.get(name)
        if field is None:
            continue
        if field.type is FieldType.EMBEDDED:
            if node.specification is not None and field.definition is not None:
                _, unknown = field.definition.accept_specification(node.specification)
                dropped.extend(unknown)
            continue
        if not field.type.is_relational:
            if node.specification is not None:
                dropped.append(Dropped(field=name, reason="not_expandable"))
            continue
        declared = relations_of(model).get(name)
        if declared is None:
            dropped.append(Dropped(field=name, reason="not_expandable"))
            continue

        many = field.many
        groups, definition = _resolve(
            repository, session, meta, records, declared, node, many, dropped, whole
        )
        for record in records:
            found = groups.get(getattr(record, meta.identity))
            if many:
                value: Any = (
                    found
                    if found is not None
                    else EntityCollection(count=Count(value=0, exact=True))
                )
            else:
                value = found.first() if found else None
            record.__dict__.setdefault(RESOLVED, {})[name] = value
        fields[name] = field.model_copy(
            update={"identified_by": declared.identified_by, "definition": definition}
        )
    return meta.model_copy(update={"fields": fields})


def resolve_whole(
    repository: Any, session: Session, model: type, record: Any, name: str
) -> None:
    """One relation of one record, whole: what a Feature touching it inside a unit of work gets."""
    resolve_relations(
        repository,
        session,
        model,
        [record],
        Specification({name: Criteria()}),
        describe(model),
        [],
        whole=True,
    )
