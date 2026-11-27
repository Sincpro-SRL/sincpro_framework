"""The in-memory store honours data filters as the database does — a Feature tested against it
meets the rule it meets in production: reads filtered, new records stamped, writes outside
refused, a filter turned off per block."""

import pytest

from sincpro_framework import exceptions
from sincpro_framework.context import use_context
from sincpro_framework.data_layer.repositories.memory_repository import MemoryRepository
from sincpro_framework.ddd.criteria import Condition, Criteria, FromContext
from sincpro_framework.ddd.repositories.data_filters import DataFilter, DataFilters
from tests.data_layer.orm.models import ROW_COUNT, Thing, Things, a_thing


class OwnerFilter(DataFilter):
    name = "owner"

    def criteria(self, aggregate):
        if aggregate is not Thing:
            return None
        return Criteria(selection=Condition(field="owner", value=FromContext(key="owner")))

    def stamp(self, record):
        if isinstance(record, Thing) and record.owner is None:
            record.owner = use_context().get("owner")


@pytest.fixture
def things() -> list[Thing]:
    return [a_thing(number) for number in range(ROW_COUNT)]


@pytest.fixture
def filtered(things) -> MemoryRepository:
    return MemoryRepository(*things, filters=[OwnerFilter()])


def _owned(things, owner):
    return {one.thing_id for one in things if one.owner == owner}


def test_reads_are_filtered_by_the_request(filtered, things):
    with use_context().scoped({"owner": "owner-1"}):
        assert {one.thing_id for one in filtered.fetch_all(Things)} == _owned(
            things, "owner-1"
        )
        assert filtered.count(Things).value == len(_owned(things, "owner-1"))
        theirs = next(one for one in things if one.owner == "owner-2")
        assert filtered.get(Things, theirs.thing_id) is None
        assert filtered.browse(Things, [theirs.thing_id]).ids == []


def test_a_new_record_is_stamped_and_one_outside_is_refused(filtered):
    fresh = a_thing(ROW_COUNT + 1)
    fresh.owner = None
    theirs = a_thing(ROW_COUNT + 2)
    theirs.owner = "owner-2"
    with use_context().scoped({"owner": "owner-1"}):
        filtered.save(fresh)
        assert fresh.owner == "owner-1"
        with pytest.raises(exceptions.ProgrammingError, match="data filter owner"):
            filtered.save(theirs)


def test_a_write_by_criteria_reaches_only_what_it_can_read(filtered, things):
    with use_context().scoped({"owner": "owner-1"}):
        changed = filtered.update_all(Things, Criteria(), {"name": "renamed"})
    assert changed == len(_owned(things, "owner-1"))


def test_turned_off_by_name_for_one_block(filtered):
    with use_context().scoped({"owner": "owner-1"}):
        with DataFilters.disabled("owner"):
            assert filtered.count(Things).value == ROW_COUNT
        assert filtered.count(Things).value < ROW_COUNT


def test_the_fingerprint_is_the_request_s_own(filtered):
    with use_context().scoped({"owner": "owner-1"}):
        one = filtered.fingerprint(Things)
    with use_context().scoped({"owner": "owner-2"}):
        assert filtered.fingerprint(Things) != one
