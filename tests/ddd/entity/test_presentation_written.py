"""A form hint's condition written as Python over the entity — `s.state != State.DRAFT` — read from
the lambda's syntax tree, never run: the same hint as the one built with `Condition.on`."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

import pytest

from sincpro_framework.ddd import Condition, Entity, Presentation, When, exceptions
from sincpro_framework.ddd.criteria import All, Operator
from sincpro_framework.ddd.metamodel import model_of


class State(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


@dataclass
class Written(Entity):
    state: State = State.DRAFT
    total: Decimal = Decimal("0")
    delivery: date | None = None
    note: str = ""

    presentation = Presentation["Written"](
        readonly_when=lambda s: When(s.state != State.DRAFT, s.total, s.note),
        required_when=lambda s: [
            When(s.state == State.CONFIRMED and s.total > 100, s.delivery),
        ],
        visible_when=lambda s: When(s.delivery is not None, s.note),
    )


@dataclass
class Built(Entity):
    state: State = State.DRAFT
    total: Decimal = Decimal("0")
    delivery: date | None = None
    note: str = ""

    presentation = Presentation["Built"](
        readonly_when=lambda s: When(
            Condition.on(s.state, Operator.NE, State.DRAFT), s.total, s.note
        ),
        required_when=lambda s: [
            When(
                All(
                    all=[
                        Condition.on(s.state, Operator.EQ, State.CONFIRMED),
                        Condition.on(s.total, Operator.GT, 100),
                    ]
                ),
                s.delivery,
            )
        ],
        visible_when=lambda s: When(
            Condition.on(s.delivery, Operator.IS_NULL, False), s.note
        ),
    )


def test_a_condition_written_as_python_is_the_one_built_by_hand():
    written, built = model_of(Written).presented, model_of(Built).presented

    assert written.readonly_when == built.readonly_when
    assert written.required_when == built.required_when
    assert written.visible_when == built.visible_when


def test_a_python_condition_and_a_built_one_live_together():
    @dataclass
    class Mixed(Entity):
        state: State = State.DRAFT
        note: str = ""

        presentation = Presentation["Mixed"](
            readonly_when=lambda s: [
                When(s.state != State.DRAFT, s.note),
                When(Condition.on(s.note, Operator.EQ, "locked"), s.state),
            ],
        )

    hints = model_of(Mixed).presented.readonly_when

    assert hints["note"] == Condition(field="state", operator=Operator.NE, value=State.DRAFT)
    assert hints["state"] == Condition(field="note", value="locked")


def test_a_condition_over_a_field_the_entity_lacks_is_refused():
    @dataclass
    class Typo(Entity):
        state: State = State.DRAFT

        presentation = Presentation["Typo"](
            readonly_when=lambda s: When(s.stat != State.DRAFT, s.state),  # type: ignore[attr-defined]
        )

    with pytest.raises(exceptions.ProgrammingError, match="stat"):
        model_of(Typo)
