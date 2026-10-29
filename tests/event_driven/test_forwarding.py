"""Forwarding: a publisher that hands its messages over and returns at once, while one thread of its
own sends them in order — waiting out a broker that is down, setting aside what can never go.
"""

import threading
from dataclasses import dataclass
from datetime import timedelta

import pytest

from sincpro_framework import exceptions
from sincpro_framework.circuit_breaker import CircuitBreaker
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.event_driven import Forwarded, Forwarding, ForwardingHook, Publisher

QUICKLY = timedelta(milliseconds=5)
WAIT = 5


@dataclass(kw_only=True)
class TicketClosed(DomainEvent):
    name = "tests.forwarding.v1.ticket_closed"
    ticket: str = ""


class Broker:
    """A queue whose broker is down while `down`, and refuses for good what `refused` names."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.down = False
        self.refused: set[str] = set()

    def put(self, message: TicketClosed) -> None:
        if message.ticket in self.refused:
            raise exceptions.ClientError(f"{message.ticket} does not fit the channel")
        if self.down:
            raise ConnectionError("the broker is down")
        self.sent.append(message.ticket)

    async def aput(self, message: TicketClosed) -> None:
        self.put(message)


class Heard(ForwardingHook):
    def __init__(self) -> None:
        self.delivered: list[tuple[str, int]] = []
        self.failed: list[tuple[str, bool]] = []
        self.first_failure = threading.Event()

    def after_delivery(self, message: TicketClosed, forwarded: Forwarded) -> None:
        self.delivered.append((message.ticket, forwarded.attempts))

    def after_failure(self, message: TicketClosed, forwarded: Forwarded) -> None:
        self.failed.append((message.ticket, forwarded.set_aside))
        self.first_failure.set()


def forwarding(*hooks: ForwardingHook) -> Forwarding:
    breaker = CircuitBreaker(
        "tests.forwarding", failures=1000, open_for=QUICKLY, store=InMemoryKeyValue()
    )
    return Forwarding(breaker=breaker, hooks=list(hooks), wait=QUICKLY, longest_wait=QUICKLY)


def test_publish_returns_while_the_broker_is_down_and_everything_goes_in_order_once_it_is_back():
    broker = Broker()
    broker.down = True
    heard = Heard()
    publisher = Publisher(broker, forwarding=forwarding(heard))

    pending = [publisher.publish(TicketClosed(ticket=f"T-{n}")) for n in range(3)]
    assert not any(one.done() for one in pending)

    assert heard.first_failure.wait(WAIT)
    broker.down = False
    answered = [one.result(timeout=WAIT) for one in pending]

    assert broker.sent == ["T-0", "T-1", "T-2"]
    assert answered[0].attempts > 1
    assert all(one.delivered_at is not None for one in answered)


def test_a_message_that_can_never_go_is_set_aside_and_the_rest_go_on():
    broker = Broker()
    broker.refused = {"T-1"}
    heard = Heard()
    publisher = Publisher(broker, forwarding=forwarding(heard))

    pending = [publisher.publish(TicketClosed(ticket=f"T-{n}")) for n in range(3)]
    answered = [one.result(timeout=WAIT) for one in pending]

    assert broker.sent == ["T-0", "T-2"]
    assert answered[1].set_aside and answered[1].delivered_at is None
    assert "does not fit" in (answered[1].last_error or "")
    assert heard.failed == [("T-1", True)]
    assert heard.delivered == [("T-0", 1), ("T-2", 1)]


def test_every_failed_attempt_is_heard_before_the_delivery():
    broker = Broker()
    broker.down = True
    heard = Heard()
    publisher = Publisher(broker, forwarding=forwarding(heard))

    pending = publisher.publish(TicketClosed(ticket="T-0"))
    assert heard.first_failure.wait(WAIT)
    broker.down = False
    answered = pending.result(timeout=WAIT)

    assert heard.failed[0] == ("T-0", False)
    assert heard.delivered == [("T-0", answered.attempts)]


def test_a_forwarded_publish_cannot_wait_for_an_answer():
    publisher = Publisher(Broker(), forwarding=True)

    with pytest.raises(exceptions.ProgrammingError, match="forwarded"):
        publisher.publish(TicketClosed(ticket="T-0"), TicketClosed)
