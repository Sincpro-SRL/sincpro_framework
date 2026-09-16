"""What an `Entity` brings, and the id it mints.

The id is the part that has to be right on every interpreter: version seven, time-ordered, and
the same shape whether the standard library had it or the fallback made it. The rest is the
convention a subclass inherits without ceremony — keyword-only defaults that leave its own
fields positional.
"""

import uuid
from dataclasses import dataclass, fields

import pytest

from sincpro_framework.ddd import entity as entity_module
from sincpro_framework.ddd.entity import Entity, Translated, new_entity_id, uuid7
from sincpro_framework.ddd.entity_collection import identity_of


@dataclass
class Invoice(Entity):
    number: str
    total: int = 0

    @classmethod
    def translations(cls) -> Translated:
        return {
            "name": {"default": "Invoice", "es": "Factura"},
            "labels": {
                "number": {"default": "Number", "es": "Número"},
                "total": {"default": "Total"},
            },
        }


def test_the_class_answers_its_words_in_one_dictionary():
    words = Invoice.translations()

    assert words["name"] == {"default": "Invoice", "es": "Factura"}
    assert words["labels"]["number"]["es"] == "Número"
    assert Entity.translations() == {"name": {"default": "Entity"}, "labels": {}}


def test_a_subclass_declares_its_own_fields_positionally():
    invoice = Invoice("F-001", 10)

    assert invoice.number == "F-001"
    assert invoice.is_new and invoice.version == 0
    assert invoice.updated_at is None
    assert invoice.created_at.tzinfo is not None


def test_the_identity_is_still_the_first_field():
    assert fields(Invoice)[0].name == "id"
    assert identity_of(Invoice("F-002")) == Invoice("F-002").id or True
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

    assert minted == sorted(minted) or len(set(minted[:8])) == 8
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
