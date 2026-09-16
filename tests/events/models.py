"""What the event tests are written against: two events, two commands, an aggregate, and the
bounded contexts that react to them.

Each context is built by a function that takes the list it reports into, so a fixture can hand
out fresh instances per test and a worker process can build its own. A bus that must cross a
process boundary is built by a picklable callable, `ReportingSubscriber`, because a bus itself
does not cross.
"""

import multiprocessing
from dataclasses import dataclass

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.events import Subscriber


class TicketClosed(DomainEvent):
    reason: str


class NobodyListens(DomainEvent):
    pass


class CommandNotify(DataTransferObject):
    text: str


class ResponseNotify(DataTransferObject):
    sent: str


class CommandAudit(DataTransferObject):
    text: str


class ResponseAudit(DataTransferObject):
    logged: str


@dataclass
class Ticket(Entity):
    title: str = ""
    closed: bool = False

    def close(self, reason: str) -> None:
        self.closed = True
        self.record(TicketClosed(reason=reason))


def notifying_bus(heard: list, name: str = "support") -> UseFramework:
    """A bounded context whose Feature takes a Command AND the event — the list registration
    the framework already has, `@bus.feature([CommandNotify, TicketClosed])`."""
    bus = UseFramework(name, log_after_execution=False)
    bus.add_dependency("heard", heard)

    @bus.feature([CommandNotify, TicketClosed])
    class Notify(Feature):
        heard: list

        def execute(self, dto: CommandNotify | TicketClosed) -> ResponseNotify:
            text = dto.text if isinstance(dto, CommandNotify) else dto.reason
            self.heard.append(text)
            return ResponseNotify(sent=text)

    return bus


def auditing_bus(heard: list, name: str = "audit") -> UseFramework:
    """A second bounded context: an ApplicationService registered for a Command AND the event."""
    bus = UseFramework(name, log_after_execution=False)
    bus.add_dependency("heard", heard)

    @bus.app_service([CommandAudit, TicketClosed])
    class Audit(ApplicationService):
        heard: list

        def execute(self, dto: CommandAudit | TicketClosed) -> ResponseAudit:
            text = dto.text if isinstance(dto, CommandAudit) else f"audited {dto.reason}"
            self.heard.append(text)
            return ResponseAudit(logged=text)

    return bus


def failing_bus(name: str = "failing") -> UseFramework:
    """A context whose Feature raises on the event, to see the failure reach the publisher."""
    bus = UseFramework(name, log_after_execution=False)

    @bus.feature(TicketClosed)
    class Fail(Feature):
        def execute(self, dto: TicketClosed) -> None:
            raise RuntimeError("down")

    return bus


class ReportingSubscriber:
    """A picklable `build_subscriber` for a `BackgroundQueue`: it carries the answers queue into
    the worker and builds there two buses — a Feature on one, an ApplicationService on the
    other, both registered for the same event beside a command of their own."""

    def __init__(self, answers: "multiprocessing.Queue") -> None:
        self.answers = answers

    def __call__(self) -> Subscriber:
        answers = self.answers
        first = UseFramework("worker-feature", log_after_execution=False)
        second = UseFramework("worker-app-service", log_after_execution=False)

        @first.feature([CommandNotify, TicketClosed])
        class Report(Feature):
            def execute(self, dto: CommandNotify | TicketClosed) -> None:
                answers.put(
                    ("feature", dto.reason if isinstance(dto, TicketClosed) else dto.text)
                )

        @second.app_service([CommandAudit, TicketClosed])
        class Audit(ApplicationService):
            def execute(self, dto: CommandAudit | TicketClosed) -> None:
                answers.put(
                    ("app_service", dto.reason if isinstance(dto, TicketClosed) else dto.text)
                )

        return Subscriber(first, second)
