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

from faststream import StreamMessage

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.entrypoints.exposure import (
    Exposure,
    ExposureRefused,
    Group,
    Operation,
    QueueBinding,
    Resolved,
    Wire,
    bindings_of,
)
from sincpro_framework.entrypoints.faststream.envelope import type_name
from sincpro_framework.entrypoints.faststream.verdicts import Verdict
from sincpro_framework.entrypoints.faststream.wire import (
    Decode,
    QueueOptions,
    QueueWire,
    Subscription,
    arrival_channel,
    channel_of,
)
from sincpro_framework.entrypoints.gateway import DEFAULT_LAYERS, Buses, Gateway, Published
from sincpro_framework.event_driven.adapters.faststream.queue import Broker
from sincpro_framework.use_bus import UseFramework


def _is_event(command: type) -> bool:
    return isinstance(command, type) and issubclass(command, DomainEvent)


class QueueGateway(Gateway):
    wire = "queue"

    def __init__(
        self,
        broker: Broker | None = None,
        instances: Buses | None = None,
        layers: Iterable[str] = DEFAULT_LAYERS,
        title: str = "sincpro",
        version: str = "1.0.0",
        *,
        options: QueueOptions | None = None,
        exposure: Exposure = Exposure.DECLARED,
        unguarded: bool = False,
        port: Wire[Any] | None = None,
    ) -> None:
        """`broker` and `options` build the wire; a `port` (a `QueueWire`) brings its own."""
        if port is None:
            if broker is None:
                raise TypeError(
                    "QueueGateway needs a FastStream broker, or port=QueueWire(...)"
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
        return self._wire.broker

    @property
    def queue_wire(self) -> QueueWire:
        return self._wire

    def _declared_on_queue(self, published: Published) -> bool:
        _, bus, packed = published
        handler = packed.handler or bus.handler_of(packed.dto) or packed.dto
        declared = bindings_of(handler, bus.replaced_for(packed.dto)).get(self.wire or "")
        _, bound = self._composed_for(packed.dto, handler)
        return declared is not None or bound

    def _published_by_default(self, published: Published) -> bool:
        """Registering a Feature for a DomainEvent already says the context hears it:
        `@queue.hears` only moves it to another group or channel."""
        return _is_event(published[2].dto)

    def _published(self) -> list[Published]:
        """In CATALOG mode, every DomainEvent — and a Command only when it declared its
        channel: a Command is never consumed by default."""
        published = super()._published()
        if self.exposure != Exposure.CATALOG:
            return published
        return [
            one for one in published if _is_event(one[2].dto) or self._declared_on_queue(one)
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
                raise ExposureRefused(problem)
            return resolved
        raise ExposureRefused(
            f"consume(as_={command.__name__}): no bus of this gateway answers it"
        )

    async def consume(
        self,
        message: StreamMessage[Any],
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
        super().__init__(broker, options)
        self._channel_of_name = channel_of_name
        self._group = group

    def derive(self, operation: Operation, group: Group) -> QueueBinding:
        derived = super().derive(operation, group)
        if derived.kind != "hears":
            return derived
        channel = self._channel_of_name(type_name(operation.command))
        return derived.model_copy(update={"channel": channel, "group": self._group})


class _EventsGateway(QueueGateway):
    def _published(self) -> list[Published]:
        return [one for one in super()._published() if _is_event(one[2].dto)]


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
