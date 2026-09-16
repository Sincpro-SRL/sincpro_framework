"""The bounded contexts of the ledger, each a `UseFramework` of its own.

    ledger        PostEntry            loads the entry with its lines, posts it, publishes
    reporting     UpdateBalances       on `EntryPosted`: moves each account, publishes
    notifications Notify               on `BalanceUpdated`: what a person would be told

Each is built by a function that takes what it depends on, so a fixture hands out fresh
instances per test and a worker process builds its own. The chain is one command in and three
contexts reacting, with `correlation_id` carrying the command through and `causation_id`
naming the event that caused each next one.
"""

from decimal import Decimal

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd.criteria import All, Condition, Criteria
from sincpro_framework.ddd.exceptions import ContractViolation, StaleAggregate
from sincpro_framework.ddd.pagination import Pagination
from sincpro_framework.events import Publisher
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .ledger import ZERO, Account, BalanceUpdated, Entry, EntryPosted, Line, Lines

RETRIES = 5
"""How many times reporting reloads an account another poster moved first. On Postgres the
row lock makes the retry rare; on SQLite, which ignores the lock, it is what keeps two
concurrent postings from losing one of them."""


class CommandPostEntry(DataTransferObject):
    entry_id: str
    correlation_id: str | None = None


class ResponsePostEntry(DataTransferObject):
    entry_id: str
    published: list[str]
    """The ids of the events published, in order."""


class CommandRebuildBalance(DataTransferObject):
    account_id: str


class ResponseBalances(DataTransferObject):
    balances: dict[str, Decimal]


class CommandNotify(DataTransferObject):
    text: str


class ResponseNotify(DataTransferObject):
    sent: str


class Heard(DataTransferObject):
    """One notification as the notifications context received it, envelope included."""

    account_id: str
    balance: Decimal
    correlation_id: str | None
    causation_id: str | None


def lines_of(ledger: Repository, entry_id: str) -> Lines:
    return ledger.fetch_all(
        Lines,
        Criteria(
            where=Condition(field="entry_id", value=entry_id),
            pagination=Pagination(limit=200),
        ),
    )


def ledger_bus(
    repository: Repository, publisher: Publisher, name: str = "ledger"
) -> UseFramework:
    bus = UseFramework(name, log_after_execution=False)
    bus.add_dependency("repository", repository)
    bus.add_dependency("publisher", publisher)

    @bus.feature(CommandPostEntry)
    class PostEntry(Feature):
        repository: Repository
        publisher: Publisher

        def execute(self, dto: CommandPostEntry) -> ResponsePostEntry:
            """1. In one unit of work: load the entry and its lines, post, save both.
            2. Pull what it recorded; the unit of work is committed by then.
            3. Final: publish each event, stamped with the command's correlation."""
            with self.repository.context() as ledger:
                entry = ledger.get(Entry, dto.entry_id)
                if entry is None:
                    raise ContractViolation(f"no entry {dto.entry_id}")
                entry.lines = list(lines_of(ledger, entry.id))
                entry.post()
                ledger.save(entry)
                for line in entry.lines:
                    ledger.save(line)
                recorded = entry.pull_events()

            published = []
            for event in recorded:
                event = event.model_copy(
                    update={"correlation_id": dto.correlation_id or event.event_id}
                )
                self.publisher.publish(event)
                published.append(event.event_id)
            return ResponsePostEntry(entry_id=entry.id, published=published)

    return bus


def reporting_bus(
    repository: Repository, publisher: Publisher, name: str = "reporting"
) -> UseFramework:
    bus = UseFramework(name, log_after_execution=False)
    bus.add_dependency("repository", repository)
    bus.add_dependency("publisher", publisher)

    @bus.feature([CommandRebuildBalance, EntryPosted])
    class UpdateBalances(Feature):
        repository: Repository
        publisher: Publisher

        def execute(self, dto: CommandRebuildBalance | EntryPosted) -> ResponseBalances:
            if isinstance(dto, CommandRebuildBalance):
                return self.rebuild(dto.account_id)
            for attempt in range(RETRIES):
                try:
                    return self.apply(dto)
                except StaleAggregate:
                    if attempt == RETRIES - 1:
                        raise
            raise AssertionError("unreachable")

        def apply(self, event: EntryPosted) -> ResponseBalances:
            """Each touched account moves by the entry's lines on it, locked while it does
            on an engine that locks; then every `BalanceUpdated` goes out, caused by this
            event and correlated with the original command."""
            with self.repository.context() as ledger:
                lines = lines_of(ledger, event.entry_id)
                recorded: list[BalanceUpdated] = []
                balances: dict[str, Decimal] = {}
                for account_id in event.account_ids:
                    account = ledger.get(Account, account_id, for_update=True)
                    assert account is not None
                    mine = lines.filtered(lambda line: line.account_id == account_id)
                    account.apply(
                        mine.sum_by(lambda line: line.debit) or ZERO,
                        mine.sum_by(lambda line: line.credit) or ZERO,
                    )
                    ledger.save(account)
                    balances[account.id] = account.balance
                    recorded.extend(account.pull_events())  # type: ignore[arg-type]

            for update in recorded:
                self.publisher.publish(
                    update.model_copy(
                        update={
                            "correlation_id": event.correlation_id,
                            "causation_id": event.event_id,
                        }
                    )
                )
            return ResponseBalances(balances=balances)

        def rebuild(self, account_id: str) -> ResponseBalances:
            """The balance recomputed from the posted lines, in SQL, over the whole ledger."""
            totals = self.repository.totals(
                Line,
                Criteria(
                    where=All(
                        all=[
                            Condition(field="account_id", value=account_id),
                            Condition(field="entry_state", value="posted"),
                        ]
                    )
                ),
                debit="sum:debit",
                credit="sum:credit",
            )
            balance = Decimal(totals["debit"] or 0) - Decimal(totals["credit"] or 0)
            return ResponseBalances(balances={account_id: balance})

    return bus


def notifications_bus(heard: list[Heard], name: str = "notifications") -> UseFramework:
    bus = UseFramework(name, log_after_execution=False)
    bus.add_dependency("heard", heard)

    @bus.app_service([CommandNotify, BalanceUpdated])
    class Notify(ApplicationService):
        heard: list[Heard]

        def execute(self, dto: CommandNotify | BalanceUpdated) -> ResponseNotify:
            if isinstance(dto, CommandNotify):
                return ResponseNotify(sent=dto.text)
            self.heard.append(
                Heard(
                    account_id=dto.account_id,
                    balance=dto.balance,
                    correlation_id=dto.correlation_id,
                    causation_id=dto.causation_id,
                )
            )
            return ResponseNotify(sent=f"{dto.account_id} is now {dto.balance}")

    return bus
