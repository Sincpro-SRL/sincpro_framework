"""What a caller sends: the filter, the ordering, the page.

One object and everything that goes inside it. A filter *is* a criteria — what the interface
calls a saved reading merges into this. See `docs/persistence/reference.md` and `specification.md`.

The filter language is deliberately small: conditions over one aggregate's own fields, combined
with and/or/not. No joins, no subqueries, no computed expressions — what this cannot say is
reached through the repository's escape hatch.
"""

import json
from collections.abc import Iterator
from enum import StrEnum
from typing import Any, cast

from pydantic import (
    Field,
    RootModel,
    ValidationInfo,
    field_validator,
)

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria.domain.expression import Expression, Node
from sincpro_framework.ddd.criteria.domain.grouping import Grouping, GroupingLevel, Measure
from sincpro_framework.ddd.criteria.domain.pagination import Cursor, Pagination
from sincpro_framework.ddd.criteria.domain.sort import Sort
from sincpro_framework.ddd.criteria.domain.strict import Strict, tolerates
from sincpro_framework.sincpro_abstractions import DataTransferObject


# No class docstring: pydantic publishes it as the description in the committed
# `criteria.schema.json` contract, which the TypeScript package copies.
# How much a page pays to say how many there are in total: NONE asks no total (the cheapest
# page), CAPPED counts up to a ceiling and answers `Count(exact=False)` past it (the default),
# EXACT counts to the end, whatever it costs.
class CountMode(StrEnum):
    NONE = "none"
    CAPPED = "capped"
    EXACT = "exact"


class Bucket(DataTransferObject):
    """One group: what its rows share, how many there are, and how to see them.

        out     Bucket(field='produced_by', value=None, count=187,
                       measures={'filas': 4012933}, criteria=Criteria(where=…))

    `criteria` is the whole point. It is this reading plus «and this bucket's value», so
    opening the group is `self.repository.search(Dataset, bucket.criteria)` and nothing else — no client
    rebuilds a filter it did not write.

    **A page of ids, when the criteria asked for a page.** `grouping` together with an explicit
    `pagination` means «a page per group»: every group of the deepest level answered carries the
    identities of its first `limit` rows in the criteria's order, an exact `count`, and a
    `cursor` to go on inside it. Ids and not records, the way Odoo's `search` answers before
    `browse` does: light enough for four thousand groups, stable enough to select, compare and
    open later.

        repository.browse(Line, bucket.ids)                            the first page, in order
        repository.search(Lines, bucket.criteria.resuming_from(bucket.cursor))   the rest
    """

    field: str
    value: Any
    count: int
    measures: dict[str, Any] = {}
    criteria: "Criteria"
    ids: list[Any] = []
    """The identities of this group's first page, in the criteria's order; empty unless the
    criteria asked for a page."""
    cursor: str | None = None
    """Where the next page inside this group starts; `None` when there is none, or none asked."""
    groups: list["Bucket"] = []
    """The level below, when the grouping named one. Empty on the deepest level."""
    openable: bool = True
    """Whether `criteria` selects this group's rows. False for a group of a date **part** («every
    March»), which no filter on the date can express: `criteria` is then its parent's, and a
    consumer that opens it would see rows that are not the group's."""


class Specification(RootModel[dict[str, "Criteria"]]):
    """Field name → the criteria that says how to bring it.

        in      {"name": {}, "lines": {"pagination": {"limit": 20}}}
        out     Specification with two fields, the second carrying its own page

    **One type and no union**: every value is a `Criteria`, the same one as outside, so a
    consumer deserialises `dict[str, Criteria]` in any language and never has to tell a string
    from an object. A scalar's is empty, because there is nothing to say about how to bring a
    number. A relation's carries where, order, pagination and its own specification, so the
    vocabulary inside a node is the vocabulary outside it, at any depth.

    A `RootModel` and not an object with one field inside: in JSON it is the bare mapping,
    without a key that means nothing, and in here it has the methods that belong to it.
    """

    root: dict[str, "Criteria"] = {}

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        """The field names asked for, in order — not pydantic's own fields."""
        return iter(self.root)

    def __contains__(self, name: str) -> bool:
        """Whether `name` was asked for."""
        return name in self.root

    def __len__(self) -> int:
        """How many fields were asked for."""
        return len(self.root)

    def __getitem__(self, name: str) -> "Criteria":
        """How to bring `name` — empty for a scalar."""
        return self.root[name]

    @property
    def named(self) -> list[str]:
        """The fields asked for, in the order they were asked.

        >>> Specification({"name": Criteria()}).named
        ['name']
        """
        return list(self.root)

    def narrowed_by(self, other: "Specification | None") -> "Specification":
        """This mask seen through another. **Intersection, never union.**

            in      {id, name, total} ∩ {id, name}      →  out  {id, name}
            in      {name} ∩ {total}                    →  out  {}   the identity alone

        A filter accumulates with AND because both restrictions hold; a mask intersects because
        a mask can only take away. That is what makes it safe as a permission: cutting twice can
        never show more than cutting once.
        """
        if other is None:
            return self
        return Specification(
            {
                name: criteria.merged_with(other[name])
                for name, criteria in self.root.items()
                if name in other
            }
        )


# No class docstring: pydantic publishes it as the description in the committed
# `criteria.schema.json` contract, which the TypeScript package copies.
# One reading of an aggregate: which rows (`where`), in what order, which page, what of each
# (`specification`), and how to group them when counted instead of listed. The same object on
# every wire, and strict: a key it does not have is refused. `Criteria()` is the store's first
# page of everything, in the store's order.
class Criteria(Strict):
    where: Expression | None = None
    """The filter: a `Condition`, or an `All`/`Any`/`Not` grouping more of them.

    **The declared type is exactly what is accepted.** No abbreviated spelling and no
    conversion, so pydantic and the type checker see the same thing and `criteria.where` reads
    with an exhaustive `match`. All the validator does is build the nodes when the tree
    arrives serialised from JSON — which is the same tree, not another form."""

    order: tuple[Sort, ...] = ()

    pagination: Pagination = Field(default_factory=lambda: Pagination())
    """How many, and from where. Always the object: `{"limit": 80, "strategy": {"token": "eyJ…"}}`."""

    specification: Specification | None = None
    """What to bring back of each record: field name → how to bring it, the value being a
    criteria exactly like this one. `None` and `{}` are NOT the same thing:

        None    nothing was asked          →  every scalar, no relation expanded
        {}      asked, nothing survived    →  the identity alone

    Read as one, a mask whose every field was dropped hands back the whole record, which is
    exactly what it existed to prevent."""

    grouping: Grouping = Field(default_factory=lambda: Grouping())
    """How to split the result set, when it is being counted rather than listed. Ignored by a
    plain search: a page of records has no buckets."""

    count: CountMode = CountMode.CAPPED

    meta: bool = True
    """Whether the answer carries the model definition — what may be filtered, ordered and
    shown, and with which operators.

    **On by default, and off only when somebody says `meta=false`.** It was opt-in first, to
    save the kilobyte and a half on every page of a listing. What that bought in bytes it cost
    in surprise: a client asking a resource got an answer it could not build a form, a column
    menu or a filter from, and had to know to ask a second time for the part that explains the
    first. A door that describes itself is worth more than the kilobyte."""

    @field_validator("where", mode="before")
    @classmethod
    def _reads_the_filter(cls, value: Any, info: ValidationInfo) -> Any:
        """The filter in the one form there is — and in its serialisation, which is the same thing.

        1. A text is JSON: that is how a filter survives a URL.
        2. Final: anything else is already the tree, and gets built.

            in      '{"field": "row_count", "operator": ">", "value": 1000}'      what a URL carries
            out     Condition(row_count, 1000, GT)

        >>> Criteria(where={'field': 'row_count', 'operator': '>', 'value': 1000}).expression
        Condition(field='row_count', value=1000, operator=<Operator.GT: '>'>)
        """
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError as error:
                raise exceptions.InvalidCriteria(
                    f"'where' does not read as JSON: {error}"
                ) from error

        return Node.read(value, tolerates(info))

    @field_validator("grouping", mode="before")
    @classmethod
    def _reads_the_grouping(cls, value: Any, info: ValidationInfo) -> Any:
        """The levels and the measures of the object, each to its type.

        in      {"group_by": [{"field": "produced_by"}], "measures": {"filas": ["sum", "row_count"]}}
        out     Grouping(group_by=(GroupingLevel(produced_by),), measures={'filas': Measure(sum, row_count)})
        """
        if isinstance(value, dict):
            tolerant = tolerates(info)
            value = dict(value)
            if "group_by" in value:
                value["group_by"] = tuple(
                    GroupingLevel.read(one, tolerant) for one in value["group_by"]
                )
            if "measures" in value:
                value["measures"] = {
                    name: Measure.read(measure, tolerant)
                    for name, measure in (value["measures"] or {}).items()
                }
            if "where_measures" in value:
                value["where_measures"] = Node.read(value["where_measures"], tolerant)
            if isinstance(value.get("order"), str):
                value["order"] = Sort.parse(value["order"])
        return value

    @property
    def limit(self) -> int:
        """How many rows this page asks for.

        >>> Criteria.model_validate({"pagination": {"limit": 80}}).limit
        80
        """
        return self.pagination.limit

    @property
    def cursor(self) -> str | None:
        """The token this page resumes from, when the strategy is one that resumes.

        >>> Criteria.model_validate({"pagination": {"strategy": {"token": "eyJ"}}}).cursor
        'eyJ'
        >>> Criteria().cursor is None
        True
        """
        return self.pagination.cursor

    def resuming_from(self, token: str | None) -> "Criteria":
        """The same reading, continued from where a page ended.

            in      a criteria paging by 80, the token that came back with the page
            out     Criteria(pagination=Pagination(limit=80, strategy=Cursor(token=…)))

        **This exists because `model_copy(update={"cursor": …})` does nothing.** `cursor` reads
        through to the strategy, so writing it past validation sets a field nobody reads: the
        page never advances and a loop that walks pages never ends. Which is exactly what
        happened, and what the tests caught.

        >>> self.repository.search(Dataset, criteria.resuming_from(page.cursor))
        """
        return self.model_copy(
            update={
                "pagination": self.pagination.model_copy(
                    update={"strategy": Cursor(token=token)}
                )
            }
        )

    @property
    def expression(self) -> Expression | None:
        """The filter. The name internals read it by, because `where` is what a caller writes.

        >>> Criteria(where={'field': 'row_count', 'operator': '>', 'value': 1000}).expression
        Condition(field='row_count', value=1000, operator=<Operator.GT: '>'>)
        >>> Criteria().expression is None
        True
        """
        return cast(Expression | None, self.where)

    def merged_with(self, other: "Criteria") -> "Criteria":
        """This criteria plus a more specific one.

            in      Criteria(where={'field': 'a', 'operator': '=', 'value': 1}, limit=20) + Criteria(where={'field': 'b', 'operator': '=', 'value': 2})
            out     Criteria(where=All([a==1, b==2]), limit=20)

        The rule has to be total or two readings quietly disagree.


        Conditions accumulate, because both filters apply. Scalars are replaced, because
        "twenty per page" and "fifty per page" have no combination. Sets union. The
        specification intersects, because a mask can only take away.

        The cursor is replaced and never carried over: it belongs to one ordering over one
        filter, and a merge that changed either would page through a set that no longer exists.
        """
        merged = Criteria(
            where=Node.combined(self.expression, other.expression),
            order=other.order or self.order,
            pagination=(
                other.pagination
                if other.pagination != Pagination()
                else self.pagination.model_copy(
                    update={"strategy": other.pagination.strategy}
                )
            ),
            specification=(
                other.specification
                if self.specification is None
                else self.specification.narrowed_by(other.specification)
            ),
            grouping=other.grouping if other.grouping.asked else self.grouping,
            count=other.count,
            # AND, not OR: with the definition travelling by default, an OR would mean nobody
            # could ever turn it off — the more specific criteria would always be overruled by
            # the default of the one it refines.
            meta=self.meta and other.meta,
        )
        # What either side asked stays asked, and nothing more: a merge must not turn an
        # untouched default into a request that would hide the entity's own (`replaced_by`).
        return Criteria.model_construct(
            self.model_fields_set | other.model_fields_set, **dict(merged)
        )

    def replaced_by(self, other: "Criteria") -> "Criteria":
        """This criteria, with every part `other` asked for put in its place.

            in      Criteria(order=-created_at, specification={runs: {}})
                    + Criteria(specification={title: {}})
            out     Criteria(order=-created_at, specification={title: {}})

        Context: how an entity's defaults meet what a caller sends. The caller wins part by
        part, and only for the parts it named (`model_fields_set`): `Criteria()` names nothing,
        so it retains every default, while a `Criteria(order=())` asks for the store's order
        on purpose. Filters are the exception: both apply, so they accumulate.

        Not `merged_with`: that one intersects the specification, because a scope can only take
        away. A default is not a scope — a caller asking for more than the default gets it.
        """
        asked = other.model_fields_set
        values = {
            name: getattr(other if name in asked else self, name)
            for name in Criteria.model_fields
        }
        values["where"] = Node.combined(self.expression, other.expression)
        return Criteria.model_construct(self.model_fields_set | asked, **values)


Specification.model_rebuild()
