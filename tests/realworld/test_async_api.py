"""The same chain through the async door: many postings at once through `get_async_bus()`,
and the async publisher's typed answer. Concurrency is where the reporting retry earns its
place: entries share accounts, so two postings move one account at the same time.
"""

import asyncio

from sincpro_framework import UseFramework
from sincpro_framework.events import Publisher, Subscriber, SyncQueue
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .contexts import (
    CommandPostEntry,
    CommandRebuildBalance,
    Heard,
    ResponseBalances,
    ResponsePostEntry,
)
from .ledger import Account, Entry

CONCURRENT = 30


def post_all(bus: UseFramework, entries: list[Entry]) -> list[ResponsePostEntry]:
    async def run() -> list[ResponsePostEntry]:
        posting = bus.get_async_bus()
        return await asyncio.gather(
            *(
                posting(
                    CommandPostEntry(entry_id=entry.id, correlation_id=f"req-{index}"),
                    ResponsePostEntry,
                )
                for index, entry in enumerate(entries)
            )
        )

    return asyncio.run(run())


def test_concurrent_postings_leave_every_balance_right(
    ledger_context: UseFramework,
    reporting: UseFramework,
    ledger: Repository,
    new_draft,
    heard: list[Heard],
    timed,
):
    entries = [new_draft() for _ in range(CONCURRENT)]
    touched = {line.account_id for entry in entries for line in entry.lines}

    with timed(f"{CONCURRENT} concurrent postings, async bus", CONCURRENT):
        responses = post_all(ledger_context, entries)

    assert len(responses) == CONCURRENT
    assert all(len(response.published) == 1 for response in responses)
    for account in ledger.browse(Account, sorted(touched)):
        rebuilt = reporting(CommandRebuildBalance(account_id=account.id), ResponseBalances)
        assert rebuilt is not None and rebuilt.balances[account.id] == account.balance
    assert {notice.correlation_id for notice in heard} == {
        f"req-{i}" for i in range(CONCURRENT)
    }
    assert len(heard) == sum(
        len({line.account_id for line in entry.lines}) for entry in entries
    )


def test_the_async_publisher_answers_typed(reporting: UseFramework, post_by_hand):
    entry, posted = post_by_hand()
    publisher = Publisher(SyncQueue(Subscriber(reporting))).get_async_publisher()

    answer = asyncio.run(publisher.publish(posted, ResponseBalances))

    assert set(answer.balances) == {line.account_id for line in entry.lines}
