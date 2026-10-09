"""The reads of one aggregate, written once: `Get`, `GetMany`, `LiteralSearch`, `Search` and
`Preview`, answered by one Feature that reads what the entity answers about itself: its
`DEFAULT_*` class methods (the key, what a record brings, the order, how a text finds it).

    class ResponseAccount(ResponseRecord):
        account: Account

    class ResponseAccounts(ResponseRecords):
        accounts: list[Account]

    class ResponseListAccounts(ResponsePaginatedQuery):
        accounts: list[Account]

    class QueryGetAccount(Get[Account, ResponseAccount]):                   one record, as the entity reads it
        pass

    class QueryGetManyAccounts(GetMany[Account, ResponseAccounts]):         several, by identity
        pass

    class QueryFindAccounts(LiteralSearch[Account, ResponseListAccounts]):  a select's literal
        pass

    class QueryListAccounts(Search[Account, ResponseListAccounts]):         a page by criteria
        pass

    class QueryPreviewAccount(Preview[Account, ResponsePreviewAccount]):    a form's question
        pass

    @accounting.feature(
        [QueryGetAccount, QueryGetManyAccounts, QueryFindAccounts, QueryListAccounts]
    )
    class AccountReads(EntityReads[Account]):
        pass

Context: the DTO names the entity and the response in its base, so one Feature answers any of
them and a project names its own DTOs. Each read is a method (`get`, `get_many`,
`literal_search`, `search`, `preview`): override one and call `super()` to act before or after it.
`reads_from` is the repository, the dependency named `repository` unless overridden. Nothing here is required: a
Feature written by hand with `matching`, `detail_of` and `repository.search` does the same.

**The caller's criteria wins, part by part.** What the entity answers is the default; every
part the caller's `criteria` names (`specification`, `order`, `pagination`, …) replaces it,
and its `where` adds to the read's own filter (`Criteria.replaced_by`). A `Criteria()` names
nothing, so it reads exactly what the entity says.
"""

import copy
import dataclasses
from collections.abc import Callable
from typing import Any

from sincpro_framework.ddd.criteria import (
    Condition,
    CountMode,
    Criteria,
    Expression,
    Operator,
    Pagination,
    Specification,
    combined,
)
from sincpro_framework.ddd.criteria.evaluate import matches
from sincpro_framework.ddd.entity.editing import adapter_of, assign, owned, recompute
from sincpro_framework.ddd.entity.entity_collection import EntityCollection, identity_name
from sincpro_framework.ddd.entity.model_meta import (
    annotations_of,
    framework_fields,
    presentation_of,
)
from sincpro_framework.ddd.entity.query_entity import detail_of, matching
from sincpro_framework.ddd.exceptions import (
    AggregateNotFound,
    ContractViolation,
    RelationNotResolved,
)
from sincpro_framework.ddd.preview import Advice, previewing
from sincpro_framework.ddd.query import (
    Query,
    ResponsePaginatedQuery,
    ResponseRecord,
    ResponseRecords,
    writing_out,
)
from sincpro_framework.ddd.repositories.repository import IRepository
from sincpro_framework.sincpro_abstractions import DataTransferObject, Feature


class Get[T, R: ResponseRecord](DataTransferObject):
    """One record by its key (`DEFAULT_GET_ID`), as the entity's `DEFAULT_READING` brings it.

    QueryGetAccount(id="1.2.3")                                    what the entity reads
    QueryGetAccount(id="1.2.3", criteria=Criteria(specification=…)) what the caller names instead
    """

    id: str
    """The value of the entity's `DEFAULT_GET_ID` field: its identity unless it names a key."""
    criteria: Criteria = Criteria()
    """Over the entity's `DEFAULT_READING`: every part it names replaces the default."""


class GetMany[T, R: ResponseRecords](DataTransferObject):
    """Several records by their keys, as a list in the order asked, each as the entity's
    `DEFAULT_READING` brings it. Not a page: every key asked is answered, or listed as missing.

    QueryGetManyAccounts(ids=["01a1…", "01a2…"])
    """

    ids: list[str]
    """Values of the entity's `DEFAULT_GET_ID` field."""
    criteria: Criteria = Criteria()
    """Over the entity's `DEFAULT_READING`: every part it names replaces the default."""


class LiteralSearch[T, R: ResponsePaginatedQuery](DataTransferObject):
    """What a select asks: the literal the user typed, matched the way the entity's `search`
    says. A short page of identity and display; a blank literal is the first page in order."""

    text: str = ""
    criteria: Criteria = Criteria()
    """Over the filled template: a filter the select adds, a page size or what each reference
    brings, each replacing the select's own."""


class Search[T, R: ResponsePaginatedQuery](Query):
    """A page by criteria: each record as the entity's `DEFAULT_READING` brings it, in its
    `DEFAULT_ORDER`, unless the criteria names its own."""


class FieldState(DataTransferObject):
    """How a form shows one field for the record a preview built: its hints, evaluated."""

    readonly: bool = False
    required: bool = False
    visible: bool = True


class ResponsePreview(DataTransferObject):
    """What a preview answers: the values the form does not have yet, each field's state over
    the new record, and what the domain advised.

    class ResponsePreviewInvoice(ResponsePreview):
        pass
    """

    values: dict[str, Any] = {}
    """Every value that differs from what the form sent — derived fields included — as JSON.
    For a new record with nothing changed yet, every value: the form it starts from."""
    fields: dict[str, FieldState] = {}
    advice: list[Advice] = []


class Preview[T, R: ResponsePreview](DataTransferObject):
    """A form's question: these values, this field changed — what does the record become?

    QueryPreviewInvoice(values={"discount": "50"}, changed=["discount"], id="01a1…")
    QueryPreviewInvoice()                                       a new record's defaults

    Nothing is stored: the answer is computed over a record nobody saves, and any write
    attempted while it runs raises `WriteInPreview`.
    """

    values: dict[str, Any] = {}
    changed: list[str] = []
    id: str | None = None
    """The `DEFAULT_GET_ID` of the stored record being edited; `None` for a new one."""


def key_of(entity: type) -> str:
    """The field `Get`, `GetMany` and `Preview` find a record by: the entity's
    `DEFAULT_GET_ID`, or its identity when it answers none (a plain model).

    in      Account (DEFAULT_GET_ID = "code")   →  out  "code"
    in      Note (Entity's default)             →  out  "id"
    """
    return presentation_of(entity).get_id or identity_name(entity)


def declared_by(dto: type) -> tuple[type, type]:
    """The entity and the response a DTO named in its base.

    in      QueryGetAccount        →  out  (Account, ResponseAccount)
    """
    for base in dto.__mro__:
        generic = getattr(base, "__pydantic_generic_metadata__", None)
        arguments = generic.get("args", ()) if generic else ()
        if len(arguments) == 2 and all(isinstance(one, type) for one in arguments):
            return arguments[0], arguments[1]
    raise ContractViolation(
        f"{dto.__name__} does not name its entity and response: "
        f"class {dto.__name__}(Get[Account, ResponseAccount])"
    )


class EntityReads[T](Feature):
    """The Feature that answers `Get`, `LiteralSearch` and `Search` for one entity.

    @accounting.feature([QueryGetAccount, QueryFindAccounts, QueryListAccounts])
    class AccountReads(EntityReads[Account]):
        def get(self, dto: QueryGetAccount) -> ResponseAccount:
            answer = super().get(dto)        # after: the record is in hand
            ...
            return answer
    """

    def reads_from(self) -> IRepository:
        """The repository read from: the dependency named `repository`. Override it when the
        bounded context named it otherwise."""
        repository = getattr(self, "repository", None)
        if not isinstance(repository, IRepository):
            raise ContractViolation(
                f"{type(self).__name__} reads from the dependency named 'repository', and the "
                "bus has none: add it, or override reads_from()"
            )
        return repository

    def execute(self, dto: Any) -> Any:
        match dto:
            case Get():
                return self.get(dto)
            case GetMany():
                return self.get_many(dto)
            case LiteralSearch():
                return self.literal_search(dto)
            case Search():
                return self.search(dto)
            case Preview():
                return self.preview(dto)
        raise ContractViolation(
            f"{type(self).__name__} answers Get, GetMany, LiteralSearch, Search and Preview, "
            f"not {type(dto).__name__}"
        )

    def get(self, dto: Any) -> Any:
        """The record found by its key, as the entity reads it, or `AggregateNotFound`."""
        entity, response = declared_by(type(dto))
        criteria = self.by_identity(
            entity, Condition(field=key_of(entity), value=dto.id), 1, dto.criteria
        )
        page = self.reads_from().search(entity, criteria)
        if not page.items:
            raise AggregateNotFound(f"{entity.__name__} {dto.id} does not exist")
        return response.of(page.items[0], page, criteria)

    def get_many(self, dto: Any) -> Any:
        """The records of `ids`, in that order, each as the entity reads it; the ids with no
        record are listed as missing."""
        entity, response = declared_by(type(dto))
        ids = list(dict.fromkeys(dto.ids))
        identity = key_of(entity)
        criteria = self.by_identity(
            entity,
            Condition(field=identity, value=ids, operator=Operator.IN),
            len(ids),
            dto.criteria,
        )
        page = self.reads_from().search(entity, criteria)
        return response.of(ids, page, criteria, identity)

    def by_identity(
        self, entity: type, where: Expression, limit: int, asked: Criteria
    ) -> Criteria:
        """The entity's `DEFAULT_READING` with every part `asked` names in its place, filtered
        by key besides `asked`'s own filter, read as one page.

        A page and not `repository.get`/`browse`, so the definition and the relations come
        back the same way from every store and the answer is cut by the same specification.
        """
        read = (detail_of(entity) or Criteria()).replaced_by(asked)
        return read.model_copy(
            update={
                "where": combined(where, read.expression),
                "pagination": Pagination(limit=limit),
                "count": CountMode.NONE,
            }
        )

    def literal_search(self, dto: Any) -> Any:
        """The short page a select shows for the literal."""
        entity, response = declared_by(type(dto))
        criteria = matching(entity, dto.text).replaced_by(dto.criteria)
        return response.of(self.reads_from().search(entity, criteria), criteria)

    def search(self, dto: Any) -> Any:
        """The page the criteria asks for, each record as the entity reads it unless the
        criteria names what to bring. The store applies the entity's order when none is named.
        """
        entity, response = declared_by(type(dto))
        criteria = (detail_of(entity) or Criteria()).replaced_by(dto.criteria)
        return response.of(self.reads_from().search(entity, criteria), criteria)

    def preview(self, dto: Any) -> Any:
        """What the record becomes with the form's values, computed over a record nobody saves.

        1. Inside `previewing()`: every write of every store raises, advice is collected.
        2. Start from a copy of the stored record, read as its `DEFAULT_READING` brings it and
           with every relation its derivations read (`AggregateNotFound` when the id has none), or from a
           new one with the class defaults.
        3. `assign` the form's values, read as each field's type; `recompute` what `changed`
           reaches, the records it owns first, as a save does — every derivation when nothing
           changed yet.
        4. Events the domain recorded are dropped: nothing happened.
        Final: the values that differ from what the form sent, compared as values (`0` and
        `0.00` are one amount) — never a field the framework writes, which a form must not
        send back —, each field's state, the advice.
        """
        entity, response = declared_by(type(dto))
        with previewing() as advice:
            record = self.previewed(entity, dto.id)
            sent = held(record)
            assign(record, dto.values)
            sent.update({name: getattr(record, name) for name in dto.values})
            recompute(record, dto.changed)
            pull = getattr(record, "pull_events", None)
            if callable(pull):
                pull()
        system = framework_fields(entity)
        values = {name: one for name, one in dumped(record).items() if name not in system}
        if dto.changed or dto.id is not None:
            now = held(record)
            values = {
                name: one
                for name, one in values.items()
                if name not in sent or sent[name] != now.get(name)
            }
        return response(values=values, fields=states_of(record), advice=list(advice))

    def previewed(self, entity: type, identity: str | None) -> Any:
        """The record a preview starts from: a copy of the stored one found by its key, read as
        its `DEFAULT_READING` brings it and with the relations its derivations read, or a new
        one holding the class defaults (`None` where a field has none)."""
        if identity is None:
            return blank(entity)
        criteria = self.by_identity(
            entity, Condition(field=key_of(entity), value=identity), 1, Criteria()
        )
        page = self.reads_from().search(entity, with_derived_relations(entity, criteria))
        if not page.items:
            raise AggregateNotFound(f"{entity.__name__} {identity} does not exist")
        return copied(page.items[0])


def with_derived_relations(entity: type, criteria: Criteria) -> Criteria:
    """`criteria`, also bringing every relation the computation of `entity` reads (`owned`) — a
    total over lines the entity's reading does not name still reads the lines.

    in      Sale (DEFAULT_READING: customer_id), subtotal ← lines
    out     the reading plus {"lines": {}}: each line, every scalar
    """
    read = set(owned(entity))
    named = criteria.specification.root if criteria.specification is not None else {}
    missing = read - set(named)
    if not missing:
        return criteria
    nodes = {**named, **{name: Criteria() for name in sorted(missing)}}
    return criteria.model_copy(update={"specification": Specification(nodes)})


def held(record: Any) -> dict[str, Any]:
    """Each field's value as the record holds it, read without resolving anything; a list is
    copied, so what is later put in its place is compared with what was there."""
    found: dict[str, Any] = {}
    for name in annotations_of(type(record)):
        try:
            value = getattr(record, name)
        except RelationNotResolved:
            continue
        found[name] = list(value) if isinstance(value, (list, EntityCollection)) else value
    return found


def copied(record: Any) -> Any:
    """A new record holding what `record` holds, built through its own constructor — and so is
    every record inside it — so nothing done to it reaches what a store keeps: a store in
    memory hands back its own instances.

    in      the stored Invoice(discount=0, lines=[line 1, line 2])
    out     another Invoice(discount=0, lines=[another line 1, another line 2])

    A relation nobody resolved is left at its default. A record met twice is copied once, and a
    back-reference (a line pointing at its invoice) points at the copy.
    """
    seen: dict[int, Any] = {}
    pending: dict[int, list[Callable[[Any], None]]] = {}
    return _copied(record, seen, pending)


def _copied(
    record: Any, seen: dict[int, Any], pending: dict[int, list[Callable[[Any], None]]]
) -> Any:
    """One record copied. While its fields are copied it stands for itself in `seen`; whatever
    met it then is pointed at its copy once the copy exists (`pending`)."""
    entity = type(record)
    if not dataclasses.is_dataclass(entity):
        return copy.copy(record)
    if id(record) in seen:
        return seen[id(record)]
    seen[id(record)] = record
    values: dict[str, Any] = {}
    for one in dataclasses.fields(entity):
        if not one.init:
            continue
        try:
            value = getattr(record, one.name)
        except RelationNotResolved:
            continue
        values[one.name] = _copied_value(value, seen, pending)
    built = entity(**values)
    seen[id(record)] = built
    for name, value in values.items():
        _awaiting(built, name, value, seen, pending)
    for point in pending.pop(id(record), []):
        point(built)
    return built


def _copied_value(
    value: Any, seen: dict[int, Any], pending: dict[int, list[Callable[[Any], None]]]
) -> Any:
    """A field's value for `copied`: a record copied, a list of them copied one by one."""
    if isinstance(value, (list, EntityCollection)):
        return [_copied_value(one, seen, pending) for one in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _copied(value, seen, pending)
    return value


def _awaiting(
    built: Any,
    name: str,
    value: Any,
    seen: dict[int, Any],
    pending: dict[int, list[Callable[[Any], None]]],
) -> None:
    """Notes where `built` still holds an original whose copy is not finished: a back-reference,
    set to the copy when that copy is built."""
    if isinstance(value, list):
        for at, one in enumerate(value):
            if _in_progress(one, seen):
                pending.setdefault(id(one), []).append(
                    lambda copy_, held_=value, at_=at: held_.__setitem__(at_, copy_)
                )
    elif _in_progress(value, seen):
        pending.setdefault(id(value), []).append(
            lambda copy_, owner=built, field=name: setattr(owner, field, copy_)
        )


def _in_progress(value: Any, seen: dict[int, Any]) -> bool:
    return seen.get(id(value)) is value and dataclasses.is_dataclass(value)


def blank(entity: type) -> Any:
    """A new record of `entity` built through its own constructor: each default, and `None`
    for a field the class requires — a form fills it in.

    in      Invoice(partner_id: str, currency_id: str = "BOB")
    out     Invoice(partner_id=None, currency_id="BOB")
    """
    if not dataclasses.is_dataclass(entity):
        return entity()
    required = {
        one.name: None
        for one in dataclasses.fields(entity)
        if one.init
        and one.default is dataclasses.MISSING
        and one.default_factory is dataclasses.MISSING
    }
    return entity(**required)


def dumped(record: Any) -> dict[str, Any]:
    """Each field of the record as JSON, read without resolving anything: a relation nobody
    resolved is left out, and a related record's own relations write their defaults."""
    annotations = annotations_of(type(record))
    written: dict[str, Any] = {}
    for name, annotation in annotations.items():
        try:
            value = getattr(record, name)
        except RelationNotResolved:
            continue
        if isinstance(value, EntityCollection):
            value = list(value)
        with writing_out():
            written[name] = adapter_of(annotation).dump_python(
                value, mode="json", warnings=False
            )
    return written


def states_of(record: Any) -> dict[str, FieldState]:
    """Each field's hints, evaluated over this record: what a client with no Criteria evaluator
    shows."""
    presented = presentation_of(type(record))
    return {
        name: FieldState(
            readonly=name in presented.readonly
            or _holds(record, presented.readonly_when.get(name)),
            required=name in presented.required
            or _holds(record, presented.required_when.get(name)),
            visible=name not in presented.visible_when
            or _holds(record, presented.visible_when[name]),
        )
        for name in annotations_of(type(record))
    }


def _holds(record: Any, condition: Any) -> bool:
    return condition is not None and matches(record, condition)
