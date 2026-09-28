"""What a project puts around its own aggregates: `Hook` classes in a `Hooks` collection, run by
the repository they are given to.

A hook validates, computes or refuses, inside the write, with what a Feature of its bus has. The
one thing refused is a write back through the repository that fired it, rather than a recursion.
`test_hook_extension.py` covers placing hooks among each other; this file, what one hook is.
"""

from dataclasses import dataclass

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.ddd.repositories import Hook, Hooks, MemoryRepository
from sincpro_framework.exceptions import DependencyNotRegistered, ExtensionRefused

NOTHING_TO_WALK = "sincpro_framework.ddd.value_object"
"""A module rather than a package, so building a collection here imports nothing: these tests
are about the mechanism, not about discovery."""


@dataclass
class Invoice(Entity):
    total: int = 0
    approved: bool = False


@dataclass
class Order(Entity):
    reference: str = ""


class BillingClient:
    def __init__(self, ceiling: int = 100) -> None:
        self.ceiling = ceiling

    def allows(self, total: int) -> bool:
        return total < self.ceiling


class DependencyContextType:
    """What this context registers, declared once — the same class the bus is parameterized
    with, so a hook inherits the autocomplete instead of redeclaring every name."""

    billing: BillingClient
    audit_log: list
    repository: MemoryRepository


class BillingHook(Hook, DependencyContextType):
    """The bounded context's base hook, as `Feature` has its own."""


def a_bus() -> UseFramework:
    bus = UseFramework[DependencyContextType]("billing-context", log_after_execution=False)
    bus.add_dependency("billing", BillingClient())
    bus.add_dependency("audit_log", [])
    return bus


def given(bus: UseFramework | None = None) -> tuple[Hooks, UseFramework]:
    """A collection filled by hand, given a bus."""
    bus = bus or a_bus()
    return Hooks(None).inject(bus), bus


# ---------------------------------------------------------------------------------------------
# What a hook is for, and what it does
# ---------------------------------------------------------------------------------------------


def test_a_hook_runs_only_for_the_aggregate_it_is_registered_for():
    hooks, bus = given()

    @hooks.on(Invoice)
    class Watches(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            self.audit_log.append(invoice.id)

    repository = MemoryRepository(hooks=hooks)
    repository.save(Invoice(id="inv"))
    repository.save(Order(id="ord"))

    assert bus.deps.audit_log == ["inv"]


def test_a_hook_refuses_a_write_by_raising_and_nothing_lands():
    hooks, _bus = given()

    @hooks.on(Invoice)
    class MustBePositive(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            if invoice.total < 0:
                raise ContractViolation("a total cannot be negative")

    repository = MemoryRepository(hooks=hooks)

    with pytest.raises(ContractViolation, match="negative"):
        repository.save(Invoice(total=-1))
    assert repository.count(Invoice).value == 0


def test_a_hook_selects_by_isinstance_so_a_subclass_is_covered():
    @dataclass
    class CreditNote(Invoice):
        pass

    hooks, bus = given()

    @hooks.on(Invoice)
    class Watches(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            self.audit_log.append(type(invoice).__name__)

    MemoryRepository(hooks=hooks).save(CreditNote())

    assert bus.deps.audit_log == ["CreditNote"]


def test_a_hook_on_object_runs_for_every_record():
    """What an audit or a log wants: every record is an `object`."""
    hooks, bus = given()

    @hooks.on(object)
    class AuditsEverything(BillingHook):
        def before_save(self, record: Entity) -> None:
            self.audit_log.append(type(record).__name__)

    repository = MemoryRepository(hooks=hooks)
    repository.save(Invoice())
    repository.save(Order())

    assert bus.deps.audit_log == ["Invoice", "Order"]


def test_after_read_may_put_another_record_in_place():
    hooks, _bus = given()

    @hooks.on(Invoice)
    class Approves(BillingHook):
        def after_read(self, invoice: Invoice) -> Invoice:
            invoice.approved = True
            return invoice

    repository = MemoryRepository(hooks=hooks)
    repository.save(Invoice(id="inv"))

    found = repository.get(Invoice, "inv")
    assert found is not None and found.approved


def test_every_moment_fires_once_per_record_on_a_write_and_a_removal():
    hooks, bus = given()

    @hooks.on(Invoice)
    class Watches(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            self.audit_log.append("before_save")

        def after_save(self, invoice: Invoice) -> None:
            self.audit_log.append("after_save")

        def before_remove(self, invoice: Invoice) -> None:
            self.audit_log.append("before_remove")

        def after_remove(self, invoice: Invoice) -> None:
            self.audit_log.append("after_remove")

    repository = MemoryRepository(hooks=hooks)
    invoice = Invoice(id="inv")
    repository.save(invoice)
    repository.remove(invoice)

    assert bus.deps.audit_log == [
        "before_save",
        "after_save",
        "before_remove",
        "after_remove",
    ]


def test_a_hook_that_implements_no_moment_is_refused_where_it_is_registered():
    hooks, _bus = given()

    with pytest.raises(ExtensionRefused, match="implements none of"):

        @hooks.on(Invoice)
        class DoesNothing(BillingHook):
            pass


def test_a_hook_that_writes_is_refused_instead_of_recursing():
    """A silent loop in production is worse than a loud refusal in development."""
    hooks, bus = given()

    @hooks.on(Invoice)
    class WritesFromInside(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            self.repository.save(Order(id="sneaky"))

    repository = MemoryRepository(hooks=hooks)
    bus.add_dependency("repository", repository)

    with pytest.raises(ContractViolation, match="does not write"):
        repository.save(Invoice())


def test_a_batch_is_refused_whole_the_way_the_engine_refuses_it():
    """Every `before_save`, then the write. Refused halfway, nothing is written and no
    `after_save` ran — pinned against the engine too, in `tests/orm/test_lifecycle_parity.py`.
    """
    hooks, bus = given()

    @hooks.on(Invoice)
    class RefusesTheSecond(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            self.audit_log.append(("before", invoice.total))
            if invoice.total == 2:
                raise ContractViolation("not this one")

        def after_save(self, invoice: Invoice) -> None:
            self.audit_log.append(("after", invoice.total))

    repository = MemoryRepository(hooks=hooks)

    with pytest.raises(ContractViolation):
        repository.save([Invoice(total=one) for one in (1, 2, 3)])

    assert bus.deps.audit_log == [("before", 1), ("before", 2)]
    assert repository.count(Invoice).value == 0


def test_a_reading_that_hands_back_no_records_fires_no_after_read():
    """`count`, `exists` and `pluck` are computed in SQL on the engine, which builds nothing — a
    hook that fired here and not there would fire in a test and never in production."""
    hooks, bus = given()

    @hooks.on(Invoice)
    class CountsReads(BillingHook):
        def after_read(self, invoice: Invoice) -> None:
            self.audit_log.append(1)

    repository = MemoryRepository(hooks=hooks)
    for _ in range(3):
        repository.save(Invoice())

    repository.count(Invoice)
    repository.exists(Invoice)
    repository.pluck(Invoice, "id")
    assert bus.deps.audit_log == []

    repository.fetch_all(Invoice)
    assert len(bus.deps.audit_log) == 3


# ---------------------------------------------------------------------------------------------
# Dependencies and context
# ---------------------------------------------------------------------------------------------


def test_a_hook_reads_the_registered_dependencies_as_its_own_attributes():
    """`self.billing`, the way a Feature writes it."""
    hooks, bus = given()

    @hooks.on(Invoice)
    class ChecksWithBilling(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            if not self.billing.allows(invoice.total):
                raise ContractViolation(f"billing refused {invoice.total}")
            self.audit_log.append(invoice.total)

    repository = MemoryRepository(hooks=hooks)
    repository.save(Invoice(total=10))

    assert bus.deps.audit_log == [10]
    with pytest.raises(ContractViolation, match="refused 500"):
        repository.save(Invoice(total=500))


def test_a_dependency_is_resolved_when_the_hook_reads_it_not_when_it_is_built():
    """Which is what makes the wiring order stop mattering: here the dependency is registered
    after the repository exists."""
    bus = UseFramework[DependencyContextType]("late", log_after_execution=False)
    hooks, _bus = given(bus)

    @hooks.on(Invoice)
    class Audits(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            self.audit_log.append(invoice.total)

    repository = MemoryRepository(hooks=hooks)
    bus.add_dependency("audit_log", [])

    repository.save(Invoice(total=3))

    assert bus.deps.audit_log == [3]


def test_a_collection_given_its_bus_after_the_repository_was_built_is_still_seen():
    """The wiring order a bus forces: the repository is a dependency *of* the bus, so the bus is
    built around it and given to the collection afterwards."""
    hooks = Hooks(NOTHING_TO_WALK)

    @hooks.on(Invoice)
    class Tags(Hook):
        tag: str

        def before_save(self, invoice: Invoice) -> None:
            self.said.append(self.tag)

    repository = MemoryRepository(hooks=hooks)
    bus = UseFramework("late", log_after_execution=False)
    hooks.inject(bus)
    bus.add_dependency("tag", "resolved")
    bus.add_dependency("said", [])
    bus.add_dependency("repository", repository)

    repository.save(Invoice())

    assert bus.deps.said == ["resolved"]


def test_a_collection_never_given_a_bus_says_where_to_give_it():
    hooks = Hooks(None)

    @hooks.on(Invoice)
    class Audits(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            self.audit_log.append(1)

    with pytest.raises(DependencyNotRegistered, match=r"hooks\.inject\(bus\)"):
        MemoryRepository(hooks=hooks).save(Invoice())


def test_an_attribute_the_hook_sets_itself_wins_over_a_registered_one():
    hooks, bus = given()

    @hooks.on(Invoice)
    class HasItsOwn(BillingHook):
        def __init__(self) -> None:
            self.audit_log = ["mine"]

        def before_save(self, invoice: Invoice) -> None:
            self.audit_log.append(invoice.total)

    MemoryRepository(hooks=hooks).save(Invoice(total=1))

    assert bus.deps.audit_log == []


def test_a_property_that_raises_keeps_its_own_message():
    """A property raising `AttributeError` lands in `__getattr__`, where answering it with a
    dependency lookup would replace a real bug's message with a wrong one."""

    class Computes(Hook):
        @property
        def total(self) -> int:
            raise AttributeError("the real bug: self.lines was never set")

        def before_save(self, invoice: Invoice) -> None: ...

    with pytest.raises(AttributeError, match="the real bug: self.lines was never set"):
        Computes().total


def test_a_hook_reads_the_request_context_of_the_bus():
    """`self.context` is what a Feature reads — a hook that refuses a write usually has to say
    on whose behalf. Outside a request it is empty."""
    hooks, bus = given()
    said: list[str | None] = []

    @hooks.on(Invoice)
    class Watches(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            said.append(self.context.get("user.id"))

    repository = MemoryRepository(hooks=hooks)
    with bus.context({"user.id": "andres"}):
        repository.save(Invoice())
    repository.save(Invoice())

    assert said == ["andres", None]


# ---------------------------------------------------------------------------------------------
# The instance: built late, once, per repository
# ---------------------------------------------------------------------------------------------


def test_nothing_is_built_while_the_repository_is():
    """A hook whose construction fails must not take the repository with it."""
    hooks, _bus = given()
    built: list[str] = []

    @hooks.on(Invoice)
    class Explodes(BillingHook):
        def __init__(self) -> None:
            built.append("built")
            raise RuntimeError("this hook cannot be built")

        def before_save(self, invoice: Invoice) -> None: ...

    repository = MemoryRepository(hooks=hooks)
    assert built == []

    with pytest.raises(RuntimeError, match="cannot be built"):
        repository.save(Invoice())


def test_a_hook_is_built_once_and_reused():
    hooks, _bus = given()
    builds: list[int] = []

    @hooks.on(Invoice)
    class CountsItsBuilds(BillingHook):
        def __init__(self) -> None:
            builds.append(1)

        def before_save(self, invoice: Invoice) -> None: ...

    repository = MemoryRepository(hooks=hooks)
    for _ in range(3):
        repository.save(Invoice())

    assert len(builds) == 1


def test_a_hook_that_implements_two_moments_is_one_object():
    """`self` means the same thing in both, so "decide it in `before_save`, act on it in
    `after_save`" keeps what it decided."""
    hooks, _bus = given()
    seen: set[int] = set()
    carried: list[str] = []

    @hooks.on(Invoice)
    class Decides(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            seen.add(id(self))
            self.decided = "what before_save worked out"

        def after_save(self, invoice: Invoice) -> None:
            seen.add(id(self))
            carried.append(self.decided)

    MemoryRepository(hooks=hooks).save(Invoice())

    assert len(seen) == 1 and carried == ["what before_save worked out"]


def test_each_repository_builds_its_own_instance_of_a_hook():
    """The instance is the store's: two repositories given one collection never share one."""
    hooks, _bus = given()
    instances: set[int] = set()

    @hooks.on(Invoice)
    class Watches(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            instances.add(id(self))

    MemoryRepository(hooks=hooks).save(Invoice())
    MemoryRepository(hooks=hooks).save(Invoice())

    assert len(instances) == 2


def test_one_instance_is_built_even_when_threads_reach_the_first_fire_together():
    """Sixteen threads at a hook's first fire build one: a hook whose `__init__` takes a
    connection would otherwise leak the copies that lost."""
    import threading

    hooks, _bus = given()
    builds: list[int] = []

    @hooks.on(Invoice)
    class Expensive(BillingHook):
        def __init__(self) -> None:
            builds.append(1)

        def before_save(self, invoice: Invoice) -> None: ...

    repository = MemoryRepository(hooks=hooks)
    ready = threading.Barrier(16)

    def fire() -> None:
        ready.wait()
        repository.save(Invoice())

    threads = [threading.Thread(target=fire) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(builds) == 1


# ---------------------------------------------------------------------------------------------
# The collection
# ---------------------------------------------------------------------------------------------


def test_a_collection_is_an_object_somebody_made_printable_and_countable():
    hooks, bus = given()

    @hooks.on(Invoice)
    class OnInvoices(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            self.audit_log.append("invoice only")

    @hooks.on(object)
    class OnEverything(BillingHook):
        def before_save(self, record: Entity) -> None:
            self.audit_log.append(f"any: {type(record).__name__}")

    assert len(hooks) == 2
    assert repr(hooks) == "Hooks(OnInvoices, OnEverything)"
    assert list(hooks) == [OnInvoices, OnEverything]

    repository = MemoryRepository(hooks=hooks)
    repository.save(Invoice())
    repository.save(Order())

    assert bus.deps.audit_log == ["invoice only", "any: Invoice", "any: Order"]


def test_the_decorator_answers_the_class_unchanged():
    hooks, _bus = given()

    @hooks.on(Invoice)
    class Something(BillingHook):
        def before_save(self, invoice: Invoice) -> None: ...

    assert Something.__name__ == "Something"
    assert hooks.entities_of(Something) == (Invoice,)
    assert not hasattr(
        Something, "entity"
    )  # what it is for is the collection's, not the class's


def test_a_collection_or_a_hook_passed_where_aggregates_go_is_refused():
    """`MemoryRepository(*records, …)` would store it as a record: nothing would run, and a
    suite written that way passes without ever running what it was written to prove."""
    hooks, _bus = given()

    @hooks.on(Invoice)
    class Watches(BillingHook):
        def before_save(self, invoice: Invoice) -> None: ...

    with pytest.raises(ContractViolation, match="hooks="):
        MemoryRepository(hooks)
    with pytest.raises(ContractViolation, match="hooks="):
        MemoryRepository(Watches)


def test_a_collection_built_in_a_module_does_not_walk_the_package_around_it():
    """Walking `myapp` from `myapp/wiring.py` imports far more than anyone asked for, and is a
    circular import waiting for the first `from myapp.wiring import repository`."""
    import types

    module = types.ModuleType("somepkg.wiring")
    module.__package__ = "somepkg"
    exec(
        "from sincpro_framework.ddd.repositories import Hooks\ncollected = Hooks()",
        vars(module),
    )

    assert vars(module)["collected"]._package == "somepkg.wiring"


def test_a_collection_walks_its_package_and_the_hooks_there_register(tmp_path, monkeypatch):
    """The hooks live in modules of the package whose `__init__` built the collection; nobody
    imports them, the first read does."""
    import importlib
    import sys

    package = tmp_path / "walkedpkg"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from sincpro_framework.ddd.repositories import Hooks\ncollected = Hooks()\n"
    )
    (package / "invoices.py").write_text(
        "from sincpro_framework.ddd.repositories import Hook\n"
        "from walkedpkg import collected\n"
        "@collected.on(object)\n"
        "class Found(Hook):\n"
        "    def before_save(self, record): ...\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    try:
        collected = importlib.import_module("walkedpkg").collected
        assert [hook.__name__ for hook in collected] == ["Found"]
    finally:
        for name in [one for one in sys.modules if one.startswith("walkedpkg")]:
            del sys.modules[name]


def test_a_collection_whose_package_fails_to_import_stays_loud(tmp_path, monkeypatch):
    """Every read fails the same way, instead of answering a half-filled collection."""
    import importlib
    import sys

    package = tmp_path / "halfpkg"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from sincpro_framework.ddd.repositories import Hooks\ncollected = Hooks()\n"
    )
    (package / "a_registers.py").write_text(
        "from sincpro_framework.ddd.repositories import Hook\n"
        "from halfpkg import collected\n"
        "@collected.on(object)\n"
        "class First(Hook):\n"
        "    def before_save(self, record): ...\n"
    )
    (package / "b_breaks.py").write_text('raise ImportError("this module is broken")\n')
    monkeypatch.syspath_prepend(str(tmp_path))

    try:
        collected = importlib.import_module("halfpkg").collected
        for _ in range(2):
            with pytest.raises(ImportError, match="this module is broken"):
                len(collected)
    finally:
        for name in [one for one in sys.modules if one.startswith("halfpkg")]:
            del sys.modules[name]


# ---------------------------------------------------------------------------------------------
# The guard counts against the store, and only on the thread that is inside a hook
# ---------------------------------------------------------------------------------------------


def _hammer(repository: MemoryRepository, reads: bool) -> list[str]:
    import sys
    import threading

    stored = [Invoice() for _ in range(20)]
    for invoice in stored:
        repository.save(invoice)
    refused: list[str] = []
    ready = threading.Barrier(8)
    was = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # force the interleaving instead of hoping for it

    def hammer() -> None:
        ready.wait()
        try:
            for step in range(200):
                if reads:
                    repository.get(Invoice, stored[step % 20].id)
                repository.save(Invoice())
        except Exception as boom:  # noqa: BLE001 — the point is that nothing is raised
            refused.append(type(boom).__name__)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(was)
    return refused


def test_a_shared_repository_with_hooks_is_usable_from_several_threads():
    """A repository is built once per bounded context and shared by request threads: the
    window one thread opens must not make another look like it writes from inside a hook."""
    hooks, _bus = given()

    @hooks.on(Invoice)
    class Watches(BillingHook):
        def before_save(self, invoice: Invoice) -> None: ...

    assert _hammer(MemoryRepository(hooks=hooks), reads=True) == []


def test_a_repository_with_no_hooks_is_usable_from_several_threads():
    assert _hammer(MemoryRepository(), reads=False) == []


def test_one_thread_inside_a_hook_does_not_refuse_another_thread_writing():
    """The hook is held open until the other thread has written, so the overlap is certain."""
    import threading

    inside = threading.Event()
    may_leave = threading.Event()
    hooks, _bus = given()

    @hooks.on(Invoice)
    class HoldsTheWindow(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            inside.set()
            may_leave.wait(timeout=5)

    repository = MemoryRepository(hooks=hooks)
    refused: list[str] = []
    holder = threading.Thread(target=lambda: repository.save(Invoice()))
    holder.start()
    assert inside.wait(timeout=5), "the hook never ran"

    try:
        repository.save(Order())
    except Exception as boom:  # noqa: BLE001
        refused.append(type(boom).__name__)
    finally:
        may_leave.set()
        holder.join(timeout=5)

    assert refused == []


def test_the_window_closes_when_a_hook_raises():
    hooks, _bus = given()

    @hooks.on(Invoice)
    class RefusesNegatives(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            if invoice.total < 0:
                raise ContractViolation("no")

    repository = MemoryRepository(hooks=hooks)
    with pytest.raises(ContractViolation):
        repository.save(Invoice(total=-1))

    repository.save(Invoice(total=1))  # the same repository writes again
    assert repository.count(Invoice).value == 1


# ---------------------------------------------------------------------------------------------
# The bus, from a hook
# ---------------------------------------------------------------------------------------------


def test_a_hook_executes_a_query_on_the_bus_it_was_given():
    """Everything a Feature of that bus has: here, the bus itself."""
    from sincpro_framework import DataTransferObject, Feature

    class QueryCeiling(DataTransferObject):
        pass

    class ResponseCeiling(DataTransferObject):
        ceiling: int

    bus = a_bus()

    @bus.feature(QueryCeiling)
    class Ceiling(Feature):
        def execute(self, dto: QueryCeiling) -> ResponseCeiling:
            return ResponseCeiling(ceiling=50)

    hooks, _bus = given(bus)

    @hooks.on(Invoice)
    class AsksTheBus(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            ceiling = self.bus(QueryCeiling(), ResponseCeiling).ceiling
            if invoice.total > ceiling:
                raise ContractViolation(f"over {ceiling}")

    repository = MemoryRepository(hooks=hooks)
    repository.save(Invoice(total=10))

    with pytest.raises(ContractViolation, match="over 50"):
        repository.save(Invoice(total=90))


def test_a_command_from_a_hook_that_writes_back_through_its_repository_is_refused():
    """The one thing the bus cannot do from a hook: fire the same hook again, forever."""
    from sincpro_framework import DataTransferObject, Feature

    class CommandStoreOrder(DataTransferObject):
        pass

    bus = a_bus()
    hooks, _bus = given(bus)

    @hooks.on(Invoice)
    class StoresAnOrder(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            self.bus(CommandStoreOrder())

    repository = MemoryRepository(hooks=hooks)
    bus.add_dependency("repository", repository)

    @bus.feature(CommandStoreOrder)
    class StoreOrder(Feature):
        repository: MemoryRepository

        def execute(self, dto: CommandStoreOrder) -> None:
            self.repository.save(Order(id="from-the-hook"))

    with pytest.raises(ContractViolation, match="does not write"):
        repository.save(Invoice())


def test_a_hook_whose_collection_has_no_bus_says_where_to_give_it():
    from sincpro_framework import DataTransferObject

    class QueryAnything(DataTransferObject):
        pass

    hooks = Hooks(None)

    @hooks.on(Invoice)
    class AsksTheBus(BillingHook):
        def before_save(self, invoice: Invoice) -> None:
            self.bus(QueryAnything())

    with pytest.raises(DependencyNotRegistered, match=r"hooks\.inject\(bus\)"):
        MemoryRepository(hooks=hooks).save(Invoice())


# ---------------------------------------------------------------------------------------------
# Typed the way a Feature is
# ---------------------------------------------------------------------------------------------


def test_a_bounded_context_types_its_hooks_as_it_types_its_features():
    """`Hook[ContextType]` types `self.context`, `DependencyContextType` types `self.<name>`,
    and `self.bus(Query, Response)` answers the Response — the three a Feature has."""
    from typing import TypedDict

    from sincpro_framework import DataTransferObject, Feature

    class BillingContext(TypedDict, total=False):
        user_id: str

    class TypedBillingHook(Hook[BillingContext], DependencyContextType):
        """What `framework.py` of the bounded context declares, once."""

    class QueryCeiling(DataTransferObject):
        pass

    class ResponseCeiling(DataTransferObject):
        ceiling: int

    bus = a_bus()

    @bus.feature(QueryCeiling)
    class Ceiling(Feature):
        def execute(self, dto: QueryCeiling) -> ResponseCeiling:
            return ResponseCeiling(ceiling=100)

    hooks, _bus = given(bus)
    said: list[str] = []

    @hooks.on(Invoice)
    class Typed(TypedBillingHook):
        def before_save(self, invoice: Invoice) -> None:
            ceiling: int = self.bus(QueryCeiling(), ResponseCeiling).ceiling
            allowed: bool = self.billing.allows(invoice.total)
            said.append(f"{self.context.get('user_id')} {ceiling} {allowed}")

    with bus.context({"user_id": "ana"}):
        MemoryRepository(hooks=hooks).save(Invoice(total=10))

    assert said == ["ana 100 True"]
