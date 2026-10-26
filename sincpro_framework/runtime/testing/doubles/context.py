"""`fresh_context`: a context of its own for each test — what one test caches, shares or stores
never reaches the next.

    @pytest.fixture(autouse=True)
    def _context():
        with fresh_context():
            yield

Context: the process level, its store and everything stored on it — `bus.cache`,
`bus.idempotency`, drafts, sessions, leases — belong to the interpreter, so a test suite shares
them unless each test opens its own. A store of its own per test is what keeps them apart: every
cache and record is held per store.
"""

from collections.abc import Generator
from contextlib import contextmanager

from sincpro_framework.context import ContextStore, context_scopes, store_context_in
from sincpro_framework.data_layer.store import InMemoryKeyValue, KeyValueStore


@contextmanager
def fresh_context(
    store: KeyValueStore | ContextStore | None = None,
) -> Generator[ContextStore, None, None]:
    """The process level as a new interpreter has it, on `store` — a new memory by default —
    for the block; everything as it was once it ends.

        with fresh_context() as store:            a new InMemoryKeyValue, its own caches
            billing(CommandRate(currency="USD"))
        with fresh_context(RedisKeyValue(fakeredis.FakeRedis())):   a shared one, faked

    Answers the context store the block saves on. A bus that set a store of its own
    (`bus.store_context_in`) keeps it: build the bus inside the test to give it a new one."""
    with context_scopes.fresh_process():
        yield store_context_in(store if store is not None else InMemoryKeyValue())
