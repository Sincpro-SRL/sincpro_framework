"""The reads of one aggregate, written once: `Get`, `GetMany`, `LiteralSearch`, `Search`,
`Export` and `PreviewEntity`, answered by one Feature that reads what the entity answers about itself: its
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

    class QueryPreviewAccount(PreviewEntity[Account, ResponsePreviewAccount]):    a form's question
        pass

    class QueryAccountEvents(DomainEvents[Account, ResponseAccountEvents]):  its events, oldest first
        pass

    class QueryExportAccounts(Export[Account, Streamed]):                   every one, as a file
        pass

    @accounting.feature(
        [QueryGetAccount, QueryGetManyAccounts, QueryFindAccounts, QueryListAccounts]
    )
    class AccountReads(EntityReads[Account]):
        pass

Context: the DTO names the entity and the response in its base, so one Feature answers any of
them and a project names its own DTOs. Each read is a method (`get`, `get_many`,
`literal_search`, `search`, `preview`, `domain_events`): override one and call `super()` to act before or after it.
`reads_from` is the repository, the dependency named `repository` unless overridden. Nothing here is required: a
Feature written by hand with `matching`, `detail_of` and `repository.search` does the same.

**The caller's criteria wins, part by part.** What the entity answers is the default; every
part the caller's `criteria` names (`specification`, `order`, `pagination`, …) replaces it,
and its `where` adds to the read's own filter (`Criteria.replaced_by`). A `Criteria()` names
nothing, so it reads exactly what the entity says.
"""

import copy
import dataclasses
from collections.abc import Callable, Mapping, Sequence
from typing import Any, ClassVar, Literal, cast

from pydantic import field_validator

from sincpro_framework.context import Draft, Drafts, Owner, use_context
from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria import (
    All,
    Any_,
    Condition,
    CountMode,
    Criteria,
    Evaluation,
    Expression,
    Node,
    Operator,
    Pagination,
    Sort,
    Specification,
)
from sincpro_framework.ddd.editing.editing import adapter_of, apply_values, recompute
from sincpro_framework.ddd.editing.preview import FormWarning, read_only
from sincpro_framework.ddd.entity.entity_collection import (
    Count,
    EntityCollection,
    identity_name,
    identity_of,
)
from sincpro_framework.ddd.entity.entity_meta import (
    records_class,
    related_class,
)
from sincpro_framework.ddd.entity.relations import relation_attribute
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.metamodel import model_of
from sincpro_framework.ddd.query import (
    ACCEPTED_FORMATS,
    PARQUET,
    PARQUET_ABOVE,
    DraftState,
    Query,
    ResponsePaginatedQuery,
    ResponseRecord,
    ResponseRecords,
    Streamed,
    writing_out,
)
from sincpro_framework.ddd.reads.defaults import detail_of, matching
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
    draft: bool = False
    """The caller's own draft of the record when there is one (`ResponseRecord.draft` says so),
    the stored record otherwise — a record only drafted so far is found too. Never someone
    else's: whose a draft is, the context says."""


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


class Export[T, R: Streamed](Query):
    """Every record by criteria, as a file — Parquet, an Arrow stream or JSON Lines —, read a
    page at a time from one snapshot and streamed as it is written.

    QueryExportEntries(criteria=…)                    every scalar field, Parquet
    QueryExportEntries(criteria=…, format="jsonl")    each record as an answer writes it

    What a data-science notebook or a nightly dump needs, and a page of 40 over HTTP is not:
    `sincpro_framework.data_layer.data_analysis.export`. The criteria filters and orders; its
    specification, when it names fields, picks the columns.
    """

    format: Literal["parquet", "arrow", "jsonl"] = "parquet"
    """`parquet` to keep or open in a dataframe library, `arrow` for a client that appends batch
    after batch, `jsonl` for one that reads JSON."""


class Search[T, R: ResponsePaginatedQuery](Query):
    """A page by criteria: each record as the entity's `DEFAULT_READING` brings it, in its
    `DEFAULT_ORDER`, unless the criteria names its own.

    QueryListInvoices(criteria=…)                   the stored records
    QueryListInvoices(criteria=…, drafts=True)      the caller's drafts of them, by the same criteria
    """

    drafts: bool = False
    """The caller's drafts instead of the stored records: filtered and ordered by the same
    criteria, the most recently saved first when it names no order — one page, an owner's
    drafts being few. What a client asks after it lost its state: what was I editing?"""

    format: Literal["auto", "json", "parquet"] = "auto"
    """How the page travels. `json`, or `parquet`: the same records, cursor and count, as a
    Parquet file (the cursor and the count in its footer). `auto`, the default: JSON, unless more
    than `parquet_above` records are asked for, over a wire whose caller reads Parquet, with
    pyarrow installed — inside the process an answer is always objects."""

    parquet_above: ClassVar[int | None] = PARQUET_ABOVE
    """Past how many records asked for `auto` answers Parquet; `None` on a search that never
    does. Published in the contract, so a client knows before it asks."""


class DomainEvents[T, R: ResponsePaginatedQuery](Query):
    """A record's domain events, oldest first: what its context's event table holds about it.

    QueryIssueEvents(id="01a1…")                                      every event, oldest first
    QueryIssueEvents(id="01a1…", names=["project.issue.v1.closed"])   only those wire names
    QueryIssueEvents(id="01a1…", criteria=Criteria(selection=…))          a time range: added

    The record is named by its key (`DEFAULT_GET_ID`), as `Get` reads it, and its type is the
    class the DTO names — never what a caller sends — so two kinds of record that share an id
    never mix. Where the events live is the class the response holds:
    `events: list[ProjectEvent]` reads the context's table, a narrower class only those events.
    """

    id: str
    """The value of the entity's `DEFAULT_GET_ID` field: its identity unless it names a key."""
    names: list[str] | str = []
    """Wire names to select (`name in names`); empty selects every event. A name no class
    records answers no rows — it is not checked against the classes imported, which may not be
    yet."""

    @field_validator("names", mode="before")
    @classmethod
    def _one_or_several(cls, value: Any) -> Any:
        """`names="InvoicePaid"` read as `["InvoicePaid"]` — one name is a list of one."""
        return [value] if isinstance(value, str) else value


class FieldState(DataTransferObject):
    """How a form shows one field for the record a preview built: its hints, evaluated."""

    readonly: bool = False
    required: bool = False
    visible: bool = True
    items: dict[str, dict[str, "FieldState"]] = {}
    """For a list of records — an invoice's lines —, how a form shows each one's fields, by the
    record: its identity, or its position for a record that has none (`"2"`). Empty for any
    other field, for a relation nobody read and for the records a many-to-many references."""


class ResponsePreviewEntity(DataTransferObject):
    """What a preview answers: the values the form does not have yet, each field's state over
    the new record, and what the domain warned.

    class ResponsePreviewInvoice(ResponsePreviewEntity):
        pass
    """

    values: dict[str, Any] = {}
    """Every value that differs from what the form sent — computed fields included — as JSON.
    For a new record with nothing changed yet, every value: the form it starts from."""
    states: dict[str, FieldState] = {}
    """Each field's state — read-only, required, visible — evaluated over the new record; a
    list of records carries each one's own in `items`, by its identity."""
    warnings: list[FormWarning] = []
    """What the domain warned the form about (`warn_form`), each saying where it points."""


class PreviewEntity[T, R: ResponsePreviewEntity](DataTransferObject):
    """A form's question: these values, this field changed — what does the record become?

    QueryPreviewInvoice(values={"discount": "50"}, changed=["discount"], id="01a1…")
    QueryPreviewInvoice()                                       a new record's defaults

    Nothing is stored: the answer is computed over a record nobody saves, and any write
    attempted while it runs raises `ProgrammingError`.
    """

    values: dict[str, Any] = {}
    changed: list[str] = []
    id: str | None = None
    """The `DEFAULT_GET_ID` of the stored record being edited; `None` for a new one."""


def key_of(entity: type) -> str:
    """The field `Get`, `GetMany` and `PreviewEntity` find a record by: the entity's
    `DEFAULT_GET_ID`, or its identity when it answers none (a plain model).

    in      Account (DEFAULT_GET_ID = "code")   →  out  "code"
    in      Note (Entity's default)             →  out  "id"
    """
    return model_of(entity).presented.get_id or identity_name(entity)


def named_in_type(dto: type) -> tuple[type, type] | None:
    """The entity and the response a DTO names in its type, or `None` when it names none.

        named_in_type(QueryGetAccount)       ->  (Account, ResponseAccount)
        # class QueryGetAccount(Get[Account, ResponseAccount])
        named_in_type(CommandEditInvoice)    ->  (Invoice, ResponseEditInvoice)
        named_in_type(QueryHealth)           ->  None

    Context: the first base in the MRO parametrized with two classes is the one read, so a
    subclass of a declared DTO answers what its parent named — every framework DTO of one
    entity (`Get`, `Search`, `Edit`, …) is written so, and whoever groups use cases by entity
    reads it here rather than keeping a list of them.
    """
    for base in dto.__mro__:
        generic = getattr(base, "__pydantic_generic_metadata__", None)
        arguments = generic.get("args", ()) if generic else ()
        if len(arguments) == 2 and all(isinstance(one, type) for one in arguments):
            return arguments[0], arguments[1]
    return None


def declared_by(dto: type) -> tuple[type, type]:
    """The entity and the response a DTO named in its base — refused when it names none.

    declared_by(QueryGetAccount)       ->  (Account, ResponseAccount)
    declared_by(QueryHealth)
        ->  raises ProgrammingError("QueryHealth does not name its entity and …")
    """
    named = named_in_type(dto)
    if named is None:
        raise exceptions.ProgrammingError(
            f"{dto.__name__} does not name its entity and response: "
            f"class {dto.__name__}(Get[Account, ResponseAccount])"
        )
    return named


def held(record: Any) -> dict[str, Any]:
    """Each field's value as the record holds it, read without resolving anything; a list is
    copied, so what is later put in its place is compared with what was there.

        held(Invoice(id="F-7", total=95, lines=[l1, l2], partner=<not read>))
            ->  {"id": "F-7", …, "total": 95, "lines": [l1, l2]}   # partner left out

    Context: a relation nobody read is left out rather than read for this — holding is not
    asking.
    """
    found: dict[str, Any] = {}
    for name in model_of(type(record)).annotations:
        try:
            value = getattr(record, name)
        except exceptions.RelationNotResolved:
            continue
        found[name] = list(value) if isinstance(value, (list, EntityCollection)) else value
    return found


def events_of(response: type) -> type:
    """The event class a `DomainEvents` response holds: where its events are read.

        events_of(ResponseIssueEvents)    ->  ProjectEvent
        # class ResponseIssueEvents: events: list[ProjectEvent]
        events_of(ResponseAccounts)       # accounts: list[Account]
            ->  raises ProgrammingError("ResponseAccounts holds list[Account], not events: …")

    The records field must be a list of a `DomainEvent` class — nothing else is read.
    """
    annotation = response.model_fields[response.records_field()].annotation
    held, many = records_class(annotation)
    held = held if many else None
    if not (isinstance(held, type) and issubclass(held, DomainEvent)):
        raise exceptions.ProgrammingError(
            f"{response.__name__} holds {annotation!r}, not events: `DomainEvents` answers "
            f"`events: list[<the context's DomainEvent>]`"
        )
    return held


def subject(entity_type: str, entity_id: str) -> Expression:
    """The events about one record: its type and its identity, together."""
    return All(
        all=[
            Condition(field="entity_type", value=entity_type),
            Condition(field="entity_id", value=entity_id),
        ]
    )


def history_of(*records: Any) -> Criteria:
    """The criteria that reads the histories of several records together, each by its type and
    identity — what one `DomainEvents` cannot ask, as an issue with its runs.

    in      issue                  →  out  entity_type = Issue and entity_id = issue.id
    in      issue, run_1, run_2    →  out  any of those three pairs
    """
    if not records:
        raise exceptions.ProgrammingError("history_of needs at least one record")
    pairs = [
        subject(type(one).__name__, str(getattr(one, identity_name(type(one)))))
        for one in records
    ]
    return Criteria(selection=pairs[0] if len(pairs) == 1 else Any_(any=pairs))


class EntityReads[T](Feature):
    """The Feature that answers `Get`, `LiteralSearch` and `Search` for one entity.

    @accounting.feature([QueryGetAccount, QueryFindAccounts, QueryListAccounts])
    class AccountReads(EntityReads[Account]):
        def get(self, dto: QueryGetAccount) -> ResponseAccount:
            answer = super().get(dto)        # after: the record is in hand
            ...
            return answer
    """

    drafts_by: ClassVar[Sequence[str]] = Owner.BY_TENANT_AND_USER
    """Whose the drafts of this form are — a user's within its tenant by default; `Owner.BY_TENANT`
    everybody's of the tenant, `()` nobody's: one draft of a record that every user opens."""

    def reads_from(self) -> IRepository:
        """The repository read from: the dependency named `repository`. Override it when the
        bounded context named it otherwise."""
        repository = getattr(self, "repository", None)
        if not isinstance(repository, IRepository):
            raise exceptions.ProgrammingError(
                f"{type(self).__name__} reads from the dependency named 'repository', and the "
                "bus has none: add it, or override reads_from()"
            )
        return repository

    def drafts(self) -> Drafts:
        """The drafts this form reads and saves, owned by the keys of `drafts_by`.

        Context: whose a draft is decides who sees it — the caller alone by default, everybody
        who opens the record with `drafts_by = ()`. Declared on the class, not computed per
        call, so every action of the form (`save_draft`, `save`, `discard`) uses one owner.

            class InvoiceForms(EntityEdits[Invoice]): ...        a user's draft, in its tenant
            class SharedForms(EntityEdits[Invoice]):
                drafts_by = ()                                   one draft of a record, for all

        Final: `use_context().drafts(by=drafts_by)`, in the context's store.
        """
        return use_context().drafts(by=self.drafts_by)

    def draft_subject(self, entity: type, key: str) -> str:
        """What a draft of a record is stored as: its key under its entity and its bounded
        context — `billing.Invoice:F-7` — so two contexts that store drafts in one store, each
        with an `Invoice` of its own, never read each other's."""
        bus = use_context().bus
        return f"{bus.label if bus is not None else ''}.{entity.__name__}:{key}"

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
                "selection": Node.combined(where, read.expression),
                "pagination": Pagination(limit=limit),
                "count": CountMode.NONE,
            }
        )

    def described(self, entity: type, criteria: Criteria) -> EntityCollection[Any]:
        """What the store says of `entity` for `criteria` without reading a row: its definition and
        what the criteria names that it cannot answer — what a draft, which the store does not
        hold, is answered with, so it goes out as a stored record does."""
        return self.reads_from().search(
            entity,
            criteria.model_copy(
                update={"pagination": Pagination(limit=0), "count": CountMode.NONE}
            ),
        )

    def ids_named(self, kind: type, value: list[Any]) -> list[Any]:
        """The identity each item of a form's list names, in order: the id itself, a mapping
        holding it — anything else in it is the other aggregate's, and ignored — or the record.

            ids_named(Label, ["l1", {"id": "l2", "name": "…"}, <Label l3>])
                ->  ["l1", "l2", "l3"]
            ids_named(Label, [{"name": "new"}])
                ->  raises InvalidValue(… never creates one …, (0,))

        A mapping without the identity is refused, pointing at it: a reference never creates
        the record it names.
        """
        identity = identity_name(kind)
        ids: list[Any] = []
        for index, one in enumerate(value):
            if isinstance(one, Mapping) and identity not in one:
                raise exceptions.InvalidValue(
                    f"a {kind.__name__} is referenced by its {identity}, and never creates "
                    f"one: {one!r} names none",
                    (index,),
                )
            if isinstance(one, Mapping):
                ids.append(one[identity])
            elif isinstance(one, kind):
                ids.append(identity_of(one))
            else:
                ids.append(one)
        return ids

    def records_named(self, kind: type, ids: list[Any]) -> list[Any]:
        """The records of `ids`, in that order, read in one search by identity.

            records_named(Label, ["l1", "l2"])    ->  [<Label l1>, <Label l2>]
            records_named(Label, ["l1", "l9"])    ->  raises InvalidValue("Label l9 does not
                                                      exist", (1,))

        An id with no record is refused, pointing at its position.
        """
        identity = identity_name(kind)
        wanted = list(dict.fromkeys(ids))
        criteria = Criteria(
            selection=Condition(field=identity, value=wanted, operator=Operator.IN),
            pagination=Pagination(limit=max(len(wanted), 1)),
            count=CountMode.NONE,
        )
        found = {
            str(identity_of(one)): one
            for one in self.reads_from().search(kind, criteria).items
        }
        for index, one in enumerate(ids):
            if str(one) not in found:
                raise exceptions.InvalidValue(
                    f"{kind.__name__} {one} does not exist", (index,)
                )
        return [found[str(one)] for one in ids]

    def referenced(self, owner: type, values: dict[str, Any]) -> dict[str, Any]:
        """The values with each many-to-many the form names by id as the records those ids
        name — what `apply_values` puts on a record of `owner`. The other aggregate is referenced,
        never created nor changed from here.

            # Ticket.labels: a many-to-many to Label
            referenced(Ticket, {"labels": ["l1", {"id": "l2", "name": "…"}]})
                ->  {"labels": [<Label l1>, <Label l2>]}
            referenced(Ticket, {"labels": ["l1", "l9"]})
                ->  raises InvalidValue("Label l9 does not exist", ("labels", 1))
            referenced(Invoice, {"lines": [{"qty": 2}]})    ->  as it came: lines are parts

        1. Only a list for a relation the store names by id (`named_by_id`, answered by the
           persistence adapter without reading anything) is looked at: the lines an aggregate
           owns are `apply_values`'s.
        2. The identity each item names (`ids_named`), then their records (`records_named`).
        Final: the values, each reference the record it names; a refusal points at the field
        and the item.
        """
        annotations = model_of(owner).annotations
        resolved = dict(values)
        for name, value in values.items():
            attribute = relation_attribute(owner, name)
            if (
                attribute is None
                or not attribute.named_by_id()
                or not isinstance(value, list)
            ):
                continue
            kind, _ = (
                related_class(annotations[name]) if name in annotations else (None, False)
            )
            if kind is None:
                continue
            try:
                resolved[name] = self.records_named(kind, self.ids_named(kind, value))
            except exceptions.InvalidValue as error:
                raise error.within(name) from error
        return resolved

    def previewed(self, entity: type, identity: str | None) -> Any:
        """The record a preview starts from: a copy of the stored one found by its key, read as
        its `DEFAULT_READING` brings it and with the relations its computations read, or a new
        one holding the class defaults (`None` where a field has none).

            previewed(Invoice, "F-7")    ->  a copy of invoice F-7, its lines read
            previewed(Invoice, None)     ->  Invoice(currency_id="BOB", total=None, …)
            previewed(Invoice, "F-404")
                ->  raises AggregateNotFound("Invoice F-404 does not exist")

        Context: a copy, so whatever the preview puts on it never reaches the instance a
        unit of work may be tracking.
        """
        if identity is None:
            return blank(entity)
        criteria = self.by_identity(
            entity, Condition(field=key_of(entity), value=identity), 1, Criteria()
        )
        page = self.reads_from().search(entity, with_computed_relations(entity, criteria))
        if not page.items:
            raise exceptions.AggregateNotFound(f"{entity.__name__} {identity} does not exist")
        return copied(page.items[0])

    def stored(self, entity: type, key: str) -> Any:
        """A copy of the stored record of `key`, as a preview reads it, or `None` when there is
        none — a record only drafted so far."""
        try:
            return self.previewed(entity, key)
        except exceptions.AggregateNotFound:
            return None

    def drafted(
        self, entity: type, response: type, key: str, draft: Draft, criteria: Criteria
    ) -> Any:
        """A draft answered as the record it holds, the way the stored one is answered: cut by the
        same criteria, with the same definition — and said to be a draft, stale when the stored
        record moved on since the draft started.

            # F-7 stored at version 4; the draft saved {"discount": "5"} from version 3
            drafted(Invoice, ResponseInvoice, "F-7", draft, criteria)
                ->  ResponseInvoice(invoice=Invoice(discount=5, …),
                        draft=DraftState(version=2, origin_version=3, stale=True))
            # only drafted, never stored  ->  a blank Invoice keyed "F-7" with the values

        A draft of a record never stored is never stale: there is nothing to move on.
        """
        stored = self.stored(entity, key)
        record = stored if stored is not None else keyed(blank(entity), entity, key)
        apply_values(record, self.referenced(type(record), draft.values))
        answer = response.of(record, self.described(entity, criteria), criteria)
        answer.draft = DraftState(
            version=draft.version,
            origin_version=draft.origin_version,
            kept_at=draft.kept_at,
            stale=stored is not None
            and getattr(stored, "version", 0) != draft.origin_version,
        )
        return answer

    def get(self, dto: Any) -> Any:
        """The record found by its key, as the entity reads it, or `AggregateNotFound` — the
        caller's draft of it instead when `draft` asks and one is saved.

            QueryGetAccount(id="01a1…")               ->  ResponseAccount(account=Account(…))
            QueryGetAccount(id="01a1…", draft=True)   ->  the saved draft, with `draft` state

        1. With `draft` asked, the caller's saved draft of this record, when there is one, is
           answered instead (`drafted`), over the criteria a stored one would be read by.
        2. Otherwise a page of one, filtered by the entity's key and read as the entity reads
           itself, the caller's criteria replacing it part by part (`by_identity`).
        Final: an empty page is `AggregateNotFound`; else the record, its page and criteria.
        """
        entity, response = declared_by(type(dto))
        if dto.draft:
            draft = self.drafts().read(self.draft_subject(entity, dto.id))
            if draft is not None:
                criteria = self.by_identity(
                    entity, Condition(field=key_of(entity), value=dto.id), 1, dto.criteria
                )
                return self.drafted(entity, response, dto.id, draft, criteria)
        criteria = self.by_identity(
            entity, Condition(field=key_of(entity), value=dto.id), 1, dto.criteria
        )
        page = self.reads_from().search(entity, criteria)
        if not page.items:
            raise exceptions.AggregateNotFound(f"{entity.__name__} {dto.id} does not exist")
        return response.of(page.items[0], page, criteria)

    def get_many(self, dto: Any) -> Any:
        """The records of `ids`, in that order, each as the entity reads it; the ids with no
        record are listed as missing.

            QueryGetManyAccounts(ids=["1.2", "9.9", "1.1", "1.2"])
                ->  ResponseAccounts(accounts=[<1.2>, <1.1>], missing=["9.9"])

        Context: a repeated id is asked once; the page is as long as the ids, and each id is
        read by the entity's key (`DEFAULT_GET_ID`), not necessarily its identity.
        """
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

    def literal_search(self, dto: Any) -> Any:
        """The short page a select shows for the literal."""
        entity, response = declared_by(type(dto))
        criteria = matching(entity, dto.text).replaced_by(dto.criteria)
        return response.of(self.reads_from().search(entity, criteria), criteria)

    def drafted_page(self, entity: type, criteria: Criteria) -> EntityCollection[Any]:
        """The caller's drafts of `entity` as one page: each as the record it holds, selected
        by the criteria's filter, in its order — the most recently saved first when it names
        none — cut to its limit.

            # drafts saved of Invoice F-9 (just now) and F-7 (earlier), and of a Partner
            drafted_page(Invoice, Criteria())
                ->  EntityCollection(items=(<F-9 drafted>, <F-7 drafted>),
                                     count=Count(2, exact=True), meta=<Invoice's>)
            drafted_page(Invoice, Criteria(selection=Condition(field="total",
                                                           operator=Operator.GT, value=100)))
                ->  only the drafted invoices above 100

        1. Each subject the caller has a draft of, the most recently saved first — only those
           of this entity, in this bounded context.
           1.1 The record it holds: the stored one, or a blank one under its key, with the
               draft's values put on it.
        2. The records selected by the criteria's filter, in memory, as the engine would.
        3. Sorted by each of the criteria's sorts — the last first, so the first one wins.
        4. Cut to the criteria's limit, when it asks for a page.
        Final: one exact page, with the definition and the drops the store answers for the
        same criteria.
        """
        drafts = self.drafts()
        prefix = self.draft_subject(entity, "")
        records = []
        for subject in drafts.subjects():
            draft = drafts.read(subject) if subject.startswith(prefix) else None
            if draft is None:
                continue
            key = subject.removeprefix(prefix)
            stored = self.stored(entity, key)
            record = stored if stored is not None else keyed(blank(entity), entity, key)
            apply_values(record, self.referenced(type(record), draft.values))
            records.append(record)
        page = EntityCollection(items=tuple(records)).filtered_by(criteria.expression)
        for sort in reversed(criteria.order):
            page = page.sorted_by(
                lambda record, field=sort.field: getattr(record, field), sort.descending
            )
        if criteria.pagination is not None:
            page = page.take(criteria.pagination.limit)
        described = self.described(entity, criteria)
        return EntityCollection(
            items=page.items,
            count=Count(value=len(page.items), exact=True),
            meta=described.meta,
            dropped=described.dropped,
        )

    def search(self, dto: Any) -> Any:
        """The page the criteria asks for, each record as the entity reads it unless the
        criteria names what to bring. The store applies the entity's order when none is named.

            drafts_only = Criteria(selection=Condition(field="state", value="draft"))
            QuerySearchInvoices(criteria=drafts_only)
                ->  ResponseInvoices(invoices=[…], cursor=…, count=…)
            QuerySearchInvoices(criteria=…, drafts=True)
                ->  the caller's drafts of invoices instead (`drafted_page`)

        The entity's `DEFAULT_READING` is the starting point; the caller's criteria replaces
        it part by part.
        """
        entity, response = declared_by(type(dto))
        criteria = (detail_of(entity) or Criteria()).replaced_by(dto.criteria)
        if dto.drafts:
            return response.of(self.drafted_page(entity, criteria), criteria)
        if encoding_of(dto, criteria) == "parquet":
            from sincpro_framework.data_layer.data_analysis.export import searched_as_parquet

            return searched_as_parquet(self.reads_from(), entity, criteria, response)
        return response.of(self.reads_from().search(entity, criteria), criteria)

    def export(self, dto: Any) -> Any:
        """Every record the criteria matches, as the file its format names, streamed a page at a
        time from one snapshot of the store this Feature reads.

            QueryExportEntries(criteria=posted)
                ->  Streamed(media_type="application/vnd.apache.parquet", filename="entry.parquet")
        """
        from sincpro_framework.data_layer.data_analysis.export import exported

        entity, _ = declared_by(type(dto))
        return exported(self.reads_from(), entity, dto.criteria, dto.format)

    def domain_events(self, dto: Any) -> Any:
        """The record's events, oldest first, each filtered by the record's type and identity.

            QueryIssueEvents(id="0192…")
                ->  ResponseIssueEvents(events=[IssueOpened(…), IssueAssigned(…), …])
            QueryIssueEvents(id="0192…", names="IssueAssigned")   ->  only those
            QueryAccountEvents(id="1.2.3")   # Account keyed by `code`: its id read first
            QueryIssueEvents(id="  ")        ->  raises ProgrammingError("… a blank one …")

        1. The event class is what the response holds (`events: list[ProjectEvent]`); one that
           is not a `DomainEvent` is refused.
        2. A blank key is refused: it would read every event about no record.
        3. The identity the events name: the key itself when the key is the identity, otherwise
           the identity of the record the key finds (`AggregateNotFound` when none).
        4. `entity_type` is the entity's own name and `entity_id` that identity, and `name` one
           of `names` when any; the caller's criteria is laid over it — its `where` adds, its
           order and page replace.
        Final: the page, answered as the response declares it.
        """
        entity, response = declared_by(type(dto))
        events = events_of(response)
        key = dto.id.strip()
        if not key:
            raise exceptions.ProgrammingError(
                f"{type(dto).__name__} needs the {entity.__name__}'s key: a blank one would read "
                "every event about no record"
            )
        identity = key
        if key_of(entity) != identity_name(entity):
            found = self.reads_from().search(
                entity,
                Criteria(
                    selection=Condition(field=key_of(entity), value=key),
                    pagination=Pagination(limit=1),
                    count=CountMode.NONE,
                ),
            )
            if not found.items:
                raise exceptions.AggregateNotFound(f"{entity.__name__} {key} does not exist")
            identity = str(getattr(found.items[0], identity_name(entity)))
        where = subject(entity.__name__, identity)
        wanted: list[str] = [dto.names] if isinstance(dto.names, str) else list(dto.names)
        if wanted:
            where = Node.combined(
                where, Condition(field="name", value=cast(Any, wanted), operator=Operator.IN)
            )
        criteria = Criteria(selection=where, order=Sort.parse("id")).replaced_by(dto.criteria)
        return response.of(self.reads_from().search(events, criteria), criteria)

    def preview(self, dto: Any) -> Any:
        """What the record becomes with the form's values, computed over a record nobody saves.

            QueryPreviewSale(id="S-1", values={"discount": "10"}, changed=["discount"])
                ->  ResponsePreviewEntity(values={"total": 90.00}, states={…}, warnings=[])
            QueryPreviewSale(values={})    # a new sale, nothing changed yet
                ->  ResponsePreviewEntity(values={<every value of a blank sale, derived>}, …)

        1. Inside `read_only()`: every write of every store raises, warnings are collected.
        2. Start from a copy of the stored record, read as its `DEFAULT_READING` brings it and
           with every relation its computations read (`AggregateNotFound` when the id has none), or from a
           new one with the class defaults.
        3. `apply_values` the form's values, read as each field's type; `recompute` what `changed`
           reaches, the records it owns first, as a save does — every computation when nothing
           changed yet.
        4. Events the domain recorded are dropped: nothing happened.
        Final: the values that differ from what the form sent, compared as values (`0` and
        `0.00` are one amount) — never a field the framework writes, which a form must not
        send back —, each field's state, the warnings. A list of records is compared as it is
        written once the form's values are in, so its lines come back when a computation moved
        one of them — each with its identity, which the warnings name it by. A new record with
        nothing changed answers every value.
        """
        entity, response = declared_by(type(dto))
        with read_only() as warnings:
            record = self.previewed(entity, dto.id)
            sent = held(record)
            apply_values(record, self.referenced(type(record), dto.values))
            sent.update({name: getattr(record, name) for name in dto.values})
            lists = {
                name: one
                for name, one in dumped(record).items()
                if isinstance(sent.get(name), list)
            }
            recompute(record, dto.changed)
            pull = getattr(record, "pull_events", None)
            if callable(pull):
                pull()
        system = model_of(entity).framework_fields
        values = {name: one for name, one in dumped(record).items() if name not in system}
        if dto.changed or dto.id is not None:
            now = held(record)
            values = {
                name: one
                for name, one in values.items()
                if name not in sent
                or (
                    one != lists.get(name)
                    if isinstance(now.get(name), list)
                    else sent[name] != now.get(name)
                )
            }
        return response(values=values, states=states_of(record), warnings=list(warnings))

    def execute(self, dto: Any) -> Any:
        """Routes the DTO to the read its base names — `Get` to `get`, `Search` to `search`, …
        — so one Feature answers every read of the entity and each stays overridable alone.

            execute(QueryGetAccount(id="1.2.3"))      ->  self.get(dto)
            execute(QueryIssueEvents(id="0192…"))     ->  self.domain_events(dto)
            execute(CommandIssueInvoice(…))
                ->  raises ProgrammingError("AccountReads answers Get, GetMany, …, not …")

        `DomainEvents` is matched before `Search`; any other DTO is a `ProgrammingError`.
        """
        match dto:
            case Export():
                return self.export(dto)
            case Get():
                return self.get(dto)
            case GetMany():
                return self.get_many(dto)
            case LiteralSearch():
                return self.literal_search(dto)
            case DomainEvents():
                return self.domain_events(dto)
            case Search():
                return self.search(dto)
            case PreviewEntity():
                return self.preview(dto)
        raise exceptions.ProgrammingError(
            f"{type(self).__name__} answers Get, GetMany, LiteralSearch, Search, Export, "
            f"DomainEvents and PreviewEntity, not {type(dto).__name__}"
        )


def parquet_written() -> bool:
    """Whether this process writes Parquet — pyarrow, the `[data-analysis]` extra, installed."""
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        return False
    return True


def encoding_of(dto: Any, criteria: Criteria) -> Literal["json", "parquet"]:
    """How a search's page travels: what the caller chose, or — `auto` — Parquet when more than
    the search's `parquet_above` records are asked for, the caller reads Parquet (`Accept`, as
    a wire writes it in the context) and this process writes it; JSON otherwise.

        format="parquet"                                   ->  "parquet"
        format="auto", limit 2 000 000, Accept: parquet    ->  "parquet"
        format="auto", limit 2 000 000, inside the process ->  "json"     (objects, never bytes)
        format="auto", limit 50                            ->  "json"
    """
    if dto.format == "parquet" and not parquet_written():
        raise exceptions.InvalidCriteria(
            "this service writes no Parquet (pyarrow, the [data-analysis] extra, is not "
            "installed): ask format=json"
        )
    if dto.format != "auto":
        return dto.format
    above = type(dto).parquet_above
    if above is None or criteria.limit <= above:
        return "json"
    accepted = use_context().get(ACCEPTED_FORMATS) or {}
    # What the caller of the wire reads is for the use case it asked, never for one that use case
    # calls inside the process.
    reads = accepted.get("types", ()) if accepted.get("for") == type(dto).__name__ else ()
    return "parquet" if PARQUET in reads and parquet_written() else "json"


def with_computed_relations(entity: type, criteria: Criteria) -> Criteria:
    """`criteria`, also bringing every relation the computation of `entity` reads (`owned`) — a
    total over lines the entity's reading does not name still reads the lines.

        # Sale: DEFAULT_READING names customer_id; subtotal ← lines
        with_computed_relations(Sale, Criteria(specification={"customer_id": {}}))
            ->  Criteria(specification={"customer_id": {}, "lines": {}})  # every scalar
        with_computed_relations(Sale, Criteria(specification={"lines": {"qty": {}}}))
            ->  the same criteria — `lines` named already, as the caller cut it

    Context: only what is missing is added, sorted by name, after what the caller named; the
    criteria comes back as it is when nothing is missing.
    """
    read = set(model_of(entity).owned)
    named = criteria.specification.root if criteria.specification is not None else {}
    missing = read - set(named)
    if not missing:
        return criteria
    nodes = {**named, **{name: Criteria() for name in sorted(missing)}}
    return criteria.model_copy(update={"specification": Specification(nodes)})


def _in_progress(value: Any, seen: dict[int, Any]) -> bool:
    """Whether `value` is a record whose copy is being built right now — `seen` still holds
    the original itself, not its copy — so whatever points at it must wait for the copy."""
    return seen.get(id(value)) is value and dataclasses.is_dataclass(value)


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


def _copied(
    record: Any, seen: dict[int, Any], pending: dict[int, list[Callable[[Any], None]]]
) -> Any:
    """One record copied. While its fields are copied it stands for itself in `seen`; whatever
    met it then is pointed at its copy once the copy exists (`pending`).

        Invoice(lines=[Line(invoice=<that invoice>)])
        ->  Invoice'(lines=[Line'(invoice=Invoice')])          the cycle closed on the copies

    1. Not a dataclass: a shallow `copy.copy`.
    2. Met before: its copy — or, while still in progress, the original, fixed up in step 5.
    3. Marked in progress, then each init field copied; a relation nobody resolved is left
       to its default.
    4. Built through its own constructor, and the copy put in `seen`.
    5. Every value of the copy still pointing at a record in progress is noted (`_awaiting`).
    Final: every point that waited for this record is pointed at the copy, which is answered.
    """
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
        except exceptions.RelationNotResolved:
            continue
        values[one.name] = _copied_value(value, seen, pending)
    built = entity(**values)
    seen[id(record)] = built
    for name, value in values.items():
        _awaiting(built, name, value, seen, pending)
    for point in pending.pop(id(record), []):
        point(built)
    return built


def copied(record: Any) -> Any:
    """A new record holding what `record` holds, built through its own constructor — and so is
    every record inside it — so nothing done to it reaches what a store holds: a store in
    memory hands back its own instances.

    in      the stored Invoice(discount=0, lines=[line 1, line 2])
    out     another Invoice(discount=0, lines=[another line 1, another line 2])

    A relation nobody resolved is left at its default. A record met twice is copied once, and a
    back-reference (a line pointing at its invoice) points at the copy.
    """
    seen: dict[int, Any] = {}
    pending: dict[int, list[Callable[[Any], None]]] = {}
    return _copied(record, seen, pending)


def _copied_value(
    value: Any, seen: dict[int, Any], pending: dict[int, list[Callable[[Any], None]]]
) -> Any:
    """A field's value for `copied`: a record copied, a list of them copied one by one."""
    if isinstance(value, (list, EntityCollection)):
        return [_copied_value(one, seen, pending) for one in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _copied(value, seen, pending)
    return value


def keyed(record: Any, entity: type, key: str) -> Any:
    """A new record answering `key`: what a caller named a record it is creating by."""
    setattr(record, key_of(entity), key)
    return record


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
    resolved is left out, and a related record's own relations write their defaults.

        Invoice(number="F-1", lines=<not resolved>)   ->  {"number": "F-1", ...}   no "lines"

    1. Each annotated field, read; a `RelationNotResolved` skips it.
    2. A collection written as the list of its records.
    Final: dumped as JSON through the field's own adapter, inside `writing_out()` so nested
    records do not try to resolve what nobody loaded.
    """
    annotations = model_of(type(record)).annotations
    written: dict[str, Any] = {}
    for name, annotation in annotations.items():
        try:
            value = getattr(record, name)
        except exceptions.RelationNotResolved:
            continue
        if isinstance(value, EntityCollection):
            value = list(value)
        with writing_out():
            written[name] = adapter_of(annotation).dump_python(
                value, mode="json", warnings=False
            )
    return written


def _holds(record: Any, condition: Any) -> bool:
    """Whether a hint's condition holds over the record; no condition never holds."""
    return condition is not None and Evaluation.matches(record, condition)


def states_of(record: Any) -> dict[str, FieldState]:
    """Each field's hints, evaluated over this record: what a client with no Criteria evaluator
    shows.

        Invoice(state=confirmed, lines=[line 1, line 2])
            ->  {"partner_id": FieldState(readonly=True), …,
                 "lines": FieldState(items={"0192…": {"quantity": FieldState(…), …}, …})}

    1. Each field read-only, required and visible as its class declared, the conditions
       evaluated over this record.
    2. A list of records it holds, already read — the lines —, each one's own states, by
       the line's class, under the line's identity — its position when it has none; a relation
       nobody read is skipped, as `dumped` does, and so are the records a many-to-many
       references (`named_by_id`): another aggregate's, not parts.
    Final: the states by field name.
    """
    presented = model_of(type(record)).presented
    states: dict[str, FieldState] = {}
    for name in model_of(type(record)).annotations:
        try:
            value = getattr(record, name)
        except exceptions.RelationNotResolved:
            value = None
        attribute = relation_attribute(type(record), name)
        referenced = attribute is not None and attribute.named_by_id()
        held = (
            list(value)
            if isinstance(value, (list, EntityCollection)) and not referenced
            else []
        )
        states[name] = FieldState(
            readonly=name in presented.readonly
            or _holds(record, presented.readonly_when.get(name)),
            required=name in presented.required
            or _holds(record, presented.required_when.get(name)),
            visible=name not in presented.visible_when
            or _holds(record, presented.visible_when[name]),
            items={
                (
                    str(getattr(one, identity_name(type(one))))
                    if identity_name(type(one)) in model_of(type(one)).framework_fields
                    else str(position)
                ): states_of(one)
                for position, one in enumerate(held)
                if dataclasses.is_dataclass(one)
            },
        )
    return states
