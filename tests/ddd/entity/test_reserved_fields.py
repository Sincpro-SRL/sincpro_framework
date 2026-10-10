"""What a subclass may not redeclare, refused when the class is declared — on every Python.

`Entity` writes `id`, `created_at`, `updated_at` and `version` itself, and a framework mixin
writes its own (`archived_at`, …): a subclass that redeclares one would see it taken over in
silence at the first save. A `DomainEvent` subclass that types `name` would stop routing. Both
are read while the class is created, which on Python 3.14 is before its annotations exist in
the class namespace (PEP 649) — so the checks read them the way 3.14 allows.
"""

from dataclasses import dataclass, field
from typing import ClassVar

import pytest

from sincpro_framework import ProgrammingError
from sincpro_framework.ddd import DomainEvent
from sincpro_framework.ddd.entity import ArchivableMixin, Entity


@pytest.mark.parametrize("reserved", ["id", "created_at", "updated_at", "version"])
def test_an_entity_field_named_like_one_the_framework_writes_is_refused(reserved):
    with pytest.raises(
        ProgrammingError, match=f"Spec.{reserved} redeclares Entity.{reserved}"
    ):
        type("Spec", (Entity,), {"__annotations__": {reserved: int}, reserved: 3})


def test_the_refusal_names_what_the_field_is_and_how_to_rename_it():
    with pytest.raises(
        ProgrammingError, match=r"\(the optimistic lock\)\. Rename it, e\.g\. `revision`"
    ):

        @dataclass
        class Spec(Entity):
            content: str = ""
            version: int = 3


def test_a_field_a_framework_mixin_writes_is_reserved_too():
    with pytest.raises(
        ProgrammingError, match="Folder.archived_at redeclares ArchivableMixin"
    ):

        @dataclass
        class Folder(ArchivableMixin, Entity):
            archived_at: str = ""  # pyright: ignore


def test_a_subclass_of_a_project_entity_may_restate_the_parents_own_field():
    @dataclass
    class Account(Entity):
        code: str = ""

    @dataclass
    class Ledger(Account):
        code: str = "1105"

    assert Ledger().code == "1105"


def test_a_class_attribute_named_like_nothing_reserved_is_not_a_field():
    @dataclass
    class Shelf(Entity):
        kind: ClassVar[str] = "shelf"
        label: str = ""

    assert Shelf.kind == "shelf"


def test_an_event_sets_its_envelope_defaults_as_the_framework_intends():
    @dataclass(kw_only=True)
    class InvoicePosted(DomainEvent):
        entity_type: str = "invoice"
        label: dict[str, str] = field(default_factory=lambda: {"default": "Invoice posted"})

    assert InvoicePosted().entity_type == "invoice"


def test_an_event_typing_name_is_refused_on_every_python():
    with pytest.raises(ProgrammingError, match="declares 'name' as a typed field"):

        @dataclass(kw_only=True)
        class InvoicePosted(DomainEvent):
            name: str = "billing.invoice.v1.posted"  # pyright: ignore


def test_an_event_assigning_name_routes_by_it():
    @dataclass(kw_only=True)
    class InvoicePosted(DomainEvent):
        name = "billing.invoice.v1.posted"

    assert InvoicePosted.name == "billing.invoice.v1.posted"


@dataclass
class Order(Entity):
    """Annotated with a class defined further down: on 3.14 reading the names while the class is
    created must not evaluate it."""

    lines: list["OrderLine"] = field(default_factory=list)


@dataclass
class OrderLine(Entity):
    qty: int = 1


def test_a_forward_reference_does_not_break_the_check():
    assert Order(lines=[OrderLine()]).lines[0].qty == 1
