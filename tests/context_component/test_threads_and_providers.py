"""Threads see the context they were started in; providers give what a use case needs; a schema types
what enters; a declared requirement refuses; a secret never travels (PRD_22 §4.1-§4.4)."""

import threading
from concurrent.futures import ThreadPoolExecutor
from enum import IntEnum
from typing import Any, TypedDict

import pytest
from pydantic import Secret

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.context import (
    Context,
    ContextExecutor,
    ContextThread,
    in_context,
    requires_context,
    use_context,
)
from sincpro_framework.exceptions import ContextRequired


class CommandWork(DataTransferObject):
    pass


class CommandSend(DataTransferObject):
    pass


def _bus(name: str, seen: list[Context]) -> UseFramework:
    bus = UseFramework(name, log_after_execution=False)

    @bus.feature(CommandWork)
    class Work(Feature):
        def execute(self, dto: CommandWork) -> None:
            seen.append(use_context())

    return bus


# --- threads ------------------------------------------------------------------------------------


def test_a_pool_hands_every_task_the_submitters_context():
    seen: list[Context] = []
    bus = _bus("context-threads-pool", seen)
    with bus.context({"tenant_id": "acme"}):
        with ContextExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: bus(CommandWork()), range(4)))

    assert [one["tenant_id"] for one in seen] == ["acme"] * 4


def test_a_bare_pool_loses_it_and_in_context_hands_it_on():
    seen: list[Context] = []
    bus = _bus("context-threads-wrapped", seen)
    with bus.context({"tenant_id": "acme"}):
        with ThreadPoolExecutor(max_workers=2) as pool:
            pool.submit(in_context(bus), CommandWork()).result()

    assert seen[0]["tenant_id"] == "acme"


def test_a_thread_starts_where_its_creator_stood():
    seen: list[Context] = []
    bus = _bus("context-threads-thread", seen)
    with bus.context({"tenant_id": "acme"}):
        thread = ContextThread(target=bus, args=(CommandWork(),))
        thread.start()
        thread.join()

    assert seen[0]["tenant_id"] == "acme"


def test_many_threads_writing_never_see_each_others_scope():
    bus = UseFramework("context-threads-many", log_after_execution=False)
    found: dict[str, Any] = {}
    barrier = threading.Barrier(8)

    class CommandStamp(DataTransferObject):
        who: str

    @bus.feature(CommandStamp)
    class Stamp(Feature):
        def execute(self, dto: CommandStamp) -> None:
            use_context().set("stamp", dto.who)
            barrier.wait(timeout=5)
            found[dto.who] = use_context()["stamp"]

    def run(who: str) -> None:
        with bus.context({"tenant_id": who}):
            bus(CommandStamp(who=who))

    threads = [threading.Thread(target=run, args=(str(index),)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert found == {str(index): str(index) for index in range(8)}


# --- providers, schema, requirements ------------------------------------------------------------


class Environment(IntEnum):
    PRODUCTION = 1
    TEST = 2


class SiatContext(TypedDict, total=False):
    TOKEN: str
    SIAT_ENV: Environment


def test_a_provider_gives_what_it_derives_once_per_scope():
    asked: list[str] = []
    sdk = UseFramework("context-providers", log_after_execution=False)
    seen: list[Context] = []

    @sdk.context_provider(needs=["nit_id"], gives=["TOKEN", "SIAT_ENV"])
    def credentials(context: Any) -> dict[str, Any]:
        asked.append(context["nit_id"])
        return {"TOKEN": f"token-of-{context['nit_id']}", "SIAT_ENV": Environment.TEST}

    @sdk.feature(CommandWork)
    class Work(Feature):
        def execute(self, dto: CommandWork) -> None:
            seen.append(use_context())

    @sdk.feature(CommandSend)
    class Send(Feature):
        def execute(self, dto: CommandSend) -> None:
            seen.append(use_context())
            sdk(CommandWork())

    with sdk.context({"nit_id": "N-1"}):
        sdk(CommandSend())
    sdk(CommandWork())

    send, nested, without = seen
    assert send["TOKEN"] == nested["TOKEN"] == "token-of-N-1"
    assert asked == ["N-1"]  # the nested execution found it on its parent
    assert "TOKEN" not in without  # nothing it needs: not asked


def test_a_schema_types_what_a_scope_is_opened_with():
    seen: list[Context] = []
    sdk = _bus("context-schema", seen)
    sdk.context_schema(SiatContext)

    with sdk.context({"TOKEN": "abc", "SIAT_ENV": 2, "tenant_id": "acme"}):
        sdk(CommandWork())

    assert seen[0]["SIAT_ENV"] is Environment.TEST
    assert seen[0]["tenant_id"] == "acme"


def test_a_declared_requirement_refuses_and_nothing_declared_is_never_checked():
    sdk = UseFramework("context-required", log_after_execution=False)
    ran: list[str] = []

    @sdk.feature(CommandSend)
    @requires_context("TOKEN", "SIAT_ENV")
    class Send(Feature):
        def execute(self, dto: CommandSend) -> None:
            ran.append("send")

    @sdk.feature(CommandWork)
    class Work(Feature):
        def execute(self, dto: CommandWork) -> None:
            ran.append("work")

    with pytest.raises(ContextRequired) as refused:
        with sdk.context({"TOKEN": "abc"}):
            sdk(CommandSend())
    sdk(CommandWork())
    with sdk.context({"TOKEN": "abc", "SIAT_ENV": Environment.TEST}):
        sdk(CommandSend())

    assert refused.value.missing == ["SIAT_ENV"]
    assert ran == ["work", "send"]


def test_a_secret_is_read_where_it_is_and_never_travels():
    seen: list[Context] = []
    sdk = _bus("context-secret", seen)

    with sdk.context({"TOKEN": Secret("abc"), "tenant_id": "acme"}):
        sdk(CommandWork())

    context = seen[0]
    assert context["TOKEN"].get_secret_value() == "abc"
    assert "abc" not in repr(context)
    assert context.to_client() == {"tenant_id": "acme"}


def test_an_async_call_keeps_what_it_writes_to_itself():
    """The audit's async isolation finding: a handler's write with no enclosing scope reached the
    next async call. Every call opens its own scope, async as sync."""
    import asyncio

    bus = UseFramework("context-async-isolation", log_after_execution=False)
    seen: list[Any] = []

    @bus.feature(CommandWork)
    class Work(Feature):
        def execute(self, dto: CommandWork) -> None:
            seen.append(self.context.get("leak"))
            self.context["leak"] = 1

    async def twice() -> None:
        await bus.get_async_bus()(CommandWork())
        await bus.get_async_bus()(CommandWork())

    asyncio.run(twice())
    bus(CommandWork())

    assert seen == [None, None, None]
