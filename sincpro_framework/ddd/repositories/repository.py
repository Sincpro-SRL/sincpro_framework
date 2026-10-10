"""The least a repository answers, and the two ways to put something around it.

    class IRepository(ReadsAggregates, WritesAggregates):    the baseline every store honours
        get · search · count · browse · fetch_all · stream · first · one · get_by
        exists · pluck · save · remove · archive
        capabilities                                what its engine honours (row locks…)

        after_read · before_save · after_save       the hooks, empty, for a subclass
        before_remove · after_remove

    MemoryRepository(Repository, Analyzes, WritesInBulk)
    orm.Repository(IRepository, Analyzes, WritesInBulk, Transacts)

**The baseline, and capabilities on top.** What every store answers with the same meaning is
here; what only some stores can — folding numbers, writing past the aggregate, a transaction —
is a capability a store adds (`capabilities.py`). A use case types the store it runs on; a
component shared across stores asks for exactly the capabilities it calls.

**An abstract class rather than a protocol.** It was a `Protocol` and nothing was written
against it structurally: the two implementations inherit it, and the double a test uses is
`MemoryRepository`, not a hand-rolled stand-in. What the protocol could not give is what an
implementation now gets: `@abstractmethod` refuses a store that forgot a method, and the type
checker compares signatures — `isinstance` against a `@runtime_checkable` protocol compares
*names only*, so five methods taking the wrong arguments passed it.

**Two ways to put something around a read or a write, for two different kinds of thing.**

A *mixin* overrides the store's own hooks when the behaviour belongs to the store itself and
ships with it — `ChangeTrackingRepositoryMixin` is the framework's own. A *`Hook`* class, in the
bounded context's `Hooks`, is given when the behaviour belongs to this deployment and to one
aggregate: an invariant, a derived field, a policy. The two never travel through each other, so a
mixin that forgets `super()` cannot switch a project's hooks off.

**A hook runs inside the write, inside the transaction.** It validates, computes or refuses, and
it has what a Feature of its bus has — other repositories, the bus, the request. One thing is
refused: a write back through the repository that fired it, directly or through a Command,
because the alternative is a loop nobody sees until production.
"""

from collections.abc import Iterable
from contextvars import ContextVar
from typing import Any

from sincpro_framework.context.infrastructure.tree import chain_for
from sincpro_framework.ddd.editing.editing import recompute_whole
from sincpro_framework.ddd.editing.preview import is_previewing
from sincpro_framework.ddd.entity.entity_collection import (
    model_and_collection,
)
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.exceptions import ContractViolation, WriteInPreview
from sincpro_framework.ddd.repositories.capabilities import (
    ReadsAggregates,
    StoreCapabilities,
    WritesAggregates,
)
from sincpro_framework.ddd.repositories.hooks import Hook, HookChain, Hooks


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

    def __init__(self, repository: "IRepository") -> None:
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


def refuse_writing_in_preview(call: str) -> None:
    """Refuses a write inside `previewing()`, naming it.

    in      "save", inside a preview    →   WriteInPreview: save inside a preview
    in      "save", outside             →   nothing

    A preview answers what a record would become; a write there would store a record, take a
    number or hold a lock that nobody asked for — silently, behind a form. Refused loudly.
    """
    if is_previewing():
        raise WriteInPreview(
            f"{call} inside a preview: a preview stores nothing — the Command that saves does"
        )


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


class IRepository(ReadsAggregates, WritesAggregates):
    """What every store answers, whatever is underneath: the baseline of reading and writing,
    and the hooks around both."""

    @property
    def capabilities(self) -> StoreCapabilities:
        """What this store's engine honours beyond the baseline — nothing, unless the store
        says so."""
        return StoreCapabilities()

    def __init__(
        self, hooks: Hooks | None = None, guard: "IRepository | None" = None
    ) -> None:
        """`hooks` is the bounded context's collection; `guard` is the repository this one is a
        view of — a unit of work, a narrowing — which runs the chain of the one it came from
        and counts as it when a hook writes."""
        self._guard: "IRepository" = guard if guard is not None else self
        self._chain: HookChain = guard._chain if guard is not None else HookChain(hooks)

    def _read(self, record: Any) -> Any:
        """The chain's `after_read`, then the store's own hook — which closes the moment, so
        what it records is the record as everything else left it."""
        if record is None:
            return None
        with self._running_hooks():
            record = self._chain.read(record)
            answered = self.after_read(record)
            record = record if answered is None else answered
        return record

    def _fire(self, moment: str, record: Any) -> None:
        """The chain's hooks for this aggregate, and then the store's own hook.

        **The store's hook is last on purpose.** It is where the framework's own bookkeeping
        lives — change tracking takes the diff and moves the baseline there — so it has to see
        the aggregate as the project's hooks left it. Run first, a field a hook computes lands
        on the far side of the baseline: missed this time, and reported next time with a value
        the aggregate no longer holds. An audit that records a fact that never happened is
        worse than one that records nothing.
        """
        with self._running_hooks():
            self._chain.fire(moment, record)
            getattr(self, moment)(record)

    def _searched(self, target: type, collection: Any) -> Any:
        """`after_search`, once for the page — the chain's hooks for this aggregate, then the
        store's own hook, the same order everything else runs in."""
        if collection is None:
            return None
        model, _holder = model_and_collection(target)
        with self._running_hooks():
            if isinstance(model, type):
                collection = self._chain.searched(model, collection)
            answered = self.after_search(collection)
            collection = collection if answered is None else answered
        return collection

    def _running_hooks(self) -> "_Hooks":
        return _Hooks(self)

    def _before_writes(self, records: list[Any]) -> list[bool]:
        """Every before-moment for a batch, and what it worked out about each record.

        **What it worked out has to be carried, not asked again.** In `before_save` an aggregate
        that was never stored says `version == 0`; by `after_save` the write has raised it, so
        asking there would call every create an update and `after_create` would never fire at
        all — which looks exactly like a framework bug and is not one.

        A new event saved inside an execution is caused by it and joins its flow, unless it already
        says otherwise — the same as one recorded or published.

        An aggregate that declares `derivations` computes them first — the records inside it
        before it (`recompute_whole`) — so the hooks see, and the store keeps, what a preview of
        the same values showed. A derivation that reads a relation nobody loaded is skipped:
        a save that worked without derivations still works.
        """
        newness = [bool(getattr(one, "is_new", False)) for one in records]
        for one, is_new in zip(records, newness):
            if is_new and isinstance(one, DomainEvent):
                for key, value in chain_for(one).items():
                    setattr(one, key, value)
            recompute_whole(one)
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
