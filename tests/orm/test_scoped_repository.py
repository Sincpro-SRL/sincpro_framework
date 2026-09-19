"""A repository narrowed to what a caller may see: every reading filtered, every write checked,
and an aggregate that cannot express the scope refused outright.

The last one is the test worth reading twice. A scope that is silently dropped reads the whole
table, which is the one failure this exists to prevent, so it fails loudly instead.
"""

import pytest

from sincpro_framework.ddd.criteria import Condition, Criteria, Operator
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .models import ROW_COUNT, Note, Notes, Thing, Things, a_thing

MINE = Criteria(where=Condition(field="owner", value="owner-1"))
BIG = Criteria(where=Condition(field="size", value=2, operator=Operator.GTE))


@pytest.fixture
def scoped(store: Repository) -> Repository:
    return store.narrowed(MINE)


def test_every_reading_is_filtered(scoped: Repository, store: Repository, things):
    mine = [one for one in things if one.owner == "owner-1"]

    assert scoped.count(Things).value == len(mine)
    assert {one.thing_id for one in scoped.fetch_all(Things)} == {
        one.thing_id for one in mine
    }
    assert (
        scoped.exists(Things, Criteria(where=Condition(field="owner", value="owner-2")))
        is False
    )
    assert store.count(Things).value == ROW_COUNT  # the wide one is untouched


def test_a_record_outside_the_scope_is_not_found_by_id_or_by_browse(
    scoped: Repository, things
):
    theirs = next(one for one in things if one.owner == "owner-2")

    assert scoped.get(Things, theirs.thing_id) is None
    assert scoped.browse(Things, [theirs.thing_id]).ids == []


def test_the_scope_reaches_grouping_and_totals(scoped: Repository, things):
    mine = [one for one in things if one.owner == "owner-1"]

    assert scoped.measures(Things, None, weight=("sum", "size")) == {
        "weight": sum(one.size for one in mine)
    }
    assert {row["owner"] for row in scoped.group_by(Things, ["owner"])} == {"owner-1"}


def test_writing_outside_the_scope_is_refused(scoped: Repository):
    theirs = a_thing(2)  # owner-2

    with pytest.raises(ContractViolation, match="outside what this repository"):
        scoped.save(theirs)
    with pytest.raises(ContractViolation):
        scoped.remove(theirs)
    with pytest.raises(ContractViolation):
        scoped.save([a_thing(1), theirs])


def test_writing_inside_the_scope_goes_through(scoped: Repository):
    mine = Thing(
        thing_id="th_9001",
        name="mine",
        size=1,
        tags=[],
        made_at=a_thing(1).made_at,
        owner="owner-1",
    )

    scoped.save(mine)

    assert scoped.get(Things, "th_9001") is not None


def test_narrowing_again_narrows_further(scoped: Repository, things):
    tighter = scoped.narrowed(BIG)

    kept = [one for one in things if one.owner == "owner-1" and one.size >= 2]
    assert tighter.count(Things).value == len(kept)
    assert tighter.count(Things).value < scoped.count(Things).value


def test_an_aggregate_that_cannot_answer_the_scope_is_refused_and_not_read_wide(
    store: Repository,
):
    """`Note` has no `owner`: dropping the condition would hand over every note."""
    scoped = store.narrowed(MINE)
    store.save(Note(title="not mine"))

    with pytest.raises(ContractViolation, match="cannot answer the scope"):
        scoped.search(Notes)
    with pytest.raises(ContractViolation):
        scoped.count(Notes)


def test_a_unit_of_work_of_a_scoped_repository_stays_scoped(scoped: Repository, things):
    with scoped.context() as unit:
        assert unit.count(Things).value == len(
            [one for one in things if one.owner == "owner-1"]
        )
        with pytest.raises(ContractViolation):
            unit.save(a_thing(2))
