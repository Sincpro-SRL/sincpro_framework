"""`QueueGateway`: what outsiders may make this process do through a broker (PRD_15 §5).

    gateway = QueueGateway(broker, [billing, accounting])      # declared Commands, every event
    gateway.build()                                             # the subscriptions, registered
    await FastStream(broker).run()                              # or `faststream run app:app`

    @billing.feature(CommandIssueInvoice)
    @queue.consumes("billing.invoices.issue", producers=("urn:svc:sales",), max_attempts=5)
    class IssueInvoice(Feature): ...

    @accounting.feature(InvoiceIssued)                        # heard: nothing to declare
    class Book(Feature): ...

Context: every DomainEvent a bus registered is heard — registering the Feature is the
declaration; `@queue.hears` only names another group or channel. A Command is consumed only from
the channel it declares with `@queue.consumes` (or `bind`), in either exposure: accepting any
Command from a broker is the queue's unintended public API. A group gives its context a channel
`prefix` and a consumer group (`namespace`, else the alias); contexts of this process hearing
one event in one group share one subscription that runs them all.
"""

from collections.abc import Callable, Iterable
from typing import Any, cast

from sincpro_framework import exceptions
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.entrypoints.adapters.queue.verdicts import Verdict
from sincpro_framework.entrypoints.adapters.queue.wire import (
    Decode,
    QueueOptions,
    QueueWire,
    Subscription,
    arrival_channel,
    channel_of,
)
from sincpro_framework.entrypoints.domain.bindings import QueueBinding
from sincpro_framework.entrypoints.domain.surface import (
    Exposure,
    Group,
    Operation,
    Resolved,
    Wire,
)
from sincpro_framework.entrypoints.entrypoint.gateway import (
    DEFAULT_LAYERS,
    Buses,
    Gateway,
    Published,
)
from sincpro_framework.entrypoints.infrastructure.registry import registry
from sincpro_framework.event_driven.adapters.envelope import type_name
from sincpro_framework.event_driven.domain.broker import Broker, Delivery
from sincpro_framework.use_bus import UseFramework


class QueueGateway(Gateway):
    """The queue entrypoint: the buses' declared Commands consumed and their events heard
    from one broker, every delivery settled from its verdict.

        gateway = QueueGateway(KafkaBroker("kafka:9092"), [billing, accounting])
        gateway.build()             ->  [Subscription("billing.invoices.issue", ...), ...]
        gateway.manifest()          # every channel, kind and access, for a CI snapshot
        gateway.asyncapi()          # the AsyncAPI document of what was built

    Context: a `Gateway` like every wire's — `add`, `group`, `bind`, `override` — whose wire
    is a `QueueWire`. Unlike the other wires, an event is published by default (registering a
    Feature for it is the declaration) and a Command never is.
    """

    wire = "queue"

    def __init__(
        self,
        broker: Broker | None = None,
        instances: Buses | None = None,
        layers: Iterable[str] = DEFAULT_LAYERS,
        title: str = "sincpro",
        version: str = "1.0.0",
        options: QueueOptions | None = None,
        exposure: Exposure = Exposure.DECLARED,
        unguarded: bool = False,
        port: Wire[Any] | None = None,
    ) -> None:
        """`broker` and `options` build the wire; a `port` (a `QueueWire`) brings its own.

            QueueGateway(KafkaBroker("kafka:9092"), [billing])   ->  over QueueWire(broker)
            QueueGateway(port=MyQueueWire(broker), instances=[billing])  ->  over that wire
            QueueGateway(instances=[billing])     ->  TypeError: needs a broker, or port=...
            QueueGateway(broker, port=MyQueueWire(broker))
            ->  ValueError: the broker and options are the port's

        1. No port: a `QueueWire` over `broker` and `options` — refused without a broker.
        2. A port that is not a `QueueWire` is refused; given with a broker or options too,
           refused as well.
        3. The port told whether the gateway is unguarded, and kept as the gateway's wire.
        Final: the `Gateway` built over it, with the buses, layers and exposure given.
        """
        if port is None:
            if broker is None:
                raise TypeError(
                    "QueueGateway needs a broker (FastStream's, an SqsBroker), or port=QueueWire(...)"
                )
            port = QueueWire(broker, options)
        elif not isinstance(port, QueueWire):
            raise TypeError(
                f"QueueGateway(port={type(port).__name__}): the port of a queue gateway is a "
                "QueueWire — subclass it to change how it derives"
            )
        elif broker is not None or options is not None:
            raise ValueError("QueueGateway(port=...): the broker and options are the port's")
        port.unguarded = unguarded
        self._wire: QueueWire = port
        super().__init__(
            instances,
            layers,
            title,
            version,
            exposure=exposure,
            unguarded=unguarded,
            port=port,
        )

    @property
    def broker(self) -> Broker:
        """The broker the wire subscribes on — what `FastStream(...)` or `start()` runs."""
        return self._wire.broker

    @property
    def queue_wire(self) -> QueueWire:
        """The wire itself — `sqs_handler` hands it each Lambda record to `process`."""
        return self._wire

    def _declared_on_queue(self, published: Published) -> bool:
        """Whether a use case has a queue binding — its decorator's, or a `bind` of the
        composition — the handler in force where it was replaced."""
        _, bus, packed = published
        handler = packed.handler or bus.handler_of(packed.dto) or packed.dto
        declared = registry.of(handler, bus.replaced_for(packed.dto)).get(self.wire or "")
        _, bound = self._composed_for(packed.dto, handler)
        return declared is not None or bound

    def _published_by_default(self, published: Published) -> bool:
        """Registering a Feature for a DomainEvent already says the context hears it:
        `@queue.hears` only moves it to another group or channel."""
        return isinstance(published[2].dto, type) and issubclass(
            published[2].dto, DomainEvent
        )

    def _published(self) -> list[Published]:
        """In CATALOG mode, every DomainEvent — and a Command only when it declared its
        channel: a Command is never consumed by default."""
        published = super()._published()
        if self.exposure != Exposure.CATALOG:
            return published
        return [
            one
            for one in published
            if isinstance(one[2].dto, type)
            and issubclass(one[2].dto, DomainEvent)
            or self._declared_on_queue(one)
        ]

    def _access_problems(self, operation: Operation, wire: str) -> list[str]:
        """The queue's fallback rule knows `producers=`, which the operation does not carry:
        `QueueWire.validate` applies it with the binding in hand."""
        return []

    def build(self) -> list[Subscription]:
        """The surface validated, then one subscription per channel and consumer group
        registered on the broker — MANUAL settlement on each."""
        return self._wire.build(self.surface())

    def _resolved_for(self, command: type) -> Resolved[QueueBinding]:
        """`command` on the surface — or, for a full-control subscriber of the project's, the
        binding the wire derives, under the same access rule."""
        for one in self.surface():
            if one.operation.command is command:
                return one
        for published in super()._published():
            alias, bus, packed = published
            if packed.dto is not command:
                continue
            handler = packed.handler or bus.handler_of(command) or command
            operation = self._operation(published, handler)
            group = self.groups[alias]
            binding = self._wire.derive(operation, group)
            resolved = Resolved[QueueBinding](
                operation=operation, group=group, binding=binding
            )
            problem = self._wire._access_problem(resolved)
            if problem:
                raise exceptions.ProgrammingError(problem)
            return resolved
        raise exceptions.ProgrammingError(
            f"consume(as_={command.__name__}): no bus of this gateway answers it"
        )

    async def consume(
        self,
        message: Delivery,
        as_: type,
        decode: Decode | None = None,
        channel: str | None = None,
    ) -> Verdict:
        """Full control: a subscriber of the project's runs the path the generated one runs —
        producers, inbox, bus, verdict — and the message is settled here. Subscribe it with
        `ack_policy=AckPolicy.MANUAL`.

            @broker.subscriber("legacy.topic", ack_policy=AckPolicy.MANUAL)
            async def legacy(message: StreamMessage = Context("message")) -> None:
                await gateway.consume(message, as_=CommandIssueInvoice, decode=legacy_decoder)
        """
        resolved = self._resolved_for(as_)
        where = channel or arrival_channel(message) or channel_of(resolved)
        return await self._wire.process(
            message, {as_.__name__: (resolved,)}, where, decode, as_given=True
        )

    def asyncapi(self) -> dict[str, Any]:
        """The AsyncAPI 3.0 document FastStream generates for the broker — every subscription
        this gateway built is a channel with its `receive` operation."""
        from faststream.specification import AsyncAPI

        document = AsyncAPI(cast(Any, self.broker), title=self._title, version=self._version)
        return document.to_specification().to_jsonable()


class _EventsWire(QueueWire):
    """Every event on the channel its name maps to, all in one consumer group."""

    def __init__(
        self,
        broker: Broker,
        options: QueueOptions | None,
        channel_of_name: Callable[[str], str],
        group: str,
    ) -> None:
        """The queue wire, plus how an event's name maps to its channel and the one consumer
        group every event is heard in."""
        super().__init__(broker, options)
        self._channel_of_name = channel_of_name
        self._group = group

    def derive(self, operation: Operation, group: Group) -> QueueBinding:
        """An event on `channel_of_name(its name)`, in the shared group; anything else as the
        queue wire derives it.

            derive(InvoiceIssued)  ->  QueueBinding(kind="hears",
                                                    channel="billing.invoice.v1.issued",
                                                    group="planning+assurance")
        """
        derived = super().derive(operation, group)
        if derived.kind != "hears":
            return derived
        channel = self._channel_of_name(type_name(operation.command))
        return derived.model_copy(update={"channel": channel, "group": self._group})


class _EventsGateway(QueueGateway):
    """The gateway `subscribe()` builds: only the buses' DomainEvents reach its surface."""

    def _published(self) -> list[Published]:
        """What the queue gateway publishes, its Commands left out."""
        return [
            one
            for one in super()._published()
            if isinstance(one[2].dto, type) and issubclass(one[2].dto, DomainEvent)
        ]


def events_gateway(
    broker: Broker,
    buses: Iterable[UseFramework],
    channel_of_name: Callable[[str], str],
    options: QueueOptions | None = None,
) -> QueueGateway:
    """What `subscribe()` builds: the buses' events only — never a Command — each channel one
    subscription in one consumer group, handing the event to every bus that registered it."""
    buses = list(buses)
    group = "+".join(bus.name for bus in buses)
    return _EventsGateway(
        instances=buses,
        port=_EventsWire(broker, options, channel_of_name, group),
        unguarded=True,
    )
