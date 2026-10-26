"""What crosses a process and what stays — the rules of propagation, one test per cell.

    level               owner                    lives on            crosses
    global              the organization         the process store   never: read where it lives
    process             the service              the process store   never
    bus                 the bounded context      its own store       never
    entrypoint → …      the flow (the request)   its own store       always, as headers

Read on the other side: the flow as the headers' snapshot always; live, by the node's id, only
when the receiver reads the same store.
"""

import json
import threading
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from structlog.testing import capture_logs

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.context import (
    ContextLevel,
    InMemoryContexts,
    context_propagation,
    context_scopes,
    store_context_in,
    use_context,
)
from sincpro_framework.context.domain.level import EntrypointKind
from tests.remote_execution.hosting import TRANSPORTS, host_over


class CommandPlace(DataTransferObject):
    pass


class CommandPublish(DataTransferObject):
    pass


class CommandRead(DataTransferObject):
    pass


class ResponseSeen(DataTransferObject):
    seen: dict[str, Any]


KEYS = ("org", "lang", "plan", "tenant_id", "discount", "shipper", "late")


def _orders(store: InMemoryContexts, sent: list[dict[str, str]]) -> UseFramework:
    """The sender: its bounded context publishes `plan`, its use cases write the flow."""
    orders = UseFramework("rules-orders", log_after_execution=False)
    orders.store_context_in(store)

    @orders.app_service(CommandPlace)
    class Place(ApplicationService):
        def execute(self, dto: CommandPlace) -> None:
            use_context().set("discount", 10)
            self.feature_bus.execute(CommandPublish())

    @orders.feature(CommandPublish)
    class Publish(Feature):
        def execute(self, dto: CommandPublish) -> None:
            use_context().set("shipper", "dhl")
            sent.append(context_propagation.inject(use_context()))
            use_context().set("late", "after the hand-off")

    return orders


def _billing(store: InMemoryContexts, read: list[dict[str, Any]]) -> UseFramework:
    """The receiver: another bounded context, reading every key."""
    billing = UseFramework("rules-billing", log_after_execution=False)
    billing.store_context_in(store)

    @billing.feature(CommandRead)
    class Read(Feature):
        def execute(self, dto: CommandRead) -> None:
            read.append({key: use_context().get(key) for key in KEYS})

    return billing


def _sent(orders: UseFramework, sent: list[dict[str, str]]) -> dict[str, str]:
    """What orders hands on when a request of tenant acme places an order."""
    with orders.context({"plan": "pro"}, global_scope=True):
        with orders.context({"tenant_id": "acme"}):
            orders(CommandPlace())
    return sent[-1]


def _received(billing: UseFramework, headers: Mapping[str, str]) -> None:
    """A queue consumer of billing, in a thread that starts with no context."""

    def consume() -> None:
        with billing.context(context_propagation.extract(headers), kind=EntrypointKind.QUEUE):
            billing(CommandRead())

    thread = threading.Thread(target=consume)
    thread.start()
    thread.join(10)


# --- what the headers carry ---------------------------------------------------------------------


def test_the_headers_carry_the_flow_and_the_identity_never_a_level_with_an_owner():
    use_context().root.set("lang", "es")
    sent: list[dict[str, str]] = []

    headers = _sent(_orders(InMemoryContexts(), sent), sent)
    carried = json.loads(headers["sincpro-context"])

    assert {"tenant_id": "acme", "discount": 10, "shipper": "dhl"}.items() <= carried.items()
    assert "lang" not in carried and "plan" not in carried
    assert {"x-correlation-id", "x-causation-id", "x-context-node"} <= headers.keys()


def test_a_plain_mapping_is_carried_as_given():
    use_context().root.set("lang", "es")

    carried = context_propagation.extract(context_propagation.inject({"lang": "en"}))

    assert carried["lang"] == "en"


def test_with_nothing_given_the_flow_in_play_is_carried():
    use_context().root.set("lang", "es")
    bus = UseFramework("rules-default", log_after_execution=False)
    sent: list[dict[str, str]] = []

    @bus.feature(CommandRead)
    class Send(Feature):
        def execute(self, dto: CommandRead) -> None:
            sent.append(context_propagation.inject())

    with bus.context({"tenant_id": "acme"}):
        bus(CommandRead())

    carried = context_propagation.extract(sent[0])
    assert carried["tenant_id"] == "acme" and "lang" not in carried
    assert context_scopes.flow() == {}


def test_a_value_the_flow_took_away_is_not_carried_and_the_other_side_reads_its_own():
    bus = UseFramework("rules-removed", log_after_execution=False)
    sent: list[dict[str, str]] = []

    @bus.feature(CommandRead)
    class Send(Feature):
        def execute(self, dto: CommandRead) -> None:
            context = use_context()
            del context["lang"]  # hidden on the scope of the call
            sent.append(context_propagation.inject(use_context()))

    with bus.context({"lang": "en", "tenant_id": "acme"}):
        bus(CommandRead())

    carried = context_propagation.extract(sent[0])
    assert "lang" not in carried and carried["tenant_id"] == "acme"


# --- the other side reads its own levels --------------------------------------------------------


def test_the_receiver_reads_its_own_process_and_bounded_context_never_the_senders():
    use_context().root.set("lang", "es")
    sent: list[dict[str, str]] = []
    read: list[dict[str, Any]] = []
    headers = _sent(_orders(InMemoryContexts(), sent), sent)
    use_context().root.set("lang", "en")  # billing's process says otherwise

    _received(_billing(InMemoryContexts(), read), headers)

    assert read[0]["lang"] == "en" and read[0]["plan"] is None
    assert read[0]["tenant_id"] == "acme"


def test_a_value_of_the_flow_wins_over_the_receivers_own_level():
    use_context().root.set("lang", "es")
    bus = UseFramework("rules-flow-wins", log_after_execution=False)
    sent: list[dict[str, str]] = []
    read: list[dict[str, Any]] = []

    @bus.feature(CommandPublish)
    class Send(Feature):
        def execute(self, dto: CommandPublish) -> None:
            sent.append(context_propagation.inject(use_context()))

    with bus.context({"lang": "fr"}):  # the request asked for French
        bus(CommandPublish())
    _received(_billing(InMemoryContexts(), read), sent[0])

    assert read[0]["lang"] == "fr"


# --- by the store each side reads ---------------------------------------------------------------


def test_one_store_the_flow_is_read_live_even_what_was_written_after_the_hand_off():
    store = InMemoryContexts()
    sent: list[dict[str, str]] = []
    read: list[dict[str, Any]] = []
    headers = _sent(_orders(store, sent), sent)

    _received(_billing(store, read), headers)

    assert read[0]["shipper"] == "dhl" and read[0]["late"] == "after the hand-off"
    assert read[0]["plan"] is None


def test_a_store_of_its_own_per_bounded_context_reads_the_flow_as_the_headers_snapshot():
    sent: list[dict[str, str]] = []
    read: list[dict[str, Any]] = []
    headers = _sent(_orders(InMemoryContexts(), sent), sent)

    _received(_billing(InMemoryContexts(), read), headers)

    assert read[0]["discount"] == 10 and read[0]["shipper"] == "dhl"
    assert read[0]["late"] is None  # live only through the same store


def test_a_store_of_its_own_and_only_the_ids_reads_the_identity_and_nothing_more():
    sent: list[dict[str, str]] = []
    read: list[dict[str, Any]] = []
    headers = _sent(_orders(InMemoryContexts(), sent), sent)
    only_ids = {name: value for name, value in headers.items() if name.startswith("x-")}

    _received(_billing(InMemoryContexts(), read), only_ids)

    assert read[0]["tenant_id"] is None and read[0]["shipper"] is None


def test_the_global_is_read_from_the_process_store_never_from_a_message():
    process = InMemoryContexts()
    store_context_in(process)
    sent: list[dict[str, str]] = []
    read: list[dict[str, Any]] = []
    orders, billing = _orders(InMemoryContexts(), sent), _billing(InMemoryContexts(), read)
    _sent(orders, sent)
    global_level = use_context().at(ContextLevel.GLOBAL)
    assert global_level is not None
    global_level.set("org", "sincpro")

    _received(billing, sent[-1])

    assert "org" not in json.loads(sent[-1]["sincpro-context"])
    assert read[0]["org"] == "sincpro"


# --- the frontend -------------------------------------------------------------------------------


def test_the_frontend_holds_the_flow_never_what_the_backend_owns():
    use_context().root.set("lang", "es")
    bus = UseFramework("rules-frontend", log_after_execution=False)
    held: list[dict[str, Any]] = []

    @bus.feature(CommandRead)
    class Answer(Feature):
        def execute(self, dto: CommandRead) -> None:
            use_context().set("step", 2)
            held.append(use_context().to_client())

    with bus.context({"plan": "pro"}, global_scope=True):
        with bus.context({"tenant_id": "acme", "user_id": "ana"}):
            bus(CommandRead())

    assert {"tenant_id": "acme", "user_id": "ana", "step": 2}.items() <= held[0].items()
    assert "lang" not in held[0] and "plan" not in held[0]


# --- another service, by the remote bus ---------------------------------------------------------


def _pricing(read: list[dict[str, Any]]) -> UseFramework:
    pricing = UseFramework("rules-pricing", log_after_execution=False)

    @pricing.feature(CommandRead)
    class Read(Feature):
        def execute(self, dto: CommandRead) -> ResponseSeen:
            seen = {key: use_context().get(key) for key in KEYS}
            read.append(seen)
            return ResponseSeen(seen=seen)

    return pricing


@pytest.fixture(params=TRANSPORTS)
def remote_pricing(request: pytest.FixtureRequest) -> Iterator[UseFramework]:
    """A reference to `rules-pricing`, hosted over the transport."""
    for address in host_over(request.param, [_pricing([])]):
        reference = _pricing([])
        reference.hosted_by(f"{address}?timeout=5")
        yield reference


def test_another_service_receives_the_flow_never_the_callers_bounded_context(remote_pricing):
    sales = UseFramework("rules-sales", log_after_execution=False)
    sales.add_dependency("pricing", remote_pricing)

    @sales.app_service(CommandPlace)
    class Price(ApplicationService):
        pricing: UseFramework

        def execute(self, dto: CommandPlace) -> ResponseSeen:
            use_context().set("discount", 10)
            answer = self.pricing(CommandRead(), ResponseSeen)
            assert answer is not None
            return answer

    with sales.context({"plan": "pro"}, global_scope=True):
        with sales.context({"tenant_id": "acme"}):
            answer = sales(CommandPlace(), ResponseSeen)

    assert answer is not None
    assert answer.seen["tenant_id"] == "acme" and answer.seen["discount"] == 10
    assert answer.seen["plan"] is None


# --- how much a header weighs -------------------------------------------------------------------


def test_a_context_past_its_budget_is_warned_about_and_carried_all_the_same():
    lines = [f"line-{n}" for n in range(1000)]

    with capture_logs() as logs:
        headers = context_propagation.inject({"lines": lines, "tenant_id": "acme"})

    assert context_propagation.extract(headers)["lines"] == lines
    assert [log for log in logs if "'lines' is its largest key" in log["event"]]
