"""A ledger small enough to race on: owners with a unique name, accounts that point at them and
may never go below zero.

Owner.name          unique, archivable     a second one is a DuplicateAggregate
Account.owner_id    NOT NULL, a real FK    a missing owner is a ConstraintViolation
Account.balance     CHECK balance >= 0     an overdraft is a ConstraintViolation
Account.reference   unique, nullable       a NULL never conflicts
"""

from dataclasses import dataclass

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd.entity import ArchivableMixin, Entity
from sincpro_framework.orm.sqlalchemy.entrypoint.templates import (
    archive_columns,
    entity_table,
)
from sincpro_framework.orm.sqlalchemy.services.data_mapper import map_aggregates


@dataclass
class Owner(ArchivableMixin, Entity):
    name: str = ""


@dataclass
class Account(Entity):
    owner_id: str = ""
    balance: int = 0
    reference: str | None = None

    def withdraw(self, amount: int) -> None:
        self.balance -= amount


bank = registry()
owner_table = entity_table(
    "owner",
    bank.metadata,
    Column("name", Text, nullable=False, unique=True),
    *archive_columns(),
)
account_table = entity_table(
    "account",
    bank.metadata,
    Column("owner_id", Text, ForeignKey("owner.id"), nullable=False),
    Column("balance", Integer, nullable=False),
    Column("reference", Text, nullable=True, unique=True),
    CheckConstraint("balance >= 0", name="account_balance_not_negative"),
)


def declare() -> None:
    map_aggregates(bank, {Owner: owner_table, Account: account_table})
