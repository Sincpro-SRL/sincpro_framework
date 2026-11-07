"""The metamodel of a bus — every model its operations reach, as its published contract, with the
operations that belong to each, under one version — and the file a build writes from it.

Each operation says what it is for its entity — its get, its search, its edit, or a command on it
— read off its DTO's type, and what it takes and answers, what those schemas share written once:
a client builds a screen from it without being told a route.

A model that changes at runtime — an extension that overlays how a field is shown — is compiled
again with another fingerprint, said in the log, and the bus's metamodel answers another version.
"""

import json
from dataclasses import dataclass
from typing import Any

from structlog.testing import capture_logs

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    DryRunnable,
    Feature,
    Preview,
    UseFramework,
)
from sincpro_framework.common.naming import registered_name
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    DomainEvent,
    DomainEvents,
    Edit,
    Entity,
    EntityCollection,
    EntityEdits,
    EntityID,
    EntityReads,
    Get,
    GetMany,
    LiteralSearch,
    PreviewEntity,
    Query,
    Reference,
    ResponseEdit,
    ResponsePaginatedQuery,
    ResponsePreviewEntity,
    ResponseRecord,
    ResponseRecords,
    Search,
)
from sincpro_framework.ddd.entity import extensions
from sincpro_framework.ddd.metamodel import contract_of, forget_models, model_of
from sincpro_framework.introspection import (
    entity_tags,
    export_metamodel,
    metamodel_of,
    operations_of,
)


@dataclass
class Partner(Entity):
    name: str = ""


@dataclass
class Invoice(Entity):
    number: str = ""
    partner: Partner | None = None


class ResponseInvoice(ResponseRecord):
    invoice: Invoice


class QueryGetInvoice(Get[Invoice, ResponseInvoice]):
    pass


class CommandVoidInvoice(DryRunnable):
    invoice_id: str


class ResponseVoided(DataTransferObject):
    voided: bool


def billing() -> UseFramework:
    bus = UseFramework("billing-metamodel", log_after_execution=False)
    bus.add_dependency("repository", MemoryRepository())

    @bus.feature(QueryGetInvoice)
    class InvoiceReads(EntityReads[Invoice]):
        pass

    @bus.feature(CommandVoidInvoice)
    @entity_tags(Invoice)
    class VoidInvoice(Feature):
        def preview(self, dto: CommandVoidInvoice) -> Preview:
            return Preview(text=f"void {dto.invoice_id}")

        def execute(self, dto: CommandVoidInvoice) -> ResponseVoided:
            return ResponseVoided(voided=True)

    return bus


def test_every_model_the_operations_reach_is_its_contract():
    metamodel = metamodel_of(billing())
    invoice = contract_of(Invoice)

    assert sorted(metamodel["models"]) == ["Invoice", "Partner"]
    assert metamodel["models"]["Partner"] == invoice.pop("$defs")["Partner"]
    # The same contract, its pointer at the related entity pointed where the metamodel holds it.
    assert metamodel["models"]["Invoice"]["properties"]["partner"]["anyOf"][0] == {
        "$ref": "#/models/Partner"
    }
    assert {**metamodel["models"]["Invoice"], "properties": {}} == {
        **invoice,
        "properties": {},
    }


def test_every_pointer_inside_the_models_resolves_in_the_metamodel():
    metamodel = metamodel_of(help_desk())

    pointers = _refs(metamodel["models"])

    assert pointers and all(_resolved(metamodel, one) is not None for one in pointers)
    assert not any(one.startswith("#/$defs/") for one in pointers)


def test_each_operation_says_the_entities_it_belongs_to_and_whether_it_runs_dry():
    operations = metamodel_of(billing())["operations"]

    assert operations["billing-metamodel.QueryGetInvoice"]["entities"] == ["Invoice"]
    assert operations["billing-metamodel.QueryGetInvoice"]["dry_run"] is False
    assert operations["billing-metamodel.CommandVoidInvoice"]["entities"] == ["Invoice"]
    assert operations["billing-metamodel.CommandVoidInvoice"]["dry_run"] is True


def test_an_operation_is_keyed_by_its_commands_identity_the_key_every_binding_is_listed_by():
    metamodel = metamodel_of(billing())

    assert metamodel["bounded_context"] == "billing-metamodel"
    assert sorted(metamodel["operations"]) == [
        registered_name(CommandVoidInvoice, "billing-metamodel"),
        registered_name(QueryGetInvoice, "billing-metamodel"),
    ]
    # A wire publishing only what it exposes names the Commands by their class.
    assert list(metamodel_of(billing(), {"QueryGetInvoice"})["operations"]) == [
        "billing-metamodel.QueryGetInvoice"
    ]


def test_the_same_code_writes_the_same_bytes_and_the_version_is_their_fingerprint(tmp_path):
    first, second = tmp_path / "first.json", tmp_path / "second.json"

    version = export_metamodel(billing(), first)
    export_metamodel(billing(), second)

    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text()) == metamodel_of(billing())
    assert version == metamodel_of(billing())["version"]


def test_an_extension_that_overlays_a_field_changes_the_model_and_says_so():
    bus = billing()
    before = metamodel_of(bus)["version"]
    model_before = model_of(Invoice).fingerprint
    try:
        extensions.overlay(Invoice, "number", {"label": {"default": "Folio"}})
        with capture_logs() as logs:
            forget_models(Invoice)
            after = metamodel_of(bus)

        assert after["version"] != before
        assert model_of(Invoice).fingerprint != model_before
        assert after["models"]["Invoice"]["properties"]["number"]["title"] == "Folio"
        assert [one["event"] for one in logs if "changed" in one["event"]] == [
            f"metamodel: Invoice changed, version {model_before[:12]} -> "
            f"{model_of(Invoice).fingerprint[:12]}"
        ]
    finally:
        extensions.OVERLAYS.pop(Invoice, None)
        forget_models(Invoice)


# ---------------------------------------------------------------------------------------------
# What each operation is for its entity, and what it takes and answers
#
# A help desk: a ticket is read every way the framework reads an entity — one, several, a
# select's literal, a page, its events, a form's preview —, edited through `Edit`, listed by a
# project's own base over `Search` (forge's `TenantSearch`), and acted on by two Commands tagged
# with it. Closing the day concerns no entity.
# ---------------------------------------------------------------------------------------------


@dataclass
class Ticket(Entity):
    subject: str = ""
    customer: Partner | None = None


@dataclass(kw_only=True)
class TicketEvent(DomainEvent):
    pass


class ResponseTicket(ResponseRecord):
    ticket: Ticket


class ResponseTickets(ResponseRecords):
    tickets: list[Ticket]


class ResponseListTickets(ResponsePaginatedQuery):
    tickets: list[Ticket]


class ResponseTicketEvents(ResponsePaginatedQuery):
    events: list[TicketEvent]


class ResponsePreviewTicket(ResponsePreviewEntity):
    pass


class ResponseEditTicket(ResponseEdit):
    ticket: Ticket


class OwnSearch[T, R: ResponsePaginatedQuery](Search[T, R]):
    """A project's own base over `Search` — forge narrows each one by the caller's tenant."""

    queue: str = ""


class QueryGetTicket(Get[Ticket, ResponseTicket]):
    pass


class QueryGetManyTickets(GetMany[Ticket, ResponseTickets]):
    pass


class QueryFindTickets(LiteralSearch[Ticket, ResponseListTickets]):
    pass


class QueryListTickets(Search[Ticket, ResponseListTickets]):
    pass


class QueryListQueuedTickets(OwnSearch[Ticket, ResponseListTickets]):
    pass


class QueryTicketEvents(DomainEvents[Ticket, ResponseTicketEvents]):
    pass


class QueryPreviewTicket(PreviewEntity[Ticket, ResponsePreviewTicket]):
    pass


class CommandEditTicket(Edit[Ticket, ResponseEditTicket]):
    pass


class CommandAssignTicket(DataTransferObject):
    ticket_id: str
    agent: str


class CommandEscalateTicket(DataTransferObject):
    ticket_id: str


class CommandCloseDay(DataTransferObject):
    day: str


class QueryTicketWorkload(Query):
    """A read of the context's own: the open tickets of each agent."""

    agent: str = ""


class ResponseTicketWorkload(DataTransferObject):
    open: int


class ResponseTicketNoted(DataTransferObject):
    noted: bool


def help_desk() -> UseFramework:
    bus = UseFramework("desk-metamodel", log_after_execution=False)
    bus.add_dependency("repository", MemoryRepository())

    @bus.feature(
        [
            QueryGetTicket,
            QueryGetManyTickets,
            QueryFindTickets,
            QueryListTickets,
            QueryListQueuedTickets,
            QueryTicketEvents,
            QueryPreviewTicket,
        ]
    )
    class TicketReads(EntityReads[Ticket]):
        pass

    @bus.feature(CommandEditTicket)
    class TicketForms(EntityEdits[Ticket]):
        pass

    @bus.feature(CommandAssignTicket)
    @entity_tags(Ticket)
    class AssignTicket(Feature):
        def execute(self, dto: CommandAssignTicket) -> None:
            return None

    @bus.feature(CommandEscalateTicket)
    @entity_tags(Ticket, Partner)
    class EscalateTicket(Feature):
        def execute(self, dto: CommandEscalateTicket) -> ResponseTicket:
            return ResponseTicket(ticket=Ticket())

    @bus.app_service(CommandCloseDay)
    class CloseDay(ApplicationService):
        def execute(self, dto: CommandCloseDay):  # declares no answer
            return None

    @bus.feature(QueryTicketWorkload)
    @entity_tags(Ticket)
    class TicketWorkload(Feature):
        def execute(self, dto: QueryTicketWorkload) -> ResponseTicketWorkload:
            return ResponseTicketWorkload(open=0)

    @bus.app_service(TicketEvent)
    @entity_tags(Ticket)
    class NoteTicketEvent(ApplicationService):
        """Reacts to what happened to a ticket — no client calls it."""

        def execute(self, dto: TicketEvent) -> ResponseTicketNoted:
            return ResponseTicketNoted(noted=True)

    return bus


DESK = "desk-metamodel"


def _local(operations: dict[str, Any]) -> dict[str, Any]:
    """The operations of the help desk by their Command's class name — its identity without the
    context."""
    return {name.removeprefix(f"{DESK}."): one for name, one in operations.items()}


def _refs(node: Any) -> list[str]:
    """Every `$ref` under `node`."""
    if isinstance(node, dict):
        return [
            *([node["$ref"]] if isinstance(node.get("$ref"), str) else []),
            *(ref for value in node.values() for ref in _refs(value)),
        ]
    if isinstance(node, list):
        return [ref for one in node for ref in _refs(one)]
    return []


def _resolved(document: dict[str, Any], pointer: str) -> Any:
    """What a `#/a/b` pointer names in `document`."""
    found: Any = document
    for part in pointer.removeprefix("#/").split("/"):
        found = found[part]
    return found


def test_each_operation_says_what_it_is_for_its_entity_read_off_its_type():
    operations = _local(metamodel_of(help_desk())["operations"])

    assert {name: one["role"] for name, one in operations.items()} == {
        "QueryGetTicket": "get",
        "QueryGetManyTickets": "get_many",
        "QueryFindTickets": "literal_search",
        "QueryListTickets": "search",
        "QueryListQueuedTickets": "search",
        "QueryTicketEvents": "events",
        "QueryPreviewTicket": "preview",
        "CommandEditTicket": "edit",
        "CommandAssignTicket": "command",
        "CommandEscalateTicket": "command",
        "CommandCloseDay": "command",
        "QueryTicketWorkload": "command",
    }
    assert operations["QueryListQueuedTickets"]["entities"] == ["Ticket"]
    assert operations["CommandEscalateTicket"]["entities"] == ["Ticket", "Partner"]
    assert operations["CommandCloseDay"]["entities"] == []


def test_a_use_case_that_reacts_to_a_domain_event_is_no_operation_a_client_calls():
    desk = help_desk()
    operations = metamodel_of(desk)["operations"]

    assert "TicketEvent" in operations_of(desk)  # the bus still runs it
    assert registered_name(TicketEvent, DESK) not in operations
    assert len(operations) == len(operations_of(desk)) - 1


def test_an_operation_says_whether_it_only_reads():
    operations = _local(metamodel_of(help_desk())["operations"])

    reads = {name for name, one in operations.items() if one["query"]}
    assert reads == {
        "QueryGetTicket",
        "QueryGetManyTickets",
        "QueryFindTickets",
        "QueryListTickets",
        "QueryListQueuedTickets",
        "QueryTicketEvents",
        "QueryPreviewTicket",
        "QueryTicketWorkload",  # a command of the context's own that is a `Query`
    }


def test_an_operation_names_the_field_that_holds_each_entitys_record():
    operations = _local(metamodel_of(help_desk())["operations"])

    assert {name: one["targets"] for name, one in operations.items()} == {
        "QueryGetTicket": {"Ticket": "id"},
        "QueryGetManyTickets": {"Ticket": "ids"},  # several keys
        "QueryFindTickets": {},
        "QueryListTickets": {},
        "QueryListQueuedTickets": {},
        "QueryTicketEvents": {"Ticket": "id"},
        "QueryPreviewTicket": {"Ticket": "id"},
        "CommandEditTicket": {"Ticket": "id"},
        "CommandAssignTicket": {"Ticket": "ticket_id"},
        "CommandEscalateTicket": {"Ticket": "ticket_id"},  # no `partner_id`: no partner named
        "CommandCloseDay": {},
        "QueryTicketWorkload": {},  # `id` is no ticket's when nothing names one
    }


def test_an_edit_and_a_preview_take_the_record_the_form_holds_as_their_entitys_model():
    operations = _local(metamodel_of(help_desk())["operations"])

    edited = operations["CommandEditTicket"]["input"]["properties"]["values"]
    previewed = operations["QueryPreviewTicket"]["input"]["properties"]["values"]

    assert previewed == {"$ref": "#/models/Ticket"}
    assert edited["$ref"] == "#/models/Ticket"
    assert edited["description"].startswith("The record as the form holds it")  # kept


def test_what_operations_share_is_written_once_and_every_pointer_resolves():
    metamodel = metamodel_of(help_desk())
    operations = _local(metamodel["operations"])

    assert "Criteria" in metamodel["$defs"]
    assert operations["QueryListTickets"]["input"]["properties"]["criteria"]["$ref"] == (
        "#/$defs/Criteria"
    )
    assert not any('"$defs":' in json.dumps(one) for one in operations.values())
    pointers = _refs(operations) + _refs(metamodel["$defs"])
    assert pointers and all(_resolved(metamodel, one) is not None for one in pointers)


class Assignment(DataTransferObject):
    ticket: Ticket
    agent: str


class ResponseAssignments(DataTransferObject):
    assignments: list[Assignment]


class QueryAssignments(Query):
    pass


def test_an_entity_inside_a_shared_definition_is_still_only_its_model():
    bus = UseFramework("desk-assignments", log_after_execution=False)

    @bus.feature(QueryAssignments)
    class Assignments(Feature):
        def execute(self, dto: QueryAssignments) -> ResponseAssignments:
            return ResponseAssignments(assignments=[])

    metamodel = metamodel_of(bus)

    assert metamodel["$defs"]["Assignment"]["properties"]["ticket"] == {
        "$ref": "#/models/Ticket"
    }
    assert not set(metamodel["$defs"]) & set(metamodel["models"])


def test_an_entity_an_operation_takes_or_answers_points_at_its_model():
    operations = _local(metamodel_of(help_desk())["operations"])

    # EntityReads declares no answer: the one its DTO names in its type is what it answers.
    answered = operations["QueryGetTicket"]["output"]
    assert answered["title"] == "ResponseTicket"
    assert answered["properties"]["ticket"] == {"$ref": "#/models/Ticket"}
    assert operations["QueryListTickets"]["output"]["properties"]["tickets"]["items"] == {
        "$ref": "#/models/Ticket"
    }
    assert operations["CommandEscalateTicket"]["output"]["properties"]["ticket"] == {
        "$ref": "#/models/Ticket"
    }


def test_an_answer_of_a_collection_publishes_the_same_contract_as_a_list():
    """The contract describes an answer in validation mode. A collection accepts a list or a
    page there; an answer writes the list, so the published field is that array — the same
    contract a `list` of the same records publishes. The response's own name is the only
    difference, and it is not part of the wire."""

    @dataclass
    class ProjectEvent(Entity):
        name: str = ""

    class QueryListEvents(Query):
        pass

    class QueryCollectEvents(Query):
        pass

    class QueryGetEvent(Query):
        pass

    class QueryGetCollectedEvent(Query):
        pass

    class QueryGetEvents(Query):
        pass

    class QueryGetCollectedEvents(Query):
        pass

    class ResponseListEvents(ResponsePaginatedQuery):
        events: list[ProjectEvent]

    class ResponseCollectEvents(ResponsePaginatedQuery):
        events: EntityCollection[ProjectEvent]

    class ResponseEvent(ResponseRecord):
        event: list[ProjectEvent]

    class ResponseCollectedEvent(ResponseRecord):
        event: EntityCollection[ProjectEvent]

    class ResponseEvents(ResponseRecords):
        events: list[ProjectEvent]

    class ResponseCollectedEvents(ResponseRecords):
        events: EntityCollection[ProjectEvent]

    def published(
        command: type, response: type
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        bus = UseFramework(command.__name__, log_after_execution=False)

        @bus.feature(command)
        @entity_tags(ProjectEvent)
        class Read(Feature):
            def execute(self, dto: command) -> response:
                raise AssertionError("the contract is read from the annotation")

        metamodel = metamodel_of(bus)
        [operation] = metamodel["operations"].values()
        output = dict(operation["output"])
        output.pop("title", None)
        return output, metamodel["models"], metamodel["$defs"]

    pairs = (
        (QueryListEvents, ResponseListEvents, QueryCollectEvents, ResponseCollectEvents),
        (QueryGetEvent, ResponseEvent, QueryGetCollectedEvent, ResponseCollectedEvent),
        (QueryGetEvents, ResponseEvents, QueryGetCollectedEvents, ResponseCollectedEvents),
    )
    for listed, list_response, collected, collection_response in pairs:
        assert published(collected, collection_response) == published(listed, list_response)


def test_an_answer_of_nothing_is_null_and_an_undeclared_one_is_not_promised():
    operations = _local(metamodel_of(help_desk())["operations"])

    assert operations["CommandAssignTicket"]["input"]["required"] == ["ticket_id", "agent"]
    assert operations["CommandAssignTicket"]["output"] == {"type": "null"}
    assert operations["CommandCloseDay"]["output"] is None


def _receiving(bus: UseFramework) -> None:
    class Line(DataTransferObject):
        quantity: int

    class CommandReceive(DataTransferObject):
        lines: list[Line]

    @bus.feature(CommandReceive)
    class Receive(Feature):
        def execute(self, dto: CommandReceive) -> None:
            return None


def _pricing(bus: UseFramework) -> None:
    class Line(DataTransferObject):
        price: float

    class CommandPrice(DataTransferObject):
        lines: list[Line]

    @bus.feature(CommandPrice)
    class Price(Feature):
        def execute(self, dto: CommandPrice) -> None:
            return None


def _counting(bus: UseFramework) -> None:
    class Line(DataTransferObject):
        quantity: int

    class CommandCount(DataTransferObject):
        lines: list[Line]

    @bus.feature(CommandCount)
    class Count(Feature):
        def execute(self, dto: CommandCount) -> None:
            return None


def _lines(metamodel: dict[str, Any], command: str) -> dict[str, Any]:
    """Where the lines of `command` point."""
    taken = metamodel["operations"][f"warehouse-metamodel.{command}"]["input"]
    return taken["properties"]["lines"]["items"]


def test_a_definition_is_named_by_its_class():
    bus = UseFramework("warehouse-metamodel", log_after_execution=False)
    _receiving(bus)

    metamodel = metamodel_of(bus)

    assert _lines(metamodel, "CommandReceive") == {"$ref": "#/$defs/Line"}


def test_two_classes_under_one_name_are_both_named_by_their_module_each_operation_its_own():
    bus = UseFramework("warehouse-metamodel", log_after_execution=False)
    _receiving(bus)
    _pricing(bus)

    metamodel = metamodel_of(bus)
    priced, received = _lines(metamodel, "CommandPrice"), _lines(metamodel, "CommandReceive")

    assert "Line" not in metamodel["$defs"]  # neither keeps the bare name
    assert "pricing" in priced["$ref"] and "receiving" in received["$ref"]
    assert list(_resolved(metamodel, priced["$ref"])["properties"]) == ["price"]
    assert list(_resolved(metamodel, received["$ref"])["properties"]) == ["quantity"]


def test_two_classes_of_one_name_and_one_shape_are_one_definition():
    bus = UseFramework("warehouse-metamodel", log_after_execution=False)
    _receiving(bus)
    _counting(bus)

    metamodel = metamodel_of(bus)

    assert _lines(metamodel, "CommandCount") == _lines(metamodel, "CommandReceive")
    assert _lines(metamodel, "CommandReceive") == {"$ref": "#/$defs/Line"}


def test_adding_an_operation_never_points_a_name_at_another_shape():
    """A client generates a type per definition: `Line` must never come to mean another class
    because an operation was added — it is kept, or it is gone and the client's build says so.
    """
    alone = UseFramework("warehouse-metamodel", log_after_execution=False)
    _pricing(alone)
    joined = UseFramework("warehouse-metamodel", log_after_execution=False)
    _pricing(joined)
    _receiving(joined)  # `CommandPrice` < `CommandReceive`: added after it in name order too

    before, after = metamodel_of(alone), metamodel_of(joined)

    for name, definition in after["$defs"].items():
        if name in before["$defs"]:
            assert definition == before["$defs"][name]
    assert list(_resolved(after, _lines(after, "CommandPrice")["$ref"])["properties"]) == [
        "price"
    ]


# ---------------------------------------------------------------------------------------------
# A key that says whose it is: the field an operation runs on, said by its type
#
# A ledger: moving an issue names two entities in fields of any name, a transfer names one
# account twice, and joining a tenant names auth's tenant without importing auth.
# ---------------------------------------------------------------------------------------------


@dataclass
class Account(Entity):
    name: str = ""


class Tenant(Reference, bounded_context="auth"):
    """auth's tenant, named once in `shared/`."""


class CommandMoveTicket(DataTransferObject):
    """Case b: fields named otherwise — the type says whose each is."""

    ticket: EntityID[Ticket]
    destination: EntityID[Account]


class CommandMergeTickets(DataTransferObject):
    """Case a: `id` beside two entities names neither, unless its type says whose it is."""

    id: EntityID[Ticket]
    into: str


class CommandTransfer(DataTransferObject):
    """Case c: one entity twice — no rule guesses which one the operation runs on."""

    source: EntityID[Account]
    target: EntityID[Account]
    account_id: str


class CommandJoinTenant(DataTransferObject):
    """Case d: a key of another context's entity."""

    tenant_id: EntityID[Tenant]


class CommandReassignTicket(DataTransferObject):
    """Precedence: the type wins over the name."""

    ticket_id: str
    replacement: EntityID[Ticket]


class CommandCloseTicket(DataTransferObject):
    """Precedence: with one entity, a bare `EntityID` wins over `id`."""

    id: str
    key: EntityID


def ledger() -> UseFramework:
    bus = UseFramework("ledger-keys", log_after_execution=False)

    @bus.feature(CommandMoveTicket)
    class MoveTicket(Feature):
        def execute(self, dto: CommandMoveTicket) -> None:
            return None

    @bus.feature(CommandMergeTickets)
    @entity_tags(Ticket, Account)
    class MergeTickets(Feature):
        def execute(self, dto: CommandMergeTickets) -> None:
            return None

    @bus.feature(CommandTransfer)
    class Transfer(Feature):
        def execute(self, dto: CommandTransfer) -> None:
            return None

    @bus.feature(CommandJoinTenant)
    class JoinTenant(Feature):
        def execute(self, dto: CommandJoinTenant) -> None:
            return None

    @bus.feature(CommandReassignTicket)
    class ReassignTicket(Feature):
        def execute(self, dto: CommandReassignTicket) -> None:
            return None

    @bus.feature(CommandCloseTicket)
    @entity_tags(Ticket)
    class CloseTicket(Feature):
        def execute(self, dto: CommandCloseTicket) -> None:
            return None

    return bus


def _ledger_operations() -> dict[str, Any]:
    """The ledger's operations by their Command's class name."""
    return {
        name.removeprefix("ledger-keys."): one
        for name, one in metamodel_of(ledger())["operations"].items()
    }


def test_a_key_typed_as_an_entitys_names_the_field_whatever_its_name():
    operations = _ledger_operations()

    assert operations["CommandMoveTicket"]["targets"] == {
        "Ticket": "ticket",
        "Account": "destination",
    }
    assert operations["CommandMergeTickets"]["targets"] == {"Ticket": "id"}


def test_one_entity_named_twice_names_no_field():
    assert _ledger_operations()["CommandTransfer"]["targets"] == {}


def test_the_type_wins_over_the_name_and_a_bare_key_over_id():
    operations = _ledger_operations()

    assert operations["CommandReassignTicket"]["targets"] == {"Ticket": "replacement"}
    assert operations["CommandCloseTicket"]["targets"] == {"Ticket": "key"}


def test_a_key_says_the_entities_an_operation_concerns_without_a_tag():
    operations = _ledger_operations()

    assert operations["CommandMoveTicket"]["entities"] == ["Ticket", "Account"]
    assert operations["CommandTransfer"]["entities"] == ["Account"]
    assert operations["CommandJoinTenant"]["entities"] == []  # auth's: no entity of this one


def test_a_key_of_another_context_says_whose_it_is_and_brings_no_model_of_it():
    metamodel = metamodel_of(ledger())
    joined = metamodel["operations"]["ledger-keys.CommandJoinTenant"]

    assert joined["input"]["properties"]["tenant_id"]["x-sincpro"] == {
        "relation": "auth.Tenant"
    }
    assert joined["targets"] == {}
    assert "Tenant" not in metamodel["models"]


# ---------------------------------------------------------------------------------------------
# One use case, N entities: reconciling move lines, a move, payments — keys in a list
# ---------------------------------------------------------------------------------------------


@dataclass
class MoveLine(Entity):
    amount: str = ""


@dataclass
class Move(Entity):
    reference: str = ""


@dataclass
class Payment(Entity):
    amount: str = ""


class CommandReconcile(DataTransferObject):
    """One reconciliation, whatever it matches: lines, a move, payments."""

    lines: list[EntityID[MoveLine]] = []
    move_id: str | None = None
    payment_ids: list[str] = []


def ledger_reconciliation() -> UseFramework:
    bus = UseFramework("ledger-reconciliation", log_after_execution=False)

    @bus.feature(CommandReconcile)
    @entity_tags(Move, Payment)
    class Reconcile(Feature):
        def execute(self, dto: CommandReconcile) -> None:
            return None

    return bus


def test_one_use_case_names_the_records_of_each_of_its_entities_a_list_of_keys_too():
    [reconcile] = metamodel_of(ledger_reconciliation())["operations"].values()
    lines = reconcile["input"]["properties"]["lines"]

    assert reconcile["entities"] == ["Move", "Payment", "MoveLine"]
    assert reconcile["targets"] == {
        "Move": "move_id",
        "Payment": "payment_ids",
        "MoveLine": "lines",
    }
    assert lines["type"] == "array"
    assert lines["items"]["x-sincpro"] == {"relation": "MoveLine"}
