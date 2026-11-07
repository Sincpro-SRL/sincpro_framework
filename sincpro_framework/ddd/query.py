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

import dataclasses
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from functools import cache
from typing import Any, Literal, Self, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    PrivateAttr,
    SerializationInfo,
    TypeAdapter,
    model_serializer,
    model_validator,
)
from pydantic.errors import PydanticInvalidForJsonSchema, PydanticSchemaGenerationError
from pydantic.json_schema import SkipJsonSchema
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
    collection_class,
    records_class,
)
from sincpro_framework.ddd.metamodel import model_of
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
    has no identity to include; a list of them, each one cut the same way.

        {"amount": 5, "currency": "BOB", "rate": 1}, {"amount": {}}   ->  {"amount": 5}

    1. `None` stays `None`; a list is cut item by item.
    2. Only the names the node lists survive.
    Final: a selected name that is itself an embedded value with its own node is cut the same
    way.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return [_shaped(one, specification, definition) for one in value]
    selected = set(specification.named)  # a value object has no identity to include
    shaped: dict[str, Any] = {}
    for name, inner in value.items():
        if name not in selected:
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
    """One record as the answer shows it: the scalars the mask selects, then the relations
    it names.

        Invoice, {"number": {}, "lines": {"specification": {"qty": {}}}}
        ->  {"id": …, "number": "F-1",
             "lines": {"items": [{"id": …, "qty": 2}], "count": {…}, "cursor": None}}

    1. The whole record dumped, its relation attributes taken out — they are written below
       from what was resolved, never from the attribute.
    2. No mask: that is the answer, every scalar.
    3. A mask selects the identity and what it names — through the model's definition when
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
        selected = {meta.identity, *meta.masked(specification)}
    else:
        display = model_of(type(record)).presented.display
        named = specification.named or ([display] if display else [])
        selected = {identity_name(type(record)), *named}
    written = {name: value for name, value in written.items() if name in selected}
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
                written[name] = TypeAdapter(
                    model_of(one.entity).annotations[name]
                ).json_schema(mode="serialization")
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


def _version_of(annotation: Any) -> str:
    """The version of the model of the records a field holds — `list[Sale]`, `Sale`,
    `Sale | None` —: the fingerprint its contract published, which a client compares with the
    one it loaded; empty for what the metamodel does not describe.

        _version_of(list[Sale])     ->  "3f9c…"
        _version_of(Sales)          ->  "3f9c…"     # class Sales(EntityCollection[Sale])
        _version_of(list[dict])     ->  ""
    """
    if collection_class(annotation) is not None and not get_args(annotation):
        annotation = records_class(annotation)[0]
    for argument in get_args(annotation):
        found = _version_of(argument)
        if found:
            return found
    if isinstance(annotation, type) and (
        dataclasses.is_dataclass(annotation) or issubclass(annotation, BaseModel)
    ):
        return model_of(annotation).fingerprint
    return ""


def _properties(
    cls: Any, fields: dict[str, Any], handler: GetJsonSchemaHandler, holder: str
) -> dict[str, Any]:
    """The properties of an answer: each declared field, the one holding the records extended.
    A records field declared as a collection travels as the array of its records — its count and
    cursor are the answer's own — and is published so."""
    written = {name: handler(one["schema"]) for name, one in fields.items()}
    declared = cls.model_fields[holder].annotation
    if collection_class(declared) is not None:
        held = written[holder].get("properties", {}).get("items")
        if held is not None:
            written[holder] = held
        declared = list[records_class(declared)[0]]  # type: ignore[misc]
    written[holder] = _extended(written[holder], declared)
    return written


def _is_page(option: Any) -> bool:
    """The page a collection accepts beside a list: `{"items", "count", "cursor"}`."""
    if not isinstance(option, dict) or option.get("type") != "object":
        return False
    items = option.get("properties", {}).get("items")
    return isinstance(items, dict) and items.get("type") == "array"


def _as_records(schema: dict[str, Any], holder: str) -> dict[str, Any]:
    """The validation schema with the records field as the array the answer writes.

    Context: a collection validates as a list or as a page. An answer writes the list — the
    count and the cursor are its own fields — and the contract is this schema, so the page is
    not published. The field's title stays, which is how a `list` of the same records is
    published.

    1. Drop the page branch.
    2. With only the array left, the field is that array.
    Final: a null beside the array stays, when the collection was declared optional.
    """
    properties = schema.get("properties")
    field = properties.get(holder) if isinstance(properties, dict) else None
    options = field.get("anyOf") if isinstance(field, dict) else None
    if (
        not isinstance(properties, dict)
        or not isinstance(field, dict)
        or not isinstance(options, list)
    ):
        return schema
    kept = [one for one in options if not _is_page(one)]
    if len(kept) == len(options):
        return schema
    if len(kept) == 1 and isinstance(kept[0], dict) and kept[0].get("type") == "array":
        carried = {
            key: field[key] for key in ("title", "description", "default") if key in field
        }
        properties[holder] = {**kept[0], **carried}
        return schema
    properties[holder] = {**field, "anyOf": kept}
    return schema


def _validation_schema(
    cls: Any,
    core_schema: Any,
    handler: GetJsonSchemaHandler,
    holder_of: Callable[[], str],
) -> dict[str, Any]:
    """What an answer publishes in validation mode — the mode the contract is built in.

    Context: building that schema in serialization mode splits every definition that differs
    between the two (`Event-Input`, `Event-Output`) and rewrites the whole contract. A class
    that declares no records field (the base answers) keeps the schema pydantic produced.
    """
    schema = handler.resolve_ref_schema(handler(core_schema))
    try:
        holder = holder_of()
    except exceptions.ProgrammingError:
        return schema
    if collection_class(cls.model_fields[holder].annotation) is None:
        return schema
    return _as_records(schema, holder)


class ResponsePaginatedQuery(DataTransferObject):
    """What any paged answer returns beside its records.

        class ResponseListDatasets(ResponsePaginatedQuery):
            datasets: list[Dataset]         →   .datasets .cursor .count .model_version

    The records carry the subclass's own name — `datasets`, `runs`, `plans` — because a reader
    of a response should see what it is about, not `items`.
    """

    cursor: str | None = None
    """Where the next page starts. `None` means there is no next one."""

    count: Count | None = None
    """How many match in total, capped unless the criteria asked for the exact number.
    `exact=False` means *at least* that many."""

    model_version: str = ""
    """The version of the model of the records — the fingerprint its published contract carries
    (`x-sincpro.version`). What may be filtered, ordered and shown is described once, in the
    discovery document; a client that holds another version loads the document again."""

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
        `{"items", "count", "cursor"}`. An empty specification selects the identity and the
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
                "model_version": self.model_version,
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
        schema loses `cursor`, `count`, `model_version`, `dropped` and the records — which is
        exactly what a client generating types needs, and why the TypeScript package had to
        write those types by hand.

        The shape is not a guess: the serializer writes the declared fields and no others, so
        the schema is built from them.

        A collection field is that array in validation mode too. The contract reads this mode,
        and the collection's own schema also accepts the page it was read from; an answer
        writes the list.

        One thing it cannot say: a `specification` cuts the fields of each record, so a record
        may arrive with fewer than its type declares. Only the identity is always there.
        """
        if handler.mode != "serialization":
            return _validation_schema(cls, core_schema, handler, cls.records_field)

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

        The page carries the description of the model it came from — the engine read it to
        answer —: its specification is the mask over the records, and the version of the model
        goes out with them, so a use case never has to know that describing a model is a thing
        that exists.

        >>> ResponseListDatasets.of(page, criteria).datasets[0].name
        'labs.csv'
        """
        records = cls.records_field()
        declared = cls.model_fields[records].annotation
        # A field declared as a collection holds the page itself — its count and cursor with it;
        # one declared as a list holds its records.
        held = page if collection_class(declared) is not None else list(page.items)
        response = cls(
            **{records: held},
            cursor=page.cursor,
            count=page.count,
            model_version=_version_of(cls.model_fields[records].annotation),
            dropped=list(page.dropped),
        )
        response._specification = criteria.specification
        response._meta = page.meta
        return response


class Query(DataTransferObject):
    """What any command that reads carries: what is asked (`criteria`), and where the reading
    goes on from (`cursor`).

        class QueryInvoices(Query):   →   QueryInvoices(criteria=Criteria(...), cursor="eyJ…")
            pass

        for page in QueryInvoices(criteria=criteria).pages(billing, ResponseInvoices):
            export(page.invoices)

    Context: one name on the way in and on the way out — an answer's `cursor` is sent back as
    it came, and `None` says there is no more. The criteria never changes between pages, so a
    client never rebuilds it: a frontend sends the same criteria and the cursor it got, an agent
    passes the cursor back, a wire carries it as any field. On validation the cursor goes into
    the criteria (`criteria.resuming_from`), so a use case reads `dto.criteria` and pages as it
    always did.
    """

    criteria: Criteria = Criteria()
    """Everything being asked: the filter, the ordering, the page and what to bring along.

    One field rather than a parameter per filter, because a filter *is* a criteria — what the
    interface calls a saved reading is the same object, merged."""

    cursor: str | None = None
    """The `cursor` the previous answer came with, sent back as it is, to read the page after
    it — none for the first page."""

    @model_validator(mode="after")
    def _resume_from_cursor(self) -> Self:
        """The criteria goes on from `cursor` when one is given — over a token the criteria
        carries itself."""
        if self.cursor is not None:
            self.criteria = self.criteria.resuming_from(self.cursor)
        return self

    def pages[R: ResponsePaginatedQuery](
        self,
        answer: Callable[[Self, type[R]], R | None],
        response_type: type[R],
        max_pages: int | None = None,
    ) -> Iterator[R]:
        """Every page of this reading, one at a time, each one call of `answer` — the bus, a
        reference to a context another service hosts, or a client of the same shape — until an
        answer has no `cursor`, or `max_pages` were read.

            for page in QueryInvoices(criteria=criteria).pages(billing, ResponseInvoices):
                export(page.invoices)
        """
        query, read = self, 0
        while max_pages is None or read < max_pages:
            page = answer(query, response_type)
            if page is None:
                return
            yield page
            read += 1
            if page.cursor is None:
                return
            query = query.model_copy(
                update={
                    "cursor": page.cursor,
                    "criteria": query.criteria.resuming_from(page.cursor),
                }
            )


class DraftState(DataTransferObject):
    """The record was answered from its owner's draft — what a form saved as a draft and
    nobody stored yet.

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
        account: Account            →   .account .model_version .dropped

    The record carries the subclass's own name for the same reason a page's records do. On the
    wire it is cut by the specification exactly as a record of a page is.
    """

    model_version: str = ""
    """The version of the model of the record, as a page answers it."""

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
                "model_version": self.model_version,
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
            return _validation_schema(cls, core_schema, handler, cls.record_field)

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
        """One record as the answer that goes out, cut as the page it was read in, with the
        version of its model.

            in      Account(1105, 'Caja'), the page it came in, the criteria it answered
            out     ResponseAccount(account=Account(1105, 'Caja'), model_version='3f9c…')
        """
        field = cls.record_field()
        response = cls(
            **{field: record},
            model_version=_version_of(cls.model_fields[field].annotation),
            dropped=list(page.dropped),
        )
        response._specification = criteria.specification
        response._meta = page.meta
        return response


class ResponseRecords(DataTransferObject):
    """What a read of several records by their identities returns: a list, not a page.

    class ResponseAccounts(ResponseRecords):
        accounts: list[Account]     →   .accounts .missing .model_version .dropped

    The records come in the order the identities were asked, each one cut by the specification
    as a record of a page is. An identity with no record is listed in `missing`, not raised:
    a list of references outlives some of what it points at.
    """

    missing: list[str] = []
    """The identities asked that have no record."""

    model_version: str = ""
    """The version of the model of the records, as a page answers it."""

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
                "model_version": self.model_version,
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
            return _validation_schema(cls, core_schema, handler, cls.records_field)

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

        Context: the identity is compared as text; the version of the model travels with what
        the page dropped.
        """
        found = {str(getattr(one, identity)): one for one in page.items}
        field = cls.records_field()
        response = cls(
            **{field: [found[one] for one in ids if one in found]},
            missing=[one for one in ids if one not in found],
            model_version=_version_of(cls.model_fields[field].annotation),
            dropped=list(page.dropped),
        )
        response._specification = criteria.specification
        response._meta = page.meta
        return response


PARQUET = "application/vnd.apache.parquet"
"""The media type of a Parquet file (IANA, 2024)."""

PARQUET_ABOVE = 1_000_000
"""Past how many records asked for a search answers Parquet instead of JSON, when its caller
reads Parquet and nobody chose the format: a JSON page of millions of records is the slowest,
largest way to send them. A search declares its own (`Search.parquet_above`, `None`: never)."""

ACCEPTED_FORMATS = "accepted_formats"
"""The context key a wire writes what its caller reads under — HTTP's `Accept` —, for the one use
case it asked: `{"for": "QueryListEntries", "types": [...]}`. Read by a search deciding its
format; absent inside the process, and ignored by a use case another one calls, where an answer
is objects."""


class Streamed(DataTransferObject):
    """An answer too large to hold: its bytes as they are produced, what they are, and the name
    to save them under — what an export answers.

        Streamed(media_type="application/vnd.apache.parquet", filename="entries.parquet",
                 chunks=<an iterator of bytes>)

    A wire writes the chunks as they come — `StreamingResponse` over HTTP — and never holds the
    whole; anywhere else, iterating it is reading it. `chunks` never travels as a field, and the
    schema says `x-sincpro-streamed` — the media types it may be — so a generated client reads
    the bytes instead of parsing JSON.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "x-sincpro-streamed": [
                "application/vnd.apache.parquet",
                "application/vnd.apache.arrow.stream",
                "application/jsonl",
            ]
        }
    )

    media_type: str
    filename: str = ""
    chunks: SkipJsonSchema[Any] = Field(default=None, exclude=True, repr=False)

    def __iter__(self) -> Iterator[bytes]:  # type: ignore[override]
        """The bytes, in order, read once."""
        return iter(self.chunks or ())

    def disposition(self) -> dict[str, str]:
        """The headers that make a browser save it under its name."""
        if not self.filename:
            return {}
        return {"Content-Disposition": f'attachment; filename="{self.filename}"'}
