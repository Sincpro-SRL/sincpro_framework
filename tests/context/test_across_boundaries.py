"""The context crosses every boundary on the transport's side channel, never in the DTO — and the
keys the framework reads have one name each (PRD_21 §5-§6, phases 3 and 4)."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest
from structlog.testing import capture_logs

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.context import (
    CAUSATION_ID,
    CORRELATION_ID,
    EXECUTION_ID,
    TENANT_ID,
    USER_ID,
    Execution,
    travelling,
)
from sincpro_framework.context.adapters.propagation import CONTEXT_HEADER, read, written
from sincpro_framework.context.domain import keys
from sincpro_framework.context.domain.keys import TENANT_IDS, standardized
from sincpro_framework.context.infrastructure.tree import current_execution
from sincpro_framework.ddd import DomainEvent
from sincpro_framework.observability.correlation import tenant, user_id


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

    with bus.context({TENANT_ID: "acme"}):
        bus(CommandLocal())
    return answered[0]


# --- the entrypoints read the identity ---------------------------------------------------------


def test_json_rpc_reads_the_identity_headers():
    from sincpro_framework.entrypoints.infrastructure.http import merge_http_context

    merged = merge_http_context(
        {"X-Correlation-Id": "flow", "X-Causation-Id": "gateway", "X-Execution-Id": "req"}
    )

    assert merged == {CORRELATION_ID: "flow", CAUSATION_ID: "gateway", EXECUTION_ID: "req"}


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

    assert found == {CORRELATION_ID: "flow", CAUSATION_ID: "gateway"}


def test_grpc_reads_and_writes_the_identity_as_metadata():
    from sincpro_framework.common.transport.grpc import (
        context_from_metadata,
        metadata_from_context,
    )

    sent = {CORRELATION_ID: "flow", CAUSATION_ID: "caller", TENANT_ID: "acme"}
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

    assert sent[TENANT_ID] == "acme"
    assert sent[CAUSATION_ID] == caller.execution_id
    assert sent[CORRELATION_ID] == caller.correlation_id
    assert EXECUTION_ID not in sent


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
    assert carried[TENANT_ID] == "acme"
    assert carried[CAUSATION_ID] == publisher.execution_id


def test_a_command_sent_to_a_broker_carries_the_senders_chain():
    pytest.importorskip("faststream")
    from sincpro_framework.entrypoints.adapters.faststream import cloud_event_headers

    headers, sender = _inside_an_execution(
        lambda: cloud_event_headers(CommandRemote(), source="identity-tests")
    )

    assert headers["ce_causationid"] == sender.execution_id
    assert headers["ce_correlationid"] == sender.correlation_id
    assert json.loads(headers[CONTEXT_HEADER])[TENANT_ID] == "acme"


def test_a_consumer_restores_what_the_message_carried():
    pytest.importorskip("faststream")
    from sincpro_framework.entrypoints.adapters.faststream.envelope import Envelope
    from sincpro_framework.entrypoints.adapters.faststream.wire import queue_context

    header = written({TENANT_ID: "acme", TENANT_IDS: ["acme", "beta"], "plan": "pro"})
    carried = queue_context(
        Envelope(correlationid="flow", causationid="sender"), {CONTEXT_HEADER: header}
    )

    assert carried == {
        TENANT_ID: "acme",
        TENANT_IDS: ["acme", "beta"],
        "plan": "pro",
        CORRELATION_ID: "flow",
        CAUSATION_ID: "sender",
    }


# --- one name per key ---------------------------------------------------------------------------


def test_a_legacy_key_is_read_as_the_standard_one_and_stays():
    keys._warned.discard("user.id")
    with capture_logs() as logs:
        found = standardized({"user.id": "ana"})
        standardized({"user.id": "bo"})

    assert found == {"user.id": "ana", USER_ID: "ana"}
    warned = [line for line in logs if "'user.id' is read as 'user_id'" in line["event"]]
    assert len(warned) == 1 and warned[0]["log_level"] == "warning"


def test_the_standard_key_wins_over_a_legacy_one():
    assert standardized({"tenant": "old", TENANT_ID: "new"})[TENANT_ID] == "new"


def test_a_feature_reads_the_standard_key_whichever_was_given():
    bus = UseFramework("identity-keys", log_after_execution=False)
    read_back: list[Any] = []

    @bus.feature(CommandLocal)
    class Local(Feature):
        def execute(self, dto: CommandLocal) -> None:
            read_back.append((self.context.get(USER_ID), self.context.get(TENANT_ID)))

    with bus.context({"user.id": "ana", "tenant": "acme"}):
        bus(CommandLocal())

    assert read_back == [("ana", "acme")]


def test_the_signals_read_the_standard_keys_and_the_legacy_ones():
    assert tenant({TENANT_ID: "acme"}) == "acme"
    assert tenant({"tenant": "legacy"}) == "legacy"
    assert user_id({USER_ID: "ana"}) == "ana"
    assert user_id({"user.id": "bo"}) == "bo"


def test_only_what_is_simple_travels():
    keys._warned.discard("travel:connection")
    found = travelling(
        {
            TENANT_IDS: ("acme", "beta"),
            "attempt": 2,
            "connection": object(),
            "carrier": {"traceparent": "00-…"},
            "nothing": None,
        }
    )

    assert found == {TENANT_IDS: ["acme", "beta"], "attempt": 2}


def test_a_header_that_is_not_a_context_is_left_out():
    assert read("not json") == {}
    assert read(json.dumps(["a", "list"])) == {}
    assert read(None) == {}
