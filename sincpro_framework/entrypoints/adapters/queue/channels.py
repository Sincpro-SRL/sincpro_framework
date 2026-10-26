"""Which channel an event is heard on — what `subscribe` maps every event the buses registered
through.

    by_name("billing.invoice.v1.issued")         ->  "billing.invoice.v1.issued"
    event_names(Subscriber(billing, accounting))  ->  ["billing.invoice.v1.issued", ...]
"""

from collections.abc import Callable

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.event_driven import Subscriber

type ChannelOfName = Callable[[str], str]


def by_name(name: str) -> str:
    """The default `channel_of_name`: an event is heard on the channel its name is.

        by_name("billing.invoice.v1.issued")  ->  "billing.invoice.v1.issued"

    Pass another `channel_of_name` to map names to channels otherwise.
    """
    return name


def event_names(subscriber: Subscriber) -> list[str]:
    """The wire names of every event some bus of the subscriber registered, in order.

        event_names(Subscriber(billing, accounting))
        ->  ["billing.invoice.v1.issued", "billing.invoice.v1.voided"]

    A name two buses registered is listed once; a Command registered is not an event.
    """
    names: dict[str, None] = {}
    for bus in subscriber.buses:
        for name, registered in bus.dto_registry.items():
            if isinstance(registered, type) and issubclass(registered, DomainEvent):
                names[name] = None
    return list(names)
