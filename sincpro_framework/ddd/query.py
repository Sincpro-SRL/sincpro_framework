"""The shape every read shares: what a query command carries, and what a paged answer returns.

Inherited rather than repeated. A use case that reads declares what it is *about* — the records
and their type — and gets the filter on the way in and the pagination on the way out for free:

    class CommandListDatasets(Query):
        pass

    class ResponseListDatasets(ResponsePaginatedQuery):
        datasets: list[Dataset]

Without these two, every listing writes the same five fields again and the sixth one writes
them slightly differently.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from typing import Any, Literal

from pydantic import (
    GetJsonSchemaHandler,
    PrivateAttr,
    SerializationInfo,
    TypeAdapter,
    model_serializer,
)

from sincpro_framework.ddd.criteria import Criteria, Specification
from sincpro_framework.ddd.entity.entity_collection import (
    Count,
    Dropped,
    EntityCollection,
    identity_name,
)
from sincpro_framework.ddd.entity.model_meta import FieldType, Meta
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.sincpro_abstractions import DataTransferObject

_SERIALISING: ContextVar[bool] = ContextVar("sincpro_serialising", default=False)


@contextmanager
def _writing_out() -> Iterator[None]:
    token = _SERIALISING.set(True)
    try:
        yield
    finally:
        _SERIALISING.reset(token)


@cache
def _adapter(aggregate: type) -> TypeAdapter:
    return TypeAdapter(aggregate)


def _shaped(value: Any, specification: Specification, definition: Meta) -> Any:
    """An embedded value, already written out, cut to what its node named plus its identity;
    a list of them, each one cut the same way."""
    if value is None:
        return None
    if isinstance(value, list):
        return [_shaped(one, specification, definition) for one in value]
    keep = set(specification.named)  # a value object has no identity to keep
    shaped: dict[str, Any] = {}
    for name, inner in value.items():
        if name not in keep:
            continue
        node = specification.root.get(name)
        field = definition.fields.get(name)
        if (
            node is not None
            and node.specification is not None
            and field is not None
            and field.definition is not None
        ):
            inner = _shaped(inner, node.specification, field.definition)
        shaped[name] = inner
    return shaped


def serialising() -> bool:
    """Whether a paged answer is being written out right now. A relation attribute read
    during that answers its default instead of resolving or refusing: the answer writes the
    relations it resolved itself, from what the specification named."""
    return _SERIALISING.get()


def _record(
    record: Any,
    specification: Specification | None,
    meta: Meta | None,
    mode: Literal["json", "python"],
) -> dict[str, Any]:
    """One record as the answer shows it: the scalars the mask keeps, then the named relations."""
    written = _adapter(type(record)).dump_python(record, mode=mode)
    relations = set(meta.relations) if meta is not None else set()
    for name in relations:
        written.pop(name, None)
    if specification is None:
        return written

    identity = meta.identity if meta is not None else identity_name(type(record))
    keep = {identity, *specification.named}
    written = {name: value for name, value in written.items() if name in keep}
    for name, node in specification.root.items():
        if meta is None or name not in meta.fields:
            continue
        field = meta.fields[name]
        if field.type is FieldType.EMBEDDED:
            if node.specification is not None and field.definition is not None:
                written[name] = _shaped(
                    written.get(name), node.specification, field.definition
                )
            continue
        if name not in relations:
            continue
        related = field
        value = record.__dict__.get("_sincpro_resolved", {}).get(name)
        if related.many:
            page = value if isinstance(value, EntityCollection) else EntityCollection()
            written[name] = {
                "items": [
                    _record(c, node.specification, related.definition, mode) for c in page
                ],
                "count": page.count.model_dump(mode=mode) if page.count else None,
                "cursor": page.cursor,
            }
        else:
            written[name] = (
                None
                if value is None
                else _record(value, node.specification, related.definition, mode)
            )
    return written


class Query(DataTransferObject):
    """What any command that reads carries.

    class CommandListDatasets(Query):   →   CommandListDatasets(criteria=Criteria(...))
        pass
    """

    criteria: Criteria = Criteria()
    """Everything being asked: the filter, the ordering, the page and what to bring along.

    One field rather than a parameter per filter, because a filter *is* a criteria — what the
    interface calls a saved reading is the same object, merged."""


class ResponsePaginatedQuery(DataTransferObject):
    """What any paged answer returns beside its records.

        class ResponseListDatasets(ResponsePaginatedQuery):
            datasets: list[Dataset]         →   .datasets .cursor .count .model_meta_data

    The records keep the subclass's own name — `datasets`, `runs`, `plans` — because a reader
    of a response should see what it is about, not `items`.
    """

    cursor: str | None = None
    """Where the next page starts. `None` means there is no next one."""

    count: Count | None = None
    """How many match in total, capped unless the criteria asked for the exact number.
    `exact=False` means *at least* that many."""

    model_meta_data: Meta | None = None
    """What may be filtered, ordered and walked, and with which operators, so a client can
    build a filter form, a column menu or a URL without carrying a schema.

    Travels with every answer unless the criteria said `meta=false` — a caller that already
    knows the model, and is paging, can turn it off."""

    dropped: list[Dropped] = []
    """Conditions the model could not answer — an unknown field, an operator its type does not
    take. Reported rather than raised: a shared link outlives the schema it was written
    against, and a dropped filter always widens the result."""

    _specification: Specification | None = PrivateAttr(default=None)
    _meta: Meta | None = PrivateAttr(default=None)

    @model_serializer(mode="plain")
    def _written_out(self, info: SerializationInfo) -> dict[str, Any]:
        """On the wire, a record shows what the specification named plus its identity, and each
        named relation under its own name: a to-one as an object or `null`, a to-many as
        `{"items", "count", "cursor"}`. Without a specification, every scalar and no relation.

        The mask is applied here and nowhere earlier: inside the process the records stay the
        typed aggregates they are, so a Feature reading the page still has every field.
        """
        mode: Literal["json", "python"] = "json" if info.mode == "json" else "python"
        records = self.records_field()
        with _writing_out():
            written = {
                records: [
                    _record(item, self._specification, self._meta, mode)
                    for item in getattr(self, records)
                ],
                "cursor": self.cursor,
                "count": self.count.model_dump(mode=mode) if self.count else None,
                "model_meta_data": (
                    self.model_meta_data.model_dump(mode=mode)
                    if self.model_meta_data
                    else None
                ),
                "dropped": [one.model_dump(mode=mode) for one in self.dropped],
            }
        return written

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: Any, handler: GetJsonSchemaHandler
    ) -> dict[str, Any]:
        """What this answer looks like, said for the OpenAPI as well as for the validator.

        **Without this, every paginated answer is published as `{"type": "object"}`.** A model
        with a plain serializer tells pydantic "I write an object" and nothing else, so the
        schema loses `cursor`, `count`, `model_meta_data`, `dropped` and the records — which is
        exactly what a client generating types needs, and why the TypeScript package had to
        write those types by hand.

        The shape is not a guess: the serializer writes the declared fields and no others, so
        the schema is built from them.

        One thing it cannot say: a `specification` cuts the fields of each record, so a record
        may arrive with fewer than its type declares. Only the identity is always there.
        """
        if handler.mode != "serialization":
            return handler(core_schema)

        fields = core_schema["schema"]["fields"]
        return {
            "type": "object",
            "title": cls.__name__,
            "description": (
                "A page of records with what the engine says about it. When the criteria "
                "carried a specification, each record holds the fields it named plus the "
                "identity, and nothing else."
            ),
            "properties": {name: handler(one["schema"]) for name, one in fields.items()},
            "required": sorted(fields),
        }

    @classmethod
    def records_field(cls) -> str:
        """The one field the subclass added: where its records go.

            class ResponseListDatasets(ResponsePaginatedQuery):
                datasets: list[Dataset]     →   out  'datasets'

        A convention rather than a declaration, the same way the first field of an aggregate
        is its identity. Anything but exactly one added field is refused, because then nobody
        — including this method — can tell which one holds the records.
        """
        own = [
            name
            for name in cls.model_fields
            if name not in ResponsePaginatedQuery.model_fields
        ]
        if len(own) != 1:
            raise ContractViolation(
                f"{cls.__name__} declares {len(own)} fields of its own ({', '.join(own) or 'none'}); "
                "a paginated response declares exactly one, holding its records"
            )
        return own[0]

    @classmethod
    def of(cls, page: EntityCollection, criteria: Criteria) -> Any:
        """A page as the answer that goes out.

            in      EntityCollection(20 records of 197, more), the criteria it answered
            out     ResponseListDatasets(datasets=[...], cursor='eyJ…', count=Count(197, True))

        The page already carries the definition of the model it came from — the engine read it
        to answer — so a use case never has to know that describing a model is a thing that
        exists. The criteria says whether the definition was asked for, and its specification
        is the one mask over the records and the definition alike.

        >>> ResponseListDatasets.of(page, criteria).datasets[0].name
        'labs.csv'
        """
        response = cls(
            **{cls.records_field(): list(page.items)},
            cursor=page.cursor,
            count=page.count,
            model_meta_data=(
                page.meta.only(criteria.specification)
                if criteria.meta and page.meta is not None
                else None
            ),
            dropped=list(page.dropped),
        )
        response._specification = criteria.specification
        response._meta = page.meta
        return response
