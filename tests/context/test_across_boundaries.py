"""The context crosses every boundary on the transport's side channel, never in the DTO — and the
keys the framework reads are the members of `ContextKey` (PRD_21 §5-§6, phases 3 and 4)."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.context import Execution, travelling
from sincpro_framework.context.adapters.propagation import CONTEXT_HEADER, read, written
from sincpro_framework.context.domain import keys
from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.context.infrastructure.tree import current_execution
from sincpro_framework.ddd import DomainEvent


class CommandLocal(DataTransferObject):
    pass


class CommandRemote(DataTransferObject):
    pass


@dataclass(kw_only=True)
class OrderShipped(DomainEvent):
    name = "tests.identity.order.v1.shipped"


def _inside_an_execution(work: Any) -> tuple[Any, Execution]:
    """What `work()` answered inside one execution of a bus, and that execution."""
    bus = UseFramework("identity-boundaries", log_after_execution=False)
    answered: list[tuple[Any, Execution]] = []

    @bus.feature(CommandLocal)
    class Local(Feature):
        def execute(self, dto: CommandLocal) -> None:
            answered.append((work(), current_execution()))  # type: ignore[arg-type]

    with bus.context({ContextKey.TENANT_ID: "acme"}):
        bus(CommandLocal())
    return answered[0]


# --- the entrypoints read the identity ---------------------------------------------------------


def test_json_rpc_reads_the_identity_headers():
    from sincpro_framework.entrypoints.infrastructure.http import merge_http_context

    merged = merge_http_context(
        {"X-Correlation-Id": "flow", "X-Causation-Id": "gateway", "X-Execution-Id": "req"}
    )

    assert merged == {
        ContextKey.CORRELATION_ID: "flow",
        ContextKey.CAUSATION_ID: "gateway",
        ContextKey.EXECUTION_ID: "req",
    }


def test_rest_reads_the_identity_headers():
    pytest.importorskip("fastapi")
    from starlette.requests import Request

    from sincpro_framework.entrypoints.adapters.fastapi.calling import request_context

    request = Request(
        {
            "type": "http",
            "headers": [(b"x-correlation-id", b"flow"), (b"x-causation-id", b"gateway")],
        }
    )
    found = asyncio.run(request_context(request)).bus_context()

    assert found == {ContextKey.CORRELATION_ID: "flow", ContextKey.CAUSATION_ID: "gateway"}


def test_grpc_reads_and_writes_the_identity_as_metadata():
    from sincpro_framework.common.transport.grpc import (
        context_from_metadata,
        metadata_from_context,
    )

    sent: dict[str, Any] = {
        ContextKey.CORRELATION_ID: "flow",
        ContextKey.CAUSATION_ID: "caller",
        ContextKey.TENANT_ID: "acme",
    }
    metadata = metadata_from_context(sent)

    assert ("x-causation-id", "caller") in metadata
    assert context_from_metadata(metadata) == sent


# --- a context hosted elsewhere -----------------------------------------------------------------


def test_a_remote_context_is_handed_the_callers_context_and_chain(monkeypatch):
    from sincpro_framework.remote_execution.domain.payload import Payload
    from sincpro_framework.remote_execution.mixins import hosted_context

    sent: dict[str, Any] = {}

    class Transport:
        def execute(self, name: str, dto: Any, request: Any) -> Payload:
            sent.update(request)
            return Payload(type="")

    monkeypatch.setattr(hosted_context, "transport_for", lambda hosted_at: Transport())
    billing = UseFramework("identity-billing-remote", log_after_execution=False)

    @billing.feature(CommandRemote)
    class Remote(Feature):
        def execute(self, dto: CommandRemote) -> None: ...

    billing.hosted_by("http://billing:8000")
    _, caller = _inside_an_execution(lambda: billing(CommandRemote()))

    assert sent[ContextKey.TENANT_ID] == "acme"
    assert sent[ContextKey.CAUSATION_ID] == caller.execution_id
    assert sent[ContextKey.CORRELATION_ID] == caller.correlation_id
    assert ContextKey.EXECUTION_ID not in sent


# --- a broker -----------------------------------------------------------------------------------


class Broker:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def publish(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self.published.append(kwargs["headers"])

    def subscriber(self, *args: Any, **kwargs: Any) -> Any: ...


def test_an_event_crosses_a_broker_with_the_publishers_context():
    pytest.importorskip("faststream")
    from sincpro_framework.event_driven.adapters.faststream import FastStreamQueue

    broker = Broker()
    queue = FastStreamQueue(broker).start()
    try:
        _, publisher = _inside_an_execution(lambda: queue.put(OrderShipped()))
    finally:
        queue.stop()

    [headers] = broker.published
    carried = json.loads(headers[CONTEXT_HEADER])
    assert carried[ContextKey.TENANT_ID] == "acme"
    assert carried[ContextKey.CAUSATION_ID] == publisher.execution_id


def test_a_command_sent_to_a_broker_carries_the_senders_chain():
    pytest.importorskip("faststream")
    from sincpro_framework.entrypoints.adapters.faststream import cloud_event_headers

    headers, sender = _inside_an_execution(
        lambda: cloud_event_headers(CommandRemote(), source="identity-tests")
    )

    assert headers["ce_causationid"] == sender.execution_id
    assert headers["ce_correlationid"] == sender.correlation_id
    assert json.loads(headers[CONTEXT_HEADER])[ContextKey.TENANT_ID] == "acme"


def test_a_consumer_restores_what_the_message_carried():
    pytest.importorskip("faststream")
    from sincpro_framework.entrypoints.adapters.faststream.envelope import Envelope
    from sincpro_framework.entrypoints.adapters.faststream.wire import queue_context

    header = written(
        {ContextKey.TENANT_ID: "acme", ContextKey.TENANT_IDS: ["acme", "beta"], "plan": "pro"}
    )
    carried = queue_context(
        Envelope(correlationid="flow", causationid="sender"), {CONTEXT_HEADER: header}
    )

    assert carried == {
        ContextKey.TENANT_ID: "acme",
        ContextKey.TENANT_IDS: ["acme", "beta"],
        "plan": "pro",
        ContextKey.CORRELATION_ID: "flow",
        ContextKey.CAUSATION_ID: "sender",
    }


# --- what travels ---------------------------------------------------------------------------------


def test_only_what_is_simple_travels():
    keys._warned.discard("travel:connection")
    found = travelling(
        {
            ContextKey.TENANT_IDS: ("acme", "beta"),
            "attempt": 2,
            "connection": object(),
            "carrier": {"traceparent": "00-…"},
            "nothing": None,
        }
    )

    assert found == {ContextKey.TENANT_IDS: ["acme", "beta"], "attempt": 2}


def test_a_header_that_is_not_a_context_is_left_out():
    assert read("not json") == {}
    assert read(json.dumps(["a", "list"])) == {}
    assert read(None) == {}
