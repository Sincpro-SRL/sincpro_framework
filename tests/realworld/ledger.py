"""An accounting ledger: the domain the real-world suite is written against.

Journals, accounts, partners, and entries made of lines; an entry is posted once, only when
its debits equal its credits, and posting says so with `EntryPosted`. An account keeps a
running balance and says `BalanceUpdated` when it moves. Double entry gives the suite an
invariant that holds at any volume: over posted lines, the sum of debits equals the sum of
credits, and every account's balance equals the sum of what was posted to it.

Plain dataclasses in this module, tables at the bottom, one `map_aggregates`; the classes
never learn where they live.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Column, DateTime, ForeignKey, Index, Numeric, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd.entity import Entity, Translated
from sincpro_framework.ddd.entity_collection import EntityCollection
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.orm.sqlalchemy.data_mapper import entity_table, map_aggregates

ZERO = Decimal("0.00")


class EntryPosted(DomainEvent):
    entry_id: str
    journal_id: str
    account_ids: list[str]


class BalanceUpdated(DomainEvent):
    account_id: str
    balance: Decimal


@dataclass
class Journal(Entity):
    code: str
    name: str


@dataclass
class Account(Entity):
    code: str
    name: str
    kind: str
    balance: Decimal = ZERO

    @classmethod
    def translations(cls) -> Translated:
        return {
            "name": {"default": "Account", "es": "Cuenta"},
            "labels": {
                "code": {"default": "Code", "es": "Código"},
                "name": {"default": "Name", "es": "Nombre"},
                "kind": {"default": "Kind", "es": "Tipo"},
                "balance": {"default": "Balance", "es": "Saldo"},
            },
        }

    def apply(self, debit: Decimal, credit: Decimal) -> None:
        """Moves the balance by what one posted entry put on this account, and says so."""
        self.balance = self.balance + debit - credit
        self.record(BalanceUpdated(account_id=self.id, balance=self.balance))


@dataclass
class Partner(Entity):
    name: str
    city: str


@dataclass
class Line(Entity):
    entry_id: str
    journal_id: str
    account_id: str
    partner_id: str | None
    label: str
    debit: Decimal
    credit: Decimal
    posted_at: datetime
    entry_state: str = "draft"
    """The state of the entry, kept on the line as well, so a report over lines can ask for
    the posted ones without a join: what a ledger does for the question it is asked most."""


@dataclass
class Entry(Entity):
    journal_id: str
    reference: str
    posted_at: datetime
    state: str = "draft"

    @property
    def lines(self) -> list[Line]:
        """The lines this entry was loaded or built with. Not a column: the repository hands
        them to the entry, so a record the mapper rebuilt starts with none."""
        return self.__dict__.setdefault("_lines", [])

    @lines.setter
    def lines(self, lines: list[Line]) -> None:
        self.__dict__["_lines"] = list(lines)

    @property
    def is_balanced(self) -> bool:
        return sum((line.debit for line in self.lines), ZERO) == sum(
            (line.credit for line in self.lines), ZERO
        )

    def post(self) -> None:
        """The rule the whole suite hangs on: once, balanced, and said aloud.

        1. Refuse a second posting and an entry without lines.
        2. Refuse an unbalanced entry; the invariant over the ledger depends on it.
        3. Final: mark it and its lines posted, and record `EntryPosted` with every account
           it touched.
        """
        if self.state == "posted":
            raise ContractViolation(f"entry {self.reference} is already posted")
        if not self.lines:
            raise ContractViolation(f"entry {self.reference} has no lines")
        if not self.is_balanced:
            raise ContractViolation(f"entry {self.reference} does not balance")
        self.state = "posted"
        for line in self.lines:
            line.entry_state = "posted"
        self.record(
            EntryPosted(
                entry_id=self.id,
                journal_id=self.journal_id,
                account_ids=sorted({line.account_id for line in self.lines}),
            )
        )


class Journals(EntityCollection[Journal]):
    pass


class Accounts(EntityCollection[Account]):
    pass


class Partners(EntityCollection[Partner]):
    pass


class Entries(EntityCollection[Entry]):
    pass


class Lines(EntityCollection[Line]):
    pass


mapper_registry = registry()
metadata = mapper_registry.metadata

journal_table = entity_table(
    "journal",
    metadata,
    Column("code", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
)

account_table = entity_table(
    "account",
    metadata,
    Column("code", Text, nullable=False, unique=True),
    Column("name", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("balance", Numeric(14, 2), nullable=False),
)

partner_table = entity_table(
    "partner",
    metadata,
    Column("name", Text, nullable=False),
    Column("city", Text, nullable=False),
)

entry_table = entity_table(
    "entry",
    metadata,
    Column("journal_id", Text, ForeignKey("journal.id"), nullable=False),
    Column("reference", Text, nullable=False),
    Column("posted_at", DateTime, nullable=False),
    Column("state", Text, nullable=False),
    Index("entry_journal_posted_at", "journal_id", "posted_at"),
)

line_table = entity_table(
    "line",
    metadata,
    Column("entry_id", Text, ForeignKey("entry.id"), nullable=False),
    Column("journal_id", Text, ForeignKey("journal.id"), nullable=False),
    Column("account_id", Text, ForeignKey("account.id"), nullable=False),
    Column("partner_id", Text, ForeignKey("partner.id")),
    Column("label", Text, nullable=False),
    Column("debit", Numeric(14, 2), nullable=False),
    Column("credit", Numeric(14, 2), nullable=False),
    Column("posted_at", DateTime, nullable=False),
    Column("entry_state", Text, nullable=False),
    Index("line_posted_at_id", "posted_at", "id"),
    Index("line_journal_account", "journal_id", "account_id"),
    Index("line_account_state", "account_id", "entry_state"),
    Index("line_entry", "entry_id"),
)

map_aggregates(
    mapper_registry,
    {
        Journal: journal_table,
        Account: account_table,
        Partner: partner_table,
        Entry: entry_table,
        Line: line_table,
    },
)
