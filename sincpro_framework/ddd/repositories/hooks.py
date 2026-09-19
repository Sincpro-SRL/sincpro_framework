"""How a repository is extended: a hook, a collection of them, and the rule they compile to.

    billing_hooks = Hooks()                     # in services/hooks/__init__.py

    @billing_hooks                              # in services/hooks/invoices.py
    class ChecksWithBilling(Hook, DependencyContextType):
        entity = Invoice
        def before_save(self, invoice): ...

    repository = Repository(database, billing_hooks, deps=bus.deps)

Nothing here knows what a repository *is* — only what can be hung around one. `repository.py`
depends on this module; this module depends on nothing of it.
"""

import inspect
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any, ClassVar, cast

from sincpro_framework.ddd.events import DomainEvent  # noqa: F401  (documented in Rule)
from sincpro_framework.exceptions import DependencyNotRegistered
from sincpro_framework.sincpro_abstractions import DataTransferObject


def callbacks_of(given: Any) -> list[Callable[[Any], Any]]:
    """What one moment of a rule was handed, always as a list: nothing, one function, or
    several. The same «one or many» `records_of` answers for aggregates — a caller never picks
    a shape by how many it has."""
    if given is None:
        return []
    if isinstance(given, Iterable):
        return list(given)
    return [given]


class Hook:
    """A rule written as a class, with the framework's registered dependencies as attributes.

        class ChecksWithBilling(Hook):
            billing: BillingClient          # declared for the IDE, resolved at call time

            def __call__(self, invoice: Invoice) -> None:
                if not self.billing.allows(invoice.total):
                    raise ContractViolation("billing refused it")

        repository = Repository(database, deps=bus.deps, rules=[
            Rule(entity=Invoice, before_save=[ChecksWithBilling()]),
        ])

    The same ergonomics a `Feature` has — `self.billing` rather than a locator handed around —
    and for the same reason: an annotation gives the IDE the type, and the name resolves
    against what `add_dependency` registered.

    **Resolved when the hook runs, not when it is built**, which is what makes the wiring order
    stop mattering: a repository is usually built before the bus that will hold the
    dependencies, and a hook that resolved eagerly could not be written at all.
    """

    @property
    def context(self) -> "Mapping[str, Any]":
        """What the request in play says, read-only — empty outside one, and empty when this
        collection was injected with dependencies rather than with a bus.

        The same thing a `Feature` reads as `self.context`, and for the same reason: a rule that
        refuses a write usually has to say on whose behalf. **`context` is therefore a name a
        hook cannot use for a dependency** — this answers first.
        """
        reading = object.__getattribute__(self, "_context")
        return reading() if reading is not None else {}

    entity: ClassVar[type] = object
    """The aggregate this hook is for. A hook that declares it needs no `Rule` around it: the
    repository reads it, and the moments are whichever methods the class implements.

    **Left alone it is `object`, which every record is**, so a hook that does not name an
    aggregate runs for all of them — what an audit or a log wants. There is no special case
    behind this: `isinstance(record, object)` is simply always true.
    """

    _deps: Any = None
    _context: "Callable[[], Mapping[str, Any]] | None" = None

    def bind(self, deps: Any) -> "Hook":
        """Hands this hook what it will resolve names against. Called by the repository it was
        given to; a hook bound twice keeps the last one.

        A bus was handed in rather than its dependencies, both come from it: the names, and the
        context behind whatever request is running.
        """
        reading = getattr(deps, "current_context", None)
        self._context = (
            cast("Callable[[], Mapping[str, Any]] | None", reading)
            if callable(reading)
            else None
        )
        self._deps = getattr(deps, "deps", deps) if reading is not None else deps
        return self

    def __getattr__(self, name: str) -> Any:
        # Only reached for a name the instance and its class do not already have, so an
        # attribute a subclass sets itself always wins over a registered dependency.
        #
        # …except that a property which raises `AttributeError` of its own also lands here,
        # and answering it with a dependency would replace a real bug's message with a wrong
        # one. When the class does declare the name, it is asked again so its own error is
        # what comes out.
        declared = inspect.getattr_static(type(self), name, None)
        if declared is not None and hasattr(type(declared), "__get__"):
            return declared.__get__(self, type(self))
        deps = object.__getattribute__(self, "_deps")
        if deps is None:
            raise DependencyNotRegistered(
                f"{type(self).__name__}.{name}: this hook was never bound to any "
                "dependencies — build the repository with `deps=bus.deps`"
            )
        return getattr(deps, name)


Callback = Callable[[Any], Any] | type[Hook] | Hook
"""What one moment of a rule may be handed: a plain function over the aggregate, a `Hook`
already built, or a `Hook` class for the repository to build.

A built `Hook` is listed on its own rather than left to `Callable`, because a hook that
implements `before_save` and nothing else is not callable and would be refused before the
repository ever saw it — for declaring exactly what it was built for.
"""

OneOrMany = Callback | Sequence[Callback] | None
"""…and any moment takes one of those or several, the way `save` takes one aggregate or
several."""


INFER: Any = object()
"""The default for `Hooks(package=…)`, so that an explicit `None` can mean something different
from saying nothing at all: `Hooks()` walks the caller's package, `Hooks(None)` walks nothing.
"""


def _calling_package() -> str | None:
    """The package the caller is in, for a collection that was not told one. `None` when there
    is nothing to walk — a script, a REPL, a module that is not part of a package."""
    frame = inspect.currentframe()
    caller = frame.f_back.f_back if frame is not None and frame.f_back is not None else None
    if caller is None:
        return None
    name = caller.f_globals.get("__name__")
    if not name or name == "__main__":
        return None
    # A package's own `__init__` is the package, and walking it is the point. Anything else is
    # one module, and that module is what gets walked — **not the package around it**. Reaching
    # for `__package__` there meant a collection written in `myapp/wiring.py` imported every
    # module of `myapp` the first time a repository read it, which is both far more than anyone
    # asked for and a circular import waiting for the first module that says
    # `from myapp.wiring import repository`.
    return name


class Hooks:
    """A collection of hooks, filled by decorating them and handed to a repository whole.

        # services/hooks/__init__.py — one per module, or per bounded context
        billing_hooks = Hooks()

        # services/hooks/invoices.py
        @billing_hooks
        class ChecksWithBilling(Hook, DependencyContextType):
            entity = Invoice
            def before_save(self, invoice): ...

        # the wiring names the collection, not every hook in it
        repository = Repository(database, hooks=billing_hooks, deps=bus.deps)

    **It is an object somebody made and passed**, not a registry the framework keeps: two
    contexts have two collections and cannot reach into each other's, and what a repository
    runs is whatever collection it was given — printable, countable, and right there in the
    wiring.

    The one thing to know: a hook in a module nobody imports never decorates itself, so it
    never runs. That is true of any decorator that registers, and it is why the collection is
    named at the wiring rather than discovered.
    """

    __slots__ = ("_hooks", "_package", "_loaded", "_deps")

    def __init__(self, package: "str | None" = INFER) -> None:
        """Remembers the package it was built in, and walks it the first time somebody reads
        the collection — never here.

        Walking at construction cannot work for the layout this is written for: a collection
        lives in a package's `__init__`, and the modules it would import do `from . import
        the_collection`, which is not bound yet while `__init__` is still running. By the
        first read it is.

        `package` names another one explicitly. Left out, it is whatever module called this;
        **`None` means walk nothing**, for a collection filled by hand and for a test that is
        about the mechanism rather than about discovery.
        """
        self._hooks: list[type[Hook]] = []
        self._loaded = False
        self._deps: Any = None
        self._package = _calling_package() if package is INFER else package

    def __call__(self, hook: "type[Hook]") -> "type[Hook]":
        """Registers the class and answers it unchanged, so the decorator is invisible to
        everything else that uses it."""
        self._hooks.append(hook)
        return hook

    def __iter__(self) -> "Iterator[type[Hook]]":
        self._load_once()
        return iter(self._hooks)

    def __len__(self) -> int:
        self._load_once()
        return len(self._hooks)

    def _load_once(self) -> None:
        """The walk, at most once, and never from `__repr__` — printing a collection while
        debugging must not import half a package as a side effect.

        **Marked done only once it is done.** Marked first, a module that failed to import left
        the collection holding whatever had registered before it, with `_loaded` already true —
        so the second read answered a half-filled collection, quietly, and every hook after the
        broken module was simply gone. Now the failure comes back every time it is asked.
        """
        if self._loaded:
            return
        if self._package is not None:
            self.load(self._package)
        self._loaded = True

    def inject(self, deps: Any) -> "Hooks":
        """What the hooks in this collection will resolve their attributes against — normally
        `bus.deps`, or the bus itself when the hooks also want `self.context`.

            billing_hooks.inject(bus.deps)     names only
            billing_hooks.inject(bus)          names, and the request context behind them
            repository = Repository(database, billing_hooks)

        Held as a reference and handed over when a hook is actually built, so this can be
        called before the dependencies it names exist, and a collection given to two
        repositories is configured once rather than at each of them.

        The dependencies belong to the hooks, not to the store: a repository that took them
        would only be passing them through.
        """
        self._deps = deps
        return self

    @property
    def deps(self) -> Any:
        """What was injected, or `None`. Read by a repository, which falls back to its own."""
        return self._deps

    def load(self, package: str) -> "Hooks":
        """Imports every module under `package`, so the hooks in them decorate themselves.

            billing_hooks = Hooks().load("myapp.services.hooks")

        **A decorator only runs if its module was imported**, and a hook in a file nobody
        imported never registers — no error, nothing happens. The usual answer is an import at
        the bottom of an `__init__` with a `noqa` on it, which works and says nothing about
        why it is there. This says it, takes the package by name, and stays scoped to that
        package: nothing else is walked, nothing is discovered behind anybody's back.

        Called where the collection is built, and answers the collection so the two read as
        one line.
        """
        import importlib
        import pkgutil

        found = importlib.import_module(package)
        if hasattr(found, "__path__"):  # a module, not a package: importing it was the job
            for module in pkgutil.walk_packages(found.__path__, f"{found.__name__}."):
                importlib.import_module(module.name)
        self._loaded = True
        return self

    def __repr__(self) -> str:
        names = ", ".join(one.__name__ for one in self._hooks) or "empty"
        return f"Hooks({names})"


class _MomentOfHook:
    """One moment of a hook, callable, that asks the store for the hook the first time it
    actually fires.

    **Nothing is constructed while a repository is being built.** A hook whose `__init__` is
    expensive, or fails, must not take the repository down with it — and a repository half
    built because of a hook is the kind of failure that looks like anything except its cause.

    **The instance is the store's, not this object's.** A hook that implements `before_save`
    and `after_save` is two of these, and each holding its own instance would make `self` mean
    something different in each moment — so the obvious "decide it in `before_save`, act on it
    in `after_save`" silently loses what it decided. The store keeps one per hook and hands the
    same one to every moment.
    """

    __slots__ = ("hook", "moment", "_store")

    def __init__(self, hook: "Hook | type[Hook]", moment: str) -> None:
        self.hook = hook
        self.moment = moment
        self._store: Any = None

    def bind(self, store: Any) -> "_MomentOfHook":
        """The repository that owns the instance and knows the dependencies. Asked at every
        fire rather than read once, so a collection injected after the repository was built is
        still seen — wiring order is not something a store should silently depend on."""
        self._store = store
        return self

    def __call__(self, record: Any) -> Any:
        return getattr(self._store._hook_instance(self), self.moment)(record)


MOMENTS = (
    "after_read",
    "before_save",
    "after_save",
    "before_create",
    "after_create",
    "before_update",
    "after_update",
    "before_remove",
    "after_remove",
    "before_archive",
    "after_archive",
)
"""Every moment a rule can be written for, over one aggregate.

Symmetric on purpose: whatever a store is about to do, there is a before and an after. `save` is
the pair that always fires; `create` and `update` are the same write told apart, so a rule that
only cares about one of them does not have to ask. `archive` wraps the save it is, so a rule
written for `before_save` still sees an archive and one written for `before_archive` sees only
those.

A reading has its own pair, `before_search` / `after_search`, and is not here: it is about a
criteria and a page, not about one aggregate.
"""
"""Every moment a rule can name, in the order they appear on it."""


class Rule(DataTransferObject):
    """What to do around one aggregate's reads and writes, handed to a repository when it is
    built.

        Rule(entity=Invoice, before_save=check_totals)
        Rule(Order, before_save=reserve_stock, after_read=compute_available)

    `entity` selects by `isinstance`, so a subclass of it is covered too. Every callback is
    optional; a rule that sets none is inert.

    Several rules may name the same aggregate: they run in the order the list was written, in
    the composition root, where somebody can read it — not in a registry another module filled
    in from somewhere else.

    **Every moment takes one function or several**, the way `save` takes one aggregate or
    several — `before_save=check_totals` and `before_save=[check_totals, check_dates]` are both
    written the same way, and several run in the order they are listed.

    `after_read` may answer a record to put in place of the one read; anything else it answers,
    including `None`, leaves the record as it was. The rest answer nothing: they validate,
    compute or refuse, and a refusal is an exception that stops the write.
    """

    entity: type
    after_read: OneOrMany = None
    before_save: OneOrMany = None
    after_save: OneOrMany = None
    before_create: OneOrMany = None
    after_create: OneOrMany = None
    before_update: OneOrMany = None
    after_update: OneOrMany = None
    before_remove: OneOrMany = None
    after_remove: OneOrMany = None
    before_archive: OneOrMany = None
    after_archive: OneOrMany = None
    after_search: OneOrMany = None
    """Each page a reading answered, **once — not once per row**, and handed the collection
    rather than an aggregate. Answer a collection to put in its place, or nothing to leave it
    alone. `after_read` still fires for each aggregate in it."""
