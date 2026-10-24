"""The unit of work of the repository in memory keeps the promises the database's keeps, so a
Feature that writes in `context()` is tested without a database and without mocking the block:
everything or nothing, a change never saved put back, a claimed row held until the block ends.
"""

import threading
import time
from dataclasses import dataclass

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework, exceptions
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.ddd.criteria import Condition, Criteria
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.entity_collection import EntityCollection


@dataclass
class Seat(Entity):
    code: str = ""
    holder: str = ""


class Seats(EntityCollection[Seat]):
    pass


class Boom(Exception):
    pass


def a_hall(*codes: str) -> tuple[MemoryRepository, list[Seat]]:
    seats = [Seat(code=code) for code in codes]
    return MemoryRepository(*seats), seats


def seat_of(hall: MemoryRepository, seat: Seat) -> Seat:
    found = hall.get(Seat, seat.id)
    assert found is not None
    return found


def test_what_a_block_saved_stays_when_it_ends():
    hall, [seat] = a_hall("A1")

    with hall.context() as unit:
        held = unit.get(Seat, seat.id)
        assert held is not None
        held.holder = "ana"
        unit.save(held)

    assert seat_of(hall, seat).holder == "ana"


def test_a_block_that_raises_leaves_everything_as_it_was():
    hall, [first, second] = a_hall("A1", "A2")

    with pytest.raises(Boom):
        with hall.context() as unit:
            held = unit.get(Seat, first.id)
            assert held is not None
            held.holder = "ana"
            unit.save(held)
            unit.remove(unit.get(Seat, second.id))
            unit.save(Seat(code="B9"))
            unit.update_all(Seat, Criteria(), {"holder": "bo"})
            raise Boom

    kept = hall.fetch_all(Seats)
    assert sorted((one.code, one.holder) for one in kept) == [("A1", ""), ("A2", "")]
    assert first.holder == "" and first.version == 0  # the object held took its values back


def test_a_change_never_saved_is_put_back_when_the_block_ends():
    hall, [seat] = a_hall("A1")

    with hall.context() as unit:
        held = unit.get(Seat, seat.id)
        assert held is not None
        held.holder = "nobody saved this"

    assert seat_of(hall, seat).holder == ""


def test_what_waits_for_the_end_runs_only_when_it_is_its_end():
    hall, _ = a_hall("A1")
    said: list[str] = []

    with hall.context() as unit:
        unit.after_commit(lambda: said.append("committed"))
        unit.after_rollback(lambda: said.append("never"))
    with pytest.raises(Boom):
        with hall.context() as unit:
            unit.after_commit(lambda: said.append("never"))
            unit.after_rollback(lambda: said.append("undone"))
            raise Boom

    assert said == ["committed", "undone"]
    with pytest.raises(exceptions.ProgrammingError, match="belongs to a unit of work"):
        hall.after_commit(lambda: None)


def test_a_block_inside_another_joins_it():
    hall, [seat] = a_hall("A1")

    with pytest.raises(Boom):
        with hall.context() as outer:
            with outer.context() as inner:
                held = inner.get(Seat, seat.id)
                assert held is not None
                held.holder = "ana"
                inner.save(held)
            raise Boom  # the outer one undoes what the inner one saved

    assert seat_of(hall, seat).holder == ""


def test_a_claim_outside_a_unit_of_work_is_refused():
    hall, [seat] = a_hall("A1")

    with pytest.raises(exceptions.ProgrammingError, match="needs a unit of work"):
        hall.get(Seat, seat.id, for_update=True)


def test_a_claimed_row_is_held_until_the_block_ends():
    hall, [seat] = a_hall("A1")
    order: list[str] = []
    claimed = threading.Event()

    def first() -> None:
        with hall.context() as unit:
            unit.get(Seat, seat.id, for_update=True)
            claimed.set()
            time.sleep(0.2)
            order.append("first done")

    def second() -> None:
        claimed.wait()
        with hall.context() as unit:
            unit.get(Seat, seat.id, for_update=True)
            order.append("second claimed")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for one in threads:
        one.start()
    for one in threads:
        one.join()

    assert order == ["first done", "second claimed"]


def test_skip_locked_passes_over_and_nowait_refuses():
    hall, [held_by_other, free] = a_hall("A1", "A2")
    claimed, done = threading.Event(), threading.Event()

    def holder() -> None:
        with hall.context() as unit:
            unit.get(Seat, held_by_other.id, for_update=True)
            claimed.set()
            done.wait()

    thread = threading.Thread(target=holder)
    thread.start()
    claimed.wait()
    try:
        with hall.context() as unit:
            open_seats = unit.search(Seats, Criteria(), for_update=True, skip_locked=True)
            assert [one.code for one in open_seats] == [free.code]
            assert unit.get(Seat, held_by_other.id, skip_locked=True) is None
            with pytest.raises(ddd_exceptions.TimedOut):
                unit.get(Seat, held_by_other.id, nowait=True)
    finally:
        done.set()
        thread.join()


def test_one_and_get_by_claim_and_answer_not_found_as_the_engine():
    hall, [seat] = a_hall("A1")

    with hall.context() as unit:
        assert unit.get_by(Seat, True, code="A1") is seat
        assert (
            unit.one(Seat, Criteria(where=Condition(field="code", value="A1")), True) is seat
        )
        with pytest.raises(ddd_exceptions.AggregateNotFound):
            unit.one(Seat, Criteria(where=Condition(field="code", value="Z9")))


class CommandTakeSeat(DataTransferObject):
    code: str
    holder: str


class TakenElsewhere(ddd_exceptions.DomainError):
    pass


def test_a_feature_that_writes_in_a_unit_of_work_needs_no_mock():
    hall, _ = a_hall("A1")
    bus = UseFramework("memory-unit-of-work", log_after_execution=False)
    bus.add_dependency("repository", hall)

    @bus.feature(CommandTakeSeat)
    class TakeSeat(Feature):
        def execute(self, dto: CommandTakeSeat) -> None:
            with self.repository.context() as unit:  # type: ignore[attr-defined]
                seat = unit.get_by(Seat, True, code=dto.code)
                if seat.holder:
                    raise TakenElsewhere(f"{dto.code} is taken")
                seat.holder = dto.holder
                unit.save(seat)

    bus(CommandTakeSeat(code="A1", holder="ana"))
    with pytest.raises(TakenElsewhere):
        bus(CommandTakeSeat(code="A1", holder="bo"))

    taken = hall.get_by(Seat, code="A1")
    assert taken is not None and taken.holder == "ana"
