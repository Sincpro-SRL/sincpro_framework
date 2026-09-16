"""How a related aggregate is declared and, when it lives somewhere no mapper reaches, how it
is brought: the vocabulary, with no database in it.

    Relation.resolved_by(Publisher, identified_by="publisher_id", resolver=fetch)
    Relation.bus(Run, execution, CommandSearchRuns, identified_by="dataset_id")

A `Resolver` is anything called with the parents' keys and a criteria that answers the related
records: a function over HTTP, a command on another context's bus, a lookup in a dictionary
that was serialised somewhere. The persistence adapter adds the kinds only a database has, a
foreign key, a table in between, a list of ids, on top of this same declaration.

`identified_by` is the column that identifies the relation: on the related aggregate for a
to-many (`Dataset.runs` by `Run.dataset_id`), on this one for a to-one (`Run.dataset` by
`Run.dataset_id`). The cardinality is the annotation's: `runs: list[Run]` is a to-many.
"""

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from sincpro_framework.ddd.criteria import Condition, CountMode, Criteria, Operator, combined
from sincpro_framework.ddd.entity_collection import (
    Count,
    EntityCollection,
    identity_name,
    identity_of,
)
from sincpro_framework.ddd.model_meta import Meta
from sincpro_framework.ddd.pagination import Pagination
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
    """

    def __init__(
        self,
        kind: str,
        related: type,
        identified_by: str,
        resolver: Resolver | None = None,
    ) -> None:
        self.kind = kind
        self.related = related
        self.identified_by = identified_by
        self.resolver = resolver

    @classmethod
    def resolved_by(cls, related: type, identified_by: str, resolver: Resolver) -> "Relation":
        """Whatever the provider wrote: `resolver(keys, criteria)` answers the related records."""
        return cls("resolver", related, identified_by, resolver)

    @classmethod
    def bus(cls, related: type, bus: Any, command: type, identified_by: str) -> "Relation":
        """Another bounded context: the command carries the reflected criteria, the bus answers
        a paged response whose records are the related aggregate."""
        return cls("resolver", related, identified_by, BusResolver(bus, command))


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
    """The field the other side filters by, and the keys to send: the parents' identities for
    a to-many, the related identities this side holds for a to-one."""
    if many:
        return relation.identified_by, [getattr(p, meta.identity) for p in parents]
    held = {getattr(p, relation.identified_by) for p in parents} - {None}
    return identity_name(relation.related), sorted(held, key=str)


def _reflected(node: Criteria, field: str, ids: list[Any], limit: int | None) -> Criteria:
    """The criteria the other side receives: the node's, plus the parents' keys."""
    asked = (
        Pagination(limit=limit * len(ids)) if limit is not None else Pagination(limit=10**9)
    )
    return Criteria(
        where=combined(
            Condition(field=field, operator=Operator.IN, value=ids), node.expression
        ),
        order=node.order,
        pagination=asked,
        specification=node.specification,
        count=CountMode.NONE,
        meta=node.specification is not None,
    )


def _records_of(answer: Any) -> tuple[list[Any], Meta | None]:
    """What came back, whatever shape the other side chose to answer in."""
    if isinstance(answer, ResponsePaginatedQuery):
        return list(getattr(answer, type(answer).records_field())), answer.model_meta_data
    if isinstance(answer, EntityCollection):
        return list(answer.items), answer.meta
    return list(answer or []), None


def _regrouped(
    meta: Meta,
    parents: Sequence[Any],
    relation: Relation,
    node: Criteria,
    many: bool,
    limit: int | None,
    records: list[Any],
    asked_for: int,
) -> Groups:
    """Related records back from a resolver, cut per parent in memory. The count is exact
    unless the other side filled the page it was asked for."""
    exact = len(records) < asked_for
    by_key: dict[Any, list[Any]] = {}
    for record in records:
        key = getattr(record, relation.identified_by) if many else identity_of(record)
        by_key.setdefault(key, []).append(record)
    groups: Groups = {}
    for parent in parents:
        key = (
            getattr(parent, meta.identity)
            if many
            else getattr(parent, relation.identified_by)
        )
        items = by_key.get(key, [])
        if items or many:
            # A parent with nothing on a page the other side filled may simply have been
            # starved by it, so its empty collection carries the same honesty as the rest.
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
    records, definition = _records_of(relation.resolver(ids, criteria))
    return (
        _regrouped(meta, parents, relation, node, many, limit, records, criteria.limit),
        definition,
    )
