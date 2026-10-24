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
from datetime import datetime
from functools import cache
from typing import Any, Literal, get_args, get_origin

from pydantic import (
    GetJsonSchemaHandler,
    PrivateAttr,
    SerializationInfo,
    TypeAdapter,
    model_serializer,
)
from pydantic.errors import PydanticInvalidForJsonSchema, PydanticSchemaGenerationError
from pydantic_core import to_jsonable_python

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria import Criteria, Specification
from sincpro_framework.ddd.entity import extensions
from sincpro_framework.ddd.entity.entity_collection import (
    Count,
    Dropped,
    EntityCollection,
    identity_name,
)
from sincpro_framework.ddd.entity.entity_meta import (
    FieldType,
    Meta,
    annotations_of,
    presentation_of,
)
from sincpro_framework.sincpro_abstractions import DataTransferObject

_SERIALISING: ContextVar[bool] = ContextVar("sincpro_serialising", default=False)


@contextmanager
def writing_out() -> Iterator[None]:
    """Marks the block in which an answer is written out, so `serialising()` says so.

        with writing_out():
            adapter.dump_python(record, mode="json")     a relation nobody resolved
                                                          answers its default, never raises

    Context: dumping a record reads every attribute, and a relation attribute read outside
    a resolution refuses (`RelationNotResolved`) — while writing out, it must not. Per thread
    and task, and restored exactly on the way out.
    """
    token = _SERIALISING.set(True)
    try:
        yield
    finally:
        _SERIALISING.reset(token)


@cache
def _adapter(aggregate: type) -> TypeAdapter:
    """The pydantic `TypeAdapter` that dumps a record of this class, built once per class."""
    return TypeAdapter(aggregate)


def _shaped(value: Any, specification: Specification, definition: Meta) -> Any:
    """An embedded value, already written out, cut to what its node named — a value object
    has no identity to keep; a list of them, each one cut the same way.

        {"amount": 5, "currency": "BOB", "rate": 1}, {"amount": {}}   ->  {"amount": 5}

    1. `None` stays `None`; a list is cut item by item.
    2. Only the names the node lists survive.
    Final: a kept name that is itself an embedded value with its own node is cut the same way.
    """
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


def _delegated(
    record: Any, written: dict[str, Any], specification: Specification | None
) -> dict[str, Any]:
    """The fields of each extension under its own key, for a record whose entity asks for it.

        {"number": "F-1", "route": "R7"}   →   {"number": "F-1", "delivery": {"route": "R7"}}

    An extension the record does not have is `null`, so the shape does not depend on the row. A
    specification that names none of an extension's fields leaves its key out.
    """
    root = next(
        (
            one
            for one in extensions.DELEGATED
            if isinstance(record, one) and one in extensions.EXTENSIONS
        ),
        None,
    )
    if root is None:
        return written
    for one in extensions.EXTENSIONS[root]:
        if specification is not None and not set(one.fields) & set(specification.named):
            for name in one.fields:
                written.pop(name, None)
            continue
        nested = {name: written.pop(name) for name in one.fields if name in written}
        written[one.key] = nested if isinstance(record, one.entity) else None
    return written


def _record(
    record: Any,
    specification: Specification | None,
    meta: Meta | None,
    mode: Literal["json", "python"],
) -> dict[str, Any]:
    """One record as the answer shows it: the cut it is given, with the fields of its
    extensions flat or under their keys as its entity asks."""
    return _delegated(record, _cut(record, specification, meta, mode), specification)


def _cut(
    record: Any,
    specification: Specification | None,
    meta: Meta | None,
    mode: Literal["json", "python"],
) -> dict[str, Any]:
    """One record as the answer shows it: the scalars the mask keeps, then the relations
    it names.

        Invoice, {"number": {}, "lines": {"specification": {"qty": {}}}}
        ->  {"id": …, "number": "F-1",
             "lines": {"items": [{"id": …, "qty": 2}], "count": {…}, "cursor": None}}

    1. The whole record dumped, its relation attributes taken out — they are written below
       from what was resolved, never from the attribute.
    2. No mask: that is the answer, every scalar.
    3. A mask keeps the identity and what it names — through the model's definition when
       there is one, else the names as given, or the entity's display when none.
    4. Each named field the model knows:
    4.1 an embedded value is cut to its own node (`_shaped`);
    4.2 a to-many relation is written as a page — items, count, cursor — of what the reading
        resolved, an empty page when nothing was;
    4.3 a to-one relation is its record, or `None`.
    Final: each related record written the same way, recursively (`_record`).
    """
    written = _adapter(type(record)).dump_python(record, mode=mode)
    relations = set(meta.relations) if meta is not None else set()
    for name in relations:
        written.pop(name, None)
    if specification is None:
        return written

    if meta is not None:
        keep = {meta.identity, *meta.masked(specification)}
    else:
        display = presentation_of(type(record)).display
        named = specification.named or ([display] if display else [])
        keep = {identity_name(type(record)), *named}
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


def _extended(schema: dict[str, Any], declared: Any) -> dict[str, Any]:
    """The schema of a record, with what the extensions of its class add.

        Move + MoveDelivery(route)    →  {"allOf": [Move], "properties": {"route": …}}
        delegated                     →  {"allOf": [Move], "properties": {"delivery": {…}}}

    The fields are optional: a record with no extension row has none of them. A list of records
    is patched in its items.
    """
    if get_origin(declared) is list:
        (declared,) = get_args(declared)
        if schema.get("type") == "array" and "items" in schema:
            return {**schema, "items": _extended(schema["items"], declared)}
    if not isinstance(declared, type):
        return schema
    added: dict[str, Any] = {}
    for one in extensions.under(declared):
        written: dict[str, Any] = {}
        for name in one.fields:
            try:
                written[name] = TypeAdapter(annotations_of(one.entity)[name]).json_schema(
                    mode="serialization"
                )
            except (PydanticSchemaGenerationError, PydanticInvalidForJsonSchema):
                # a type pydantic cannot say: the field is there, untyped
                written[name] = {}
        if one.root in extensions.DELEGATED:
            added[one.key] = {
                "anyOf": [{"type": "object", "properties": written}, {"type": "null"}]
            }
        else:
            added.update(written)
    if not added:
        return schema
    return {"allOf": [schema], "properties": added}


def _properties(
    cls: Any, fields: dict[str, Any], handler: GetJsonSchemaHandler, holder: str
) -> dict[str, Any]:
    """The properties of an answer: each declared field, the one holding the records extended."""
    written = {name: handler(one["schema"]) for name, one in fields.items()}
    written[holder] = _extended(written[holder], cls.model_fields[holder].annotation)
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
            datasets: list[Dataset]         →   .datasets .cursor .count .entity_meta_data

    The records keep the subclass's own name — `datasets`, `runs`, `plans` — because a reader
    of a response should see what it is about, not `items`.
    """

    cursor: str | None = None
    """Where the next page starts. `None` means there is no next one."""

    count: Count | None = None
    """How many match in total, capped unless the criteria asked for the exact number.
    `exact=False` means *at least* that many."""

    entity_meta_data: Meta | None = None
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
            raise exceptions.ProgrammingError(
                f"{cls.__name__} declares {len(own)} fields of its own ({', '.join(own) or 'none'}); "
                "a paginated response declares exactly one, holding its records"
            )
        return own[0]

    @model_serializer(mode="plain")
    def _written_out(self, info: SerializationInfo) -> dict[str, Any]:
        """On the wire, a record shows what the specification named plus its identity, and each
        named relation under its own name: a to-one as an object or `null`, a to-many as
        `{"items", "count", "cursor"}`. An empty specification keeps the identity and the
        display field. Without a specification, every scalar and no relation.

        The mask is applied here and nowhere earlier: inside the process the records stay the
        typed aggregates they are, so a Feature reading the page still has every field.
        """
        mode: Literal["json", "python"] = "json" if info.mode == "json" else "python"
        records = self.records_field()
        with writing_out():
            written = {
                records: [
                    _record(item, self._specification, self._meta, mode)
                    for item in getattr(self, records)
                ],
                "cursor": self.cursor,
                "count": self.count.model_dump(mode=mode) if self.count else None,
                "entity_meta_data": (
                    self.entity_meta_data.model_dump(mode=mode)
                    if self.entity_meta_data
                    else None
                ),
                "dropped": [one.model_dump(mode=mode) for one in self.dropped],
            }
        for name in type(self).model_fields:
            if name not in written:
                value = getattr(self, name)
                written[name] = to_jsonable_python(value) if mode == "json" else value
        return written

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: Any, handler: GetJsonSchemaHandler
    ) -> dict[str, Any]:
        """What this answer looks like, said for the OpenAPI as well as for the validator.

        **Without this, every paginated answer is published as `{"type": "object"}`.** A model
        with a plain serializer tells pydantic "I write an object" and nothing else, so the
        schema loses `cursor`, `count`, `entity_meta_data`, `dropped` and the records — which is
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
            "properties": _properties(cls, fields, handler, cls.records_field()),
            "required": sorted(fields),
        }

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
            entity_meta_data=(
                page.meta.only(criteria.specification)
                if criteria.meta and page.meta is not None
                else None
            ),
            dropped=list(page.dropped),
        )
        response._specification = criteria.specification
        response._meta = page.meta
        return response


class DraftState(DataTransferObject):
    """The record was answered from its owner's draft — what a form kept and nobody saved yet.

    `version` is what the next edit names as `version`; `stale` says the stored record moved on
    since the draft started, so saving it would be refused.
    """

    version: int
    origin_version: int
    kept_at: datetime
    stale: bool = False


class ResponseRecord(DataTransferObject):
    """What a read of one record returns beside it.

    class ResponseAccount(ResponseRecord):
        account: Account            →   .account .entity_meta_data .dropped

    The record keeps the subclass's own name for the same reason a page's records do. On the
    wire it is cut by the specification exactly as a record of a page is.
    """

    entity_meta_data: Meta | None = None
    """What may be asked about the model, unless the criteria said `meta=false`."""

    dropped: list[Dropped] = []
    """What the specification named and the model could not answer."""

    draft: DraftState | None = None
    """`None`: the record as stored. Otherwise its owner's draft, asked for with `draft=True`
    (`Get`) or answered by an edit (`Edit`)."""

    _specification: Specification | None = PrivateAttr(default=None)
    _meta: Meta | None = PrivateAttr(default=None)

    @classmethod
    def record_field(cls) -> str:
        """The one field the subclass added: where its record goes.

        class ResponseAccount(ResponseRecord):
            account: Account            →   out  'account'
        """
        declared = {
            name
            for base in cls.__mro__
            if base.__module__.startswith("sincpro_framework.")
            and hasattr(base, "model_fields")
            for name in base.model_fields
        }
        own = [name for name in cls.model_fields if name not in declared]
        if len(own) != 1:
            raise exceptions.ProgrammingError(
                f"{cls.__name__} declares {len(own)} fields of its own ({', '.join(own) or 'none'}); "
                "a record response declares exactly one, holding its record"
            )
        return own[0]

    @model_serializer(mode="plain")
    def _written_out(self, info: SerializationInfo) -> dict[str, Any]:
        """On the wire, the record shows what the specification named plus its identity, and
        each named relation under its own name, as a record of a page does; every other field
        follows as itself."""
        mode: Literal["json", "python"] = "json" if info.mode == "json" else "python"
        field = self.record_field()
        with writing_out():
            written = {
                field: _record(getattr(self, field), self._specification, self._meta, mode),
                "entity_meta_data": (
                    self.entity_meta_data.model_dump(mode=mode)
                    if self.entity_meta_data
                    else None
                ),
                "dropped": [one.model_dump(mode=mode) for one in self.dropped],
            }
        for name in type(self).model_fields:
            if name not in written:
                value = getattr(self, name)
                written[name] = to_jsonable_python(value) if mode == "json" else value
        return written

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: Any, handler: GetJsonSchemaHandler
    ) -> dict[str, Any]:
        """The declared fields, said for the OpenAPI too: a plain serializer alone publishes
        `{"type": "object"}`."""
        if handler.mode != "serialization":
            return handler(core_schema)

        fields = core_schema["schema"]["fields"]
        return {
            "type": "object",
            "title": cls.__name__,
            "description": (
                "One record with what the engine says about it. When the criteria carried a "
                "specification, the record holds the fields it named plus the identity."
            ),
            "properties": _properties(cls, fields, handler, cls.record_field()),
            "required": sorted(fields),
        }

    @classmethod
    def of(cls, record: Any, page: EntityCollection, criteria: Criteria) -> Any:
        """One record as the answer that goes out, with the definition of the page it was
        read in.

            in      Account(1105, 'Caja'), the page it came in, the criteria it answered
            out     ResponseAccount(account=Account(1105, 'Caja'), entity_meta_data=Meta(...))
        """
        response = cls(
            **{cls.record_field(): record},
            entity_meta_data=(
                page.meta.only(criteria.specification)
                if criteria.meta and page.meta is not None
                else None
            ),
            dropped=list(page.dropped),
        )
        response._specification = criteria.specification
        response._meta = page.meta
        return response


class ResponseRecords(DataTransferObject):
    """What a read of several records by their identities returns: a list, not a page.

    class ResponseAccounts(ResponseRecords):
        accounts: list[Account]     →   .accounts .missing .entity_meta_data .dropped

    The records come in the order the identities were asked, each one cut by the specification
    as a record of a page is. An identity with no record is listed in `missing`, not raised:
    a list of references outlives some of what it points at.
    """

    missing: list[str] = []
    """The identities asked that have no record."""

    entity_meta_data: Meta | None = None
    """What may be asked about the model, unless the criteria said `meta=false`."""

    dropped: list[Dropped] = []
    """What the specification named and the model could not answer."""

    _specification: Specification | None = PrivateAttr(default=None)
    _meta: Meta | None = PrivateAttr(default=None)

    @classmethod
    def records_field(cls) -> str:
        """The one field the subclass added: where its records go.

        class ResponseAccounts(ResponseRecords):
            accounts: list[Account]     →   out  'accounts'
        """
        own = [name for name in cls.model_fields if name not in ResponseRecords.model_fields]
        if len(own) != 1:
            raise exceptions.ProgrammingError(
                f"{cls.__name__} declares {len(own)} fields of its own ({', '.join(own) or 'none'}); "
                "a records response declares exactly one, holding its records"
            )
        return own[0]

    @model_serializer(mode="plain")
    def _written_out(self, info: SerializationInfo) -> dict[str, Any]:
        """On the wire, each record shows what the specification named plus its identity."""
        mode: Literal["json", "python"] = "json" if info.mode == "json" else "python"
        field = self.records_field()
        with writing_out():
            written = {
                field: [
                    _record(one, self._specification, self._meta, mode)
                    for one in getattr(self, field)
                ],
                "missing": list(self.missing),
                "entity_meta_data": (
                    self.entity_meta_data.model_dump(mode=mode)
                    if self.entity_meta_data
                    else None
                ),
                "dropped": [one.model_dump(mode=mode) for one in self.dropped],
            }
        return written

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: Any, handler: GetJsonSchemaHandler
    ) -> dict[str, Any]:
        """The declared fields, said for the OpenAPI too: a plain serializer alone publishes
        `{"type": "object"}`."""
        if handler.mode != "serialization":
            return handler(core_schema)

        fields = core_schema["schema"]["fields"]
        return {
            "type": "object",
            "title": cls.__name__,
            "description": (
                "The records asked by identity, in the order asked, and the identities that "
                "have none. When the criteria carried a specification, each record holds the "
                "fields it named plus the identity."
            ),
            "properties": _properties(cls, fields, handler, cls.records_field()),
            "required": sorted(fields),
        }

    @classmethod
    def of(
        cls, ids: list[str], page: EntityCollection, criteria: Criteria, identity: str
    ) -> Any:
        """The records of a page, put back in the order of `ids`.

            ResponseAccounts.of(["b", "gone", "a"], <page holding a and b>, criteria, "id")
                ->  ResponseAccounts(accounts=[b, a], missing=["gone"])

        Context: the identity is compared as text; the definition travels — cut by the
        specification — only when the criteria asked for it, with what the page dropped.
        """
        found = {str(getattr(one, identity)): one for one in page.items}
        response = cls(
            **{cls.records_field(): [found[one] for one in ids if one in found]},
            missing=[one for one in ids if one not in found],
            entity_meta_data=(
                page.meta.only(criteria.specification)
                if criteria.meta and page.meta is not None
                else None
            ),
            dropped=list(page.dropped),
        )
        response._specification = criteria.specification
        response._meta = page.meta
        return response
