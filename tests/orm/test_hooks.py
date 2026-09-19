"""Rules and hooks on the SQLAlchemy repository — the store that has units of work.

`tests/ddd/repositories/test_repository_rules.py` pins the mechanism against the in-memory
double. What only exists here is the unit of work, and that is where the reentrancy guard has
to hold: `context()` and `narrowed()` hand back a *different* `Repository` object over the same
data, so a guard kept on the instance let the idiomatic write walk straight out of it.
"""

import pytest

from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.ddd.repositories.repository import Rule
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .models import Thing, a_thing


def test_a_rule_runs_on_the_real_store(database: Database):
    seen: list[Thing] = []
    repository = Repository(database, rules=[Rule(entity=Thing, before_save=seen.append)])

    repository.save(a_thing(1))

    assert [one.name for one in seen] == [a_thing(1).name]


def test_a_rule_that_writes_through_the_repository_is_refused(database: Database):
    repository = Repository(database)

    def writes(thing: Thing) -> None:
        repository.save(a_thing(99))

    repository._rules = (Rule(entity=Thing, before_save=writes),)
    with pytest.raises(ContractViolation, match="wrote through the same repository"):
        repository.save(a_thing(1))


def test_a_rule_cannot_escape_the_guard_by_opening_a_unit_of_work(database: Database):
    """`with repository.context()` is how everything in this codebase writes, and the block
    hands back a new `Repository`. Counted per object, that one started outside the window and
    the rule recursed until the stack ran out — the silent loop the guard exists to prevent.
    """
    depth = {"reached": 0}

    def writes_through_a_unit(thing: Thing) -> None:
        depth["reached"] += 1
        if depth["reached"] > 5:
            raise AssertionError("the guard was escaped; this recursed")
        with repository.context() as unit:
            unit.save(a_thing(99))

    repository = Repository(database)
    repository._rules = (Rule(entity=Thing, before_save=writes_through_a_unit),)

    with pytest.raises(ContractViolation, match="wrote through the same repository"):
        repository.save(a_thing(1))
    assert depth["reached"] == 1  # refused on the first try, never recursed


def test_a_rule_cannot_escape_the_guard_by_narrowing(database: Database):
    """The same hole by the other route: `narrowed()` is also a new object over one store."""
    from sincpro_framework.ddd.criteria import Condition, Criteria

    depth = {"reached": 0}

    def writes_through_a_narrowing(thing: Thing) -> None:
        depth["reached"] += 1
        if depth["reached"] > 5:
            raise AssertionError("the guard was escaped; this recursed")
        repository.narrowed(Criteria(where=Condition(field="size", value=1))).save(
            a_thing(99)
        )

    repository = Repository(database)
    repository._rules = (Rule(entity=Thing, before_save=writes_through_a_narrowing),)

    with pytest.raises(ContractViolation, match="wrote through the same repository"):
        repository.save(a_thing(1))
    assert depth["reached"] == 1


def test_a_rule_may_still_read_through_the_repository(database: Database):
    """Reading from inside a rule is the whole point of one — only writing is refused."""
    found: list[int] = []
    repository = Repository(database)
    repository.save(a_thing(7))

    def looks_around(thing: Thing) -> None:
        found.append(repository.count(Thing).value)

    repository._rules = (Rule(entity=Thing, before_save=looks_around),)
    repository.save(a_thing(8))

    assert found == [1]  # the one already stored, read from inside the rule


def test_two_unrelated_repositories_do_not_share_a_guard(database: Database):
    """One store inside a hook must not refuse a write to a different store."""
    other = Repository(database)
    wrote: list[int] = []

    def writes_elsewhere(thing: Thing) -> None:
        other.save(a_thing(50))
        wrote.append(1)

    repository = Repository(database)
    repository._rules = (Rule(entity=Thing, before_save=writes_elsewhere),)
    repository.save(a_thing(1))

    assert wrote == [1]
