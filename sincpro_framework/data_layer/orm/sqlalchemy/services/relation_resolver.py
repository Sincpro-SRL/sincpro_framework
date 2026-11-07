"""Resolves the relations a specification names: once per node, for a whole page, never per row.

    page of 20 datasets, specification {"runs": C}
    →  one statement over Run: for each of the 20 ids, C.where, C.order, C.limit + 1 rows
       (LATERAL on PostgreSQL, a window partitioned by dataset_id elsewhere)
    →  dataset.runs = EntityCollection[Run](items, count, cursor) on each parent

The kinds only a database has live here, partitioned in SQL with a window function: a foreign
key, a table in between, a list of ids held in a JSON column. Anything brought from elsewhere,
a bus, a function, goes through its `Resolver` in `sincpro_framework.ddd.entity.relations`, one call
per node, cut per parent in memory. Same shape either way, chosen by what the data mapper
declared.

Defaults for what a caller asks, never a ceiling on it: a to-many node without a page gets 40
per parent and its count stops at a thousand («at least»), and a node that asks for ten million
rows, or an exact count, gets them. A relation read whole by touching it inside a unit of work
brings all of it; past `WHOLE_WARNING` the log says so.
"""

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import column, func, literal_column, select, true, values
from sqlalchemy.orm import Session, aliased

from sincpro_framework.data_layer import exceptions
from sincpro_framework.data_layer.orm.sqlalchemy.domain.relations import Held, HeldRelations
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.mapping_registry import (
    mapping_registry,
)
from sincpro_framework.data_layer.orm.sqlalchemy.services import sql_translator
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import (
    ModelIntrospection,
    is_mapped,
)
from sincpro_framework.ddd.criteria import CountMode, Criteria, Specification
from sincpro_framework.ddd.entity import relations
from sincpro_framework.ddd.entity.entity import collection_fields
from sincpro_framework.ddd.entity.entity_collection import (
    Count,
    Dropped,
    EntityCollection,
    identity_of,
)
from sincpro_framework.ddd.entity.entity_meta import FieldType, Meta
from sincpro_framework.ddd.entity.relations import (
    Groups,
    RelationKind,
    counted,
    cut,
    key_pair,
    limit_of,
    resolve_elsewhere,
)
from sincpro_framework.sincpro_logger import logger

Prepare = Callable[[type, Criteria], tuple[Any, tuple, Meta, list[Dropped]]]
"""What turns a criteria into its WHERE, its ordering, its definition and what it dropped — the
one thing a resolution needs from the repository it runs for."""

PARENT_KEY = "_sincpro_parent_key"
PARENTS, CHILDREN, COUNTED, CAPPED = (
    "_sincpro_parents",
    "_sincpro_children",
    "_sincpro_counted",
    "_sincpro_capped",
)
POSITION, TOTAL = sql_translator.POSITION, sql_translator.TOTAL
"""What every kind answers: the related records per parent identity."""

if TYPE_CHECKING:
    from sincpro_framework.data_layer.orm.sqlalchemy.domain.relations import TableRelation


def _windowed(
    session: Session,
    parent_key: Any,
    related: type,
    base: Any,
    ids: list[Any],
    clause: Any,
    sorts: tuple,
    limit: int | None,
    count: CountMode,
) -> list[tuple[Any, Any, int | None]]:
    """The portable form, over a window partitioned by the parent key.

        SELECT * FROM (SELECT line.*, invoice_id AS parent,
                              row_number() OVER (PARTITION BY invoice_id ORDER BY …) AS pos,
                              count(*) OVER (PARTITION BY invoice_id) AS total
                       FROM line WHERE invoice_id IN (…) AND <node's filter>)
        WHERE pos <= 6 ORDER BY parent, pos

    1. The node's ordering, a `row_number` — and a `count` unless the node asked for none —
       partitioned by the parent key.
    2. The parents and the node's filter applied inside, so the numbering and the total count
       only what matches.
    3. Final: ordered by parent then position, cut one past `limit` when there is one.
    """
    order = sql_translator.order_clauses(related, sorts)
    columns = [
        parent_key.label(PARENT_KEY),
        func.row_number().over(partition_by=parent_key, order_by=order).label(POSITION),
    ]
    if count is not CountMode.NONE:
        columns.append(func.count().over(partition_by=parent_key).label(TOTAL))
    inner = base.where(parent_key.in_(ids)).add_columns(*columns)
    if clause is not None:
        inner = inner.where(clause)
    sub = inner.subquery()
    alias = aliased(related, sub)
    totals = [sub.c[TOTAL]] if count is not CountMode.NONE else []
    statement = select(alias, sub.c[PARENT_KEY], *totals).order_by(
        sub.c[PARENT_KEY], sub.c[POSITION]
    )
    if limit is not None:
        statement = statement.where(sub.c[POSITION] <= limit + 1)
    return [
        (row[0], row[1], row[2] if totals else None)
        for row in session.execute(statement).all()
    ]


def _laterally(
    session: Session,
    parent_key: Any,
    related: type,
    base: Any,
    ids: list[Any],
    clause: Any,
    sorts: tuple,
    limit: int | None,
    count: CountMode,
) -> list[tuple[Any, Any, int | None]]:
    """The PostgreSQL form: for each parent, its first rows and its count, each one stopping
    where it has read enough.

        SELECT children.*, counted.total
        FROM (VALUES (:p1), (:p2), …) AS parents (key)
        JOIN LATERAL (SELECT count(*) AS total FROM (SELECT 1 FROM line
                      WHERE invoice_id = parents.key AND <node's filter> LIMIT 1001) AS capped)
             AS counted ON true
        JOIN LATERAL (SELECT line.*, invoice_id AS parent, row_number() OVER (ORDER BY …) AS pos
                      FROM line WHERE invoice_id = parents.key AND <node's filter>
                      ORDER BY … LIMIT 6) AS children ON true
        ORDER BY children.parent, children.pos

    1. The parents as a `VALUES` list, each one the key its rows are matched by.
    2. The count first, once per parent: none with `CountMode.NONE`, every row with
       `CountMode.EXACT`, at most one past the cap otherwise.
    3. The rows, one past `limit`, numbered in the node's order inside each parent.
    Final: ordered by parent then position. A parent with no row matching is absent, as with
    the window.
    """
    parents = values(column("key", parent_key.type), name=PARENTS).data([(k,) for k in ids])
    matched = parent_key == parents.c.key
    order = sql_translator.order_clauses(related, sorts)
    rows = base.add_columns(
        parent_key.label(PARENT_KEY),
        func.row_number().over(order_by=order).label(POSITION),
    ).where(matched)
    if clause is not None:
        rows = rows.where(clause)
    rows = rows.order_by(*order)
    if limit is not None:
        rows = rows.limit(limit + 1)
    children = rows.lateral(CHILDREN)
    alias = aliased(related, children)
    joined: Any = parents
    totals: list[Any] = []
    if count is not CountMode.NONE:
        every = base.with_only_columns(literal_column("1"), maintain_column_froms=True).where(
            matched
        )
        if clause is not None:
            every = every.where(clause)
        if count is CountMode.CAPPED:
            every = every.limit(relations.count_cap(limit) + 1)
        counted = (
            select(func.count().label(TOTAL))
            .select_from(every.correlate(parents).subquery(CAPPED))
            .lateral(COUNTED)
        )
        joined = joined.join(counted, true())
        totals = [counted.c[TOTAL]]
    statement = (
        select(alias, children.c[PARENT_KEY], *totals)
        .select_from(joined.join(children, true()))
        .order_by(children.c[PARENT_KEY], children.c[POSITION])
    )
    return [
        (row[0], row[1], row[2] if totals else None)
        for row in session.execute(statement).all()
    ]


def _partitioned(
    session: Session,
    parent_key: Any,
    related: type,
    base: Any,
    ids: list[Any],
    clause: Any,
    sorts: tuple,
    limit: int | None,
    count: CountMode,
) -> list[tuple[Any, Any, int | None]]:
    """The rows of one node as `(related record, parent key, total)`, in the node's order, at
    most `limit + 1` per parent — the one past the limit is how a parent knows there is more —
    in one statement, whatever the number of parents.

    Context: a page of 50 invoices showing their first 5 lines is one query, not 50, and each
    parent still gets the count its node asked for, for the «5 of 23» a client shows. How the
    statement reads per parent depends on the engine: on PostgreSQL a `LATERAL` join reads
    `limit + 1` rows per parent off an index on (key, order) and stops (`_laterally`), so a
    parent with a million children costs what one with fifty does; elsewhere a window numbers
    each parent's rows (`_windowed`), which reads every row of the partition — PostgreSQL's own
    window optimisation does not stop early under `PARTITION BY`.

    `total` is the count per parent when the node asked for one (`CountMode`), exact or
    stopped one past the cap (`count_cap`); `None` with `CountMode.NONE`.
    """
    # Two parents may share the value they are matched by (a relation by a business key): asked
    # once, or a LATERAL join would bring each child once per parent holding it.
    ids = list(dict.fromkeys(ids))
    if session.get_bind().dialect.name == "postgresql":
        return _laterally(
            session, parent_key, related, base, ids, clause, sorts, limit, count
        )
    return _windowed(session, parent_key, related, base, ids, clause, sorts, limit, count)


def _grouped(
    rows: list[tuple[Any, Any, int | None]], node: Criteria, sorts: tuple, limit: int | None
) -> Groups:
    """Partitioned rows folded into one collection per parent: cut to `limit`, a cursor when a
    row past it came back, and the count its node asked for (`relations.counted`)."""
    groups: dict[Any, list[Any]] = {}
    totals: dict[Any, int | None] = {}
    for record, parent_key, total in rows:
        groups.setdefault(parent_key, []).append(record)
        totals[parent_key] = total
    collections: Groups = {}
    for key, items in groups.items():
        more = limit is not None and len(items) > limit
        shown = items[:limit] if more else items
        collections[key] = EntityCollection(
            items=tuple(shown),
            count=counted(node, len(shown), more, totals[key], limit),
            # A page of none (`limit: 0`) asks for the count alone: nothing to go on from.
            cursor=node.pagination.next_from(shown[-1], sorts) if more and shown else None,
        )
    return collections


def _nested(
    prepare: Prepare,
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
        prepare, session, related, list(records), node.specification, definition, dropped
    )
    return resolved.only(node.specification)


def _kept(groups: Groups) -> list[Any]:
    """The records the collections keep — never the one read past a limit to know there is more,
    whose own relations nobody will see."""
    return [record for collection in groups.values() for record in collection.items]


def _by_foreign_key_on_related(
    prepare, session, meta, parents, relation, node, limit, dropped
):
    """A to-many: the related aggregate holds the field this one is matched by.

        Invoice.lines (line.invoice_id → invoice.id)
            ->  {invoice_id: EntityCollection[Line]}

    1. The node prepared against the related aggregate: its filter, ordering and definition.
    2. The value each parent is matched by; none at all answers no groups.
    3. One windowed read of every parent's related rows (`_partitioned`), grouped per value.
    4. The groups re-keyed by each parent's identity — the matched field need not be it.
    Final: the groups, and the node's own relations resolved over all the rows at once.
    """
    related = relation.related
    here, there = key_pair(meta.identity, relation, many=True)
    clause, sorts, definition, node_dropped = prepare(related, node)
    dropped.extend(node_dropped)
    ids = [getattr(parent, here) for parent in parents]
    if not ids:
        return {}, _nested(prepare, session, related, [], node, definition, dropped)
    key = getattr(related, there)
    rows = _partitioned(
        session, key, related, select(related), ids, clause, sorts, limit, node.count
    )
    grouped = _grouped(rows, node, sorts, limit)
    # `_grouped` keys by the value the two sides share; a caller reads a parent's records by
    # its identity, and the two only coincide when the match is the identity-anchored one.
    groups = {
        getattr(parent, meta.identity): found
        for parent in parents
        if (found := grouped.get(getattr(parent, here))) is not None
    }
    return groups, _nested(
        prepare, session, related, _kept(grouped), node, definition, dropped
    )


def _by_foreign_key_here(prepare, session, meta, parents, relation, node, dropped):
    """A to-one: this aggregate holds the field the related one is matched by.

        Line.invoice (line.invoice_id → invoice.id)
            ->  {line_id: EntityCollection[(Invoice,)]}

    1. The distinct non-NULL values the parents hold; none answers no groups.
    2. One read of the related records matching them, the node's filter added.
    3. Final: each parent gets a one-item collection of its match — none when the filter left
       it out — and the node's own relations are resolved over what was found.
    """
    related = relation.related
    here, there = key_pair(meta.identity, relation, many=False)
    clause, sorts, definition, node_dropped = prepare(related, node)
    dropped.extend(node_dropped)
    wanted = {getattr(parent, here) for parent in parents} - {None}
    if not wanted:
        return {}, _nested(prepare, session, related, [], node, definition, dropped)
    matched = getattr(related, there)
    statement = select(related).where(matched.in_(wanted))
    if clause is not None:
        statement = statement.where(clause)
    found = {getattr(r, there): r for r in session.scalars(statement)}
    groups = {
        getattr(parent, meta.identity): EntityCollection(items=(found[key],))
        for parent in parents
        if (key := getattr(parent, here)) in found
    }
    return groups, _nested(
        prepare, session, related, list(found.values()), node, definition, dropped
    )


def _through_table(prepare, session, meta, parents, relation, node, limit, dropped):
    """A many-to-many: the pairs live in a table in between.

        Dataset.tags through dataset_tag(dataset_id, tag_id)   ->  {dataset_id: [Tag, …]}

    1. The parents' identities; none answers no groups.
    2. Refused when the relation has no table in between or no key on it for the related side.
    3. The related aggregate joined to the pairs, filtered to these parents, read windowed per
       parent (`_partitioned`).
    Final: one collection per parent, and the node's own relations over all the rows.
    """
    related = relation.related
    clause, sorts, definition, node_dropped = prepare(related, node)
    dropped.extend(node_dropped)
    ids = [getattr(parent, meta.identity) for parent in parents]
    if not ids:
        return {}, _nested(prepare, session, related, [], node, definition, dropped)
    table = relation.through
    if table is None or relation.related_key is None:
        raise exceptions.ProgrammingError(
            f"{relation.related.__name__}: a many-to-many needs the table in between and "
            "the key on it pointing at the related aggregate"
        )
    this_key = table.c[relation.identified_by]
    related_key = table.c[relation.related_key]
    base = select(related).join(table, related_key == getattr(related, definition.identity))
    rows = _partitioned(
        session, this_key, related, base, ids, clause, sorts, limit, node.count
    )
    groups = _grouped(rows, node, sorts, limit)
    return groups, _nested(
        prepare, session, related, _kept(groups), node, definition, dropped
    )


def _by_id_list(prepare, session, meta, parents, relation, node, limit, dropped):
    """A to-many whose ids this aggregate holds in a column; the cut per parent is in memory,
    because the related aggregate has no column pointing back.

        Dataset.sources (dataset.source_ids = ["ds_1", "ds_2"])
            ->  {dataset_id: [Dataset, …]}

    1. Every id any parent holds; none answers no groups.
    2. One read of those records, filtered and ordered as the node asked.
    3. Per parent: the records whose id it holds, cut to `limit` with an exact count.
    Final: the groups, and the node's own relations over everything read.
    """
    related = relation.related
    clause, sorts, definition, node_dropped = prepare(related, node)
    dropped.extend(node_dropped)
    wanted: set[Any] = set()
    for parent in parents:
        wanted.update(getattr(parent, relation.identified_by) or [])
    if not wanted:
        return {}, _nested(prepare, session, related, [], node, definition, dropped)
    identity = getattr(related, definition.identity)
    statement = (
        select(related)
        .where(identity.in_(wanted))
        .order_by(*sql_translator.order_clauses(related, sorts))
    )
    if clause is not None:
        statement = statement.where(clause)
    fetched = list(session.scalars(statement))
    groups: Groups = {}
    for parent in parents:
        mine = set(getattr(parent, relation.identified_by) or [])
        items = [r for r in fetched if getattr(r, definition.identity) in mine]
        groups[getattr(parent, meta.identity)] = cut(items, node, sorts, limit, exact=True)
    return groups, _nested(prepare, session, related, fetched, node, definition, dropped)


def _scoped(declared: relations.Relation, node: Criteria) -> Criteria:
    """What the relation declared, with what the caller asked merged on top.

    The declared scope is the base, so its conditions accumulate with AND and a node can only
    narrow the relation further, never widen it. A scope the related aggregate cannot answer is
    refused rather than dropped: a filter that quietly disappears widens the answer, which is
    the one thing a scope exists to prevent — `Repository.narrowed` refuses on the same grounds.

    **The check only reaches what this process can see.** A relation may resolve a type mapped
    nowhere here — what a function resolver answers, or an aggregate another service owns — and
    there is no definition to check the scope against. It travels as declared, and honouring it
    is that provider's business.
    """
    if declared.scope is None:
        return node
    if declared.scope.expression is not None and is_mapped(declared.related):
        accepted, unknown = ModelIntrospection.describe(declared.related).accept(
            declared.scope.expression
        )
        if unknown or accepted is None:
            raise exceptions.ProgrammingError(
                f"{declared.related.__name__} cannot answer the scope its relation declares "
                f"({', '.join(one.field for one in unknown) or 'nothing survived'}); "
                "resolving it wide is not an option"
            )
    return declared.scope.merged_with(node)


def _resolve(
    prepare: Prepare,
    session: Session,
    meta: Meta,
    parents: Sequence[Any],
    declared: relations.Relation,
    node: Criteria,
    many: bool,
    dropped: list[Dropped],
    whole: bool,
) -> tuple[Groups, Meta | None]:
    """One node, by the kind the data mapper declared, over what the relation itself declares:
    the scope is merged here and nowhere else, so every kind honours it alike. Read whole, no
    page and no count are asked: what comes back is all there is."""
    node = _scoped(declared, node)
    if whole:
        node = node.model_copy(update={"count": CountMode.NONE})
    limit = limit_of(node, whole)
    if declared.kind is RelationKind.RESOLVER:
        return resolve_elsewhere(meta, parents, declared, node, many, limit)
    relation = cast("TableRelation", declared)
    match relation.kind:
        case RelationKind.FOREIGN_KEY if many:
            return _by_foreign_key_on_related(
                prepare, session, meta, parents, relation, node, limit, dropped
            )
        case RelationKind.FOREIGN_KEY:
            return _by_foreign_key_here(
                prepare, session, meta, parents, relation, node, dropped
            )
        case RelationKind.MANY_TO_MANY:
            return _through_table(
                prepare, session, meta, parents, relation, node, limit, dropped
            )
        case RelationKind.ID_LIST:
            return _by_id_list(
                prepare, session, meta, parents, relation, node, limit, dropped
            )
    raise ValueError(f"unknown relation kind {relation.kind!r}")


def _held_whole(
    model: type, name: str, declared: relations.Relation, groups: Groups
) -> Groups:
    """Every parent's relation as read whole, counted exactly — and the log told when one holds
    more than `WHOLE_WARNING`.

        Customer.invoices, 3 invoices      ->  {customer_id: EntityCollection(3, Count(3))}
        Customer.invoices, 2 million       ->  the same, all of them, and a warning naming it

    Context: the framework reads what it is asked to — touching a relation inside a unit of work
    is asking for all of it. Two million rows in memory is rarely what the Feature meant, so it
    is said, once per reading, with the cheaper ways to ask: a page, a count, each one by cursor.
    """
    for found in groups.values():
        if len(found.items) > relations.WHOLE_WARNING:
            logger.warning(
                f"{model.__name__}.{name} was read whole: {len(found.items):,} "
                f"{declared.related.__name__} in memory. A page, a count or each one by cursor "
                "(repository.search / count / stream with the relation's key) asks for less"
            )
    return {
        key: replace(found, cursor=None, count=Count(value=len(found.items), exact=True))
        for key, found in groups.items()
    }


# ---------------------------------------------------------------------------- the two doors


def resolve_relations(
    prepare: Prepare,
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

        resolve_relations(prepare, session, Dataset, [d1, d2], Specification({"runs":
                          Criteria()}), meta, dropped)
            ->  meta with fields["runs"].definition = Run's; d1.runs, d2.runs held WHOLE
        the same with Specification({"name": Criteria()})   # name is text
            ->  meta unchanged; dropped == [Dropped(field="name", reason="not_expandable")]

    1. A scalar with children is `not_expandable`; an unknown name was already reported.
    2. A relation nobody declared how to resolve is `not_expandable` too.
    3. Per named relation: one resolution for all the records, grouped by parent, assigned;
       then the node's own specification, recursively, over all the related records at once.
    Final: the same definition whether the page holds records or none — a relation with no
    parent to match is described without a query, so an empty answer says what a full one says.
    """
    if specification is None:
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
        declared = mapping_registry.relations_of(model).get(name)
        if declared is None:
            dropped.append(Dropped(field=name, reason="not_expandable"))
            continue
        if node.grouping.asked:
            # The slot is already spoken for: a node's `grouping` is how «so many per parent»
            # travels to the other side, so a caller's own cannot ride there too. Said out loud
            # rather than ignored — group the related aggregate on its own instead.
            dropped.append(Dropped(field=name, reason="not_groupable_in_a_relation"))

        many = field.many
        groups, definition = _resolve(
            prepare, session, meta, records, declared, node, many, dropped, whole
        )
        if whole and many:
            groups = _held_whole(model, name, declared, groups)
        declared_as = collection_fields(model).get(name) if many else None
        for record in records:
            found = groups.get(getattr(record, meta.identity))
            if many:
                value: Any = (
                    found
                    if found is not None
                    else EntityCollection(count=counted(node, 0, False, 0, None))
                )
            else:
                value = found.first() if found else None
            if declared_as is not None and not isinstance(value, declared_as):
                value = declared_as.of_page(value)  # a field declared `Lines` holds a `Lines`
            cut = not whole and (node.expression is not None or (many and value.is_partial))
            read = frozenset(identity_of(one) for one in value) if many and not cut else None
            HeldRelations(record).hold(name, value, Held.CUT if cut else Held.WHOLE, read)
        fields[name] = field.model_copy(
            update={
                "identified_by": declared.identified_by or field.identified_by,
                "parent_field": declared.parent_field or field.parent_field,
                "related_field": declared.related_field or field.related_field,
                "definition": definition,
            }
        )
    return meta.model_copy(update={"fields": fields})


def resolve_whole(
    prepare: Prepare, session: Session, model: type, record: Any, name: str
) -> None:
    """One relation of one record, whole: what a Feature touching it inside a unit of work gets."""
    resolve_relations(
        prepare,
        session,
        model,
        [record],
        Specification({name: Criteria()}),
        ModelIntrospection.describe(model),
        [],
        whole=True,
    )
