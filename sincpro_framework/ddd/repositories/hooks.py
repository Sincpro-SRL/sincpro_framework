"""Hooks: what a project hangs around the reads and writes of its own aggregates.

    billing_hooks = Hooks()                                      # services/hooks/__init__.py

    @billing_hooks.on(Invoice)                                   # services/hooks/invoices.py
    class InvoiceMustBalance(BillingHook):
        def before_save(self, invoice: Invoice) -> None: ...

    billing_hooks.inject(billing)                                # the composition root
    repository = Repository(database, billing_hooks)

Context: three pieces, one form. A `Hook` is a class — its moments are the methods it
implements, `self.<name>` a dependency of the bus, `self.context` the request in play. `Hooks`
is the collection a bounded context fills with `on(...)`, and where each hook is placed among
the rest. `HookChain` is what one repository runs: the collection compiled at its first use, in order, with
one instance of each hook. The why, the guarantees and the research: `docs/persistence/hooks.md`.
"""

import importlib
import inspect
import pkgutil
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Any, Generic, cast

from sincpro_framework.exceptions import DependencyNotRegistered, ExtensionRefused
from sincpro_framework.ordering import DEFAULT_SEQUENCE, Ordered, Placement, name_of, ordered
from sincpro_framework.sincpro_abstractions import ContextT
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.use_bus import UseFramework

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
    "after_search",
)
"""Every method a hook may implement. Symmetric: whatever a store does, a before and an after;
each is handed one aggregate, but `after_search`, handed the page once."""

type Entities = type | Sequence[type]
"""The aggregate a hook is for, or several — `Invoice` or `[Invoice, CreditNote]`."""


class Hook(Generic[ContextT]):
    """One concern around an aggregate: its moments are the methods it implements.

        class BillingHook(Hook[BillingContext], DependencyContextType):   # framework.py, once
            ...

        @billing_hooks.on(Invoice)
        class InvoiceMustBalance(BillingHook):
            def before_save(self, invoice: Invoice) -> None:
                if not self.billing.allows(invoice.total):          # a dependency of the bus
                    raise ContractViolation(f"refused for {self.context['user_id']}")

    Typed the way a Feature is: the dependencies by inheriting the context's
    `DependencyContextType`, the request's context by `Hook[ContextT]`.

    Context: everything a Feature of that bus has — `self.<name>` is any dependency the bus
    registered (another repository, a client), `self.context` the request in play, `self.bus`
    the bus itself — resolved against the bus the collection was given, when it is read, so the
    wiring order does not matter. One instance serves every moment and every request of its
    repository, like a Feature: state lives in locals, never in `self`.
    """

    _hooks: "Hooks | None" = None

    def _given_bus(self) -> "UseFramework | None":
        hooks = object.__getattribute__(self, "_hooks")
        return hooks.bus if hooks is not None else None

    def _refuse_no_bus(self, name: str) -> DependencyNotRegistered:
        return DependencyNotRegistered(
            f"{type(self).__name__}.{name}: its collection was never given a bus — "
            "`hooks.inject(bus)` in the composition root"
        )

    @property
    def context(self) -> ContextT:
        """The request in play, read-only — `{}` outside one — typed as `Hook[ContextT]` says."""
        bus = self._given_bus()
        return cast(ContextT, bus.current_context() if bus is not None else {})

    @property
    def bus(self) -> "UseFramework":
        """The bus the collection was given, to execute a Command or a Query from a hook as an
        ApplicationService does.

            answer = self.bus(QueryCreditOf(customer_id=invoice.customer_id), ResponseCredit)

        Context: it runs inside the write, in its transaction. A write back through the
        repository that fired this hook is refused, as any write from a hook through it is — it
        would fire the hook again, forever.
        """
        bus = self._given_bus()
        if bus is None:
            raise self._refuse_no_bus("bus")
        return bus

    def __getattr__(self, name: str) -> Any:
        """A name the instance does not have: a dependency of the bus.

        1. A descriptor the class declares (a property whose getter raised `AttributeError`)
           is asked again, so its own error comes out — not a wrong dependency lookup.
        2. No bus given to the collection: refused, saying where to give it.
        3. Final: the dependency registered under `name`.
        """
        declared = inspect.getattr_static(type(self), name, None)
        if declared is not None and hasattr(type(declared), "__get__"):
            return declared.__get__(self, type(self))
        bus = self._given_bus()
        if bus is None:
            raise self._refuse_no_bus(name)
        return getattr(bus.deps, name)


def _implemented(hook: type[Hook]) -> tuple[str, ...]:
    return tuple(moment for moment in MOMENTS if getattr(hook, moment, None) is not None)


INFER: Any = object()
"""The default for `Hooks(package=…)`: `Hooks()` walks the caller's module, `Hooks(None)`
walks nothing."""


def _calling_package() -> str | None:
    """The module that built the collection — a package's `__init__` is the package — or
    `None` for a script or a REPL. Context: the module, never the package around it; walking
    `myapp` from `myapp/wiring.py` imports far more than asked, and is a circular import."""
    frame = inspect.currentframe()
    caller = frame.f_back.f_back if frame is not None and frame.f_back is not None else None
    if caller is None:
        return None
    name = caller.f_globals.get("__name__")
    return None if not name or name == "__main__" else name


class Hooks:
    """The hooks of a bounded context: registered with `on`, ordered, and handed to a
    repository whole.

        billing_hooks = Hooks()                          # walks this package on first read
        repository = Repository(database, billing_hooks)

    Context: an object somebody made and passed, never a registry the framework keeps — two
    contexts have two collections. A hook in a module nobody imports never registers, which is
    why the collection walks its own package the first time a repository uses it.
    """

    __slots__ = (
        "_placements",
        "_entities",
        "_off",
        "_package",
        "_loaded",
        "_read",
        "_bus",
        "_result",
    )

    def __init__(self, package: "str | None" = INFER) -> None:
        """`package` is walked on first read — never here, where the modules it holds cannot
        import the collection yet. Left out, it is the calling module; `None` walks nothing.
        """
        self._placements: list[Placement] = []
        self._entities: dict[type[Hook], tuple[type, ...]] = {}
        self._off: tuple[type[Hook], ...] = ()
        self._loaded = False
        self._read = False
        self._bus: "UseFramework | None" = None
        self._result: Ordered | None = None
        self._package = _calling_package() if package is INFER else package

    def _refuse_late(self, hook: type[Hook]) -> None:
        if self._read:
            raise ExtensionRefused(
                f"{hook.__name__} registered late: this collection was already read by a "
                "repository, so it would never run — import its module before the repository "
                "first saves or reads"
            )

    def _checked(
        self, hook: type[Hook], replaces: type[Hook] | None, extends: type[Hook] | None
    ) -> type[Hook] | None:
        """What the hook takes the place of, once what it declares is checked.

        1. Refused what cannot work: `extends` on a class that is not a subclass — `super()`
           would fail at the first save — and a class that implements no moment.
        2. A warning for what works, only not as said: `replaces` and `extends` together (it
           extends), and `replaces` on a subclass, whose `super()` still runs the original.
        3. Final: the hook it takes the place of, or `None`.
        """
        if extends is not None and not issubclass(hook, extends):
            raise ExtensionRefused(
                f"{hook.__name__} extends {extends.__name__}, so it has to be a subclass of it "
                f"— super() is how it runs {extends.__name__}; to run beside it, "
                f"register it with after=({extends.__name__},)"
            )
        if not _implemented(hook):
            raise ExtensionRefused(
                f"{hook.__name__} implements none of {', '.join(MOMENTS)}, so it would never "
                "run"
            )
        if replaces is not None and extends is not None:
            logger.warning(
                f"{hook.__name__} both replaces and extends {replaces.__name__}: it extends it"
            )
            return extends
        if replaces is not None and issubclass(hook, replaces):
            logger.warning(
                f"{hook.__name__} replaces {replaces.__name__} and is a subclass of it, so "
                f"super() still runs it — that is extends={replaces.__name__}"
            )
        return replaces or extends

    def on[H: type[Hook]](
        self,
        entity: Entities,
        replaces: type[Hook] | None = None,
        extends: type[Hook] | None = None,
        before: Sequence[type[Hook]] = (),
        after: Sequence[type[Hook]] = (),
        sequence: int = DEFAULT_SEQUENCE,
    ) -> Callable[[H], H]:
        """Register the decorated class for `entity`, placed among the rest.

            @hooks.on(Invoice)                                  for invoices
            @hooks.on([Invoice, CreditNote])                    for each one listed
            @hooks.on(object)                                   for every record
            @hooks.on(Invoice, replaces=Checks)                 instead of Checks, in its place
            @hooks.on(Invoice, extends=Checks)                  a subclass: super() runs Checks
            @hooks.on(Invoice, after=(Checks,), sequence=5)     ordered among the rest

        1. Refused when it is late, or when what it declares cannot work (see `_checked`).
        2. The aggregates it is for kept by the collection, never written onto the class.
        3. Final: placed — `replaces` and `extends` take the position of the one they name.
        """
        entities = tuple(entity) if isinstance(entity, Sequence) else (entity,)

        def register(hook: H) -> H:
            self._refuse_late(hook)
            takes_the_place_of = self._checked(hook, replaces, extends)
            self._entities[hook] = entities
            self._placements.append(
                Placement(hook, sequence, tuple(before), tuple(after), takes_the_place_of)
            )
            return hook

        return register

    def inject(self, bus: "UseFramework") -> "Hooks":
        """The bus the hooks read their dependencies and context from — asked when a hook
        reads a name, so it may be given before or after the repository is built."""
        self._bus = bus
        return self

    @property
    def bus(self) -> "UseFramework | None":
        return self._bus

    def entities_of(self, hook: type[Hook]) -> tuple[type, ...]:
        return self._entities[hook]

    def load(self, package: str) -> "Hooks":
        """Import every module under `package`, so the hooks in them register.

        billing_hooks = Hooks(None).load("myapp.services.hooks")
        """
        found = importlib.import_module(package)
        if hasattr(found, "__path__"):
            for module in pkgutil.walk_packages(found.__path__, f"{found.__name__}."):
                importlib.import_module(module.name)
        self._loaded = True
        return self

    def _load_once(self) -> None:
        """The walk, at most once, then the collection closed to late registrations.

        1. Not walked yet: import every module of its package — the hooks in them register.
        2. Final: marked walked and read; from here a new registration is refused as late.

        Context: both marks come after the walk. Marked before it, the hooks the walk imports
        were refused as late — the very ones it walks for — and a module that fails to import
        left a half-filled collection marked done; now that failure comes back on every read.
        """
        if not self._loaded and self._package is not None:
            self.load(self._package)
        self._loaded = True
        self._read = True

    def _ordered(self) -> Ordered:
        """The order the hooks run in (see `sincpro_framework.ordering`), worked out once — the
        collection is closed by then — with what was asked and could not be done as said
        logged once, not refused.

            on(A) · on(B, sequence=5) · on(C, replaces=A)      →   [B, C]
        """
        self._load_once()
        if self._result is not None:
            return self._result
        result = ordered(self._placements, self._off)
        notes = list(result.notes)
        for replacement, chain in result.replaced.items():
            covered, replaced = set(self._entities[replacement]), set(
                self._entities[chain[-1]]
            )
            if covered != replaced:
                notes.append(
                    f"{name_of(replacement)} replaces {name_of(chain[-1])}, but it covers "
                    f"{', '.join(sorted(one.__name__ for one in covered))} and what it replaces "
                    f"covers {', '.join(sorted(one.__name__ for one in replaced))}"
                )
        for note in notes:
            logger.warning(note)
        self._result = result
        return result

    def __iter__(self) -> Iterator[type[Hook]]:
        yield from self._ordered().items

    def __len__(self) -> int:
        return len(self._ordered().items)

    @property
    def replacements(self) -> dict[str, tuple[str, ...]]:
        """Each replacement or extension in force → what it took the place of, oldest first.

        {"billing.ChecksStrictly": ("billing.Checks",)}
        """
        return {
            name_of(item): tuple(name_of(one) for one in chain)
            for item, chain in self._ordered().replaced.items()
        }

    @property
    def switched_off(self) -> tuple[str, ...]:
        return tuple(name_of(item) for item in self._off)

    def _copy(self) -> "Hooks":
        copied = Hooks(None)
        copied._placements = list(self._placements)
        copied._entities = dict(self._entities)
        copied._off = self._off
        copied._bus = self._bus
        return copied

    def without(self, *hooks: type[Hook]) -> "Hooks":
        """A collection with everything this one has but `hooks`; this one stays whole.

        billing_hooks.without(Audits)       →   Hooks(Checks)      billing_hooks unchanged
        """
        self._load_once()
        copied = self._copy()
        copied._off = (*self._off, *hooks)
        return copied

    def combined_with(self, *others: "Hooks") -> "Hooks":
        """This collection with `others` after it: a project's hooks on a core's, which may
        replace, extend or run beside the core's without touching them.

            core_hooks.combined_with(client_hooks)    →   the core, with the client's changes
        """
        self._load_once()
        combined = self._copy()
        for other in others:
            other._load_once()
            combined._placements.extend(other._placements)
            combined._entities.update(other._entities)
            combined._off = (*combined._off, *other._off)
            combined._bus = combined._bus if combined._bus is not None else other._bus
        return combined

    def __repr__(self) -> str:
        names = ", ".join(one.item.__name__ for one in self._placements) or "empty"
        return f"Hooks({names})"


@dataclass(frozen=True)
class _Link:
    hook: type[Hook]
    entities: tuple[type, ...]


class HookChain:
    """The hooks one repository runs at each moment, in order, with one instance of each.

        chain = HookChain(billing_hooks)                nothing read yet
        chain.fire("before_save", invoice)              every hook for Invoice, in order
        chain.read(invoice)            →  invoice        or the record an after_read answered
        chain.searched(Invoice, page)  →  page           or the page an after_search answered

    Context: lazy, the way the bus is — nothing is read while the repository is built. The
    collection is read, and its package walked, the first time a moment fires, when every module
    of the bounded context has loaded; so a repository may be built anywhere — inside
    `register_dependencies()` or at the top of `dependencies.py` — and a hook module may import
    its base from wherever the project re-exports it. From that first read the collection refuses
    a hook registered late. An instance is built the first time one of its moments fires, so a
    hook that cannot be built does not take the repository down, and `self` is the same object
    in `before_save` and `after_save`. A unit of work and a narrowing are the same store seen
    differently, so they run the chain of the repository they came from. The repository closes
    each moment with its own hook, after the chain.
    """

    def __init__(self, hooks: Hooks | None) -> None:
        self._hooks = hooks
        self._by_moment: dict[str, tuple[_Link, ...]] | None = None
        self._compiling = Lock()
        self._instances: dict[type[Hook], Hook] = {}
        self._building = Lock()

    def _compiled(self) -> dict[str, tuple[_Link, ...]]:
        """For each moment, the hooks that implement it and the aggregates they are for — read
        from the collection on first use, once.

        1. The collection's hooks in the order they run (its package walked, if not yet).
        2. Final: grouped by moment. Checked again inside the lock: threads reaching the first
           fire together would otherwise each read the collection.
        """
        compiled = self._by_moment
        if compiled is not None:
            return compiled
        with self._compiling:
            if self._by_moment is None:
                hooks = self._hooks
                links = [_Link(one, hooks.entities_of(one)) for one in hooks] if hooks else []
                self._by_moment = {
                    moment: tuple(
                        one for one in links if getattr(one.hook, moment, None) is not None
                    )
                    for moment in MOMENTS
                }
            return self._by_moment

    def _instance(self, hook: type[Hook]) -> Hook:
        """The one instance of `hook`, built on first use. Context: checked again inside the
        lock — threads reaching a first fire together would otherwise each build one."""
        built = self._instances.get(hook)
        if built is not None:
            return built
        with self._building:
            built = self._instances.get(hook)
            if built is None:
                built = hook()
                built._hooks = self._hooks
                self._instances[hook] = built
            return built

    def fire(self, moment: str, record: Any) -> None:
        """Every hook implementing `moment` whose aggregate `record` is, in order.

        fire("before_save", Invoice(total=-1))   →   ContractViolation from InvoiceMustBalance
        """
        for link in self._compiled()[moment]:
            if isinstance(record, link.entities):
                getattr(self._instance(link.hook), moment)(record)

    def read(self, record: Any) -> Any:
        """`after_read`, in order; a hook that answers a record puts it in place, `None` keeps
        the one it was handed.

            read(Invoice(approved=False))   →   Invoice(approved=True)    when a hook approves it
        """
        for link in self._compiled()["after_read"]:
            if isinstance(record, link.entities):
                answered = getattr(self._instance(link.hook), "after_read")(record)
                record = record if answered is None else answered
        return record

    def searched(self, model: type, page: Any) -> Any:
        """`after_search`, once for the page, for the hooks whose aggregate `model` is; a hook
        that answers a page puts it in place."""
        for link in self._compiled()["after_search"]:
            if issubclass(model, link.entities):
                answered = getattr(self._instance(link.hook), "after_search")(page)
                page = page if answered is None else answered
        return page
