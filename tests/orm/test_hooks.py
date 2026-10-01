"""Hooks on the SQLAlchemy repository — the store that has units of work.

`tests/ddd/repositories/test_repository_hooks.py` pins the mechanism against the in-memory
double. What only exists here is the unit of work, and that is where the reentrancy guard has
to hold: `context()` and `narrowed()` hand back a *different* `Repository` object over the same
data, so a guard kept on the instance let the idiomatic write walk straight out of it.
"""

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.ddd.criteria import Condition, Criteria
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.ddd.repositories import Hook, Hooks
from sincpro_framework.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.orm.sqlalchemy.infrastructure.database import Database

from .models import Thing, a_thing


class ThingHook(Hook):
    repository: Repository
    other: Repository
    seen: list


def _wired(database: Database, hooks: Hooks) -> tuple[Repository, UseFramework]:
    """The repository, and a bus that holds it — the hooks read it as `self.repository`."""
    bus = UseFramework("things", log_after_execution=False)
    hooks.inject(bus)
    repository = Repository(database, hooks)
    bus.add_dependency("repository", repository)
    bus.add_dependency("seen", [])
    return repository, bus


def test_a_hook_runs_on_the_real_store(database: Database):
    hooks = Hooks(None)

    @hooks.on(Thing)
    class Sees(ThingHook):
        def before_save(self, thing: Thing) -> None:
            self.seen.append(thing.name)

    repository, bus = _wired(database, hooks)
    repository.save(a_thing(1))

    assert bus.deps.seen == [a_thing(1).name]


def test_a_hook_that_writes_through_the_repository_is_refused(database: Database):
    hooks = Hooks(None)

    @hooks.on(Thing)
    class Writes(ThingHook):
        def before_save(self, thing: Thing) -> None:
            self.repository.save(a_thing(99))

    repository, _bus = _wired(database, hooks)

    with pytest.raises(ContractViolation, match="wrote through the same repository"):
        repository.save(a_thing(1))


def test_a_hook_cannot_escape_the_guard_by_opening_a_unit_of_work(database: Database):
    """`with repository.context()` hands back a new `Repository`. Counted per object, that one
    started outside the window and the hook recursed until the stack ran out."""
    hooks = Hooks(None)
    depth = {"reached": 0}

    @hooks.on(Thing)
    class WritesThroughAUnit(ThingHook):
        def before_save(self, thing: Thing) -> None:
            depth["reached"] += 1
            if depth["reached"] > 5:
                raise AssertionError("the guard was escaped; this recursed")
            with self.repository.context() as unit:
                unit.save(a_thing(99))

    repository, _bus = _wired(database, hooks)

    with pytest.raises(ContractViolation, match="wrote through the same repository"):
        repository.save(a_thing(1))
    assert depth["reached"] == 1


def test_a_hook_cannot_escape_the_guard_by_narrowing(database: Database):
    """The same hole by the other route: `narrowed()` is also a new object over one store."""
    hooks = Hooks(None)
    depth = {"reached": 0}

    @hooks.on(Thing)
    class WritesThroughANarrowing(ThingHook):
        def before_save(self, thing: Thing) -> None:
            depth["reached"] += 1
            if depth["reached"] > 5:
                raise AssertionError("the guard was escaped; this recursed")
            narrowed = self.repository.narrowed(
                Criteria(where=Condition(field="size", value=1))
            )
            narrowed.save(a_thing(99))

    repository, _bus = _wired(database, hooks)

    with pytest.raises(ContractViolation, match="wrote through the same repository"):
        repository.save(a_thing(1))
    assert depth["reached"] == 1


def test_a_unit_of_work_runs_the_hooks_of_the_repository_it_came_from(database: Database):
    hooks = Hooks(None)

    @hooks.on(Thing)
    class Sees(ThingHook):
        def before_save(self, thing: Thing) -> None:
            self.seen.append(thing.name)

    repository, bus = _wired(database, hooks)
    with repository.context() as unit:
        unit.save(a_thing(2))

    assert bus.deps.seen == [a_thing(2).name]


def test_a_hook_may_still_read_through_the_repository(database: Database):
    """Reading from inside a hook is the whole point of one — only writing is refused."""
    hooks = Hooks(None)

    @hooks.on(Thing)
    class LooksAround(ThingHook):
        def before_save(self, thing: Thing) -> None:
            self.seen.append(self.repository.count(Thing).value)

    repository, bus = _wired(database, hooks)
    Repository(database).save(a_thing(7))
    repository.save(a_thing(8))

    assert bus.deps.seen == [1]


def test_two_unrelated_repositories_do_not_share_a_guard(database: Database):
    """One store inside a hook must not refuse a write to a different store."""
    hooks = Hooks(None)

    @hooks.on(Thing)
    class WritesElsewhere(ThingHook):
        def before_save(self, thing: Thing) -> None:
            if thing.name != a_thing(50).name:
                self.other.save(a_thing(50))
                self.seen.append(1)

    repository, bus = _wired(database, hooks)
    bus.add_dependency("other", Repository(database))
    repository.save(a_thing(1))

    assert bus.deps.seen == [1]
