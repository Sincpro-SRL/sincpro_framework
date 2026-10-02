"""What a store can answer, one capability at a time — the baseline every store honours, and
the capabilities a store adds when it can honour them.

    ReadsAggregates      get · search · count · browse · fetch_all · stream    the baseline
                         first · one · get_by · exists · pluck · fingerprint
    WritesAggregates     save · remove · archive                               the baseline
    Analyzes             distinct · measures · group_by · group_by_levels
    WritesInBulk         upsert · update_all · remove_all
    Transacts            context · after_commit · after_rollback

    StoreCapabilities    what varies by engine inside one store: row locks, savepoints…

**Why split at all.** A store is held to what it says it answers. One block of twenty-two
methods held a key-value or wide-column store to measures and bulk updates it can only fake —
a full scan behind a method that promised a query — which is how a framework over many stores
fails (Hibernate OGM, Spring Data's silent Scan). Every capability here answers the same
question with the same meaning on every store that has it; a store without it does not
inherit it, and the type checker says so before production does.

**Two levels, never repeated.** What a store *can be asked* is its type: a Feature or a shared
component asks for `Analyzes` and a store that lacks it does not type-check. What varies *inside
one store by its engine* — SQLite has no row locks, Postgres does — is `StoreCapabilities`,
read at run time. A capability the type already states is never repeated there.

**Abstract classes, not protocols**, for the reason `Repository` gives: `@abstractmethod`
refuses a store that forgot a method, and signatures are compared, not names.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Self, overload

from sincpro_framework.ddd.criteria import Bucket, Criteria
from sincpro_framework.ddd.entity.entity_collection import (
    Count,
    EntityCollection,
    model_and_collection,
)
from sincpro_framework.ddd.repositories.fingerprint import fingerprint_of


@dataclass(frozen=True)
class StoreCapabilities:
    """What one store can honour **on the engine underneath it** — the part a type cannot say,
    because the same class runs on several engines.

        repository.capabilities.row_locks      Postgres True · SQLite False · memory False

    Every flag is `False` unless the store states otherwise: a store that forgot to declare one
    under-promises, and a caller that checks finds out before relying on it. What is a matter
    of the type — whether a store transacts, analyzes, writes in bulk — is never repeated here.
    """

    row_locks: bool = False
    """`for_update` holds the rows it read until the unit of work ends."""
    skip_locked: bool = False
    """`skip_locked` passes over rows somebody else holds."""
    nowait: bool = False
    """`nowait` fails at once on a row somebody else holds."""
    savepoints: bool = False
    """A part of a unit of work can be undone on its own."""
    percentiles: bool = False
    """`measures` can compute a percentile."""


@dataclass(frozen=True)
class Upserted:
    """What an upsert wrote, per distinct key: inserted or overwritten, and left as it was — a
    conflict with nothing to overwrite, or a stored row outside the repository's scope."""

    written: int
    skipped: int


class ReadsAggregates(ABC):
    """The baseline of reading: every store answers these, with the same meaning."""

    @abstractmethod
    def get[T](
        self,
        target: type[T],
        identity: Any,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> T | None:
        """One aggregate by its identity, or `None`.

        `for_update` claims the row until the unit of work around it ends; `skip_locked`
        passes over what somebody else already holds, and `nowait` fails at once on it.
        **They are on the baseline rather than on the stores that can do them**: a Feature
        says `for_update=True` whatever is underneath, and a store that swallowed the word
        would let it pass its tests and lose the race in production. Whether this engine
        holds the lock is `capabilities.row_locks`.
        """

    @overload
    def search[C: EntityCollection](
        self,
        target: type[C],
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> C: ...

    @overload
    def search[T](
        self,
        target: type[T],
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> EntityCollection[T]: ...

    @abstractmethod
    def search(
        self,
        target: type,
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> EntityCollection: ...

    def fingerprint(self, target: type, criteria: Criteria | None = None) -> str:
        """One key for every read of `target` that answers the same rows, whatever page it asks
        for — what `QueryCache` keeps a read under. A repository that reads under a scope makes
        the scope part of it."""
        model, _ = model_and_collection(target)
        return fingerprint_of(model, criteria or Criteria())

    @abstractmethod
    def count(self, target: type, criteria: Criteria | None = None) -> Count: ...

    @overload
    def browse[C: EntityCollection](self, target: type[C], ids: Sequence[Any]) -> C: ...

    @overload
    def browse[T](self, target: type[T], ids: Sequence[Any]) -> EntityCollection[T]: ...

    @abstractmethod
    def browse(self, target: type, ids: Sequence[Any]) -> Any:
        """The aggregates with these ids, as one collection — the ids that are not there are
        simply absent, which is what makes this the answer to a list of references."""

    @overload
    def fetch_all[C: EntityCollection](
        self, target: type[C], criteria: Criteria | None = None
    ) -> C: ...

    @overload
    def fetch_all[T](
        self, target: type[T], criteria: Criteria | None = None
    ) -> EntityCollection[T]: ...

    @abstractmethod
    def fetch_all(self, target: type, criteria: Criteria | None = None) -> Any:
        """Every aggregate the criteria matches, as one complete collection: the reading that
        does not page, for a result somebody already knows is small."""

    @overload
    def stream[C: EntityCollection](
        self, target: type[C], criteria: Criteria | None = None
    ) -> Iterator[C]: ...

    @overload
    def stream[T](
        self, target: type[T], criteria: Criteria | None = None
    ) -> Iterator[EntityCollection[T]]: ...

    @abstractmethod
    def stream(self, target: type, criteria: Criteria | None = None) -> Iterator[Any]:
        """The same aggregates one at a time, for a result too large to hold at once."""

    @overload
    def first[T](
        self, target: type[EntityCollection[T]], criteria: Criteria | None = None
    ) -> T | None: ...

    @overload
    def first[T](self, target: type[T], criteria: Criteria | None = None) -> T | None: ...

    @abstractmethod
    def first(self, target: type, criteria: Criteria | None = None) -> Any:
        """The first aggregate the criteria matches, or `None`."""

    @overload
    def one[T](
        self, target: type[EntityCollection[T]], criteria: Criteria | None = None
    ) -> T: ...

    @overload
    def one[T](self, target: type[T], criteria: Criteria | None = None) -> T: ...

    @abstractmethod
    def one(self, target: type, criteria: Criteria | None = None) -> Any:
        """The single aggregate the criteria matches; more than one is an error, because the
        caller said there would be one."""

    @overload
    def get_by[T](self, target: type[EntityCollection[T]], **values: Any) -> T | None: ...

    @overload
    def get_by[T](self, target: type[T], **values: Any) -> T | None: ...

    @abstractmethod
    def get_by(self, target: type, **values: Any) -> Any:
        """One aggregate by a natural key — the values that identify it besides its id."""

    @abstractmethod
    def exists(self, target: type, criteria: Criteria | None = None) -> bool:
        """Whether anything matches, without reading it."""

    @abstractmethod
    def pluck(self, target: type, field: str, criteria: Criteria | None = None) -> list[Any]:
        """One field of every match, without building the aggregates."""


class WritesAggregates(ABC):
    """The baseline of writing: an aggregate goes in whole and comes out whole, through its
    hooks, version checked."""

    @abstractmethod
    def save(self, record: Any) -> None:
        """One aggregate or several — `save(invoice)`, `save(invoices)`, `save(page)`. Several
        are written as one flush, with the same promises paid once instead of once per record.

        A store that maps relations writes the children a root holds with it, and settles the
        ones an assignment dropped; `MemoryRepository` keeps the root as one object, children
        inside, so a test there sees the aggregate it built rather than rows it wrote.
        """

    @abstractmethod
    def remove(self, record: Any) -> None:
        """Deletes one aggregate or several. The row is gone — and, on a store that maps
        relations, the children of every relation declared owned go with it; `MemoryRepository`
        holds an aggregate as one object, its children inside, so they go with it there too.

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


class Analyzes(ABC):
    """Folding the matches into numbers inside the store. A relational or document store
    answers it; a key-value or wide-column one cannot without reading everything, so it does
    not claim it."""

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


class WritesInBulk(ABC):
    """The writes that skip the aggregate on purpose, and are named for it: no hook, no
    cascade. A store that can only write one key at a time does not claim them."""

    @abstractmethod
    def upsert(
        self, record: Any, on: Sequence[str], update: Sequence[str] | None = None
    ) -> Upserted:
        """Inserts each record, or overwrites the stored one holding the same `on`. It
        overwrites by definition — no version check — and the records handed in are not
        refreshed. `update` names what a conflict overwrites; `update=()` leaves it as it is.
        Answers how many were written and how many skipped.
        """

    @abstractmethod
    def update_all(self, target: type, criteria: Criteria, values: Mapping[str, Any]) -> int:
        """Sets these values on every record the filter matches; how many is the answer. Past
        the aggregate — no hook, no cascade — but its version is raised. A page, or a condition
        the aggregate cannot answer, is refused: a write never runs wider than it was asked.
        """

    @abstractmethod
    def remove_all(self, target: type, criteria: Criteria) -> int:
        """Deletes every record the filter matches; how many is the answer. No hook, no
        cascade. A page, or a condition that cannot be answered, is refused."""


class Transacts(ABC):
    """Several reads and writes as one transaction, and what waits for its end.

        with repository.context() as unit:
            unit.save(invoice)
            unit.after_commit(lambda: announce(invoice))

    The least every transacting store opens with. A store's own `context()` takes the options
    its engine has — isolation, read-only, a timeout — as keywords with defaults, so code written
    against this one runs on it unchanged.
    """

    @abstractmethod
    def context(self) -> AbstractContextManager[Self]:
        """The same store bound to one transaction: committed when the block ends, rolled
        back when it raises."""

    @abstractmethod
    def after_commit(self, callback: Callable[[], Any]) -> None:
        """Runs `callback` once the unit of work committed; never if it is undone."""

    @abstractmethod
    def after_rollback(self, callback: Callable[[], Any]) -> None:
        """Runs `callback` once the unit of work was undone."""
