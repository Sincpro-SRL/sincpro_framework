"""Data filters on a database: every door of the store reads through them and every write is
checked against them — a repository, a unit of work joined by another Feature, a plain session.

The filter here is the shape tenancy, ownership or any project rule takes: the field it reads,
and a value of the request's context (`FromContext`) read when the statement runs.
"""

import pytest
from sqlalchemy import func, select, update

from sincpro_framework import exceptions
from sincpro_framework.context import use_context
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.ddd.criteria import Condition, Criteria, FromContext
from sincpro_framework.ddd.repositories.data_filters import DataFilter, DataFilters

from .models import ROW_COUNT, Thing, Things, a_thing, mapper_registry


class OwnerFilter(DataFilter):
    """Things of the owner the context names; a new thing takes that owner."""

    name = "owner"

    def criteria(self, aggregate):
        if aggregate is not Thing:
            return None
        return Criteria(selection=Condition(field="owner", value=FromContext(key="owner")))

    def stamp(self, record):
        if isinstance(record, Thing) and record.owner is None:
            record.owner = use_context().get("owner")


@pytest.fixture
def filtered(things) -> Repository:
    database = Database("sqlite://", filters=[OwnerFilter()])
    mapper_registry.metadata.create_all(database.engine)
    repository = Repository(database)
    with DataFilters.disabled("owner"), repository.context() as unit:
        for one in things:
            unit.save(one)
    return repository


def _owned(things, owner):
    return {one.thing_id for one in things if one.owner == owner}


def test_every_read_of_the_repository_is_filtered_by_the_request(filtered, things):
    with use_context().scoped({"owner": "owner-1"}):
        assert {one.thing_id for one in filtered.fetch_all(Things)} == _owned(
            things, "owner-1"
        )
        assert filtered.count(Things).value == len(_owned(things, "owner-1"))
    with use_context().scoped({"owner": "owner-2"}):
        assert filtered.count(Things).value == len(_owned(things, "owner-2"))


def test_a_record_outside_is_not_found_by_id_nor_by_browse(filtered, things):
    theirs = next(one for one in things if one.owner == "owner-2")
    with use_context().scoped({"owner": "owner-1"}):
        assert filtered.get(Things, theirs.thing_id) is None
        assert filtered.browse(Things, [theirs.thing_id]).ids == []


def test_a_plain_session_reads_through_the_filters_too(filtered, things):
    with use_context().scoped({"owner": "owner-1"}), filtered.context() as unit:
        count = unit.session.scalar(select(func.count()).select_from(Thing))
        rows = unit.session.scalars(select(Thing)).all()
    assert count == len(_owned(things, "owner-1"))
    assert {one.thing_id for one in rows} == _owned(things, "owner-1")


def test_an_update_by_a_plain_session_reaches_only_what_it_can_read(filtered, things):
    with use_context().scoped({"owner": "owner-1"}), filtered.context() as unit:
        unit.session.execute(update(Thing).values(name="renamed"))
    with DataFilters.disabled("owner"):
        renamed = {
            one.thing_id for one in filtered.fetch_all(Things) if one.name == "renamed"
        }
    assert renamed == _owned(things, "owner-1")


def test_a_new_record_is_stamped_from_the_context(filtered):
    fresh = a_thing(ROW_COUNT + 1)
    fresh.owner = None
    with use_context().scoped({"owner": "owner-1"}):
        filtered.save(fresh)
        assert filtered.get(Things, fresh.thing_id).owner == "owner-1"


def test_writing_outside_the_filter_is_refused_whichever_door(filtered, things):
    theirs = a_thing(ROW_COUNT + 2)
    theirs.owner = "owner-2"
    with use_context().scoped({"owner": "owner-1"}):
        with pytest.raises(exceptions.ProgrammingError, match="data filter owner"):
            filtered.save(theirs)
        with pytest.raises(exceptions.ProgrammingError, match="data filter owner"):
            with filtered.context() as unit:
                unit.session.add(theirs)


def test_turned_off_by_name_for_one_block_only(filtered):
    with use_context().scoped({"owner": "owner-1"}):
        with DataFilters.disabled("owner"):
            assert filtered.count(Things).value == ROW_COUNT
        assert filtered.count(Things).value < ROW_COUNT


def test_two_requests_never_share_a_fingerprint(filtered):
    with use_context().scoped({"owner": "owner-1"}):
        one = filtered.fingerprint(Things)
    with use_context().scoped({"owner": "owner-2"}):
        other = filtered.fingerprint(Things)
    assert one != other


def test_a_filter_naming_a_field_the_model_lacks_is_refused(things):
    class Broken(DataFilter):
        name = "broken"

        def criteria(self, aggregate):
            return Criteria(selection=Condition(field="nowhere", value=1))

    database = Database("sqlite://", filters=[Broken()])
    mapper_registry.metadata.create_all(database.engine)
    repository = Repository(database)
    with pytest.raises(exceptions.ProgrammingError, match="cannot answer the data filters"):
        repository.count(Things)


def test_a_database_without_filters_reads_as_before(store):
    assert store.count(Things).value == ROW_COUNT


def test_a_narrowing_by_a_value_of_the_context_writes_what_it_reads(store):
    """A scope written with `FromContext` judges a write by the request's value — before, every
    write was refused, because the check compared the record with the unread placeholder."""
    mine = a_thing(ROW_COUNT + 3)
    mine.owner = "owner-1"
    scoped = store.narrowed(
        Criteria(selection=Condition(field="owner", value=FromContext(key="owner")))
    )
    with use_context().scoped({"owner": "owner-1"}):
        scoped.save(mine)
        assert scoped.get(Things, mine.thing_id) is not None
