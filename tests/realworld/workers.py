"""What runs in another process: a second writer, and a subscriber with its own database.

Module-level and picklable, because `multiprocessing` with `spawn` imports this module in the
child and calls what it was handed. A bus, a repository or a session never cross; a URL does,
and the child builds its own.
"""

import multiprocessing
from typing import Any

from sincpro_framework.ddd.exceptions import StaleAggregate
from sincpro_framework.events import Publisher, Subscriber, SyncQueue

from .contexts import Heard, notifications_bus, reporting_bus
from .databases import open_ledger
from .ledger import Account


def move_account(
    url: str,
    account_id: str,
    order: str,
    loaded: Any,
    first_saved: Any,
    outcomes: Any,
) -> None:
    """One of two writers racing on the same account.

        both      load the account, then wait for each other at `loaded`
        first     saves, commits, then releases `first_saved`
        second    waits for `first_saved`, then saves what it loaded before

    The second one is holding a version the row has moved past, and reports what the save
    answered: `StaleAggregate`, or the name of anything else.
    """
    ledger = open_ledger(url)
    try:
        with ledger.context() as unit:
            account = unit.get(Account, account_id)
            assert account is not None
            loaded.wait()
            if order == "second":
                first_saved.wait()
            account.name = f"{account.name} by {order}"
            unit.save(account)
        if order == "first":
            first_saved.set()
        outcomes.put((order, "saved"))
    except StaleAggregate:
        outcomes.put((order, "StaleAggregate"))
    except Exception as error:  # pragma: no cover - reported, then asserted on
        outcomes.put((order, type(error).__name__))


class LedgerSubscriber:
    """The picklable `build_subscriber` for a `BackgroundQueue`: in the worker it opens its
    own database and builds reporting and notifications there, reporting each notification
    into `answers`."""

    def __init__(self, url: str, answers: "multiprocessing.Queue") -> None:
        self.url = url
        self.answers = answers

    def __call__(self) -> Subscriber:
        ledger = open_ledger(self.url)
        heard: list[Heard] = []
        answers = self.answers

        class Reporting(list):
            def append(self, item: Heard) -> None:
                answers.put(item.model_dump(mode="json"))

        notifications = notifications_bus(Reporting(heard))
        reporting = reporting_bus(ledger, Publisher(SyncQueue(Subscriber(notifications))))
        return Subscriber(reporting, notifications)
