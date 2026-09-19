"""What an `Entity` brings, and the id it mints.

The id is the part that has to be right on every interpreter: version seven, time-ordered, and
the same shape whether the standard library had it or the fallback made it. The rest is the
convention a subclass inherits without ceremony — keyword-only defaults that leave its own
fields positional.
"""

import uuid
from dataclasses import dataclass, field, fields
from decimal import Decimal

import pytest

from sincpro_framework.ddd import entity as entity_module
from sincpro_framework.ddd.entity import Entity, Translated, new_entity_id, uuid7
from sincpro_framework.ddd.entity.entity_collection import identity_of


@dataclass
class Account(Entity):
    code: str
    balance: Decimal = Decimal("0.00")


@dataclass
class Invoice(Entity):
    number: str = field(metadata={"label": {"default": "Number", "es": "Número"}})
    total: int = field(default=0, metadata={"label": {"default": "Total"}})

    @classmethod
    def translations(cls) -> Translated:
        return {"default": "Invoice", "es": "Factura"}


def test_the_class_answers_its_own_name_in_every_language():
    assert Invoice.translations() == {"default": "Invoice", "es": "Factura"}
    assert Entity.translations() == {"default": "Entity"}


def test_a_subclass_declares_its_own_fields_positionally():
    invoice = Invoice("F-001", 10)

    assert invoice.number == "F-001"
    assert invoice.is_new and invoice.version == 0
    assert invoice.updated_at is None
    assert invoice.created_at.tzinfo is not None


def test_the_identity_is_still_the_first_field():
    assert fields(Invoice)[0].name == "id"
    invoice = Invoice("F-003")
    assert identity_of(invoice) == invoice.id


def test_an_id_can_be_handed_in():
    assert Invoice("F-004", id="inv_custom").id == "inv_custom"


def test_the_minted_id_is_a_version_seven_uuid():
    minted = uuid.UUID(hex=new_entity_id())

    assert minted.version == 7
    assert minted.variant == uuid.RFC_4122
    assert len(new_entity_id()) == 32


def test_ids_minted_in_order_sort_in_order():
    """The property the default ordering leans on: newest first is highest id first."""
    minted = [new_entity_id() for _ in range(50)]

    assert minted == sorted(minted)  # uuid7: minted in order is sorted in order
    assert len(set(minted)) == 50


@pytest.mark.parametrize("native", [True, False])
def test_the_fallback_and_the_native_generator_agree_on_the_shape(monkeypatch, native):
    """Python 3.12 has no `uuid.uuid7`; the fallback is what runs there."""
    if not native:
        monkeypatch.delattr(uuid, "uuid7", raising=False)
    elif not hasattr(uuid, "uuid7"):
        pytest.skip("this interpreter has no native uuid7")

    minted = entity_module.uuid7()

    assert minted.version == 7
    assert minted.variant == uuid.RFC_4122
    assert isinstance(uuid7(), uuid.UUID)


def test_as_json_round_trips_decimal_and_datetime():
    account = Account(code="110", balance=Decimal("42.75"))

    rebuilt = Account.from_json(account.as_json())

    assert rebuilt == account
    assert rebuilt.balance == Decimal("42.75")
    assert rebuilt.created_at == account.created_at


def test_from_json_accepts_an_already_parsed_dict():
    account = Account(code="110", balance=Decimal("42.75"))

    rebuilt = Account.from_json(
        {
            "id": account.id,
            "created_at": account.created_at.isoformat(),
            "updated_at": None,
            "version": 0,
            "code": "110",
            "balance": "42.75",
        }
    )

    assert rebuilt == account
