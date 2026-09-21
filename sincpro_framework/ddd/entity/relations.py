"""How a related aggregate is declared and, when it lives somewhere no mapper reaches, how it
is brought: the vocabulary, with no database in it.

    Relation.resolved_by(Publisher, identified_by="publisher_id", resolver=fetch)
    Relation.bus(Run, execution, CommandSearchRuns, identified_by="dataset_id")

A `Resolver` is anything called with the parents' keys and a criteria that answers the related
records: a function over HTTP, a command on another context's bus, a lookup in a dictionary
that was serialised somewhere. The persistence adapter adds the kinds only a database has, a
foreign key, a table in between, a list of ids, on top of this same declaration.

**How the two sides are matched, said in full or said short.**

    parent_field="customer_id", related_field="id"      both sides named, read as written
    identified_by="dataset_id"                          the short form, anchored to an identity

Naming both sides is the one that never has to be guessed, and the one to reach for on a bus,
where the other side is a context away and nobody can look the column up. `identified_by` is
the short form for the common case, and what it means depends on the cardinality: the column
on the related aggregate for a to-many (`Dataset.runs` by `Run.dataset_id`), the column on this
one for a to-one (`Run.dataset` by `Run.dataset_id`), matched in both cases against the other
side's identity. `key_pair` below is the single place that expands it.

The cardinality itself is always the annotation's: `runs: list[Run]` is a to-many, and it
decides whether a collection or a single record comes back — nothing else.
"""

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from sincpro_framework.ddd.criteria import (
    Condition,
    CountMode,
    Criteria,
    Grouping,
    Level,
    Operator,
    Pagination,
    combined,
)
from sincpro_framework.ddd.entity.entity_collection import (
    Count,
    EntityCollection,
    identity_name,
    identity_of,
)
from sincpro_framework.ddd.entity.model_meta import Meta
from sincpro_framework.ddd.query import ResponsePaginatedQuery

NESTED_LIMIT = 40
"""How many of a to-many come per parent when the node did not say. A default, not a ceiling."""

Groups = dict[Any, EntityCollection]
"""What every resolution answers: the related records per parent identity."""


@runtime_checkable
class Resolver(Protocol):
    """Answers the related records for these keys, honouring what of the criteria it can.

    The answer may be an `EntityCollection`, a `ResponsePaginatedQuery` or a plain sequence
    of records; each record carries `identified_by` (to-many) or its own identity (to-one).
    """

    def __call__(self, keys: Sequence[Any], criteria: Criteria) -> Any: ...


class BusResolver:
    """A command on another bounded context's bus, carrying the reflected criteria."""

    def __init__(self, bus: Any, command: type) -> None:
        self.bus = bus
        self.command = command

    def __call__(self, keys: Sequence[Any], criteria: Criteria) -> Any:
        return self.bus(self.command(criteria=criteria))


class Relation:
    """One relation, declared once beside the aggregate's mapping.

    `kind` names how it is brought. The two kinds here need no database; a persistence adapter
    subclasses this and adds its own.

    **`scope` is what the relation says before anybody asks.** A `Criteria` like any other: the
    filter every reading of this relation starts from, and the order and page it falls back to.
    What a caller names in the specification merges on top and can only narrow it further —
    conditions accumulate with AND, so a node adds to the declared filter and can never drop
    it. It is `Repository.narrowed`'s scope at the scale of one relation, and the same rule
    holds there: a scope the related aggregate cannot answer is refused, never dropped.

        Relation.bus(ProjectEvent, project, CommandSearchEvents, identified_by="entity_id",
                     scope=Criteria(where=Condition(field="entity_type", value="project")))

    Declaring the order there is what makes «newest first» a property of the relation instead
    of something every caller repeats: `scope=Criteria(order=parse_order("-created_at"))`.
    """

    def __init__(
        self,
        kind: str,
        related: type,
        identified_by: str = "",
        resolver: Resolver | None = None,
        scope: Criteria | None = None,
        parent_field: str | None = None,
        related_field: str | None = None,
    ) -> None:
        self.kind = kind
        self.related = related
        self.identified_by = identified_by
        self.resolver = resolver
        self.scope = scope
        self.parent_field = parent_field
        self.related_field = related_field

    @classmethod
    def resolved_by(
        cls,
        related: type,
        identified_by: str = "",
        resolver: Resolver | None = None,
        scope: Criteria | None = None,
        parent_field: str | None = None,
        related_field: str | None = None,
    ) -> "Relation":
        """Whatever the provider wrote: `resolver(keys, criteria)` answers the related records."""
        return cls(
            "resolver",
            related,
            identified_by,
            resolver,
            scope=scope,
            parent_field=parent_field,
            related_field=related_field,
        )

    @classmethod
    def bus(
        cls,
        related: type,
        bus: Any,
        command: type,
        identified_by: str = "",
        scope: Criteria | None = None,
        parent_field: str | None = None,
        related_field: str | None = None,
    ) -> "Relation":
        """Another bounded context: the command carries the reflected criteria, the bus answers
        a paged response whose records are the related aggregate."""
        return cls(
            "resolver",
            related,
            identified_by,
            BusResolver(bus, command),
            scope=scope,
            parent_field=parent_field,
            related_field=related_field,
        )


def key_pair(identity: str, relation: Relation, many: bool) -> tuple[str, str]:
    """The two fields a relation matches on: the one read here, and the one read there.

        out     ("id", "entity_id")            Invoice.events, a to-many
        out     ("customer_id", "id")          Invoice.customer, a to-one

    A relation that named both sides is taken at its word. One that only said `identified_by`
    gets the convention it has always had, and this is the single place that knows it: this
    aggregate's identity against that field for a to-many, that field against the related
    aggregate's own identity for a to-one. Naming both is how a declaration stops depending on
    the cardinality to be read correctly — and the definition publishes the pair either way,
    so a client never has to know the convention at all.
    """
    if relation.parent_field is not None and relation.related_field is not None:
        return relation.parent_field, relation.related_field
    if many:
        return identity, relation.identified_by
    return relation.identified_by, identity_name(relation.related)


def limit_of(node: Criteria, whole: bool) -> int | None:
    """How many per parent: none when the relation is read whole, the default when the node
    did not say, the node's own otherwise."""
    if whole:
        return None
    return node.limit if "limit" in node.pagination.model_fields_set else NESTED_LIMIT


def cut(
    items: list[Any], node: Criteria, sorts: tuple, limit: int | None, exact: bool
) -> EntityCollection:
    """One parent's related records as a collection: cut to the limit, counted, with a cursor
    when there is more."""
    total = len(items)
    kept = items if limit is None else items[:limit]
    return EntityCollection(
        items=tuple(kept),
        count=Count(value=total, exact=exact),
        cursor=(
            node.pagination.next_from(kept[-1], sorts)
            if kept and limit is not None and total > limit
            else None
        ),
    )


def _keys_for(
    meta: Meta, parents: Sequence[Any], relation: Relation, many: bool
) -> tuple[str, list[Any]]:
    """The field the other side filters by, and the keys to send."""
    here, there = key_pair(meta.identity, relation, many)
    held = {getattr(parent, here) for parent in parents} - {None}
    if relation.parent_field is None and many:
        # The identity-anchored to-many keeps sending one key per parent, in order: they are
        # unique already, and the count of what was asked for is read off this list.
        return there, [getattr(parent, here) for parent in parents]
    return there, sorted(held, key=str)


def _reflected(node: Criteria, field: str, ids: list[Any], limit: int | None) -> Criteria:
    """The criteria the other side receives: the node's, plus the parents' keys.

    With a limit, `grouping` by the key travels with the page: on a repository of ours that
    means «`limit` rows for every key», so no parent starves the others. A side that does not
    know the meaning takes the page as a whole, and the cut per parent happens here afterwards.
    """
    keys = Condition(field=field, operator=Operator.IN, value=ids)
    if limit is None:
        return Criteria(
            where=combined(keys, node.expression),
            order=node.order,
            pagination=Pagination(limit=10**9),
            specification=node.specification,
            count=CountMode.NONE,
            meta=node.specification is not None,
        )
    return Criteria(
        where=combined(keys, node.expression),
        order=node.order,
        pagination=Pagination(limit=limit),
        grouping=Grouping(group_by=(Level(field=field),)),
        specification=node.specification,
        count=CountMode.NONE,
        meta=node.specification is not None,
    )


def _records_of(answer: Any, limit: int | None) -> tuple[list[Any], Meta | None, bool]:
    """What came back, whatever shape the other side chose to answer in, and whether it cut a
    page per key: a repository of ours answers a partitioned page with no cursor and an exact
    count, so a parent with fewer rows than the limit got all of them."""
    if isinstance(answer, ResponsePaginatedQuery):
        records = list(getattr(answer, type(answer).records_field()))
        partitioned = (
            answer.cursor is None and answer.count is not None and answer.count.exact
        )
        return records, answer.model_meta_data, partitioned and limit is not None
    if isinstance(answer, EntityCollection):
        partitioned = (
            answer.cursor is None and answer.count is not None and answer.count.exact
        )
        return list(answer.items), answer.meta, partitioned and limit is not None
    return list(answer or []), None, False


def _regrouped(
    meta: Meta,
    parents: Sequence[Any],
    relation: Relation,
    node: Criteria,
    many: bool,
    limit: int | None,
    records: list[Any],
    asked_for: int,
    partitioned: bool,
) -> Groups:
    """Related records back from a resolver, cut per parent in memory.

    With the partition honoured, a parent that got fewer than `limit` rows got all of them. A
    side that ignored the partition and filled the whole page could have starved a parent, so
    then nothing is claimed exact unless the whole page came back short.
    """
    here, there = key_pair(meta.identity, relation, many)
    by_key: dict[Any, list[Any]] = {}
    for record in records:
        key = (
            getattr(record, there, None)
            if relation.parent_field or many
            else identity_of(record)
        )
        by_key.setdefault(key, []).append(record)
    groups: Groups = {}
    for parent in parents:
        items = by_key.get(getattr(parent, here), [])
        if items or many:
            exact = (
                (limit is None or len(items) < limit)
                if partitioned
                else len(records) < asked_for
            )
            groups[getattr(parent, meta.identity)] = cut(
                items, node, tuple(node.order), limit, exact
            )
    return groups


def resolve_elsewhere(
    meta: Meta,
    parents: Sequence[Any],
    relation: Relation,
    node: Criteria,
    many: bool,
    limit: int | None,
) -> tuple[Groups, Meta | None]:
    """One node through its resolver: one call with every parent's key, the answer regrouped."""
    field, ids = _keys_for(meta, parents, relation, many)
    if not ids or relation.resolver is None:
        return {}, None
    criteria = _reflected(node, field, ids, limit)
    records, definition, partitioned = _records_of(relation.resolver(ids, criteria), limit)
    asked_for = limit * len(ids) if limit is not None and not partitioned else criteria.limit
    return (
        _regrouped(
            meta, parents, relation, node, many, limit, records, asked_for, partitioned
        ),
        definition,
    )
