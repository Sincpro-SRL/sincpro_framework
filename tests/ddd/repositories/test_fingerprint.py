"""`Repository.fingerprint`: one key for every read that answers the same rows — whatever page it
asks for — and a different one the moment the rows could differ."""

from dataclasses import dataclass
from decimal import Decimal

from sincpro_framework.ddd import Criteria, Entity, MemoryRepository


@dataclass
class Line(Entity):
    journal: str = ""
    amount: Decimal = Decimal("0")
    quantity: int = 0


def _key(raw: dict) -> str:
    return MemoryRepository().fingerprint(Line, Criteria.model_validate(raw))


POSTED = {"field": "journal", "operator": "=", "value": "SAL"}
BIG = {"field": "quantity", "operator": ">", "value": 3}


def test_the_page_asked_for_is_not_part_of_the_key():
    assert _key({"where": POSTED, "pagination": {"limit": 10}}) == _key(
        {"where": POSTED, "pagination": {"limit": 500}}
    )


def test_a_different_filter_order_or_mask_is_a_different_key():
    base = _key({"where": POSTED})

    assert _key({"where": BIG}) != base
    assert (
        _key({"where": POSTED, "order": [{"field": "quantity", "descending": True}]}) != base
    )
    assert _key({"where": POSTED, "specification": {"journal": {}}}) != base


def test_values_are_read_as_their_fields_type_before_they_are_keyed():
    assert _key({"where": {"field": "quantity", "operator": ">", "value": "3"}}) == _key(
        {"where": BIG}
    )
    assert _key({"where": {"field": "amount", "operator": "=", "value": "1.0"}}) == _key(
        {"where": {"field": "amount", "operator": "=", "value": "1.00"}}
    )


def test_the_order_of_the_parts_of_an_all_does_not_change_the_key():
    assert _key({"where": {"all": [POSTED, BIG]}}) == _key({"where": {"all": [BIG, POSTED]}})
