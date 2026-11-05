"""A many-to-many written: a support desk where a ticket carries labels.

The labels are an aggregate of their own — created, renamed and retired somewhere else. A ticket
only says which ones it carries, and those pairs live in a table in between. Saving a ticket
writes its pairs and never a label; removing a ticket takes its pairs and leaves the labels.
"""

from dataclasses import dataclass, field

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, Table, Text, select
from sqlalchemy.orm import registry

from sincpro_framework import UseFramework, exceptions
from sincpro_framework.common.failures import problem_of
from sincpro_framework.context import InMemoryContexts
from sincpro_framework.data_layer.orm import TableRelation, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.ddd import (
    Edit,
    EditAction,
    EntityEdits,
    ResponseEdit,
    Specification,
)
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.relations import (
    RelationAttribute,
    RelationKind,
    relation_attribute,
)


@dataclass
class Label(Entity):
    name: str


@dataclass
class Ticket(Entity):
    subject: str
    labels: list[Label] = field(default_factory=list)

    @classmethod
    def DEFAULT_READING(cls) -> Specification:
        return Specification.model_validate({"labels": {}})


desk = registry(metadata=MetaData())
label_table = entity_table("m2m_label", desk.metadata, Column("name", Text, nullable=False))
ticket_table = entity_table(
    "m2m_ticket", desk.metadata, Column("subject", Text, nullable=False)
)
ticket_label = Table(
    "m2m_ticket_label",
    desk.metadata,
    Column("ticket_id", Text, ForeignKey("m2m_ticket.id"), primary_key=True),
    Column("label_id", Text, ForeignKey("m2m_label.id"), primary_key=True),
)
map_aggregates(
    desk,
    {Label: label_table, Ticket: ticket_table},
    relations={
        Ticket: {
            "labels": TableRelation.many_to_many(
                Label, through=ticket_label, this_key="ticket_id", related_key="label_id"
            )
        }
    },
)


@pytest.fixture
def store() -> Repository:
    database = Database("sqlite://")
    desk.metadata.create_all(database.engine)
    return Repository(database)


@pytest.fixture
def labels(store: Repository) -> dict[str, Label]:
    named = {name: Label(name=name) for name in ("urgent", "billing", "network")}
    with store.context() as repository:
        repository.save(list(named.values()))
    return named


def pairs(store: Repository, ticket: Ticket) -> set[str]:
    with store.database.engine.connect() as connection:
        statement = select(ticket_label.c.label_id).where(
            ticket_label.c.ticket_id == ticket.id
        )
        return set(connection.scalars(statement))


def ids(*labels: Label) -> set[str]:
    return {one.id for one in labels}


def a_ticket_with(store: Repository, *labels: Label) -> Ticket:
    ticket = Ticket(subject="the printer is on fire", labels=list(labels))
    with store.context() as repository:
        repository.save(ticket)
    return ticket


# ---------------------------------------------------------------------------------------------
# The repository: pairs written, labels never
# ---------------------------------------------------------------------------------------------


def test_a_new_ticket_is_stored_with_the_pairs_of_its_labels(store, labels):
    ticket = a_ticket_with(store, labels["urgent"], labels["billing"])

    assert pairs(store, ticket) == ids(labels["urgent"], labels["billing"])


def test_assigning_other_labels_over_a_whole_reading_replaces_the_pairs(store, labels):
    ticket = a_ticket_with(store, labels["urgent"], labels["billing"])

    with store.context() as repository:
        stored = repository.get(Ticket, ticket.id)
        assert stored is not None
        stored.labels = [labels["billing"], labels["network"]]
        repository.save(stored)

    assert pairs(store, ticket) == ids(labels["billing"], labels["network"])


def test_changing_the_pairs_raises_the_ticket_version(store, labels):
    ticket = a_ticket_with(store, labels["urgent"])

    with store.context() as repository:
        stored = repository.get(Ticket, ticket.id)
        assert stored is not None
        before = stored.version
        stored.labels = []
        repository.save(stored)

    with store.context() as repository:
        after = repository.get(Ticket, ticket.id)
        assert after is not None and after.version == before + 1
    assert pairs(store, ticket) == set()


def test_saving_a_ticket_never_writes_the_labels_it_carries(store, labels):
    ticket = Ticket(subject="vpn down", labels=[labels["network"]])
    labels["network"].name = "renamed in memory"
    with store.context() as repository:
        repository.save(ticket)

    with store.context() as repository:
        label = repository.get(Label, labels["network"].id)
        assert label is not None and label.name == "network"


def test_a_blind_assignment_links_what_it_holds_and_unlinks_nothing(store, labels):
    ticket = a_ticket_with(store, labels["urgent"])

    with store.context() as repository:
        stored = repository.get(Ticket, ticket.id)
    assert stored is not None
    stored.labels = [
        labels["billing"]
    ]  # outside the unit of work: what it replaced is unknown
    with store.context() as repository:
        repository.save(stored)

    assert pairs(store, ticket) == ids(labels["urgent"], labels["billing"])


def test_a_pair_another_writer_linked_meanwhile_is_not_inserted_twice(store, labels):
    ticket = a_ticket_with(store)
    with store.context() as repository:
        stored = repository.get(Ticket, ticket.id)
        assert stored is not None
        with store.database.engine.begin() as connection:
            connection.execute(
                ticket_label.insert().values(
                    ticket_id=ticket.id, label_id=labels["urgent"].id
                )
            )
        stored.labels = [labels["urgent"]]
        repository.save(stored)

    assert pairs(store, ticket) == ids(labels["urgent"])


def test_removing_a_ticket_takes_its_pairs_and_leaves_the_labels(store, labels):
    ticket = a_ticket_with(store, labels["urgent"], labels["billing"])

    with store.context() as repository:
        stored = repository.get(Ticket, ticket.id)
        assert stored is not None
        repository.remove(stored)

    assert pairs(store, ticket) == set()
    with store.context() as repository:
        assert repository.get(Label, labels["urgent"].id) is not None


# ---------------------------------------------------------------------------------------------
# The form: labels named by id
# ---------------------------------------------------------------------------------------------


class ResponseEditTicket(ResponseEdit):
    ticket: Ticket


class CommandEditTicket(Edit[Ticket, ResponseEditTicket]):
    pass


def a_help_desk(store: Repository) -> UseFramework:
    bus = UseFramework("help-desk", log_after_execution=False)
    bus.add_dependency("repository", store)
    bus.store_context_in(InMemoryContexts())

    @bus.feature(CommandEditTicket)
    class TicketForms(EntityEdits[Ticket]):
        pass

    return bus


def saved(bus: UseFramework, **sent) -> ResponseEditTicket:
    answer = bus(CommandEditTicket(action=EditAction.SAVE, **sent), ResponseEditTicket)
    assert answer is not None
    return answer


def test_a_form_names_the_labels_by_id_and_the_save_stores_the_pairs(store, labels):
    bus = a_help_desk(store)

    answer = saved(
        bus,
        values={"subject": "no wifi", "labels": [labels["urgent"].id, labels["network"].id]},
    )

    assert [one.name for one in answer.ticket.labels] == ["urgent", "network"]
    assert pairs(store, answer.ticket) == ids(labels["urgent"], labels["network"])


def test_a_form_that_drops_a_label_unlinks_it(store, labels):
    ticket = a_ticket_with(store, labels["urgent"], labels["billing"])
    bus = a_help_desk(store)

    saved(bus, id=ticket.id, values={"labels": [{"id": labels["billing"].id}]})

    assert pairs(store, ticket) == ids(labels["billing"])


def test_a_label_that_does_not_exist_is_answered_pointing_at_it(store, labels):
    bus = a_help_desk(store)

    with pytest.raises(ddd_exceptions.InvalidValue) as refused:
        saved(bus, values={"subject": "x", "labels": [labels["urgent"].id, "no-such-label"]})

    [error] = problem_of(refused.value).errors
    assert error.pointer == "#/values/labels/1"
    assert "Label no-such-label does not exist" in error.detail


def test_a_label_is_never_created_from_the_ticket_form(store, labels):
    bus = a_help_desk(store)

    with pytest.raises(ddd_exceptions.InvalidValue, match="never creates one") as refused:
        saved(bus, values={"subject": "x", "labels": [{"name": "brand new"}]})

    assert problem_of(refused.value).errors[0].pointer == "#/values/labels/0"


def test_labels_kept_in_a_draft_are_stored_when_it_is_saved(store, labels):
    ticket = a_ticket_with(store, labels["urgent"])
    bus = a_help_desk(store)

    with bus.context({"user_id": "ana"}):
        draft = bus(
            CommandEditTicket(id=ticket.id, values={"labels": [labels["network"].id]}),
            ResponseEditTicket,
        )
        assert draft is not None and draft.draft is not None
        assert pairs(store, ticket) == ids(labels["urgent"])
        saved(bus, id=ticket.id, version=draft.draft.version)

    assert pairs(store, ticket) == ids(labels["network"])


def test_the_labels_a_ticket_references_carry_no_form_state_of_their_own(store, labels):
    bus = a_help_desk(store)

    answer = saved(bus, values={"subject": "x", "labels": [labels["urgent"].id]})

    assert answer.states["labels"].items == {}


def test_a_label_never_stored_is_refused_before_any_pair_is_written(store, labels):
    ticket = Ticket(subject="x", labels=[Label(name="brand new")])

    with pytest.raises(exceptions.ProgrammingError, match="which is not stored"):
        with store.context() as repository:
            repository.save(ticket)

    with store.context() as repository:
        assert repository.get(Ticket, ticket.id) is None


def test_a_label_saved_in_the_same_call_is_linked(store, labels):
    fresh = Label(name="brand new")
    ticket = Ticket(subject="x", labels=[fresh])

    with store.context() as repository:
        repository.save([fresh, ticket])

    assert pairs(store, ticket) == ids(fresh)


def test_the_relation_answers_the_domain_through_its_contract():
    labels_attribute = relation_attribute(Ticket, "labels")

    assert isinstance(labels_attribute, RelationAttribute)
    assert labels_attribute.named_by_id() is True
    assert relation_attribute(Ticket, "subject") is None


def test_a_relation_kind_is_read_from_its_word_and_an_unknown_one_is_refused():
    declared = TableRelation(
        RelationKind.MANY_TO_MANY, Label, "ticket_id", through=ticket_label
    )

    assert TableRelation("many_to_many", Label, "ticket_id").kind is declared.kind
    with pytest.raises(ValueError, match="'graph' is not a valid RelationKind"):
        TableRelation("graph", Label)
