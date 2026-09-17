"""One command, three contexts: `PostEntry` publishes `EntryPosted`, reporting moves the
accounts and publishes `BalanceUpdated`, notifications hears each one. The envelope is what
is checked at the end of the chain: the command's correlation on every event, and the event
that caused each next one named by `causation_id`.
"""

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.events import Publisher, Subscriber, SyncQueue
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .contexts import (
    CommandPostEntry,
    CommandRebuildBalance,
    Heard,
    ResponseBalances,
    ResponsePostEntry,
    ledger_bus,
)
from .ledger import ZERO, Account, Entry


def expected_moves(entry: Entry) -> dict[str, object]:
    """What each account should move by when this entry is posted."""
    moves: dict[str, object] = {}
    for line in entry.lines:
        moves[line.account_id] = moves.get(line.account_id, ZERO) + line.debit - line.credit  # type: ignore[operator]
    return moves


def balances_of(ledger: Repository, account_ids) -> dict[str, object]:
    return {
        account.id: account.balance for account in ledger.browse(Account, list(account_ids))
    }


def test_posting_moves_every_touched_account_and_notifications_hears_each(
    ledger_context: UseFramework,
    reporting: UseFramework,
    ledger: Repository,
    new_draft,
    heard: list[Heard],
):
    entry = new_draft()
    moves = expected_moves(entry)
    before = balances_of(ledger, moves)

    response = ledger_context(
        CommandPostEntry(entry_id=entry.id, correlation_id="req-1"), ResponsePostEntry
    )

    assert response is not None and len(response.published) == 1
    after = balances_of(ledger, moves)
    assert after == {account_id: before[account_id] + moves[account_id] for account_id in moves}  # type: ignore[operator]
    assert {notice.account_id for notice in heard} == set(moves)
    assert all(notice.correlation_id == "req-1" for notice in heard)
    assert all(notice.causation_id == response.published[0] for notice in heard)
    assert {notice.account_id: notice.balance for notice in heard} == after
    for account_id in moves:
        rebuilt = reporting(CommandRebuildBalance(account_id=account_id), ResponseBalances)
        assert rebuilt is not None and rebuilt.balances[account_id] == after[account_id]


def test_posting_twice_is_refused_and_nothing_more_is_heard(
    ledger_context: UseFramework, new_draft, heard: list[Heard]
):
    entry = new_draft()
    ledger_context(CommandPostEntry(entry_id=entry.id), ResponsePostEntry)
    heard_once = list(heard)

    with pytest.raises(ContractViolation):
        ledger_context(CommandPostEntry(entry_id=entry.id), ResponsePostEntry)

    assert heard == heard_once


def test_an_unbalanced_entry_is_refused_before_anything_moves(
    ledger_context: UseFramework, ledger: Repository, new_draft, heard: list[Heard]
):
    entry = new_draft(unbalanced=True)
    before = balances_of(ledger, expected_moves(entry))

    with pytest.raises(ContractViolation):
        ledger_context(CommandPostEntry(entry_id=entry.id), ResponsePostEntry)

    stored = ledger.get(Entry, entry.id)
    assert stored is not None and stored.state == "draft"
    assert balances_of(ledger, expected_moves(entry)) == before
    assert heard == []


def test_the_typed_publish_waits_for_reporting_to_answer(
    reporting: UseFramework, ledger: Repository, post_by_hand
):
    entry, posted = post_by_hand()

    answer = Publisher(SyncQueue(Subscriber(reporting))).publish(posted, ResponseBalances)

    assert set(answer.balances) == set(expected_moves(entry))
    assert answer.balances == balances_of(ledger, answer.balances)


def test_the_chain_crosses_a_process_boundary(
    ledger: Repository, background_queue, answers, new_draft
):
    """Reporting and notifications live in the worker with a database of their own over the
    same file; the entry is posted here, and the balances are read back here."""
    entry = new_draft()
    moves = expected_moves(entry)
    before = balances_of(ledger, moves)
    posting = ledger_bus(ledger, Publisher(background_queue), name="ledger-background")

    response = posting(
        CommandPostEntry(entry_id=entry.id, correlation_id="req-bg"), ResponsePostEntry
    )
    notices = [Heard.model_validate(answers.get(timeout=60)) for _ in moves]

    assert response is not None
    assert {notice.account_id for notice in notices} == set(moves)
    assert all(notice.correlation_id == "req-bg" for notice in notices)
    assert all(notice.causation_id == response.published[0] for notice in notices)
    assert balances_of(ledger, moves) == {
        account_id: before[account_id] + moves[account_id] for account_id in moves  # type: ignore[operator]
    }
