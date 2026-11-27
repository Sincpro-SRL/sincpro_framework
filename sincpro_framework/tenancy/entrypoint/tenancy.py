"""`Tenancy` — multi-tenancy for a bounded context, opt-in: the tenant context of every
execution, the data it may read and write, and the use cases that work across tenants.

    tenancy = Tenancy()                         an aggregate with a `tenant_id` is partitioned
    tenancy.on(billing)                         resolved per call; `self.tenancy` in handlers
    tenancy.isolate(database)                   the data filter on the store

    with billing.context({"tenant_id": "acme"}):
        billing(CommandIssueInvoice(...))       stamped acme; every read sees acme only

    @billing.feature(CommandMonthlyClose)
    @tenancy.required                           no tenant named → TenantRequired
    class MonthlyClose(Feature): ...

    @billing.app_service(CommandConsolidatedReport)
    @tenancy.without_tenant(reason="consolidated report")      every tenant's data, logged
    class ConsolidatedReport(ApplicationService): ...

    @billing.app_service(CommandNightlyDunning)
    @tenancy.each_tenant                        once per active tenant of the catalog
    class NightlyDunning(ApplicationService): ...

Nothing here is required by the rest of the framework, and nothing of it is required here: a bus
without `Tenancy` reads `tenant_id` from its context as before, and a project with a
multi-tenancy of its own keeps it.
"""

from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, TypeVar

from sincpro_framework.context import use_context
from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.repositories.data_filters import DataFilters
from sincpro_framework.exceptions import ProgrammingError
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.tenancy.domain.catalog import Tenant, TenantCatalog
from sincpro_framework.tenancy.domain.discriminator import Shared, TenantDiscriminator
from sincpro_framework.tenancy.domain.exceptions import TenantNotChosen, TenantRequired
from sincpro_framework.tenancy.domain.memberships import Memberships
from sincpro_framework.tenancy.domain.tenant_context import TenantContext
from sincpro_framework.tenancy.services.discrimination import TENANT_FILTER, TenantFilter
from sincpro_framework.tenancy.services.resolution import allowed_tenants, resolved

if TYPE_CHECKING:
    from sincpro_framework.use_bus import UseFramework

Handler = TypeVar("Handler", bound=type)

TENANCY_SEQUENCE = -(10**9) + 1
"""Just inside the guard of auth: the tenant is resolved once the caller is known, and before
any interceptor of the project's reads it."""

DECLARED = "__sincpro_tenancy__"
"""The attribute a handler's declaration is held in."""


class TenantDeclaration:
    """What a handler declared about tenants: whether it needs one, works across them, or runs
    once per tenant."""

    def __init__(self) -> None:
        self.required = False
        self.across: str | None = None
        """The reason it reads across tenants, when it does."""
        self.each = False


def _declaration_of(handler: Any) -> TenantDeclaration:
    """The handler's own declaration — created on first use, inherited by a subclass."""
    found = handler.__dict__.get(DECLARED)
    if found is None:
        inherited = getattr(handler, DECLARED, None)
        found = TenantDeclaration()
        if inherited is not None:
            found.__dict__.update(inherited.__dict__)
        setattr(handler, DECLARED, found)
    return found


class AcrossTenants:
    """Reading and writing across tenants, by name — the tenant filter off and the crossing
    logged with its reason. A context manager for a block, a decorator for a whole use case.

        with tenancy.without_tenant(reason="migration"):
            repository.search(Invoices)                    every tenant's

        @tenancy.without_tenant(reason="consolidated report")
        class ConsolidatedReport(ApplicationService): ...
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self._disabled: Any = None

    def __call__(self, handler: Handler) -> Handler:
        _declaration_of(handler).across = self.reason
        return handler

    def __enter__(self) -> "AcrossTenants":
        logger.info("across tenants", reason=self.reason)
        self._disabled = DataFilters.disabled(TENANT_FILTER)
        self._disabled.__enter__()
        return self

    def __exit__(self, *raised: Any) -> None:
        self._disabled.__exit__(*raised)


class Tenancy:
    """Multi-tenancy for the buses it is put on and the stores it isolates.

    Step by step, every step optional:

        Tenancy()                                       the context names the tenant
        Tenancy(field="company_id", shared=Shared.WHEN_EMPTY)
        Tenancy(memberships=..., catalog=...)           what a caller may name, validated
    """

    def __init__(
        self,
        field: str = "tenant_id",
        shared: Shared = Shared.NEVER,
        memberships: Memberships | None = None,
        catalog: TenantCatalog | None = None,
    ) -> None:
        """`field` is the tenant discriminator of every aggregate that has it; `shared` whether
        an empty one is everyone's. `memberships` says which tenants a subject may name;
        `catalog` which tenants exist and are served — both ports, both optional."""
        self.filter = TenantFilter(field, shared)
        """The data filter `isolate` adds to a store."""
        self.memberships = memberships
        self.catalog = catalog

    @property
    def allowed(self) -> frozenset[str] | None:
        """What the caller may select — `None` without memberships, or nobody known."""
        return allowed_tenants(self.memberships, use_context().get(ContextKey.USER_ID))

    # --- scopes -------------------------------------------------------------------------
    @contextmanager
    def with_tenants(
        self, tenants: Iterable[str], active: str | None = None
    ) -> Generator[TenantContext]:
        """A block that works in these tenants — reading all of them, writing to `active` —
        validated against the caller's memberships as an entrance is.

            with self.tenancy.with_tenants(["acme", "acme-sur"]):    a sub-selection
                ...
        """
        selected = list(dict.fromkeys(tenants))
        values: dict[str, Any] = {
            ContextKey.TENANT_IDS: selected,
            ContextKey.TENANT_ID: (
                active
                if active is not None
                else (selected[0] if len(selected) == 1 else None)
            ),
        }
        with use_context().scoped(values) as scope:
            yield resolved(scope, self.memberships, self.catalog)

    @contextmanager
    def with_tenant(self, tenant: str) -> Generator[TenantContext]:
        """A block that works in one tenant — ABP's `CurrentTenant.Change`, acts_as_tenant's
        `with_tenant`.

            with self.tenancy.with_tenant("acme"):
                billing(CommandIssueInvoice(...))
        """
        with self.with_tenants([tenant], tenant) as tenants:
            yield tenants

    def each(
        self, run: Callable[[], Any], tenants: Iterable[str] | None = None
    ) -> dict[str, Any]:
        """`run` once per tenant, each in its own tenant context — every active tenant of the
        catalog when none are named. A tenant that fails does not stop the others: every
        failure is raised together at the end, as an `ExceptionGroup`.

            self.tenancy.each(lambda: self.feature_bus.execute(CommandSendReminders()))
                ->  {"acme": …, "globex": …}
        """
        if tenants is None:
            if self.catalog is None:
                raise TenantRequired(
                    "each() without tenants needs a catalog to list them — Tenancy(catalog=...)"
                )
            tenants = self.catalog.ids()
        answers: dict[str, Any] = {}
        failures: list[Exception] = []
        for one in tenants:
            try:
                with self.with_tenant(one):
                    answers[one] = run()
            except Exception as failure:
                logger.exception("a tenant's run failed", tenant=one)
                failure.add_note(f"tenant {one!r}")
                failures.append(failure)
        if failures:
            raise ExceptionGroup(f"{len(failures)} tenant(s) failed", failures)
        return answers

    def _intercept(self, handler: Any, dto: Any, call_next: Callable[[Any], Any]) -> Any:
        """One execution of a bus tenancy is on.

        1. The tenant context resolved and validated (`resolution.resolved`).
        2. Declared `each_tenant`: once per tenant.
        3. Declared `without_tenant`: with the tenant filter off.
        4. Declared `required` with no active tenant: refused before it runs.
        Final: the use case.
        """
        context = use_context()
        tenants = resolved(context, self.memberships, self.catalog)
        declared = getattr(handler, DECLARED, None) if handler is not None else None
        if declared is None:
            return call_next(dto)
        if declared.each:
            answered = self.each(lambda: call_next(dto))
            if any(one is not None for one in answered.values()):
                raise ProgrammingError(
                    f"{getattr(handler, '__name__', handler)} runs once per tenant and answers "
                    "nothing; collect an answer per tenant with self.tenancy.each(...)"
                )
            return None
        if declared.across is not None:
            with AcrossTenants(declared.across):
                return call_next(dto)
        if declared.required and tenants.active is None:
            allowed = self.allowed
            several = allowed is not None and len(allowed) > 1
            refusal = TenantNotChosen if several else TenantRequired
            raise refusal(
                f"{getattr(handler, '__name__', handler)} runs in one tenant and this "
                "execution names none"
            )
        return call_next(dto)

    # --- wiring -------------------------------------------------------------------------
    def _attach(self, bus: "UseFramework") -> None:
        """What `on(bus)` wires, and every generation of the bus wires again."""

        def resolve_tenant(dto: Any, call_next: Callable[[Any], Any]) -> Any:
            return self._intercept(bus.handler_of(type(dto)), dto, call_next)

        bus.interceptor(sequence=TENANCY_SEQUENCE)(resolve_tenant)
        bus.add_dependency("tenancy", self)

    def on(self, bus: "UseFramework") -> "Tenancy":
        """Resolves the tenant context of every execution of `bus` and injects this object in
        its handlers as `self.tenancy`."""
        bus.extend(self._attach)
        return self

    def isolate[Store](self, store: Store) -> Store:
        """Adds the tenant filter to a store — a `Database`, a `MemoryRepository`, anything with
        `filters` — so every read of it is narrowed to the selected tenants and every write is
        stamped and checked.

            database = tenancy.isolate(Database(url))
        """
        filters: DataFilters = getattr(store, "filters")
        filters.add(self.filter)
        return store

    def discriminator(
        self,
        aggregate: type,
        field: str | None = None,
        shared: Shared | None = None,
        criteria: "Callable[[TenantContext], Criteria] | None" = None,
    ) -> None:
        """Declares how `aggregate` is partitioned when the convention does not say it — another
        field, shared when empty, or a rule of the project's own.

            tenancy.discriminator(Branch, field="company_id")
            tenancy.discriminator(Project, criteria=lambda tenants: Project.seen_by(tenants))
        """
        convention = self.filter.convention
        self.filter.discriminate(
            aggregate,
            TenantDiscriminator(
                field or convention.field,
                shared if shared is not None else convention.shared,
                criteria,
            ),
        )

    def shared(self, *aggregates: type) -> None:
        """Declares aggregates every tenant reads, though they have the field — a catalog of
        currencies, the tenants themselves."""
        for one in aggregates:
            self.filter.discriminate(one, None)

    # --- the tenant context ------------------------------------------------------------
    @property
    def tenants(self) -> TenantContext:
        """The tenant context of the execution in play."""
        return TenantContext.of(use_context())

    @property
    def active(self) -> str | None:
        """Where a write goes."""
        return self.tenants.active

    @property
    def selected(self) -> tuple[str, ...]:
        """What a read sees."""
        return self.tenants.selected

    def tenant(self) -> Tenant | None:
        """The active tenant as the catalog knows it — `None` without a catalog or an active one."""
        active = self.active
        if self.catalog is None or active is None:
            return None
        return self.catalog.get(active)

    def criteria(self, aggregate: type) -> Criteria | None:
        """The criteria tenancy adds to a read of `aggregate` — to merge into a query of the
        project's own, a statement or a report: `self.tenancy.criteria(Invoice).merged_with(dto.criteria)`.
        """
        return self.filter.criteria(aggregate)

    def without_tenant(self, reason: str) -> AcrossTenants:
        """Across tenants, by name and logged — a block (`with`) or a whole use case (`@`)."""
        return AcrossTenants(reason)

    # --- declarations -------------------------------------------------------------------
    def required(self, handler: Handler) -> Handler:
        """The use case runs only with an active tenant — `TenantRequired` (or
        `TenantNotChosen`, for a member of several) before it starts."""
        _declaration_of(handler).required = True
        return handler

    def each_tenant(self, handler: Handler) -> Handler:
        """The use case runs once per active tenant of the catalog, each in its own tenant
        context — a cron's work for every customer (spatie's `eachCurrent`). It answers nothing:
        an answer per tenant is collected with `self.tenancy.each(...)` inside a use case."""
        _declaration_of(handler).each = True
        return handler
