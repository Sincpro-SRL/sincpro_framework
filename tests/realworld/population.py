"""Fills a ledger deterministically, at whatever volume the run asks for.

The same seed gives the same ledger, so a failure at 25 000 entries reproduces at 25 000
entries. Entries are written with `save_all` in batches inside one `context()` each, committed per
batch: the shape a real import has, and the one that keeps memory flat at the stress volume.

What comes out is a `Census`, the facts the tests compare against without counting again.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from random import Random

from sincpro_framework.ddd.criteria import Condition, Operator
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .ledger import ZERO, Account, Entry, Journal, Line, Partner

SEED = 7
BATCH = 500
FIRST_DAY = datetime(2024, 1, 1, 9, 0, 0)
MONTHS = 24
DRAFT_EVERY = 10
"""One entry in ten stays a draft, so the population leaves something to post."""

DRAFT_DAY = datetime(2026, 6, 1, 12, 0)
"""When every draft a test writes is dated: after the population's last day, so a reading test
can leave them out with `POPULATION` and compare against the census."""

POPULATION = Condition(field="posted_at", value="2026-01-01T00:00:00", operator=Operator.LT)
"""The rows the population wrote and nothing a test added since."""

JOURNALS = (("SAL", "Sales"), ("PUR", "Purchases"), ("BNK", "Bank"), ("MSC", "Miscellaneous"))
KINDS = ("asset", "liability", "income", "expense")
CITIES = ("Cochabamba", "La Paz", "Santa Cruz", "Sucre", "Tarija")


@dataclass(frozen=True)
class Census:
    entries: int
    posted: int
    drafts: int
    lines: int
    journals: int
    accounts: int
    partners: int
    first_day: datetime
    last_day: datetime


def money(rng: Random) -> Decimal:
    """An amount with two decimals, never zero: what a real line carries."""
    return Decimal(rng.randint(100, 500_000)) / 100


def build_entry(
    rng: Random,
    number: int,
    journals: list[Journal],
    accounts: list[Account],
    partners: list[Partner],
    posted_at: datetime,
    state: str = "posted",
) -> tuple[Entry, list[Line]]:
    """One balanced entry of two to four lines: the debits on one side, one credit line on
    the other closing the total, a partner on most lines and none on some."""
    journal = rng.choice(journals)
    entry = Entry(
        journal_id=journal.id, reference=f"{journal.code}/{number:06d}", posted_at=posted_at
    )
    debits = [money(rng) for _ in range(rng.randint(1, 3))]
    touched = rng.sample(accounts, len(debits) + 1)
    lines = [
        Line(
            entry_id=entry.id,
            journal_id=journal.id,
            account_id=account.id,
            partner_id=None if rng.random() < 0.15 else rng.choice(partners).id,
            label=f"{entry.reference} line {index}",
            debit=amount,
            credit=ZERO,
            posted_at=posted_at,
        )
        for index, (account, amount) in enumerate(zip(touched, debits), start=1)
    ]
    lines.append(
        Line(
            entry_id=entry.id,
            journal_id=journal.id,
            account_id=touched[-1].id,
            partner_id=rng.choice(partners).id,
            label=f"{entry.reference} counterpart",
            debit=ZERO,
            credit=sum(debits, ZERO),
            posted_at=posted_at,
        )
    )
    entry.state = state
    for line in lines:
        line.entry_state = state
    entry.lines = lines
    return entry, lines


def populate(repository: Repository, entries: int, seed: int = SEED) -> Census:
    """Writes a whole ledger and answers its census.

    1. Master data: four journals, forty accounts, two hundred partners.
    2. Entries spread over two years, one in ten left as a draft, in batches of 500; the
       balance of every account accumulates from the posted lines as they are built.
    3. Final: the accounts are written last, balances included, so the invariant holds from
       the first read.
    """
    rng = Random(seed)
    journals = [Journal(code=code, name=name) for code, name in JOURNALS]
    accounts = [
        Account(
            code=f"{(index // 10) + 1}{index % 10:02d}0",
            name=f"Account {index:02d}",
            kind=KINDS[index % len(KINDS)],
        )
        for index in range(40)
    ]
    partners = [
        Partner(name=f"Partner {index:03d}", city=rng.choice(CITIES)) for index in range(200)
    ]
    balances = {account.id: ZERO for account in accounts}
    span_minutes = MONTHS * 30 * 24 * 60

    with repository.context() as ledger:
        ledger.save_all([*journals, *partners])
        ledger.commit()

    posted = drafts = line_count = 0
    last_day = FIRST_DAY
    for start in range(0, entries, BATCH):
        batch_entries: list[Entry] = []
        batch_lines: list[Line] = []
        for number in range(start, min(start + BATCH, entries)):
            posted_at = FIRST_DAY + timedelta(
                minutes=(number * span_minutes) // max(entries, 1)
            )
            state = "draft" if number % DRAFT_EVERY == DRAFT_EVERY - 1 else "posted"
            entry, lines = build_entry(
                rng, number, journals, accounts, partners, posted_at, state
            )
            if state == "posted":
                posted += 1
                for line in lines:
                    balances[line.account_id] += line.debit - line.credit
            else:
                drafts += 1
            last_day = posted_at
            batch_entries.append(entry)
            batch_lines.extend(lines)
        line_count += len(batch_lines)
        with repository.context() as ledger:
            ledger.save_all(batch_entries)
            ledger.save_all(batch_lines)
            ledger.commit()

    for account in accounts:
        account.balance = balances[account.id]
    with repository.context() as ledger:
        ledger.save_all(accounts)
        ledger.commit()

    return Census(
        entries=entries,
        posted=posted,
        drafts=drafts,
        lines=line_count,
        journals=len(journals),
        accounts=len(accounts),
        partners=len(partners),
        first_day=FIRST_DAY,
        last_day=last_day,
    )
