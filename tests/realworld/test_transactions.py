"""Writing to the ledger the way a service does: batches with a checkpoint per group, two
writers on one row in one process and in two, and what a unit of work leaves behind when it
fails.
"""

import multiprocessing
from datetime import datetime, timezone

import pytest

from sincpro_framework.ddd.exceptions import ContractViolation, StaleAggregate
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .contexts import lines_of
from .ledger import Account, Entry
from .workers import move_account


def post_in_place(unit: Repository, entry_id: str) -> None:
    """One group of a batch: mark the lines first, then post the entry, so a refusal from
    `post` leaves lines that the savepoint has to take back."""
    entry = unit.get(Entry, entry_id)
    assert entry is not None
    entry.lines = list(lines_of(unit, entry.id))
    for line in entry.lines:
        line.entry_state = "posted"
        unit.save(line)
    entry.post()
    unit.save(entry)


def test_a_batch_keeps_the_groups_that_posted_and_undoes_only_the_one_that_did_not(
    ledger: Repository, new_draft
):
    good = [new_draft() for _ in range(4)]
    bad = new_draft(unbalanced=True)
    refused = []

    with ledger.context() as unit:
        for entry in good[:2] + [bad] + good[2:]:
            try:
                with unit.savepoint():
                    post_in_place(unit, entry.id)
            except ContractViolation as error:
                refused.append((entry.id, str(error)))

    assert [entry_id for entry_id, _ in refused] == [bad.id]
    assert "does not balance" in refused[0][1]
    for entry in good:
        stored = ledger.get(Entry, entry.id)
        assert stored is not None and stored.state == "posted"
    assert all(line.entry_state == "draft" for line in lines_of(ledger, bad.id))
    stored_bad = ledger.get(Entry, bad.id)
    assert stored_bad is not None and stored_bad.state == "draft"


def test_a_unit_of_work_that_raises_leaves_nothing_behind(ledger: Repository, new_draft):
    entry = new_draft()

    with pytest.raises(RuntimeError):
        with ledger.context() as unit:
            post_in_place(unit, entry.id)
            raise RuntimeError("the caller changed its mind")

    stored = ledger.get(Entry, entry.id)
    assert stored is not None and stored.state == "draft"
    assert all(line.entry_state == "draft" for line in lines_of(ledger, entry.id))


def test_the_second_of_two_writers_in_one_process_is_stale(
    ledger: Repository, masters: dict[str, list]
):
    account_id = masters["accounts"][5].id
    first = ledger.get(Account, account_id)
    second = ledger.get(Account, account_id)
    assert first is not None and second is not None
    before = first.version

    first.name = "renamed by the first"
    ledger.save(first)
    second.name = "renamed by the second"
    with pytest.raises(StaleAggregate):
        ledger.save(second)

    stored = ledger.get(Account, account_id)
    assert stored is not None
    assert stored.name == "renamed by the first"
    assert stored.version == before + 1


def test_the_second_of_two_writers_in_two_processes_is_stale(
    database_url: str, masters: dict[str, list], census
):
    """Two interpreters, two connections, one row: the version check is in the database,
    not in memory, so it has to hold here too."""
    context = multiprocessing.get_context("spawn")
    loaded, first_saved, outcomes = context.Barrier(2), context.Event(), context.Queue()
    account_id = masters["accounts"][6].id
    writers = [
        context.Process(
            target=move_account,
            args=(database_url, account_id, order, loaded, first_saved, outcomes),
        )
        for order in ("first", "second")
    ]

    for writer in writers:
        writer.start()
    results = dict(outcomes.get(timeout=60) for _ in writers)
    for writer in writers:
        writer.join(timeout=30)

    assert results == {"first": "saved", "second": "StaleAggregate"}


def test_updated_at_is_stamped_on_the_update_and_not_on_the_insert(
    ledger: Repository, new_draft
):
    entry = new_draft()
    fresh = ledger.get(Entry, entry.id)
    assert fresh is not None
    assert fresh.updated_at is None and fresh.version == 1

    with ledger.context() as unit:
        post_in_place(unit, entry.id)

    stored = ledger.get(Entry, entry.id)
    assert stored is not None
    assert stored.version == 2
    assert stored.updated_at is not None
    assert stored.updated_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc)


def test_a_lock_outside_a_unit_of_work_is_refused(
    ledger: Repository, masters: dict[str, list]
):
    with pytest.raises(ContractViolation):
        ledger.get(Account, masters["accounts"][0].id, for_update=True)


def test_a_row_lock_holds_the_second_writer_until_the_first_commits(
    postgres_only, ledger: Repository, masters: dict[str, list]
):
    """Real on Postgres only: the second reader with `skip_locked` sees nothing while the
    first holds the row, and sees it again once the first committed."""
    import threading

    from sincpro_framework.ddd.criteria import Condition, Criteria

    account_id = masters["accounts"][8].id
    held, release = threading.Event(), threading.Event()
    seen_while_held: list[int] = []

    def holder() -> None:
        with ledger.context() as unit:
            unit.get(Account, account_id, for_update=True)
            held.set()
            release.wait(timeout=30)

    thread = threading.Thread(target=holder)
    thread.start()
    held.wait(timeout=30)
    with ledger.context() as unit:
        page = unit.search(
            Account,
            Criteria(where=Condition(field="id", value=account_id)),
            for_update=True,
            skip_locked=True,
        )
        seen_while_held.append(len(page))
    release.set()
    thread.join(timeout=30)

    with ledger.context() as unit:
        after = unit.search(
            Account, Criteria(where=Condition(field="id", value=account_id)), for_update=True
        )

    assert seen_while_held == [0]
    assert len(after) == 1
