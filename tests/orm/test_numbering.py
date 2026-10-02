"""Gapless numbers: taken in the unit of work that saves what carries them, given back by its
rollback, and never the same twice however many transactions take at once."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import MetaData

from sincpro_framework.ddd import MemoryNumbering
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.orm import Numbering, Repository, numbering_table
from sincpro_framework.orm.sqlalchemy.entrypoint import numbering as numbering_module

from .engines import fresh
from .ledger_models import Owner, bank, declare

counters = numbering_table("numbering", bank.metadata)


@pytest.fixture
def ledger(engine_url: str) -> Repository:
    declare()
    return Repository(fresh(engine_url, bank.metadata))


@pytest.fixture
def numbering(ledger: Repository) -> Numbering:
    return Numbering(ledger.database, counters)


@pytest.fixture(params=["one statement", "update then insert"])
def either_path(request, numbering, monkeypatch) -> Numbering:
    """Both ways a counter is taken, on every engine: the upsert, and the path the dialects
    without one take."""
    if request.param == "update then insert":
        monkeypatch.setattr(numbering_module, "WITH_UPSERT", {})
    return numbering


def test_the_first_numbers_of_a_series_start_at_one(ledger, either_path):
    with ledger.context():
        assert either_path.take("F", count=3) == range(1, 4)
        assert either_path.next_number("F") == 4


def test_n_numbers_are_one_contiguous_run(ledger, either_path):
    with ledger.context():
        either_path.take("F", count=2)
    with ledger.context():
        assert list(either_path.take("F", count=5)) == [3, 4, 5, 6, 7]


def test_each_scope_counts_on_its_own(ledger, either_path):
    with ledger.context():
        assert either_path.next_number("F", scope="branch-1/2026") == 1
        assert either_path.next_number("F", scope="branch-2/2026") == 1
        assert either_path.next_number("F", scope="branch-1/2026") == 2


def test_a_rollback_gives_the_numbers_back(ledger, either_path):
    with ledger.context():
        either_path.take("F", count=10)
    with pytest.raises(RuntimeError):
        with ledger.context() as unit:
            either_path.take("F", count=3)
            unit.save(Owner(name="ana"))
            raise RuntimeError("the invoice failed")

    with ledger.context():
        assert either_path.next_number("F") == 11
    assert ledger.count(Owner).value == 0


def test_outside_a_unit_of_work_a_number_is_refused(numbering):
    with pytest.raises(ContractViolation, match="inside the context"):
        numbering.next_number("F")


def test_a_read_only_unit_of_work_takes_no_number(ledger, numbering):
    with ledger.context(read_only=True):
        with pytest.raises(ContractViolation, match="read_only"):
            numbering.next_number("F")


def test_at_least_one_number_is_taken(ledger, numbering):
    with ledger.context():
        with pytest.raises(ContractViolation, match="at least one"):
            numbering.take("F", count=0)


def test_transactions_taking_at_once_never_share_a_number(ledger, either_path):
    if ledger.database.engine.dialect.name == "sqlite":
        pytest.skip("an in-memory SQLite has one connection: there is no second transaction")

    def till() -> list[int]:
        taken: list[int] = []
        for _ in range(20):
            with ledger.context():
                taken.extend(either_path.take("F", count=2))
        return taken

    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = [number for run in pool.map(lambda _: till(), range(4)) for number in run]

    assert sorted(runs) == list(range(1, 161))


def test_the_double_counts_the_same_way():
    numbering = MemoryNumbering()
    assert numbering.take("F", count=2) == range(1, 3)
    assert numbering.next_number("F", scope="other") == 1
    assert numbering.next_number("F") == 3


def test_the_table_is_the_projects_to_declare():
    metadata = MetaData()
    table = numbering_table("invoice_numbers", metadata)
    assert [column.name for column in table.primary_key] == ["series", "scope"]
