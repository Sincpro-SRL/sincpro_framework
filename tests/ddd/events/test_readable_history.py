"""A history a screen reads with no code of its own (#261, item 5): each event answers which one it
is (`name`), every field of the log — the envelope's and each event's own — has a label in the
contract, a grouping offers only what means something to a person, and a record's lifecycle facts
say who it was when they happened.
"""

from dataclasses import dataclass, field

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    ChangeTrackingMixin,
    DomainEvent,
    DomainEvents,
    Entity,
    EntityDeleted,
    EntityReads,
    EntityUpdated,
    ResponsePaginatedQuery,
)
from sincpro_framework.introspection import metamodel_of


@dataclass(kw_only=True)
class DuesEvent(DomainEvent):
    name = "dues.v1.event"


@dataclass(kw_only=True)
class PaymentRefused(DuesEvent):
    name = "dues.payment.v1.refused"
    reason: str = field(default="", metadata={"label": {"default": "Reason", "es": "Motivo"}})


@dataclass
class Member(ChangeTrackingMixin, Entity):
    name: str = ""
    code: str = field(default="", metadata={"groupable": False})


class ResponseMemberEvents(ResponsePaginatedQuery):
    events: list[DuesEvent]


class QueryMemberEvents(DomainEvents[Member, ResponseMemberEvents]):
    pass


def dues() -> tuple[UseFramework, MemoryRepository]:
    bus = UseFramework("dues", log_after_execution=False)
    repository = MemoryRepository()
    bus.add_dependency("repository", repository)

    @bus.feature(QueryMemberEvents)
    class MemberReads(EntityReads[Member]):
        pass

    return bus, repository


def test_each_event_answers_which_one_it_is():
    bus, repository = dues()
    member = Member(name="Ana Pérez")
    member.record(PaymentRefused(reason="blurry proof"))
    repository.save(member)

    page = bus(QueryMemberEvents(id=member.id), ResponseMemberEvents).model_dump(mode="json")

    [refused] = page["events"]
    assert (refused["name"], refused["reason"]) == ("dues.payment.v1.refused", "blurry proof")


def test_every_field_of_a_log_has_a_label_and_only_the_meaningful_ones_group():
    bus, repository = dues()
    member = Member(name="Ana Pérez")
    member.record(PaymentRefused(reason="blurry proof"))
    repository.save(member)

    model = metamodel_of(bus)["models"]["DuesEvent"]["properties"]
    said = {name: prop["x-sincpro"] for name, prop in model.items()}

    assert {name: one.get("label", {}).get("es") for name, one in said.items()} == {
        "id": "ID",
        "name": "Evento",
        "label": "Qué pasó",
        "created_at": "Cuándo",
        "updated_at": "Actualizado el",
        "version": "Versión",
        "entity_type": "Tipo de registro",
        "entity_id": "ID del registro",
        "correlation_id": "Correlación",
        "causation_id": "Causado por",
        "entity_version": "Versión del registro",
        "reason": "Motivo",  # an event's own field keeps the label its class gives it
    }
    assert sorted(name for name, one in said.items() if one.get("groupable")) == [
        "created_at",
        "entity_id",
        "entity_type",
        "name",
        "reason",
        "updated_at",
    ]


def test_a_field_of_the_projects_own_may_say_it_is_never_grouped_by():
    bus, _ = dues()

    member = metamodel_of(bus)["models"]["Member"]["properties"]

    assert member["name"]["x-sincpro"]["groupable"] is True
    assert "groupable" not in member["code"]["x-sincpro"]  # what says nothing is left out


def test_a_lifecycle_fact_says_who_the_record_was_when_it_happened():
    _, repository = dues()
    member = Member(name="Ana Pérez")
    repository.save(member)

    held = repository.get(Member, member.id)
    assert held is not None
    held.name = "Ana Pérez Soto"
    repository.save(held)
    repository.remove(held)

    updated, deleted = held.pull_events()
    assert isinstance(updated, EntityUpdated) and isinstance(deleted, EntityDeleted)
    assert (updated.entity_display, deleted.entity_display) == ("Ana Pérez Soto",) * 2
    assert deleted.label["es"] == "Se eliminó"
