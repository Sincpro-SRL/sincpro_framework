"""`Repository.fingerprint`: one key for every read that answers the same rows — whatever page it
asks for — and a different one the moment the rows could differ."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sincpro_framework.context.entrypoint.facade import carrying
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Criteria, Entity
from sincpro_framework.ddd.criteria import ResolutionContext
from sincpro_framework.ddd.repositories.fingerprint import fingerprint_of


@dataclass
class Line(Entity):
    journal: str = ""
    amount: Decimal = Decimal("0")
    quantity: int = 0
    posted: date = date(2026, 1, 1)
    owner: str = ""


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


def _on(day: int, raw: dict) -> str:
    resolution = ResolutionContext(now=datetime(2026, 10, day, 12, tzinfo=UTC))
    return fingerprint_of(Line, Criteria.model_validate(raw), resolution=resolution)


TODAY = {"field": "posted", "operator": "<", "value": {"$": "today"}}


def test_a_today_is_keyed_by_the_day_it_ran_and_not_by_the_word():
    assert _on(12, {"where": TODAY}) != _on(13, {"where": TODAY})


def test_a_today_is_the_same_key_as_the_date_it_stood_for():
    written = {"field": "posted", "operator": "<", "value": "2026-10-12"}

    assert _on(12, {"where": TODAY}) == _on(12, {"where": written})


def test_two_users_asking_for_their_own_never_share_a_key():
    mine = {"where": {"field": "owner", "value": {"$": "context", "key": "user_id"}}}

    with carrying({"user_id": "ana"}):
        ana = _key(mine)
    with carrying({"user_id": "beto"}):
        beto = _key(mine)

    assert ana != beto
