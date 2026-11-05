"""An entity compiled once into the model every reader takes its part from.

The cases are the sale of `tests/ddd/entity/test_checks.py`: a total computed from its lines, a
payment term proposed from the customer, a delivery the confirmed state requires, a product once
per sale. What the model works out — the lines a pass runs first, what each field's change reaches,
its fingerprint — is what a preview, a save and a client rely on.
"""

import subprocess
import sys
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pytest

from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Entity, check, compute
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.ddd.entity.entity_meta import FieldType
from sincpro_framework.ddd.metamodel import (
    StoreFacts,
    description_of,
    forget_models,
    model_of,
)
from tests.ddd.entity.test_checks import Sale, SaleLine

ROOT = Path(__file__).parents[3]


def test_a_model_is_compiled_once_and_the_same_after():
    assert model_of(Sale) is model_of(Sale)


def test_a_forgotten_model_is_compiled_again_with_the_same_fingerprint():
    first = model_of(Sale)

    forget_models(Sale)
    again = model_of(Sale)

    assert again is not first
    assert again.fingerprint == first.fingerprint


def test_the_model_knows_which_lines_a_pass_runs_first():
    assert model_of(Sale).owned == ("lines",)


def test_the_model_knows_what_each_change_reaches_and_what_judges_it():
    model = model_of(Sale)

    assert model.affects == {"customer": ("payment_term",), "lines": ("total",)}
    assert model.judged_by["state"] == ("delivery_when_confirmed",)
    assert model.judged_by["lines"] == ("each_product_once",)
    assert "notes" not in model.judged_by


def test_a_line_knows_what_its_own_changes_reach():
    assert model_of(SaleLine).affects == {
        "product": ("price", "amount"),
        "qty": ("amount",),
        "price": ("amount",),
    }


def test_the_fingerprint_is_the_same_in_another_process():
    script = (
        "from sincpro_framework.ddd.metamodel import model_of;"
        "from tests.ddd.entity.test_checks import Sale;"
        "print(model_of(Sale).fingerprint)"
    )
    other = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()

    assert other == model_of(Sale).fingerprint


def test_the_fingerprint_changes_when_what_the_entity_declares_changes():
    @dataclass
    class StrictSale(Sale):
        @check(lambda s: s.customer)
        def named_customer(self) -> None:
            if not self.customer:
                raise ddd_exceptions.InvalidValue("A sale names its customer")

    assert model_of(StrictSale).fingerprint != model_of(Sale).fingerprint


def test_a_declaration_that_names_no_field_fails_when_it_is_compiled():
    @dataclass
    class Broken(Entity):
        total: Decimal = Decimal("0")

        @compute(lambda b: b.totl, depends=lambda b: b.total)
        def total_of(self) -> Decimal:
            return self.total

    with pytest.raises(ddd_exceptions.ProgrammingError, match="names totl"):
        model_of(Broken)


def test_threads_asking_for_one_class_at_once_get_one_model():
    @dataclass
    class Crowded(Entity):
        name: str = ""

    seen = []
    start = threading.Barrier(8)

    def ask() -> None:
        start.wait()
        seen.append(model_of(Crowded))

    threads = [threading.Thread(target=ask) for _ in range(8)]
    for one in threads:
        one.start()
    for one in threads:
        one.join()

    assert len({id(one) for one in seen}) == 1


@dataclass
class Size:
    width: int
    height: int


@dataclass
class Parcel(Entity):
    label: str = ""
    size: Size | None = None
    sizes: list[Size] = field(default_factory=list)


def test_a_value_object_inside_the_record_is_described_with_its_shape():
    fields = MemoryRepository().definition(Parcel).fields

    assert fields["size"].type is FieldType.EMBEDDED
    assert fields["size"].definition is not None
    assert set(fields["size"].definition.fields) == {"width", "height"}
    assert fields["sizes"].many is True


def test_a_value_object_is_described_with_no_identity_of_its_own():
    assert description_of(Size).identity == ""
    assert description_of(Parcel, StoreFacts(identity="id")).identity == "id"
