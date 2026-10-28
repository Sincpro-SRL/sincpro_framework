"""A category is named once, in its enum, and every aggregate that uses it says the same words.

A select needs what each value is called, and a field of an enum type needs a label: written on
every entity that uses the enum, the same words drift apart. The enum answers both — its own
name with `translations()`, as an entity does, and each value's with `choice_translations()` —
and a field's own `label` still wins where the field means something more specific.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

import pytest

from sincpro_framework.ddd import MultiLanguage, exceptions
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.entity_meta import describe_class


class Priority(StrEnum):
    LOW = "low"
    HIGH = "high"

    @classmethod
    def translations(cls) -> MultiLanguage:
        return MultiLanguage("Priority", es="Prioridad")

    @classmethod
    def choice_translations(cls) -> dict[str, MultiLanguage]:
        return {
            cls.LOW: MultiLanguage("Low", es="Baja"),
            cls.HIGH: MultiLanguage("High", es="Alta"),
        }


class Channel(StrEnum):
    """Says nothing about itself: a select of raw values, as before."""

    EMAIL = "email"
    PHONE = "phone"


@dataclass
class Ticket(Entity):
    priority: Priority = Priority.LOW
    escalation: Priority | None = None
    customer_priority: Priority = field(
        default=Priority.LOW,
        metadata={"label": {"default": "Customer priority", "es": "Prioridad del cliente"}},
    )
    channel: Channel = Channel.EMAIL


def test_a_field_that_says_nothing_is_called_what_its_enum_calls_itself():
    fields = describe_class(Ticket, "id").fields

    assert fields["priority"].label == {"default": "Priority", "es": "Prioridad"}
    assert fields["escalation"].label == {"default": "Priority", "es": "Prioridad"}


def test_the_field_own_label_wins_over_its_enum():
    label = describe_class(Ticket, "id").fields["customer_priority"].label

    assert label == {"default": "Customer priority", "es": "Prioridad del cliente"}


def test_every_field_of_the_enum_carries_what_each_value_is_called():
    fields = describe_class(Ticket, "id").fields
    expected = {
        "low": {"default": "Low", "es": "Baja"},
        "high": {"default": "High", "es": "Alta"},
    }

    assert fields["priority"].choices == ["low", "high"]
    assert fields["priority"].choice_labels == expected
    assert fields["customer_priority"].choice_labels == expected
    assert (
        describe_class(Ticket, "id").model_dump(mode="json")["fields"]["priority"][
            "choice_labels"
        ]
        == expected
    )


def test_an_enum_that_says_nothing_publishes_its_values_alone():
    channel = describe_class(Ticket, "id").fields["channel"]

    assert channel.choices == ["email", "phone"]
    assert channel.label == {} and channel.choice_labels == {}


def test_the_labels_follow_the_order_the_enum_declares_its_values_in():
    class Stage(StrEnum):
        DRAFT = "draft"
        REVIEW = "review"
        APPROVED = "approved"

        @classmethod
        def choice_translations(cls) -> dict[str, Mapping[str, str]]:
            return {
                cls.APPROVED: {"default": "Approved"},
                cls.DRAFT: {"default": "Draft"},
                cls.REVIEW: {"default": "Review"},
            }

    @dataclass
    class Document(Entity):
        stage: Stage = Stage.DRAFT

    stage = describe_class(Document, "id").fields["stage"]

    assert stage.choices == ["draft", "review", "approved"]
    assert list(stage.choice_labels) == stage.choices


def test_a_value_the_enum_does_not_have_is_refused_when_described():
    class Typo(StrEnum):
        OPEN = "open"

        @classmethod
        def choice_translations(cls) -> dict[str, Mapping[str, str]]:
            return {"opne": {"default": "Open"}}

    @dataclass
    class Case(Entity):
        state: Typo = Typo.OPEN

    with pytest.raises(exceptions.ProgrammingError, match="'opne'"):
        describe_class(Case, "id")
