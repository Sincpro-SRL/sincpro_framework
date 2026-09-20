"""The least a repository answers, and the two ways to put something around it.

    class Repository(ABC):
        get · search · count · save · remove · archive          the writes and the store
        browse · fetch_all · stream · first · one · get_by      the readings a use case makes
        exists · pluck · distinct · measures · group_by         …and the ones it asks in SQL

        after_read · before_save · after_save       the hooks, empty, for a subclass
        before_remove · after_remove

    Repository(database, rules=[Rule(entity=Invoice, before_save=check_totals)])

**An abstract class rather than a protocol.** It was a `Protocol` and nothing was written
against it structurally: the two implementations inherit it, and the double a test uses is
`MemoryRepository`, not a hand-rolled stand-in. What the protocol could not give is what an
implementation now gets: `@abstractmethod` refuses a store that forgot a method, and the type
checker compares signatures — `isinstance` against a `@runtime_checkable` protocol compares
*names only*, so five methods taking the wrong arguments passed it.

**Two ways to put something around a read or a write, for two different kinds of thing.**

A *mixin* overrides the hooks when the behaviour belongs to the store itself and ships with it
— `ChangeTrackingRepositoryMixin` is the framework's own. A *rule* is injected when the
behaviour belongs to this deployment and to one aggregate: an invariant, a derived field, a
policy. Rules never travel through a hook override, so a mixin that forgets `super()` cannot
switch them off.

**A rule is a domain service: a plain function over one aggregate.** It runs inside the write,
inside the transaction. It does not publish, it does not call a bus, and it does not write —
writing from inside a hook is refused, because the alternative is a loop nobody sees until
production. Anything that needs several aggregates, or makes a decision, is a use case: a
Feature, not a rule.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Sequence
from contextvars import ContextVar
from copy import copy
from threading import Lock
from typing import Any

from sincpro_framework.ddd.criteria import Bucket, Criteria
from sincpro_framework.ddd.entity.entity_collection import (
    Count,
    EntityCollection,
    model_and_collection,
)
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.ddd.repositories.hooks import (
    MOMENTS,
    Hook,
    Hooks,
    Rule,
    _MomentOfHook,
    callbacks_of,
)


def records_of(given: Any) -> list[Any]:
    """What a write was handed, always as a list: one aggregate, or several.

        in  invoice                    →  out  [invoice]
        in  [a, b]  ·  a page  ·  ()   →  out  [a, b]  ·  [its records]  ·  []

    One `save` and one `remove` take either, so a caller never picks a method by how many it
    holds. An `Entity` is not iterable and a collection is, which is the whole test — nothing
    inspects types beyond that.
    """
    if isinstance(given, Iterable) and not isinstance(given, (str, bytes)):
        return list(given)
    return [given]


_running: ContextVar[frozenset[int]] = ContextVar("sincpro_hooks_running")
"""Which repositories are inside a hook **right now, on this thread or task**.

A plain attribute cannot say this. A repository is built once per bounded context and shared,
so two threads writing through it would each see the other's window and refuse a write neither
of them made — and a pair of windows closed out of order would leave the flag raised for good,
with every later write refused and no way back. A `ContextVar` is per thread and per async
task, and `reset(token)` restores exactly what this block found.
"""


class _Hooks:
    """Marks the window a hook runs in, so a write from inside one is refused rather than
    quietly recursing. Re-entrant by design: a hook that reads is fine, and reading fires
    `after_read`, which is another window inside this one."""

    __slots__ = ("_guard", "_token")

    def __init__(self, repository: "Repository") -> None:
        self._guard = id(repository._guard)
        self._token: Any = None

    def __enter__(self) -> None:
        self._token = _running.set(_running.get(frozenset()) | {self._guard})

    def __exit__(self, *_: Any) -> None:
        _running.reset(self._token)


def refuse_wiring_as_a_record(records: "tuple[Any, ...]") -> None:
    """Refuses wiring that was handed in where aggregates go.

    A store whose first arguments are the records it is seeded with will happily take a `Hooks`
    collection as one of them — and then it holds zero rules, fires nothing, and says nothing.
    A suite written that way passes without ever running the rules it was written to prove,
    which is worse than any error.

    Named for what to write instead, because the two stores do not agree on the position:
    `Repository(database, billing_hooks)` on the engine, `hooks=` here.
    """
    wrong = [one for one in records if isinstance(one, (Hooks, Hook)) or _is_hook_class(one)]
    if wrong:
        named = wrong[0].__name__ if isinstance(wrong[0], type) else type(wrong[0]).__name__
        raise ContractViolation(
            f"{named} was passed where aggregates go, so it would be stored as a record and "
            "never run — write it as `hooks=` instead"
        )


def _is_hook_class(given: Any) -> bool:
    return isinstance(given, type) and issubclass(given, Hook)


def answers(declared: type, name: str) -> bool:
    """Whether instances of this class really have that method.

    Read off the MRO rather than with `getattr`, which for a dunder on a *class* finds the
    metaclass's: `getattr(AnyClass, "__call__")` is always something, because every class is
    callable. Asking that way, a hook that implements nothing looked like it implemented
    `__call__` and was refused much later, by a message about a missing dependency.
    """
    return any(name in base.__dict__ for base in declared.__mro__)


def refuse_locking(for_update: bool) -> None:
    """Refuses a row lock in a store that has no unit of work to hold it for.

    A lock taken by a call that commits on its way out is released before the caller can act on
    it, so it protects nothing. The engine refuses this outside `context()`; a store with no
    `context()` at all is always outside one, and **a double that quietly allowed it would let
    a Feature pass every test and then lose the race in production.**
    """
    if for_update:
        raise ContractViolation(
            "for_update needs a unit of work to hold the lock until, and this store has "
            "none — the row would be released before the caller could act on it"
        )


def refuse_unarchivable(records: list[Any]) -> None:
    """Refuses anything that cannot say it was archived, before a single one is touched.

    Checked for the whole batch first so a mixed list is refused whole rather than half
    archived — `archive` either applies to all of them or to none.
    """
    from sincpro_framework.ddd.entity import ArchivableMixin

    wrong = {type(one).__name__ for one in records if not isinstance(one, ArchivableMixin)}
    if wrong:
        raise ContractViolation(
            f"{', '.join(sorted(wrong))} cannot be archived: it does not inherit "
            "ArchivableMixin, so there is nowhere to stamp when it left — `remove` deletes it"
        )


class Repository(ABC):
    """What a use case can be written against, whatever is underneath."""

    def __init__(
        self,
        rules: Sequence[Rule] | None = None,
        hooks: Iterable["Hook | type[Hook]"] | None = None,
        deps: Any = None,
        guard: "Repository | None" = None,
    ) -> None:
        """`deps` is what a `Hook` in these rules resolves its attributes against — normally
        `bus.deps`, the read-only locator over everything `add_dependency` registered. A rule
        written as a plain function needs none of it.

        `guard` is the repository this one counts as, for refusing a write from inside a hook.
        A unit of work and a narrowing are the same store seen differently, so they name the
        one they came from; left out, a repository is its own.
        """
        self._deps = deps
        self._guard: "Repository" = guard if guard is not None else self
        self._collection = hooks if isinstance(hooks, Hooks) else None
        self._instances: dict[int, Hook] = {}
        self._building = Lock()
        rules = list(rules or ()) + [self._as_rule(one) for one in (hooks or ())]
        # Prepared once, into new rules rather than over the ones handed in: a caller's list is
        # theirs, and a `Hook` bound in place would follow it into another repository.
        self._rules: tuple[Rule, ...] = tuple(
            rule.model_copy(
                update={
                    moment: [
                        self._prepared(callback, moment)
                        for callback in callbacks_of(getattr(rule, moment))
                    ]
                    for moment in MOMENTS
                }
            )
            for rule in rules
        )

    def _as_rule(self, hook: "Hook | type[Hook]") -> Rule:
        """A hook that declares `entity` is a rule already: its moments are the methods it
        implements. Written this way there is no plumbing to repeat — the class says what it
        is for and what it does, and nothing else has to say it again.
        """
        declared = hook if isinstance(hook, type) else type(hook)
        entity = getattr(declared, "entity", object)
        # Read off the class, never the instance: asking an instance would go through
        # `__getattr__` and look for a dependency by that name. And nothing is built here —
        # see `_moment_of`.
        moments = {
            moment: _MomentOfHook(hook, moment)
            for moment in MOMENTS
            if getattr(declared, moment, None) is not None
        }
        if not moments:
            raise ContractViolation(
                f"{declared.__name__} implements none of {', '.join(MOMENTS)}; "
                "a hook that does nothing at any moment would never run"
            )
        return Rule(entity=entity, **moments)

    def _prepared(self, callback: Any, moment: str) -> Any:
        """Every hook becomes a moment this repository owns; a plain function is left exactly
        as it came.

        **The slot it was written in says which method to call.** A hook put in `before_save=`
        that implements `before_save` is called there; one that implements only `__call__` is
        called as that, which is how a hook written for a single job reads. Asking for
        `__call__` regardless made the first kind fail at its first fire, looking for a
        dependency named `__call__`.

        **A moment handed in is rebuilt rather than reused.** `context()` and `narrowed()` pass
        the rules they were prepared from, and binding those in place would reach back into the
        repository they came from — the thing the copy above exists to prevent.
        """
        if isinstance(callback, _MomentOfHook):
            return _MomentOfHook(callback.hook, callback.moment).bind(self)
        declared = callback if isinstance(callback, type) else type(callback)
        if isinstance(declared, type) and issubclass(declared, Hook):
            named = moment if answers(declared, moment) else "__call__"
            if not answers(declared, named):
                raise ContractViolation(
                    f"{declared.__name__} implements neither {moment} nor __call__, so "
                    "nothing here could call it"
                )
            return _MomentOfHook(callback, named).bind(self)
        return callback

    def _deps_now(self) -> Any:
        """What a hook resolves names against, read at the moment it fires.

        A collection carries its own; anything handed in loose falls back to this repository's,
        and the nearer one wins. **Asked now rather than copied at construction**, because
        `Hooks().inject(bus.deps)` is normally called after the repository exists — the bus
        holding the dependencies is usually built around the store, not before it.
        """
        if self._collection is not None and self._collection.deps is not None:
            return self._collection.deps
        return self._deps

    def _hook_instance(self, moment: "_MomentOfHook") -> "Hook":
        """The one instance of this hook that belongs to this store, built on first fire.

        Kept on the guard, which is the store rather than the object: a unit of work and a
        narrowing are the same store seen differently, so a hook is not rebuilt for each block.
        A hook handed in already built is copied, so the caller's object is never bound behind
        their back and two stores never share one.
        """
        owner = self._guard
        key = id(moment.hook)
        built = owner._instances.get(key)
        if built is not None:
            return built
        with owner._building:
            # Asked again inside the lock: two threads reaching a hook's first fire together
            # would otherwise each build one, and a hook whose `__init__` takes a connection or
            # a handle would leak the copy that lost.
            built = owner._instances.get(key)
            if built is None:
                built = moment.hook() if isinstance(moment.hook, type) else copy(moment.hook)
                owner._instances[key] = built.bind(owner._deps_now())
            return built

    def _read(self, record: Any) -> Any:
        """The rules that named this aggregate, then the store's own hook — which closes the
        moment, so what it records is the record as everything else left it."""
        if record is None:
            return None
        with self._running_hooks():
            for rule in self._rules_for(record):
                for callback in callbacks_of(rule.after_read):
                    answered = callback(record)
                    record = record if answered is None else answered
            answered = self.after_read(record)
            record = record if answered is None else answered
        return record

    def _fire(self, moment: str, record: Any) -> None:
        """Every rule for this aggregate, and then the store's own hook.

        **The store's hook is last on purpose.** It is where the framework's own bookkeeping
        lives — change tracking takes the diff and moves the baseline there — so it has to see
        the aggregate as the project's rules left it. Run first, a field a rule computes lands
        on the far side of the baseline: missed this time, and reported next time with a value
        the aggregate no longer holds. An audit that records a fact that never happened is
        worse than one that records nothing.
        """
        with self._running_hooks():
            for rule in self._rules_for(record):
                for callback in callbacks_of(getattr(rule, moment)):
                    callback(record)
            getattr(self, moment)(record)

    def _searched(self, target: type, collection: Any) -> Any:
        """`after_search`, once for the page — the rules that named this aggregate, then the
        store's own hook, the same order everything else runs in."""
        if collection is None:
            return None
        model, _holder = model_and_collection(target)
        with self._running_hooks():
            for rule in self._rules:
                if not (isinstance(model, type) and issubclass(model, rule.entity)):
                    continue
                for callback in callbacks_of(rule.after_search):
                    answered = callback(collection)
                    collection = collection if answered is None else answered
            answered = self.after_search(collection)
            collection = collection if answered is None else answered
        return collection

    def _rules_for(self, record: Any) -> tuple[Rule, ...]:
        return tuple(one for one in self._rules if isinstance(record, one.entity))

    def _running_hooks(self) -> "_Hooks":
        return _Hooks(self)

    def _before_save(self, record: Any) -> None:
        self._fire("before_save", record)

    def _after_save(self, record: Any) -> None:
        self._fire("after_save", record)

    def _before_writes(self, records: list[Any]) -> list[bool]:
        """Every before-moment for a batch, and what it worked out about each record.

        **What it worked out has to be carried, not asked again.** In `before_save` an aggregate
        that was never stored says `version == 0`; by `after_save` the write has raised it, so
        asking there would call every create an update and `after_create` would never fire at
        all — which looks exactly like a framework bug and is not one.
        """
        newness = [bool(getattr(one, "is_new", False)) for one in records]
        for one, is_new in zip(records, newness):
            self._fire("before_save", one)
            self._fire("before_create" if is_new else "before_update", one)
        return newness

    def _after_writes(self, records: list[Any], newness: list[bool]) -> None:
        for one, is_new in zip(records, newness):
            self._fire("after_save", one)
            self._fire("after_create" if is_new else "after_update", one)

    def _before_archive(self, record: Any) -> None:
        self._fire("before_archive", record)

    def _after_archive(self, record: Any) -> None:
        self._fire("after_archive", record)

    def _before_remove(self, record: Any) -> None:
        self._fire("before_remove", record)

    def _after_remove(self, record: Any) -> None:
        self._fire("after_remove", record)

    def _refuse_reentrant_write(self) -> None:
        """A write from inside a hook would fire the hooks again, and the loop is silent until
        it is a production incident. A rule validates, computes or refuses; it does not write.

        **Counted against the store, not the object.** `context()` and `narrowed()` hand back a
        different `Repository` over the same data, and writing through one of those from inside
        a hook is the same recursion by a longer route — so they share one guard.
        """
        if id(self._guard) in _running.get(frozenset()):
            raise ContractViolation(
                "a repository hook wrote through the same repository; a rule validates, "
                "computes or refuses, it does not write — what needs several aggregates is a "
                "Feature"
            )

    @abstractmethod
    def get(
        self,
        target: type,
        identity: Any,
        for_update: bool = False,
        skip_locked: bool = False,
    ) -> Any | None:
        """One aggregate by its identity, or `None`.

        `for_update` claims the row until the unit of work around it ends, and `skip_locked`
        passes over what somebody else already holds. **They are on the abstraction rather than
        on the one store that can do them**: a Feature written against `Repository` says
        `for_update=True`, and a store that swallowed the word would let that Feature pass its
        tests and lose the race in production.
        """

    @abstractmethod
    def search(
        self,
        target: type,
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
    ) -> EntityCollection: ...

    @abstractmethod
    def count(self, target: type, criteria: Criteria | None = None) -> Count: ...

    @abstractmethod
    def save(self, record: Any) -> None:
        """One aggregate or several — `save(invoice)`, `save(invoices)`, `save(page)`. Several
        are written as one flush, with the same promises paid once instead of once per record.
        """

    @abstractmethod
    def remove(self, record: Any) -> None:
        """Deletes one aggregate or several. The row is gone.

        **It deletes, and only deletes.** Putting a record away without losing it is
        `archive`, which is a different fact and says so — a `remove` that quietly archived
        would be a name that lies about what happened to the data.
        """

    @abstractmethod
    def archive(self, record: Any) -> None:
        """Puts one aggregate or several away without deleting them: the row stays, stamped
        with when it left, and readings leave it out unless they ask for it by name.

        Only for an aggregate that inherits `ArchivableMixin` — anything else is refused,
        because there is nowhere to write that it was archived.
        """

    # --- the readings a use case is actually written against -------------------------------
    #
    # Declared here because they are what Features call, not because a store might have them.
    # Left off, `repository: Repository` type-checked against six methods while the code around
    # it called eighteen — so a third store could satisfy this class and break every Feature,
    # and a type checker would have said nothing either time.

    @abstractmethod
    def browse(self, target: type, ids: Sequence[Any]) -> Any:
        """The aggregates with these ids, as one collection — the ids that are not there are
        simply absent, which is what makes this the answer to a list of references."""

    @abstractmethod
    def fetch_all(self, target: type, criteria: Criteria | None = None) -> Any:
        """Every aggregate the criteria matches, as one complete collection: the reading that
        does not page, for a result somebody already knows is small."""

    @abstractmethod
    def stream(self, target: type, criteria: Criteria | None = None) -> Iterator[Any]:
        """The same aggregates one at a time, for a result too large to hold at once."""

    @abstractmethod
    def first(self, target: type, criteria: Criteria | None = None) -> Any:
        """The first aggregate the criteria matches, or `None`."""

    @abstractmethod
    def one(self, target: type, criteria: Criteria | None = None) -> Any:
        """The single aggregate the criteria matches; more than one is an error, because the
        caller said there would be one."""

    @abstractmethod
    def get_by(self, target: type, **values: Any) -> Any:
        """One aggregate by a natural key — the values that identify it besides its id."""

    @abstractmethod
    def exists(self, target: type, criteria: Criteria | None = None) -> bool:
        """Whether anything matches, without reading it."""

    @abstractmethod
    def pluck(self, target: type, field: str, criteria: Criteria | None = None) -> list[Any]:
        """One field of every match, without building the aggregates."""

    @abstractmethod
    def distinct(
        self, target: type, field: str, criteria: Criteria | None = None
    ) -> list[Any]:
        """The values that field takes across the matches, each once."""

    @abstractmethod
    def measures(
        self, target: type, criteria: Criteria | None = None, **measures: Any
    ) -> dict[str, Any]:
        """Measures over the whole result set, each named by the caller."""

    @abstractmethod
    def group_by(
        self, target: type, by: Sequence[str], criteria: Criteria | None = None
    ) -> list[dict[str, Any]]:
        """The matches folded by these fields, one row per combination."""

    @abstractmethod
    def group_by_levels(self, target: type, criteria: Criteria | None = None) -> list[Bucket]:
        """The same folding as a tree, the way the criteria's grouping describes it."""

    def after_read(self, record: Any) -> Any:
        """Every aggregate this store hands back, before the caller sees it. Answer a record
        to put in its place, or nothing to leave it alone. Empty here on purpose."""
        return record

    def before_save(self, record: Any) -> None:
        """Each aggregate about to be written. Raise to refuse the write."""

    def record_changes(self, record: Any) -> Any:
        """Writes down what changed about this aggregate **now**, and hands the event back.

            event = repository.record_changes(invoice)
            if event is not None:
                events.save(event)                 # an event store
                publisher.publish(event)           # or a queue

        The explicit mode. The automatic one is simply this happening on its own as part of the
        write, and **what comes back is the same event `pull_events()` would hand over, not a
        second one** — publish one or the other, never both.

        `None` when nothing differs, and when the aggregate has never been stored: a first save
        is a Created fact, written by hand, not this.

        **On the store and not on the aggregate**, because the diff is the store's answer. One
        that has an engine asks it; one that does not compares against what it handed out. An
        aggregate cannot tell which of those it came from.
        """
        return record.record_changes() if hasattr(record, "record_changes") else None

    def after_search(self, collection: Any) -> Any:
        """Each page a reading answered, once. Answer a collection to put in its place, or
        nothing to leave it alone. Empty here on purpose.

        **Once for the page, not once per row** — that is `after_read`, which still fires for
        every aggregate in it. A rule about the set rather than about an aggregate belongs here:
        counting what a reading cost, or refusing a page that came back too wide.
        """
        return collection

    def before_create(self, record: Any) -> None:
        """…and this one had never been stored. Fired after `before_save`, never instead."""

    def after_create(self, record: Any) -> None:
        """…and it had never been stored. What the write decided, carried — the aggregate's own
        version has already been raised by the time this runs."""

    def before_update(self, record: Any) -> None:
        """…and this one had been stored before."""

    def after_update(self, record: Any) -> None:
        """…and it had been stored before."""

    def before_archive(self, record: Any) -> None:
        """Each aggregate about to be put away. An archive is a save, so `before_save` fires
        for it too; this is the one that fires only for archiving."""

    def after_archive(self, record: Any) -> None:
        """Each aggregate put away."""

    def after_save(self, record: Any) -> None:
        """Each aggregate just written.

        **Not «after the commit».** Inside `context()` a save flushes and the block commits
        later, so what runs here can still be undone. Publishing from here announces a fact a
        rollback then takes back.
        """

    def before_remove(self, record: Any) -> None:
        """Each aggregate about to be removed. Raise to refuse it."""

    def after_remove(self, record: Any) -> None:
        """Each aggregate just removed. The same caveat `after_save` carries applies."""
