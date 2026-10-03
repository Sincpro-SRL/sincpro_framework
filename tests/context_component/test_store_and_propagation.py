"""Contexts kept by name outside the process — N of them, in memory or over any `KeyValueStore` — and
the context written on a transport's headers and read back (PRD_22 §4.5-§4.7)."""

import time
from datetime import timedelta
from enum import IntEnum
from typing import TypedDict

import pytest
from pydantic import Secret

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.caching import InMemoryKeyValue
from sincpro_framework.context import (
    CAUSATION_ID,
    CORRELATION_ID,
    Context,
    ContextStore,
    InMemoryContexts,
    KeyValueContexts,
    PickleCodec,
    PlainCodec,
    TypedCodec,
    use_context,
)
from sincpro_framework.context.adapters.propagation import (
    BAGGAGE_HEADER,
    CONTEXT_HEADER,
    extract,
    inject,
    unbaggaged,
)


class Environment(IntEnum):
    PRODUCTION = 1
    TEST = 2


class SiatContext(TypedDict, total=False):
    TOKEN: Secret[str]
    SIAT_ENV: Environment
    tenant_id: str


class CommandWork(DataTransferObject):
    pass


def _bus(name: str, seen: list[Context]) -> UseFramework:
    bus = UseFramework(name, log_after_execution=False)

    @bus.feature(CommandWork)
    class Work(Feature):
        def execute(self, dto: CommandWork) -> None:
            seen.append(use_context())

    return bus


@pytest.fixture(params=["memory", "key_value"])
def store(request) -> ContextStore:
    return (
        InMemoryContexts()
        if request.param == "memory"
        else KeyValueContexts(InMemoryKeyValue())
    )


# --- a store ------------------------------------------------------------------------------------


def test_n_contexts_kept_by_name_with_their_versions(store: ContextStore):
    store.keep("tenant:acme", {"lang": "es", "plan": "pro"})
    store.keep("session:s1", {"user_id": "ana"})
    store.keep("tenant:acme", {"lang": "es", "plan": "enterprise"})

    assert store.restore("tenant:acme") == {"lang": "es", "plan": "enterprise"}
    assert store.restore("session:s1") == {"user_id": "ana"}
    assert (store.version("tenant:acme"), store.version("session:s1")) == (2, 1)
    assert store.restore("nothing") is None and store.version("nothing") == 0
    store.forget("session:s1")
    assert store.restore("session:s1") is None


def test_a_context_kept_for_a_while_is_gone_after_it():
    store = InMemoryContexts()
    store.keep("flow:1", {"step": 1}, ttl=timedelta(milliseconds=20))
    time.sleep(0.05)

    assert store.restore("flow:1") is None


def test_scopes_restore_several_contexts_in_order_the_last_winning(store: ContextStore):
    seen: list[Context] = []
    bus = _bus("context-store-restore", seen)
    store.keep("tenant:acme", {"lang": "es", "tz": "America/La_Paz"})
    store.keep("session:s1", {"user_id": "ana", "lang": "en"})

    with bus.context({"plan": "pro"}, restore=["tenant:acme", "session:s1"], store=store):
        bus(CommandWork())

    assert {key: seen[0][key] for key in ("lang", "tz", "user_id", "plan")} == {
        "lang": "en",
        "tz": "America/La_Paz",
        "user_id": "ana",
        "plan": "pro",
    }


def test_a_flow_kept_is_resumed_elsewhere_in_its_chain(store: ContextStore):
    seen: list[Context] = []
    bus = _bus("context-store-keep", seen)
    bus.context_store(store)

    with bus.context({"tenant_id": "acme", CORRELATION_ID: "flow-7"}, keep_as="sale-77"):
        pass
    with bus.context(restore="sale-77"):
        bus(CommandWork())

    resumed = seen[0]
    assert resumed["tenant_id"] == "acme"
    assert resumed.execution is not None and resumed.execution.correlation_id == "flow-7"


def test_the_store_is_found_where_it_was_set():
    seen: list[Context] = []
    bus = _bus("context-store-found", seen)
    process_store = InMemoryContexts()
    process_store.keep("tenant:acme", {"lang": "es"})
    use_context().root.set(ContextStore, process_store)

    with bus.context(restore="tenant:acme"):
        bus(CommandWork())

    assert seen[0]["lang"] == "es"


def test_a_restore_with_no_store_anywhere_says_where_to_set_one():
    bus = UseFramework("context-store-missing", log_after_execution=False)
    with pytest.raises(LookupError, match="ContextStore"):
        with bus.context(restore="anything"):
            pass


# --- the codecs ---------------------------------------------------------------------------------


def test_a_typed_codec_gives_every_value_its_type_back():
    codec = TypedCodec(SiatContext)
    data = codec.dumps({"TOKEN": Secret("abc"), "SIAT_ENV": Environment.TEST, "extra": 1})
    back = codec.loads(data)

    assert back["SIAT_ENV"] is Environment.TEST
    assert isinstance(back["TOKEN"], Secret) and back["TOKEN"].get_secret_value() == "abc"
    assert "extra" not in back


def test_plain_and_pickle_codecs():
    plain = PlainCodec().loads(PlainCodec().dumps({"tenant_ids": ("a", "b"), "x": object()}))
    pickled = PickleCodec().loads(PickleCodec().dumps({"env": Environment.TEST}))

    assert plain == {"tenant_ids": ["a", "b"]}
    assert pickled["env"] is Environment.TEST


# --- the process level, shared by replicas ------------------------------------------------------


def test_the_process_level_is_shared_by_every_replica_through_a_store():
    shared = KeyValueContexts(InMemoryKeyValue())
    use_context().root.share(shared, every=timedelta(0))
    use_context().root.set("maintenance", True)

    another_replica = shared.restore("process")

    assert another_replica == {"maintenance": True}
    shared.keep("process", {"maintenance": False, "banner": "v2"})
    assert use_context().root["banner"] == "v2"
    assert use_context().root["maintenance"] is False


def test_only_the_process_level_is_shared():
    bus = UseFramework("context-share-refused", log_after_execution=False)
    with bus.context({"tenant_id": "acme"}):
        with pytest.raises(TypeError, match="only the process level"):
            use_context().share(InMemoryContexts())


# --- headers ------------------------------------------------------------------------------------


def test_what_is_injected_is_extracted_the_same():
    bus = UseFramework("context-propagation", log_after_execution=False)
    sent: list[tuple[dict[str, str], Context]] = []

    @bus.feature(CommandWork)
    class Send(Feature):
        def execute(self, dto: CommandWork) -> None:
            sent.append((inject(use_context()), use_context()))

    with bus.context(
        {"tenant_id": "acme", "tenant_ids": ["acme", "beta"], "TOKEN": Secret("x")}
    ):
        bus(CommandWork())

    headers, sender = sent[0]
    back = extract(headers)
    assert back["tenant_id"] == "acme" and back["tenant_ids"] == ["acme", "beta"]
    assert back[CAUSATION_ID] == sender.execution.execution_id  # type: ignore[union-attr]
    assert back["TOKEN"] == "x"
    assert unbaggaged(headers[BAGGAGE_HEADER])["tenant_id"] == "acme"


def test_a_frontend_sends_back_what_it_was_given():
    found = extract({"Sincpro-Context": '{"lang": "en", "tenant_id": "acme"}'})

    assert found == {"lang": "en", "tenant_id": "acme"}


def test_baggage_is_read_under_the_frameworks_own_header():
    found = extract(
        {BAGGAGE_HEADER: "lang=es,tenant_id=acme;ttl=10", CONTEXT_HEADER: '{"lang": "en"}'}
    )

    assert found == {"lang": "en", "tenant_id": "acme"}
