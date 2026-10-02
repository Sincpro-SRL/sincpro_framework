"""The context of a request follows it into every bus it reaches.

Each bus keeps its own context, so a bus executed from inside another one — a Feature calling
`self.common(...)`, a subscriber reached through a `SyncQueue` — used to see an empty one: the
tenant and the user stopped at the first boundary. The bus in progress now hands its context to
the one it calls; that one adds its own on top, and nothing flows back.
"""

from typing import Any

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd import DomainEvent
from sincpro_framework.event_driven import (
    Publisher,
    Subscriber,
    SyncQueue,
)


class CommandResolveTenant(DataTransferObject):
    pass


class CommandCreateQuotation(DataTransferObject):
    pass


class ResponseContext(DataTransferObject):
    seen: dict[str, Any]


def _buses() -> tuple[UseFramework, UseFramework]:
    common = UseFramework("common", log_after_execution=False)
    sales = UseFramework("sales", log_after_execution=False)
    sales.add_dependency("common", common)

    @common.feature(CommandResolveTenant)
    class ResolveTenant(Feature):
        def execute(self, dto: CommandResolveTenant) -> ResponseContext:
            return ResponseContext(seen=dict(self.context))

    @sales.feature(CommandCreateQuotation)
    class CreateQuotation(Feature):
        def execute(self, dto: CommandCreateQuotation) -> ResponseContext:
            inner = self.common(CommandResolveTenant(), ResponseContext)
            assert inner is not None
            return ResponseContext(seen={"inner": inner.seen})

    return common, sales


def test_a_bus_called_from_another_sees_the_callers_context():
    _, sales = _buses()

    with sales.context({"tenant": "acme", "user.id": "ana"}):
        answer = sales(CommandCreateQuotation(), ResponseContext)

    assert answer is not None
    assert answer.seen["inner"] == {"tenant": "acme", "user.id": "ana"}


def test_the_inner_bus_adds_its_own_keys_on_top_and_nothing_flows_back():
    class CommandOverride(DataTransferObject):
        pass

    common = UseFramework("common-override", log_after_execution=False)
    sales = UseFramework("sales-override", log_after_execution=False)

    @common.feature(CommandResolveTenant)
    class ResolveTenant(Feature):
        def execute(self, dto: CommandResolveTenant) -> ResponseContext:
            return ResponseContext(seen=dict(self.context))

    @sales.feature(CommandOverride)
    class Override(Feature):
        def execute(self, dto: CommandOverride) -> ResponseContext:
            with common.context({"tenant": "override", "stage": "inner"}):
                inner = common(CommandResolveTenant(), ResponseContext)
            assert inner is not None
            return ResponseContext(
                seen={"inner": inner.seen, "outer_after": dict(self.context)}
            )

    with sales.context({"tenant": "acme", "user.id": "ana"}):
        answer = sales(CommandOverride(), ResponseContext)

    assert answer is not None
    assert answer.seen["inner"] == {"tenant": "override", "user.id": "ana", "stage": "inner"}
    assert answer.seen["outer_after"] == {"tenant": "acme", "user.id": "ana"}


def test_nothing_is_inherited_outside_an_execution():
    common, sales = _buses()

    with sales.context({"tenant": "acme"}):
        pass
    answer = common(CommandResolveTenant(), ResponseContext)

    assert answer is not None
    assert answer.seen == {}


def test_a_subscriber_reached_through_a_sync_queue_sees_the_publishers_context():
    class InvoicePosted(DomainEvent):
        pass

    class CommandPost(DataTransferObject):
        pass

    billing = UseFramework("billing-ctx", log_after_execution=False)
    notifications = UseFramework("notifications-ctx", log_after_execution=False)
    heard: list[dict[str, Any]] = []
    publisher = Publisher(SyncQueue(Subscriber(notifications)))

    @notifications.feature(InvoicePosted)
    class Email(Feature):
        def execute(self, dto: InvoicePosted) -> None:
            heard.append(dict(self.context))

    @billing.feature(CommandPost)
    class Post(Feature):
        def execute(self, dto: CommandPost) -> None:
            publisher.publish(InvoicePosted())

    with billing.context({"tenant": "acme"}):
        billing(CommandPost())

    assert heard == [{"tenant": "acme"}]
