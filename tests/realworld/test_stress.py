"""The same questions at the stress volume, timed. `make test-stress` runs this alone with
`SINCPRO_REALWORLD_ENTRIES=25000`; the assertions are about correctness, the timings go to
the summary at the end of the run so a regression is a number and not an impression.
"""

import pytest

from sincpro_framework.ddd.criteria import CountMode, Criteria, parse_order
from sincpro_framework.ddd.pagination import Pagination
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .contexts import CommandRebuildBalance, ResponseBalances
from .ledger import Account, Line, Lines
from .population import Census
from .test_async_api import post_all
from .test_reporting import FOUR_LEVELS, leaves

pytestmark = pytest.mark.stress

POSTINGS = 100


def test_walk_every_line_in_pages_of_a_thousand(ledger: Repository, census: Census, timed):
    criteria = Criteria(order=parse_order("-posted_at"), pagination=Pagination(limit=1000))

    with timed("stress: keyset walk, pages of 1000", census.lines):
        seen = sum(len(page) for page in ledger.stream(Lines, criteria))

    assert seen == census.lines


def test_exact_and_capped_counts(ledger: Repository, census: Census, timed):
    with timed("stress: capped count", census.lines):
        capped = ledger.count(Line)
    with timed("stress: exact count", census.lines):
        exact = ledger.count(Line, Criteria(count=CountMode.EXACT))

    assert exact.value == census.lines
    assert capped.value <= exact.value


def test_group_four_levels_over_everything(ledger: Repository, census: Census, timed):
    with timed("stress: grouping, four levels", census.lines):
        tree = ledger.group_by_levels(Line, FOUR_LEVELS)

    flat = leaves(tree)
    assert sum(count for count, _ in flat.values()) == sum(bucket.count for bucket in tree)
    assert len(flat) > census.accounts


def test_a_hundred_concurrent_postings(
    ledger_context, reporting, ledger: Repository, new_draft, heard, timed
):
    entries = [new_draft() for _ in range(POSTINGS)]
    touched = sorted({line.account_id for entry in entries for line in entry.lines})

    with timed(f"stress: {POSTINGS} concurrent postings", POSTINGS):
        responses = post_all(ledger_context, entries)

    assert len(responses) == POSTINGS
    for account in ledger.browse(Account, touched):
        rebuilt = reporting(CommandRebuildBalance(account_id=account.id), ResponseBalances)
        assert rebuilt is not None and rebuilt.balances[account.id] == account.balance
