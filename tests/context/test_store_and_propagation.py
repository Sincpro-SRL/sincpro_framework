"""Contexts kept by name outside the process — N of them, in memory or over any `KeyValueStore` — and
the context written on a transport's headers and read back (PRD_22 §4.5-§4.7)."""

import sys
import threading
import time
import types
from datetime import timedelta
from enum import IntEnum
from typing import TypedDict

import pytest
from pydantic import Secret
from structlog.testing import capture_logs

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.context import (
    Context,
    ContextKey,
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
    unbaggaged,
)
from sincpro_framework.context.infrastructure.shared import SharedValues
from sincpro_framework.context.services.propagation import inject
from sincpro_framework.data_layer.store import InMemoryKeyValue


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
    store.save("tenant:acme", {"lang": "es", "plan": "pro"})
    store.save("session:s1", {"user_id": "ana"})
    store.save("tenant:acme", {"lang": "es", "plan": "enterprise"})

    assert store.load("tenant:acme") == {"lang": "es", "plan": "enterprise"}
    assert store.load("session:s1") == {"user_id": "ana"}
    assert (store.version("tenant:acme"), store.version("session:s1")) == (2, 1)
    assert store.load("nothing") is None and store.version("nothing") == 0
    store.delete("session:s1")
    assert store.load("session:s1") is None


def test_a_context_kept_for_a_while_is_gone_after_it():
    store = InMemoryContexts()
    store.save("flow:1", {"step": 1}, ttl=timedelta(milliseconds=20))
    time.sleep(0.05)

    assert store.load("flow:1") is None


def test_a_context_kept_for_a_while_leaves_no_version_behind():
    key_values = InMemoryKeyValue()
    store = KeyValueContexts(key_values)
    threads = [
        threading.Thread(
            target=store.save, args=("node:01J9", {"step": n}, timedelta(milliseconds=20))
        )
        for n in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert store.version("node:01J9") == 8
    time.sleep(0.05)
    assert store.version("node:01J9") == 0
    assert key_values.get("sincpro:context:node:01J9:version") is None


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
    use_context().root.share(shared, "process:billing", every=timedelta(0))
    use_context().root.set("maintenance", True)

    another_replica = shared.load("process:billing")

    assert another_replica == {"maintenance": True}
    shared.save("process:billing", {"maintenance": False, "banner": "v2"})
    assert use_context().root["banner"] == "v2"
    assert use_context().root["maintenance"] is False


def test_two_replicas_writing_two_names_keep_both(store: ContextStore):
    first = SharedValues(store, "process:billing", timedelta(0), seed={"lang": "es"})
    second = SharedValues(store, "process:billing", timedelta(hours=1))

    first.set("plan", "pro")
    second.set("maintenance", True)
    second.unset("lang")

    assert store.load("process:billing") == {"plan": "pro", "maintenance": True}
    assert dict(first.current) == {"plan": "pro", "maintenance": True}


def test_a_default_a_later_release_brings_reaches_the_store_and_a_saved_value_wins(
    store: ContextStore,
):
    SharedValues(store, "process:billing", timedelta(0), seed={"lang": "es"}).set(
        "lang", "en"
    )

    released = SharedValues(
        store, "process:billing", timedelta(0), seed={"lang": "es", "beta": True}
    )

    assert dict(released.current) == {"lang": "en", "beta": True}
    assert store.load("process:billing") == {"lang": "en", "beta": True}


def test_a_value_that_does_not_travel_stays_in_its_process_after_another_replica_writes():
    store = KeyValueContexts(InMemoryKeyValue())
    settings = object()
    first = SharedValues(store, "process:billing", timedelta(0), seed={"lang": "es"})
    first.set("settings", settings)

    SharedValues(store, "process:billing", timedelta(0)).set("plan", "pro")

    assert first.current["settings"] is settings
    assert first.current["plan"] == "pro"
    assert store.load("process:billing") == {"lang": "es", "plan": "pro"}


def test_a_change_lands_on_what_another_replica_wrote_between_the_read_and_the_write():
    store = KeyValueContexts(InMemoryKeyValue())
    store.save("process:billing", {"lang": "es"})
    calls = []

    def changing(held: dict) -> dict:
        if not calls:
            store.save("process:billing", {**held, "maintenance": True})
        calls.append(held)
        return {**held, "plan": "pro"}

    changed = store.change("process:billing", changing)

    assert changed == {"lang": "es", "maintenance": True, "plan": "pro"}
    assert store.load("process:billing") == changed
    assert len(calls) == 2


def test_a_change_that_changes_nothing_keeps_the_version(store: ContextStore):
    store.save("process:billing", {"lang": "es"})

    store.change("process:billing", lambda held: {**held, "lang": "es"})

    assert store.version("process:billing") == 1


def test_a_store_of_a_projects_own_changes_with_a_read_and_a_save():
    class ProjectContexts(ContextStore):
        def __init__(self) -> None:
            self.saved: dict[str, dict] = {}

        def save(self, key, values, ttl=None):
            self.saved[key] = dict(values)

        def load(self, key):
            return self.saved.get(key)

        def delete(self, key):
            self.saved.pop(key, None)

        def version(self, key):
            return 0

    store = ProjectContexts()

    assert store.change("tenant:acme", lambda held: {**held, "lang": "es"}) == {"lang": "es"}
    assert store.load("tenant:acme") == {"lang": "es"}


def test_only_the_process_level_is_shared():
    bus = UseFramework("context-share-refused", log_after_execution=False)
    with bus.context({"tenant_id": "acme"}):
        with pytest.raises(TypeError, match="only the process level"):
            use_context().share(InMemoryContexts(), "process:billing")


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
    assert back[ContextKey.CAUSATION_ID] == sender.execution.execution_id  # type: ignore[union-attr]
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


def test_a_pickled_value_whose_class_the_reader_lacks_is_left_out_and_the_rest_is_read():
    module = types.ModuleType("only_in_the_writer")

    class Order:
        def __init__(self, number: str) -> None:
            self.number = number

    Order.__module__ = module.__name__
    Order.__qualname__ = "Order"
    module.Order = Order  # type: ignore[attr-defined]
    sys.modules[module.__name__] = module
    try:
        data = PickleCodec().dumps({"order": Order("F-9"), "env": Environment.TEST})
        assert PickleCodec().loads(data)["order"].number == "F-9"  # the reader has the code
    finally:
        del sys.modules[module.__name__]

    with capture_logs() as logs:
        back = PickleCodec().loads(data)  # the reader lacks it

    assert back == {"env": Environment.TEST}
    assert any("'order' could not be read here" in line["event"] for line in logs)


def test_a_value_that_cannot_be_pickled_stays_in_its_process():
    back = PickleCodec().loads(PickleCodec().dumps({"lock": threading.Lock(), "n": 1}))

    assert back == {"n": 1}
