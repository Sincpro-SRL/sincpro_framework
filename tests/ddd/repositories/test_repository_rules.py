"""What a project puts around its own aggregates, injected where the repository is built.

A rule is a domain service: a plain function over one aggregate. It validates, computes or
refuses. It runs inside the write, so it does not publish, does not call a bus, and does not
write — the last one is refused rather than left to recurse.
"""

from dataclasses import dataclass

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.ddd.repositories import MemoryRepository
from sincpro_framework.ddd.repositories.repository import Hook, Hooks, Rule

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


def test_a_rule_runs_only_for_the_aggregate_it_names():
    seen: list[str] = []
    repository = MemoryRepository(
        rules=[Rule(entity=Invoice, before_save=lambda one: seen.append(one.id))]
    )

    repository.save(Invoice(id="inv"))
    repository.save(Order(id="ord"))

    assert seen == ["inv"]


def test_a_rule_refuses_a_write_by_raising():
    def must_balance(invoice: Invoice) -> None:
        if invoice.total < 0:
            raise ContractViolation("a total cannot be negative")

    repository = MemoryRepository(rules=[Rule(entity=Invoice, before_save=must_balance)])

    with pytest.raises(ContractViolation, match="negative"):
        repository.save(Invoice(total=-1))

    assert repository.count(Invoice).value == 0  # nothing landed


def test_one_moment_takes_one_function_or_several_in_order():
    """The same «one or many» `save` takes, so a caller never picks a shape."""
    order: list[str] = []
    repository = MemoryRepository(
        rules=[
            Rule(
                entity=Invoice,
                before_save=[
                    lambda one: order.append("first"),
                    lambda one: order.append("second"),
                ],
            )
        ]
    )

    repository.save(Invoice())

    assert order == ["first", "second"]


def test_several_rules_for_the_same_aggregate_run_in_the_order_listed():
    order: list[str] = []
    repository = MemoryRepository(
        rules=[
            Rule(entity=Invoice, before_save=lambda one: order.append("a")),
            Rule(entity=Invoice, before_save=lambda one: order.append("b")),
        ]
    )

    repository.save(Invoice())

    assert order == ["a", "b"]


def test_a_rule_selects_by_isinstance_so_a_subclass_is_covered():
    @dataclass
    class CreditNote(Invoice):
        pass

    seen: list[str] = []
    repository = MemoryRepository(
        rules=[Rule(entity=Invoice, before_save=lambda one: seen.append(type(one).__name__))]
    )

    repository.save(CreditNote())

    assert seen == ["CreditNote"]


def test_after_read_may_put_another_record_in_place():
    def approve(invoice: Invoice) -> Invoice:
        invoice.approved = True
        return invoice

    repository = MemoryRepository(rules=[Rule(entity=Invoice, after_read=approve)])
    repository.save(Invoice(id="inv"))

    found = repository.get(Invoice, "inv")

    assert found is not None and found.approved


def test_every_moment_fires_once_per_record_on_a_write_and_a_removal():
    fired: list[str] = []
    repository = MemoryRepository(
        rules=[
            Rule(
                entity=Invoice,
                before_save=lambda one: fired.append("before_save"),
                after_save=lambda one: fired.append("after_save"),
                before_remove=lambda one: fired.append("before_remove"),
                after_remove=lambda one: fired.append("after_remove"),
            )
        ]
    )

    invoice = Invoice(id="inv")
    repository.save(invoice)
    repository.remove(invoice)

    assert fired == ["before_save", "after_save", "before_remove", "after_remove"]


def test_a_rule_that_writes_is_refused_instead_of_recursing():
    """A silent loop in production is worse than a loud refusal in development."""
    repository = MemoryRepository()

    def writes_from_inside(invoice: Invoice) -> None:
        repository.save(Order(id="sneaky"))

    repository._rules = (Rule(entity=Invoice, before_save=writes_from_inside),)

    with pytest.raises(ContractViolation, match="does not write"):
        repository.save(Invoice())


# --------------------------------------------------------------- a rule written as a class


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


class ChecksWithBilling(Hook, DependencyContextType):
    def __call__(self, invoice: Invoice) -> None:
        if not self.billing.allows(invoice.total):
            raise ContractViolation(f"billing refused {invoice.total}")


class Audits(Hook, DependencyContextType):
    def __call__(self, invoice: Invoice) -> None:
        self.audit_log.append(invoice.total)


def a_bus():
    bus = UseFramework[DependencyContextType]("billing-context", log_after_execution=False)
    bus.add_dependency("billing", BillingClient())
    bus.add_dependency("audit_log", [])
    return bus


def test_a_hook_reads_the_registered_dependencies_as_its_own_attributes():
    """`self.billing`, the way a Feature writes it — and `bus.deps` handed over once, to the
    repository, not to every hook."""
    bus = a_bus()
    repository = MemoryRepository(
        deps=bus.deps,
        rules=[Rule(entity=Invoice, before_save=[ChecksWithBilling(), Audits()])],
    )

    repository.save(Invoice(total=10))

    assert bus.deps.audit_log == [10]
    with pytest.raises(ContractViolation, match="refused 500"):
        repository.save(Invoice(total=500))


def test_a_rule_may_name_the_class_and_let_the_repository_build_it():
    """The way a bus is handed a Feature class rather than an instance."""
    bus = a_bus()
    repository = MemoryRepository(
        deps=bus.deps, rules=[Rule(entity=Invoice, before_save=[Audits])]
    )

    repository.save(Invoice(total=7))

    assert bus.deps.audit_log == [7]


def test_a_dependency_is_resolved_when_the_hook_runs_not_when_it_is_built():
    """Which is what makes the wiring order stop mattering: the repository here is built
    before the dependency it will need exists."""
    bus = UseFramework[DependencyContextType]("late", log_after_execution=False)
    repository = MemoryRepository(
        deps=bus.deps, rules=[Rule(entity=Invoice, before_save=[Audits])]
    )

    bus.add_dependency("audit_log", [])  # registered after the repository was built

    repository.save(Invoice(total=3))

    assert bus.deps.audit_log == [3]


def test_a_hook_that_was_never_bound_says_so_instead_of_failing_obscurely():
    from sincpro_framework.exceptions import DependencyNotRegistered

    repository = MemoryRepository(rules=[Rule(entity=Invoice, before_save=[Audits])])

    with pytest.raises(DependencyNotRegistered, match="deps=bus.deps"):
        repository.save(Invoice())


def test_an_attribute_the_hook_sets_itself_wins_over_a_registered_one():
    bus = a_bus()

    class HasItsOwn(Hook, DependencyContextType):
        def __init__(self) -> None:
            self.audit_log = ["mine"]

        def __call__(self, invoice: Invoice) -> None:
            self.audit_log.append(invoice.total)

    repository = MemoryRepository(
        deps=bus.deps, rules=[Rule(entity=Invoice, before_save=[HasItsOwn()])]
    )
    repository.save(Invoice(total=1))

    assert bus.deps.audit_log == []  # the registered one was never touched


# ------------------------------------------------------- a hook that declares what it is for


class Watches(Hook, DependencyContextType):
    entity = Invoice

    def before_save(self, invoice: Invoice) -> None:
        self.audit_log.append(("before", invoice.total))

    def after_save(self, invoice: Invoice) -> None:
        self.audit_log.append(("after", invoice.total))


def test_a_hook_that_declares_its_entity_needs_no_rule_around_it():
    """One class per concern, saying what it is for and implementing whichever moments it
    cares about — no plumbing repeated in the wiring."""
    bus = a_bus()
    repository = MemoryRepository(deps=bus.deps, hooks=[Watches])

    repository.save(Invoice(total=5))

    assert bus.deps.audit_log == [("before", 5), ("after", 5)]


def test_nothing_is_built_while_the_repository_is():
    """A hook whose construction is expensive, or fails, must not take the repository with
    it — and a repository half built because of a hook is a failure that looks like anything
    except its cause."""
    built: list[str] = []

    class Explodes(Hook):
        entity = Invoice

        def __init__(self) -> None:
            built.append("built")
            raise RuntimeError("this hook cannot be built")

        def before_save(self, invoice: Invoice) -> None: ...

    repository = MemoryRepository(hooks=[Explodes])  # builds fine
    assert built == []

    with pytest.raises(RuntimeError, match="cannot be built"):
        repository.save(Invoice())  # only now


def test_a_hook_is_built_once_and_reused():
    builds: list[int] = []

    class CountsItsBuilds(Hook):
        entity = Invoice

        def __init__(self) -> None:
            builds.append(1)

        def before_save(self, invoice: Invoice) -> None: ...

    repository = MemoryRepository(hooks=[CountsItsBuilds])
    for _ in range(3):
        repository.save(Invoice())

    assert len(builds) == 1


def test_a_hook_that_names_no_entity_runs_for_every_record():
    """What an audit or a log wants. No special case behind it: the default is `object`, and
    every record is one."""
    bus = a_bus()

    class AuditsEverything(Hook, DependencyContextType):
        def before_save(self, record) -> None:
            self.audit_log.append(type(record).__name__)

    repository = MemoryRepository(deps=bus.deps, hooks=[AuditsEverything])

    repository.save(Invoice())
    repository.save(Order())

    assert bus.deps.audit_log == ["Invoice", "Order"]


def test_a_hook_that_implements_no_moment_says_so():
    class DoesNothing(Hook):
        entity = Invoice

    with pytest.raises(ContractViolation, match="implements none of"):
        MemoryRepository(hooks=[DoesNothing])


def test_hooks_are_collected_by_decorating_them_and_handed_over_whole():
    """The wiring names the collection, not every hook in it — and the collection is an object
    somebody made, printable and countable, not a registry the framework keeps."""
    bus = a_bus()
    billing_hooks = Hooks()

    @billing_hooks
    class OnInvoices(Hook, DependencyContextType):
        entity = Invoice

        def before_save(self, invoice: Invoice) -> None:
            self.audit_log.append("invoice only")

    @billing_hooks
    class OnEverything(Hook, DependencyContextType):
        def before_save(self, record) -> None:
            self.audit_log.append(f"any: {type(record).__name__}")

    assert len(billing_hooks) == 2
    assert repr(billing_hooks) == "Hooks(OnInvoices, OnEverything)"

    repository = MemoryRepository(hooks=billing_hooks, deps=bus.deps)
    repository.save(Invoice())
    repository.save(Order())

    assert bus.deps.audit_log == ["invoice only", "any: Invoice", "any: Order"]


def test_the_decorator_answers_the_class_unchanged():
    collected = Hooks()

    @collected
    class Something(Hook):
        entity = Invoice

        def before_save(self, invoice: Invoice) -> None: ...

    assert Something.__name__ == "Something"  # still the class, usable as itself
    assert list(collected) == [Something]


# --- the guard counts against the store, and only on the thread that is inside a hook ------


def test_a_shared_repository_is_usable_from_several_threads():
    """A repository is built once per bounded context and injected, so request threads share
    one. Held on the instance, the window one thread opened made every other thread look like
    it was writing from inside a hook — a refusal for something it never did."""
    import sys
    import threading

    repository = MemoryRepository(rules=[Rule(entity=Invoice, before_save=lambda one: None)])
    stored = [Invoice() for _ in range(20)]
    for invoice in stored:
        repository.save(invoice)

    refused: list[str] = []
    ready = threading.Barrier(8)
    was = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # force the interleaving instead of hoping for it

    def hammer(which: int) -> None:
        ready.wait()
        try:
            for step in range(200):
                repository.get(Invoice, stored[step % 20].id)
                repository.save(Invoice())
        except Exception as boom:  # noqa: BLE001 — the point is that nothing is raised
            refused.append(type(boom).__name__)

    threads = [threading.Thread(target=hammer, args=(one,)) for one in range(8)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(was)

    assert refused == []


def test_a_repository_with_no_rules_at_all_is_usable_from_several_threads():
    """The window was entered on every read and every write, rules or not — so this failed
    for aggregates nobody ever wrote a rule for."""
    import sys
    import threading

    repository = MemoryRepository()
    refused: list[str] = []
    ready = threading.Barrier(8)
    was = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)

    def hammer(which: int) -> None:
        ready.wait()
        try:
            for _ in range(200):
                repository.save(Invoice())
        except Exception as boom:  # noqa: BLE001
            refused.append(type(boom).__name__)

    threads = [threading.Thread(target=hammer, args=(one,)) for one in range(8)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(was)

    assert refused == []


def test_one_thread_inside_a_hook_does_not_refuse_another_thread_writing():
    """The sharp edge of the same flag: while a rule runs on one thread, an unrelated write on
    another was refused, with a message accusing it of writing from inside a hook. Here the
    rule is held open until the other thread has finished, so the overlap is certain rather
    than hoped for."""
    import threading

    inside = threading.Event()
    may_leave = threading.Event()

    def holds_the_window(invoice: Invoice) -> None:
        inside.set()
        may_leave.wait(timeout=5)

    repository = MemoryRepository(rules=[Rule(entity=Invoice, before_save=holds_the_window)])
    refused: list[str] = []

    def writes_through_the_hook() -> None:
        repository.save(Invoice())

    holder = threading.Thread(target=writes_through_the_hook)
    holder.start()
    assert inside.wait(timeout=5), "the rule never ran"

    try:
        repository.save(Order())  # an unrelated aggregate, from the main thread
    except Exception as boom:  # noqa: BLE001
        refused.append(type(boom).__name__)
    finally:
        may_leave.set()
        holder.join(timeout=5)

    assert refused == []


def test_the_window_closes_when_a_rule_raises():
    def refuses(invoice: Invoice) -> None:
        raise ContractViolation("no")

    repository = MemoryRepository(rules=[Rule(entity=Invoice, before_save=refuses)])
    with pytest.raises(ContractViolation):
        repository.save(Invoice())

    MemoryRepository().save(Invoice())  # a different repository is unaffected
    repository._rules = ()
    repository.save(Invoice())  # and this one recovers


# --- the hook instance belongs to the store, and the dependencies are read when it fires ---


def test_a_collection_injected_after_the_repository_was_built_is_still_seen():
    """The wiring order a bus forces: the repository is a dependency *of* the bus, so the bus
    is built around it and `bus.deps` is injected afterwards. Read once at construction, this
    was a silent no-op that surfaced much later as `DependencyNotRegistered` inside a save —
    blaming the caller for not injecting when they had."""
    collected = Hooks(package=NOTHING_TO_WALK)
    said: list[str] = []

    @collected
    class Audits(Hook):
        entity = Invoice

        def before_save(self, invoice: Invoice) -> None:
            said.append(self.tag)

    repository = MemoryRepository(hooks=collected)  # built first
    bus = UseFramework("late", log_after_execution=False)
    collected.inject(bus.deps)  # injected after
    bus.add_dependency("tag", "resolved")
    bus.add_dependency("repository", repository)  # and the cycle closes here

    repository.save(Invoice())

    assert said == ["resolved"]


def test_a_hook_that_implements_two_moments_is_one_object():
    """`self` has to mean the same thing in both, or the obvious "decide it in `before_save`,
    act on it in `after_save`" loses what it decided — silently, because reading the attribute
    back goes through `__getattr__` and hunts for a dependency by that name."""
    collected = Hooks(package=NOTHING_TO_WALK)
    seen: set[int] = set()
    carried: list[str] = []

    @collected
    class Decides(Hook):
        entity = Invoice

        def before_save(self, invoice: Invoice) -> None:
            seen.add(id(self))
            self.decided = "what before_save worked out"

        def after_save(self, invoice: Invoice) -> None:
            seen.add(id(self))
            carried.append(getattr(self, "decided", "lost"))

    MemoryRepository(hooks=collected).save(Invoice())

    assert len(seen) == 1
    assert carried == ["what before_save worked out"]


def test_two_repositories_given_one_built_hook_do_not_cross_wire_their_dependencies():
    """Bound in place, the second repository's dependencies followed the object back into the
    first — one bounded context resolving another's, with no error. In a process holding two
    tenants that is a data leak."""
    resolved: list[str] = []

    class Reads(Hook):
        entity = Invoice

        def before_save(self, invoice: Invoice) -> None:
            resolved.append(self.tag)

    shared = Reads()
    first = UseFramework("first", log_after_execution=False)
    first.add_dependency("tag", "first")
    second = UseFramework("second", log_after_execution=False)
    second.add_dependency("tag", "second")

    here = MemoryRepository(hooks=[shared], deps=first.deps)
    there = MemoryRepository(hooks=[shared], deps=second.deps)

    here.save(Invoice())
    there.save(Invoice())
    here.save(Invoice())

    assert resolved == ["first", "second", "first"]


def test_the_caller_s_own_hook_object_is_never_bound_behind_their_back():
    class Reads(Hook):
        entity = Invoice

        def before_save(self, invoice: Invoice) -> None: ...

    mine = Reads()
    bus = UseFramework("mine", log_after_execution=False)
    MemoryRepository(hooks=[mine], deps=bus.deps).save(Invoice())

    assert mine._deps is None  # the repository copied it; this one is untouched


# --- wiring mistakes are refused where they are made ---------------------------------------


def test_a_collection_passed_where_aggregates_go_is_refused():
    """`MemoryRepository(*records, …)` would store the collection as a record: zero rules, no
    error, and a suite that passes without ever running what it was written to prove. The two
    stores do not agree on the position, so the mistake is easy and the silence is the danger.
    """
    collected = Hooks(package=NOTHING_TO_WALK)

    @collected
    class Watches(Hook):
        entity = Invoice

        def before_save(self, invoice: Invoice) -> None: ...

    with pytest.raises(ContractViolation, match="hooks="):
        MemoryRepository(collected)
    with pytest.raises(ContractViolation, match="hooks="):
        MemoryRepository(Watches)

    assert len(MemoryRepository(hooks=collected)._rules) == 1


def test_a_hook_already_built_can_be_put_in_a_rule():
    """Documented as one of the three things a moment takes, and it used to be refused by
    pydantic before the repository ever saw it: a hook that implements `before_save` and
    nothing else is not `Callable`."""
    ran: list[str] = []

    class Checks(Hook):
        entity = Invoice

        def before_save(self, invoice: Invoice) -> None:
            ran.append("before_save")

    MemoryRepository(rules=[Rule(entity=Invoice, before_save=Checks())]).save(Invoice())

    assert ran == ["before_save"]


def test_the_slot_a_hook_is_written_in_says_which_method_runs():
    """A hook written for one job implements `__call__`; one written for a moment implements
    that moment. Both belong in the same slot and both have to work."""
    ran: list[str] = []

    class Named(Hook):
        entity = Invoice

        def before_save(self, invoice: Invoice) -> None:
            ran.append("before_save")

    class Callable_(Hook):
        entity = Invoice

        def __call__(self, invoice: Invoice) -> None:
            ran.append("__call__")

    for hook in (Named(), Callable_(), Callable_):
        MemoryRepository(rules=[Rule(entity=Invoice, before_save=hook)]).save(Invoice())

    assert ran == ["before_save", "__call__", "__call__"]


def test_a_hook_that_answers_nothing_is_refused_at_wiring():
    """Every class is callable, so asking `getattr(cls, "__call__")` found the metaclass's and
    this got through to its first fire, where it failed as a missing dependency named
    `__call__`."""

    class Empty(Hook):
        entity = Invoice

    with pytest.raises(ContractViolation, match="neither before_save nor __call__"):
        MemoryRepository(rules=[Rule(entity=Invoice, before_save=Empty())])


def test_a_property_that_raises_keeps_its_own_message():
    """`DependencyNotRegistered` is an `AttributeError`, and a property raising one of its own
    lands in `__getattr__` too — where answering it with a dependency lookup replaced a real
    bug's message with a wrong one."""

    class Computes(Hook):
        entity = Invoice

        @property
        def total(self) -> int:
            raise AttributeError("the real bug: self.lines was never set")

        def before_save(self, invoice: Invoice) -> None: ...

    with pytest.raises(AttributeError, match="the real bug: self.lines was never set"):
        Computes().total


def test_a_collection_built_in_a_module_does_not_walk_the_package_around_it():
    """`Hooks()` remembered `__package__`, so one written in `myapp/wiring.py` imported every
    module of `myapp` the first time a repository read it — far more than anyone asked for, and
    a circular import waiting for the first `from myapp.wiring import repository`."""
    import types

    module = types.ModuleType("somepkg.wiring")
    module.__package__ = "somepkg"
    exec(
        "from sincpro_framework.ddd.repositories import Hooks\ncollected = Hooks()",
        vars(module),
    )

    assert vars(module)["collected"]._package == "somepkg.wiring"


def test_a_reading_that_hands_back_no_records_fires_no_after_read():
    """`count`, `exists`, `pluck` and a grouping are computed in SQL on the engine, which
    materialises nothing — so a hook that ran here and not there would fire N times in a test
    and zero times in production, and one that *replaces* an aggregate would have its
    replacement thrown away."""
    fired: list[int] = []
    repository = MemoryRepository(
        rules=[Rule(entity=Invoice, after_read=lambda one: fired.append(1))]
    )
    for _ in range(3):
        repository.save(Invoice())

    repository.count(Invoice)
    repository.exists(Invoice)
    repository.pluck(Invoice, "id")
    assert fired == []

    repository.fetch_all(Invoice)
    assert len(fired) == 3  # handed back, so each one is read


def test_a_hook_reads_the_request_context_when_the_collection_was_given_the_bus():
    """`self.context` is what a `Feature` reads, and a rule that refuses a write usually has to
    say on whose behalf. Given only `bus.deps` there is no request to read, and it is empty
    rather than an error."""
    collected = Hooks(package=NOTHING_TO_WALK)
    said: list[str | None] = []

    @collected
    class Watches(Hook):
        entity = Invoice

        def before_save(self, invoice: Invoice) -> None:
            said.append(self.context.get("user.id"))

    bus = UseFramework("with-context", log_after_execution=False)
    collected.inject(bus)
    repository = MemoryRepository(hooks=collected)

    with bus.context({"user.id": "andres"}):
        repository.save(Invoice())
    repository.save(Invoice())  # outside any request

    assert said == ["andres", None]


def test_a_batch_is_refused_whole_the_way_the_engine_refuses_it():
    """The engine runs every `before_save`, then one flush. Interleaved per record, a batch
    whose second aggregate is refused left the first one written and its `after_save` already
    fired — records a test would see and production never stored. Pinned here and in
    `tests/orm/test_hooks.py`, which measures the engine doing the same thing."""
    trail: list[tuple[str, int]] = []

    def refuses_the_second(invoice: Invoice) -> None:
        trail.append(("before", invoice.total))
        if invoice.total == 2:
            raise ContractViolation("not this one")

    repository = MemoryRepository(
        rules=[
            Rule(entity=Invoice, before_save=refuses_the_second),
            Rule(entity=Invoice, after_save=lambda one: trail.append(("after", one.total))),
        ]
    )

    with pytest.raises(ContractViolation):
        repository.save([Invoice(total=one) for one in (1, 2, 3)])

    assert trail == [("before", 1), ("before", 2)]  # no after_save ran
    assert repository.count(Invoice).value == 0  # and nothing was written


def test_a_collection_whose_package_fails_to_import_stays_loud(tmp_path, monkeypatch):
    """Marked loaded before the walk, a module that raised left whatever had registered before
    it with the flag already set — so the second read answered a half-filled collection, and
    every hook after the broken module was gone, silently and for good."""
    import sys

    package = tmp_path / "halfpkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "a_registers.py").write_text(
        "from sincpro_framework.ddd.repositories import Hook\n"
        "class First(Hook):\n"
        "    def before_save(self, record): ...\n"
    )
    (package / "b_breaks.py").write_text('raise ImportError("this module is broken")\n')
    monkeypatch.syspath_prepend(str(tmp_path))

    collected = Hooks("halfpkg")
    try:
        for _ in range(2):  # every read, not only the first
            with pytest.raises(ImportError, match="this module is broken"):
                len(collected)
    finally:
        for name in [one for one in sys.modules if one.startswith("halfpkg")]:
            del sys.modules[name]


def test_one_instance_is_built_even_when_threads_reach_the_first_fire_together():
    """An unlocked check-then-set: sixteen threads arriving at a hook's first fire each built
    one, and a hook whose `__init__` takes a connection or a handle leaked the copies that
    lost."""
    import threading

    class Expensive(Hook):
        entity = Invoice
        built = 0

        def __init__(self) -> None:
            type(self).built += 1

        def before_save(self, invoice: Invoice) -> None: ...

    repository = MemoryRepository(hooks=[Expensive])
    ready = threading.Barrier(16)

    def fire() -> None:
        ready.wait()
        repository.save(Invoice())

    threads = [threading.Thread(target=fire) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert Expensive.built == 1
