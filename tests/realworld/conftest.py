"""One populated ledger per session, fresh contexts per test, and a place for timings.

**The database is built once and populated once.** At the default volume that is two thousand
entries and around six thousand lines; at the stress volume it is what a small company has
after a few years. Every test that writes creates its own entries, so the census the reading
tests compare against never changes under them.

**Volume and engine come from the environment**, not from the code:

    SINCPRO_REALWORLD_ENTRIES   how many entries; 2 000 by default, 25 000 under `make test-stress`
    DATABASE_URL                any SQLAlchemy URL; a SQLite file under the pytest tmp dir by default

Every test in this directory is marked `realworld`; `make test` leaves them out.
"""

import multiprocessing
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from random import Random
from time import perf_counter

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.events import BackgroundQueue, Publisher, Subscriber, SyncQueue
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .contexts import Heard, ledger_bus, lines_of, notifications_bus, reporting_bus
from .databases import open_database
from .ledger import Accounts, Entry, EntryPosted, Journals, Partners, metadata
from .population import DRAFT_DAY, Census, build_entry, populate
from .workers import LedgerSubscriber

DEFAULT_ENTRIES = 2_000


@dataclass
class Timing:
    name: str
    seconds: float
    rows: int


TIMINGS: list[Timing] = []


def pytest_collection_modifyitems(items) -> None:
    """Everything under this directory is `realworld`; the stress tests say so themselves."""
    here = os.path.dirname(__file__)
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(pytest.mark.realworld)


def pytest_terminal_summary(terminalreporter) -> None:
    if not TIMINGS:
        return
    terminalreporter.section("real-world timings")
    width = max(len(timing.name) for timing in TIMINGS)
    for timing in TIMINGS:
        rate = f"{timing.rows / timing.seconds:,.0f} rows/s" if timing.seconds else ""
        terminalreporter.write_line(
            f"{timing.name.ljust(width)}  {timing.seconds:8.3f}s  {timing.rows:>9,} rows  {rate}"
        )


@pytest.fixture(scope="session")
def volume() -> int:
    return int(os.environ.get("SINCPRO_REALWORLD_ENTRIES", DEFAULT_ENTRIES))


@pytest.fixture(scope="session")
def database_url(tmp_path_factory) -> str:
    configured = os.environ.get("DATABASE_URL")
    if configured:
        return configured
    return f"sqlite:///{tmp_path_factory.mktemp('realworld')}/ledger.sqlite3"


@pytest.fixture(scope="session")
def database(database_url: str) -> Database:
    """The ledger's database with its tables, empty."""
    fresh = open_database(database_url)
    metadata.drop_all(fresh.engine)
    metadata.create_all(fresh.engine)
    return fresh


@pytest.fixture(scope="session")
def census(database: Database, volume: int) -> Census:
    """Populates once, and answers the facts the tests compare against."""
    started = perf_counter()
    facts = populate(Repository(database), volume)
    TIMINGS.append(Timing("populate", perf_counter() - started, facts.lines + facts.entries))
    return facts


@pytest.fixture
def ledger(database: Database, census: Census) -> Repository:
    """The repository every test reads and writes through, over the populated ledger."""
    return Repository(database)


@pytest.fixture(scope="session")
def masters(database: Database, census: Census) -> dict[str, list]:
    """Journals, accounts and partners as stored, loaded once."""
    repository = Repository(database)
    return {
        "journals": list(repository.fetch_all(Journals)),
        "accounts": list(repository.fetch_all(Accounts)),
        "partners": list(repository.fetch_all(Partners)),
    }


@pytest.fixture
def new_draft(ledger: Repository, masters: dict[str, list]) -> Callable[..., Entry]:
    """Writes a fresh, balanced draft entry with its lines and hands it back; each call a new
    one, so a test that posts never touches what another test reads."""
    counter = iter(range(10_000_000, 20_000_000))
    rng = Random(perf_counter())

    def make(unbalanced: bool = False) -> Entry:
        entry, lines = build_entry(
            rng,
            next(counter),
            masters["journals"],
            masters["accounts"],
            masters["partners"],
            DRAFT_DAY,
            state="draft",
        )
        if unbalanced:
            lines[-1].credit += 1
        with ledger.context() as unit:
            unit.save(entry)
            unit.save(lines)
        entry.lines = lines
        return entry

    return make


@pytest.fixture
def post_by_hand(ledger: Repository, new_draft) -> Callable[[], tuple[Entry, EntryPosted]]:
    """Posts a fresh draft in one unit of work, lines included, without any bus, and hands
    back the entry and the `EntryPosted` it recorded: for the tests that publish themselves.
    """

    def post() -> tuple[Entry, EntryPosted]:
        entry = new_draft()
        with ledger.context() as unit:
            stored = unit.get(Entry, entry.id)
            assert stored is not None
            stored.lines = list(lines_of(unit, entry.id))
            stored.post()
            unit.save(stored)
            for line in stored.lines:
                unit.save(line)
            [posted] = stored.pull_events()
        return stored, posted  # type: ignore[return-value]

    return post


@pytest.fixture
def heard() -> list[Heard]:
    return []


@pytest.fixture
def notifications(heard: list[Heard]) -> UseFramework:
    return notifications_bus(heard)


@pytest.fixture
def reporting(ledger: Repository, notifications: UseFramework) -> UseFramework:
    """Reporting publishes to notifications, synchronously."""
    return reporting_bus(ledger, Publisher(SyncQueue(Subscriber(notifications))))


@pytest.fixture
def publisher(reporting: UseFramework) -> Publisher:
    """What the ledger context publishes through: a sync queue with reporting on it."""
    return Publisher(SyncQueue(Subscriber(reporting)))


@pytest.fixture
def ledger_context(ledger: Repository, publisher: Publisher) -> UseFramework:
    return ledger_bus(ledger, publisher)


@pytest.fixture
def answers() -> "multiprocessing.Queue":
    return multiprocessing.get_context("spawn").Queue()


@pytest.fixture
def background_queue(database_url: str, answers, census: Census) -> Iterator[BackgroundQueue]:
    """Reporting and notifications in another process, over the same database file."""
    queue = BackgroundQueue(LedgerSubscriber(database_url, answers)).start()
    yield queue
    queue.stop()


@pytest.fixture
def timed() -> Callable[[str, int], "_Timer"]:
    """`with timed("walk lines", rows):` records how long the block took, for the summary."""
    return _Timer


class _Timer:
    def __init__(self, name: str, rows: int) -> None:
        self.name = name
        self.rows = rows

    def __enter__(self) -> "_Timer":
        self.started = perf_counter()
        return self

    def __exit__(self, *_: object) -> None:
        TIMINGS.append(Timing(self.name, perf_counter() - self.started, self.rows))


@pytest.fixture
def postgres_only(database: Database) -> None:
    if database.engine.dialect.name != "postgresql":
        pytest.skip("row locks are real on Postgres only; SQLite ignores FOR UPDATE")
