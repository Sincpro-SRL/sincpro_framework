"""An event recorded, published or saved inside an execution is caused by it and joins its flow —
by the same rule `caused_by` follows — unless it already says otherwise (PRD_21 §2, phase 2).
"""

from dataclasses import dataclass

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.context import Execution, chained, current_execution
from sincpro_framework.ddd import DomainEvent, Entity
from sincpro_framework.ddd.repositories import MemoryRepository
from sincpro_framework.event_driven import Publisher, RepositoryQueue


@dataclass(kw_only=True)
class InvoiceEvent(DomainEvent):
    name = "tests.identity.invoice.v1.event"


@dataclass(kw_only=True)
class InvoiceIssued(InvoiceEvent):
    name = "tests.identity.invoice.v1.issued"


@dataclass
class Invoice(Entity):
    total: int = 0

    def issue(self) -> None:
        self.record(InvoiceIssued())


class CommandIssue(DataTransferObject):
    pass


class Sink:
    def __init__(self) -> None:
        self.sent: list[DomainEvent] = []

    def put(self, event: DomainEvent) -> None:
        self.sent.append(event)

    async def aput(self, event: DomainEvent) -> None:
        self.sent.append(event)


def _run(work) -> Execution:  # type: ignore[no-untyped-def]
    """`work()` inside one execution; the execution it ran in."""
    bus = UseFramework("identity-events", log_after_execution=False)
    ran: list[Execution] = []

    @bus.feature(CommandIssue)
    class Issue(Feature):
        def execute(self, dto: CommandIssue) -> None:
            ran.append(current_execution())  # type: ignore[arg-type]
            work()

    bus(CommandIssue())
    return ran[0]


def test_a_recorded_event_is_caused_by_the_execution_recording_it():
    invoice = Invoice(total=10)
    execution = _run(invoice.issue)

    [event] = invoice.pull_events()
    assert event.causation_id == execution.execution_id
    assert event.correlation_id == execution.correlation_id


def test_a_published_event_goes_out_in_its_chain_and_the_one_handed_in_is_untouched():
    sink = Sink()
    handed = InvoiceIssued()
    execution = _run(lambda: Publisher(sink).publish(handed))

    [sent] = sink.sent
    assert sent.causation_id == execution.execution_id
    assert handed.causation_id is None
    assert sent.id == handed.id


def test_an_event_saved_on_its_own_joins_the_chain():
    repository = MemoryRepository()
    closed = InvoiceIssued()
    execution = _run(lambda: repository.save([closed]))

    [kept] = repository.fetch_all(InvoiceIssued).items
    assert kept.causation_id == execution.execution_id


def test_an_event_kept_through_the_outbox_queue_joins_the_chain():
    repository = MemoryRepository()
    execution = _run(lambda: Publisher(RepositoryQueue(repository)).publish(InvoiceIssued()))

    [kept] = repository.fetch_all(InvoiceIssued).items
    assert kept.correlation_id == execution.correlation_id


def test_what_an_event_already_says_is_kept():
    sink = Sink()
    cause = InvoiceIssued(correlation_id="flow-elsewhere")
    _run(lambda: Publisher(sink).publish(InvoiceIssued().caused_by(cause)))

    [sent] = sink.sent
    assert (sent.causation_id, sent.correlation_id) == (cause.id, "flow-elsewhere")


def test_an_event_already_stored_keeps_its_own_history():
    """A relay re-publishing yesterday's event does not make today's execution its cause."""
    sink = Sink()
    stored = InvoiceIssued()
    stored.version = 1
    _run(lambda: Publisher(sink).publish(stored))

    assert sink.sent[0].causation_id is None


def test_outside_an_execution_nothing_is_stamped():
    sink = Sink()
    Publisher(sink).publish(InvoiceIssued())

    assert sink.sent[0].causation_id is None and sink.sent[0].correlation_id is None


def test_an_event_and_an_execution_follow_one_rule():
    cause = InvoiceIssued()
    effect = InvoiceIssued().caused_by(cause)

    assert (effect.causation_id, effect.correlation_id) == chained(cause.id, None, "unused")
    assert chained(None, None, "own") == (None, "own")
    assert chained(None, "flow", "own") == (None, "flow")
    assert chained("cause", "flow", "own") == ("cause", "flow")
