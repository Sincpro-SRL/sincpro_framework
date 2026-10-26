"""`fresh_context()`: what one test caches, shares or stores never reaches the next."""

import fakeredis

from sincpro_framework import cache
from sincpro_framework.context import ContextStore, KeyValueContexts, use_context
from sincpro_framework.data_layer.store import RedisKeyValue
from sincpro_framework.runtime.testing import fresh_context


def test_what_one_block_caches_the_next_never_reads():
    asked: list[str] = []

    @cache
    def rate(currency: str) -> str:
        asked.append(currency)
        return "6.96"

    with fresh_context():
        rate("USD")
        rate("USD")
    with fresh_context():
        rate("USD")

    assert asked == ["USD", "USD"]


def test_the_process_is_put_back_as_it_was():
    before = use_context().root.get(ContextStore)
    use_context().root.set("lang", "es")

    with fresh_context() as store:
        inside = use_context().root.get(ContextStore)
        use_context().root.set("lang", "en")

    assert inside is store and use_context().root.get(ContextStore) is before
    assert use_context().root.get("lang") == "es"


def test_a_store_of_the_tests_own_is_the_one_saved_on():
    server = fakeredis.FakeServer()

    with fresh_context(RedisKeyValue(fakeredis.FakeRedis(server=server))) as store:
        use_context().root.set("maintenance", True)

    assert isinstance(store, KeyValueContexts)
