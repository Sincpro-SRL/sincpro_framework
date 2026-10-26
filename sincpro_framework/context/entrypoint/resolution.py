"""Where the service saves what it saves beside the contexts — leases, circuits, caches,
idempotency records — and the one line that puts it all on a store every replica shares.

    store_context_in(RedisKeyValue(redis))     the whole service: every bus with no store of its own
    store_context_in(KeyValueContexts(...))    the same, with a codec or a prefix of its own

**The context decides which store is used, by being handed one.** With nothing set, it is this
process's memory; `store_context_in` makes it a store the replicas share — the same as
`use_context().root.set(ContextStore, ...)`, read in one line. A bounded context with a store of
its own says so on its bus (`billing.store_context_in(...)`), and what it saves goes there
instead. The context's rule stands: sharing is said in code, never turned on by an environment
variable.

The components ask here, not each in its own way:

    current_store()             the running context's: the bus in play, else the process,
                                else memory
    process_store()             the process's, else memory
    key_value_store_of(store)   the `KeyValueStore` a `ContextStore` stands on — a store of a
                                project's own that has none is answered by this process's
                                memory, said once, never a failed call
"""

from sincpro_framework.context.adapters.stores import KeyValueContexts, in_memory_contexts
from sincpro_framework.context.domain import exceptions
from sincpro_framework.context.domain.store import ContextStore
from sincpro_framework.context.entrypoint.facade import use_context
from sincpro_framework.data_layer.store import KeyValueStore
from sincpro_framework.sincpro_logger import logger

_said: set[type] = set()


def store_context_in(store: KeyValueStore | ContextStore) -> ContextStore:
    """Save what the context saves, for the whole process, on `store` — a `KeyValueStore`
    (Redis, Valkey, Memcached, DynamoDB) or a `ContextStore` built with a codec of its own.
    Answers the store set.

        store_context_in(RedisKeyValue(redis))   ->  KeyValueContexts(RedisKeyValue(redis))

    A bus that set its own store (`bus.store_context_in(...)`) goes on using that one.
    """
    contexts = store if isinstance(store, ContextStore) else KeyValueContexts(store)
    use_context().root.set(ContextStore, contexts)
    return contexts


def process_store() -> ContextStore:
    """The process's store, else this process's memory."""
    found = use_context().root.get(ContextStore)
    return found if isinstance(found, ContextStore) else in_memory_contexts()


def current_store() -> ContextStore:
    """The store of the running context — the bus in play, else the process — else memory."""
    found = use_context().store()
    return found if found is not None else in_memory_contexts()


def key_value_store_of(store: ContextStore) -> KeyValueStore:
    """The `KeyValueStore` `store` stands on. A store of a project's own that has none is
    answered by this process's memory, said once — what is saved there is never a failed call.

        key_value_store_of(KeyValueContexts(RedisKeyValue(redis)))   ->  that RedisKeyValue
        key_value_store_of(InMemoryContexts())                       ->  its InMemoryKeyValue
        key_value_store_of(ProjectContexts())    # raises ProgrammingError for it
            ->  the process memory's InMemoryKeyValue, and one warning for ProjectContexts

    The warning is said once per store class, not per call."""
    try:
        return store.key_value_store()
    except exceptions.ProgrammingError:
        if type(store) not in _said:
            _said.add(type(store))
            logger.warning(
                f"{type(store).__name__} stands on no KeyValueStore: what the context saves "
                "beside it — leases, circuits, caches, idempotency — is saved in this process's "
                "memory; implement key_value_store() to share it"
            )
        return in_memory_contexts().key_value_store()
