"""The queue wire's surface (PRD_15 §5.1): what is consumed, what is heard, what the build refuses,
what is registered on the broker and what AsyncAPI documents.

Each rule is an incident it prevents: a Command any producer can make this process run, two
handlers racing for one command channel, a subscription left on FastStream's at-most-once default.
"""

from dataclasses import dataclass
from datetime import timedelta

import pytest
from faststream import AckPolicy
from faststream.kafka import KafkaBroker
from faststream.rabbit import RabbitBroker

from sincpro_framework import DataTransferObject, Feature, ProgrammingError, UseFramework
from sincpro_framework.auth import AccessControl, Permission
from sincpro_framework.ddd import DomainEvent
from sincpro_framework.entrypoints import Exposure
from sincpro_framework.entrypoints.adapters.faststream import (
    QueueGateway,
    QueueOptions,
    QueueWire,
)
from sincpro_framework.entrypoints.domain.bindings import QueueBinding
from sincpro_framework.entrypoints.entrypoint.decorators import queue

# --- the contexts every queue test is written against ----------------------------------------


class CommandIssueInvoice(DataTransferObject):
    customer_id: int
    amount: int


class CommandReconcile(DataTransferObject):
    """A Command nobody declared for the queue — it must never be consumed from one."""

    day: str


@dataclass(kw_only=True)
class InvoiceIssued(DomainEvent):
    name = "billing.invoice.v1.issued"
    number: str = ""


@dataclass(kw_only=True)
class InvoiceVoided(DomainEvent):
    name = "billing.invoice.v1.voided"
    number: str = ""


ISSUE_CHANNEL = "billing.invoices.issue"
SALES = "urn:svc:sales"


def billing_bus(ran: list[str], name: str = "billing") -> UseFramework:
    """`IssueInvoice` consumed from its channel, from sales only; `Reconcile` not on the queue.
    The amount picks what happens: negative a domain refusal, 0 an inside failure."""
    from sincpro_framework.ddd.exceptions import DomainError

    bus = UseFramework(name, log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @queue.consumes(ISSUE_CHANNEL, producers=(SALES,), max_attempts=3)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> None:
            if dto.amount < 0:
                raise DomainError("an invoice cannot be negative")
            if dto.amount == 0:
                raise RuntimeError("the database went away")
            ran.append(f"issued {dto.customer_id}:{dto.amount}")

    @bus.feature(CommandReconcile)
    class Reconcile(Feature):
        def execute(self, dto: CommandReconcile) -> None:
            ran.append(f"reconciled {dto.day}")

    return bus


def accounting_bus(heard: list[str], name: str = "accounting") -> UseFramework:
    """Hears `InvoiceIssued` in group `accounting` (`@queue.hears`) and `InvoiceVoided` by being
    registered for it — in the context's own group."""
    bus = UseFramework(name, log_after_execution=False)

    @bus.feature(InvoiceIssued)
    @queue.hears(group="accounting")
    class Book(Feature):
        def execute(self, dto: InvoiceIssued) -> None:
            heard.append(f"booked {dto.number}")

    @bus.feature(InvoiceVoided)
    class Unbook(Feature):
        def execute(self, dto: InvoiceVoided) -> None:
            heard.append(f"unbooked {dto.number}")

    return bus


def _channels(gateway: QueueGateway) -> dict[str, str]:
    """Each channel and what it carries, by identity: a Command's `context.Class`, an event's
    name — written here without the context, so one expectation fits every bus."""
    return {
        entry.name: entry.command.removeprefix(f"{entry.context}.")
        for entry in gateway.manifest()
    }


# --- what is consumed ------------------------------------------------------------------------


def test_declared_consumes_its_declared_commands_and_hears_every_registered_event():
    """Registering a Feature for a DomainEvent already says the context hears it — no second
    declaration. A Command is consumed only from the channel it declares."""
    gateway = QueueGateway(
        KafkaBroker(), [billing_bus([]), accounting_bus([])], unguarded=True
    )

    published = _channels(gateway)

    assert "CommandReconcile" not in published.values()
    assert published == {
        ISSUE_CHANNEL: "CommandIssueInvoice",
        InvoiceIssued.name: InvoiceIssued.name,
        InvoiceVoided.name: InvoiceVoided.name,
    }


def test_an_event_hears_without_access_or_unguarded_declarations():
    """The fallback access rule guards what an outsider makes this process do — a Command. An
    event reacts to what already happened: neither a guarded nor an unguarded context has to
    declare anything to hear it."""

    class Can(Permission):
        BOOK = "accounting.book"

    guarded = accounting_bus([], "guarded")
    AccessControl[Can]().on(guarded)

    assert QueueGateway(KafkaBroker(), [accounting_bus([])]).verify() == []
    assert QueueGateway(KafkaBroker(), [guarded]).verify() == []


def test_catalog_hears_every_event_but_never_consumes_an_undeclared_command():
    """Accepting any Command from a broker is the queue's unintended public API."""
    gateway = QueueGateway(
        KafkaBroker(),
        [billing_bus([]), accounting_bus([])],
        exposure=Exposure.CATALOG,
        unguarded=True,
    )

    published = _channels(gateway)

    assert "CommandReconcile" not in published.values()
    assert published == {
        ISSUE_CHANNEL: "CommandIssueInvoice",
        InvoiceIssued.name: InvoiceIssued.name,
        InvoiceVoided.name: InvoiceVoided.name,
    }


def test_a_group_prefixes_the_channels_and_names_the_consumer_group():
    broker = KafkaBroker()
    gateway = QueueGateway(broker, [billing_bus([]), accounting_bus([])], unguarded=True)
    gateway.group("billing", prefix="staging.")
    gateway.group("accounting", prefix="staging.", namespace="ledger")

    subscriptions = {one.channel: one.group for one in gateway.build()}

    assert subscriptions == {
        f"staging.{ISSUE_CHANNEL}": "billing",
        f"staging.{InvoiceIssued.name}": "accounting",  # the binding's own group wins
        f"staging.{InvoiceVoided.name}": "ledger",  # heard by registration: the group's
    }


def test_the_manifest_carries_the_binding():
    gateway = QueueGateway(KafkaBroker(), [billing_bus([])], unguarded=True)

    (entry,) = gateway.manifest()

    assert entry.wire == "queue"
    assert entry.binding["kind"] == "consumes"
    assert entry.binding["producers"] == [SALES]
    assert entry.binding["max_attempts"] == 3


# --- what the build refuses ------------------------------------------------------------------


def test_two_handlers_on_one_command_channel_are_refused():
    gateway = QueueGateway(
        KafkaBroker(),
        {"billing": billing_bus([]), "copy": billing_bus([], "copy")},
        unguarded=True,
    )

    problems = "\n".join(gateway.verify())

    assert (
        f"command channel {ISSUE_CHANNEL} is consumed by billing:CommandIssueInvoice, "
        "copy:CommandIssueInvoice" in problems
    )
    with pytest.raises(ProgrammingError):
        gateway.build()


def test_a_command_channel_that_also_carries_events_is_refused():
    gateway = QueueGateway(
        KafkaBroker(), [billing_bus([]), accounting_bus([])], unguarded=True
    )
    gateway.override(InvoiceIssued, channel=ISSUE_CHANNEL)

    assert any("also carries events" in one for one in gateway.verify())


def test_two_contexts_hearing_one_event_in_one_group_share_one_subscription():
    """One consumer group is one delivery: two contexts of this process in it are one
    subscription that runs both — two would compete for the message on RabbitMQ."""
    gateway = QueueGateway(
        KafkaBroker(),
        {"accounting": accounting_bus([]), "again": accounting_bus([], "again")},
        unguarded=True,
    )

    assert gateway.verify() == []
    issued = [one for one in gateway.build() if one.channel == InvoiceIssued.name]
    assert [(one.group, len(one.by_type[InvoiceIssued.name])) for one in issued] == [
        ("accounting", 2)
    ]


def test_a_consumer_says_who_may_send_it():
    """The PRD_14 fallback rule on a queue: `producers=` or the bus's AccessControl — an
    unguarded bus without either is refused unless the gateway is told."""
    no_producers = UseFramework("open", log_after_execution=False)

    @no_producers.feature(CommandReconcile)
    @queue.consumes("ops.reconcile")
    class Reconcile(Feature):
        def execute(self, dto: CommandReconcile) -> None: ...

    refused = QueueGateway(KafkaBroker(), [no_producers]).verify()
    with_producers = QueueGateway(KafkaBroker(), [billing_bus([])]).verify()

    assert any("declare producers=" in one for one in refused)
    assert with_producers == []


def test_a_guarded_consumer_that_declares_no_access_is_refused():
    class Can(Permission):
        ISSUE = "billing.issue"

    auth = AccessControl[Can]()
    bus = billing_bus([])
    auth.on(bus)

    assert any(
        "declares neither" in one for one in QueueGateway(KafkaBroker(), [bus]).verify()
    )


def test_a_time_limit_past_the_inbox_claim_is_refused():
    options = QueueOptions(
        time_limit=timedelta(minutes=10), in_progress_for=timedelta(minutes=5)
    )
    gateway = QueueGateway(KafkaBroker(), [billing_bus([])], options=options)

    assert any("time_limit" in one for one in gateway.verify())


def test_attempts_and_concurrency_are_positive():
    gateway = QueueGateway(KafkaBroker(), [billing_bus([])])
    gateway.override(CommandIssueInvoice, max_attempts=0)

    assert any("max_attempts=0" in one for one in gateway.verify())


def test_a_port_is_a_queue_wire_and_brings_its_own_broker():
    wire = QueueWire(KafkaBroker())

    assert QueueGateway(port=wire, instances=[billing_bus([])]).queue_wire is wire
    with pytest.raises(ValueError):
        QueueGateway(KafkaBroker(), port=wire)
    with pytest.raises(TypeError):
        QueueGateway()


# --- what is registered ----------------------------------------------------------------------


@pytest.mark.parametrize("broker_cls", [KafkaBroker, RabbitBroker])
def test_every_subscription_settles_manually(broker_cls):
    broker = broker_cls()
    QueueGateway(broker, [billing_bus([]), accounting_bus([])], unguarded=True).build()

    assert len(broker.subscribers) == 3  # the Command and both events it hears
    assert all(one.ack_policy is AckPolicy.MANUAL for one in broker.subscribers)


def test_building_twice_registers_once():
    broker = KafkaBroker()
    gateway = QueueGateway(broker, [billing_bus([])])

    gateway.build()
    gateway.build()

    assert len(broker.subscribers) == 1


def test_kafka_subscriptions_join_their_consumer_group_with_their_workers():
    broker = KafkaBroker()
    gateway = QueueGateway(broker, [billing_bus([])])
    gateway.override(CommandIssueInvoice, concurrency=4)

    gateway.build()

    (subscription,) = broker.subscribers
    assert subscription.group_id == "billing"  # type: ignore[attr-defined]


def test_asyncapi_lists_every_subscription_it_built():
    gateway = QueueGateway(
        KafkaBroker(), [billing_bus([]), accounting_bus([])], title="billing", unguarded=True
    )
    gateway.build()

    document = gateway.asyncapi()

    assert document["asyncapi"].startswith("3.")
    channels = {one["address"] for one in document["channels"].values()}
    assert channels == {ISSUE_CHANNEL, InvoiceIssued.name, InvoiceVoided.name}
    actions = {one["action"] for one in document["operations"].values()}
    assert actions == {"receive"}


def test_a_queue_binding_is_bound_by_the_composition():
    bus = billing_bus([])
    gateway = QueueGateway(KafkaBroker(), [bus])
    gateway.bind(
        CommandReconcile,
        QueueBinding(kind="consumes", channel="ops.reconcile", producers=(SALES,)),
    )

    assert set(_channels(gateway)) == {ISSUE_CHANNEL, "ops.reconcile"}
