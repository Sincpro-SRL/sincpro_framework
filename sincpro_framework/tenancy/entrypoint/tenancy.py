"""`Tenancy` — the doors to the use cases of tenancy. Every behaviour is one use case in
`services/`; a decorator declares it for a whole use case of the bus, a `with` runs it for a
block, `self.tenancy` reaches it from a handler — the same use case behind each door.

    tenancy = Tenancy(memberships=..., catalog=...)
    tenancy.discriminator(Invoice, field="company_id")
    tenancy.on(billing)                              resolved per call; self.tenancy

| Use case | Declared on a use case | In a block, or asked |
|---|---|---|
| isolation | `@tenancy.isolated`, `@tenancy.isolated(when_missing=...)` | `with self.tenancy.isolated():`, `self.tenancy.repository(repo)`, `self.tenancy.criteria(X, c)` |
| a tenant required | `@tenancy.required` | `with self.tenancy.required():` |
| crossing tenants | `@tenancy.without_tenant(reason=...)` | `with self.tenancy.without_tenant(reason=...):` |
| switching tenant | — | `with self.tenancy.with_tenant(t):`, `with self.tenancy.with_tenants([...]):` |
| every tenant | `@tenancy.each_tenant` | `self.tenancy.each(run)` |
| who may | — | `self.tenancy.allows(t)`, `.allowed`, `.active`, `.selected`, `.tenant()` |

The nearest declaration wins and a use case that declares nothing inherits its caller's; a bus
put on with `isolated=True` isolates the use cases nobody wrapped. Nothing here is required by
the rest of the framework, and nothing of it is required here.
"""

from collections.abc import Callable, Generator, Iterable
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, overload

from sincpro_framework.context import use_context
from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.exceptions import ProgrammingError
from sincpro_framework.tenancy.domain.catalog import Tenant, TenantCatalog
from sincpro_framework.tenancy.domain.discriminator import (
    Shared,
    TenantDiscriminator,
    WhenMissing,
)
from sincpro_framework.tenancy.domain.memberships import Memberships
from sincpro_framework.tenancy.domain.tenant_context import TenantContext
from sincpro_framework.tenancy.services import crossing, fan_out, isolation, switching
from sincpro_framework.tenancy.services.discrimination import Discriminators
from sincpro_framework.tenancy.services.resolution import CrossTenant, Resolution
from sincpro_framework.tenancy.services.views import IsolatedRepository

if TYPE_CHECKING:
    from sincpro_framework.use_bus import UseFramework

TENANCY_SEQUENCE = -(10**9) + 1
"""Just inside the guard of auth: the tenant is resolved once the caller is known, and before
any interceptor of the project's reads it."""

DECLARED = "__sincpro_tenancy__"
"""The attribute a use case's declaration is held in."""


@dataclass(frozen=True)
class TenantDeclaration:
    """What a use case declared about tenants."""

    isolated: WhenMissing | None = None
    required: bool = False
    across: str | None = None
    """The reason it works across tenants, when it does."""
    each: bool = False


def _declare[H](handler: H, **declared: Any) -> H:
    """Adds to what `handler` declared — its own declaration, over what it inherits."""
    current = getattr(handler, DECLARED, None) or TenantDeclaration()
    setattr(handler, DECLARED, replace(current, **declared))
    return handler


class Door:
    """A use case of tenancy reached two ways: called on a class, it is declared for that whole
    use case; entered with `with`, it runs for the block."""

    def __init__(
        self,
        run: Callable[[], AbstractContextManager[Any]],
        refused_in_a_block: str | None = None,
        **declared: Any,
    ) -> None:
        self._run = run
        self._refused_in_a_block = refused_in_a_block
        self._declared = declared
        self._entered: list[AbstractContextManager[Any]] = []

    def __call__[H](self, handler: H) -> H:
        return _declare(handler, **self._declared)

    def __enter__(self) -> Any:
        if self._refused_in_a_block is not None:
            raise ProgrammingError(self._refused_in_a_block)
        entered = self._run()
        self._entered.append(entered)
        return entered.__enter__()

    def __exit__(self, *raised: Any) -> Any:
        return self._entered.pop().__exit__(*raised)


class Tenancy:
    """Multi-tenancy for the buses it is put on — every step optional:

    Tenancy()                                       the context names the tenant
    Tenancy(memberships=..., catalog=...)           what a caller may name, validated
    Tenancy(cross_tenant=lambda: auth.allows(P.SUPPORT))   who may work in any tenant
    """

    def __init__(
        self,
        memberships: Memberships | None = None,
        catalog: TenantCatalog | None = None,
        cross_tenant: CrossTenant | None = None,
        shared: Shared = Shared.NEVER,
    ) -> None:
        """`memberships` says which tenants a subject may name, `catalog` which tenants exist
        and are served, `cross_tenant` who may work in any of them — auth's to answer; `shared`
        whether an empty tenant is everyone's, for what is declared without saying."""
        self.resolution = Resolution(memberships, catalog, cross_tenant)
        self.discriminators = Discriminators(shared)

    @property
    def memberships(self) -> Memberships | None:
        """The memberships a caller is validated against."""
        return self.resolution.memberships

    @property
    def catalog(self) -> TenantCatalog | None:
        """The tenants this service knows."""
        return self.resolution.catalog

    # --- configuration ------------------------------------------------------------------
    def discriminator(
        self,
        aggregate: type,
        field: str = "tenant_id",
        shared: Shared | None = None,
        criteria: "Callable[[TenantContext], Criteria] | None" = None,
    ) -> None:
        """Declares that `aggregate` — and what inherits it — is partitioned by `field`; an
        empty one everyone's when `shared` says so; `criteria` a rule of the project's own.

            tenancy.discriminator(Invoice)                                  by tenant_id
            tenancy.discriminator(Branch, field="company_id")
            tenancy.discriminator(Project, criteria=lambda tenants: Project.seen_by(tenants))
        """
        self.discriminators.declare(
            aggregate,
            TenantDiscriminator(
                field, shared if shared is not None else self.discriminators.shared, criteria
            ),
        )

    def shared(self, *aggregates: type) -> None:
        """Declares aggregates every tenant reads — a catalog of currencies — though they, or a
        class they inherit, are partitioned."""
        for one in aggregates:
            self.discriminators.declare(one, None)

    # --- the tenant context -------------------------------------------------------------
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

    @property
    def allowed(self) -> frozenset[str] | None:
        """What the caller may select — `None` when nothing limits it."""
        return self.resolution.allowed(use_context())

    def allows(self, tenant: str) -> bool:
        """Whether the caller may work in `tenant` — without raising, as `auth.allows`."""
        return self.resolution.allows(use_context(), tenant)

    def tenant(self) -> Tenant | None:
        """The active tenant as the catalog knows it."""
        active = self.active
        if self.catalog is None or active is None:
            return None
        return self.catalog.get(active)

    # --- isolation ----------------------------------------------------------------------
    def _isolating(self, when_missing: WhenMissing) -> AbstractContextManager[TenantContext]:
        """The isolation use case, with what it does when no tenant is named."""
        return isolation.isolated(self.discriminators, when_missing)

    @overload
    def isolated[H: type](self, handler: H) -> H: ...

    @overload
    def isolated(
        self, handler: None = None, when_missing: WhenMissing = WhenMissing.RAISE
    ) -> Door: ...

    def isolated(
        self, handler: Any = None, when_missing: WhenMissing = WhenMissing.RAISE
    ) -> Any:
        """Isolation: every read narrowed to the selected tenants, every new record stamped with
        the active one, every write outside answered as not found.

            @tenancy.isolated                                   the whole use case
            @tenancy.isolated(when_missing=WhenMissing.SKIP)    no tenant: it does not run
            with self.tenancy.isolated():                       a block
        """
        refused = (
            "WhenMissing.SKIP skips a whole use case (@tenancy.isolated(when_missing=SKIP)); "
            "a block asks `if self.tenancy.active is None: ...` before it"
            if when_missing is WhenMissing.SKIP
            else None
        )
        door = Door(lambda: self._isolating(when_missing), refused, isolated=when_missing)
        return door if handler is None else door(handler)

    def criteria(self, aggregate: type, criteria: Criteria | None = None) -> Criteria:
        """`criteria` with the tenant's AND-ed — the isolation written out, for a read of the
        project's own: `self.repository.measures(Invoice, self.tenancy.criteria(Invoice, c))`.
        An aggregate not partitioned answers `criteria` as given."""
        given = criteria or Criteria()
        scope = self.discriminators.criteria(aggregate, self.tenants, WhenMissing.RAISE)
        return given if scope is None else scope.merged_with(given)

    def repository[R](
        self, repository: R, when_missing: WhenMissing = WhenMissing.RAISE
    ) -> R:
        """A view of `repository` whose every call runs isolated; the rest of the use case does
        not. Typed as the repository it wraps."""
        view = IsolatedRepository(repository, lambda: self._isolating(when_missing))
        return view  # type: ignore[return-value]

    # --- a tenant required, crossing, switching, every tenant ---------------------------
    @contextmanager
    def _requiring(self) -> Generator[TenantContext]:
        """The use case of a tenant required, for a block."""
        yield self.resolution.required(use_context(), "this block")

    @overload
    def required[H: type](self, handler: H) -> H: ...

    @overload
    def required(self, handler: None = None) -> Door: ...

    def required(self, handler: Any = None) -> Any:
        """The use case, or the block, runs only with an active tenant — `TenantRequired` (or
        `TenantNotChosen` for a member of several) before it starts; nothing is filtered."""
        door = Door(self._requiring, required=True)
        return door if handler is None else door(handler)

    def without_tenant(self, reason: str) -> Door:
        """Across tenants, by name and logged — a use case (`@`) or a block (`with`). Whether the
        caller may is auth's to decide."""
        return Door(lambda: crossing.across_tenants(reason), across=reason)

    def with_tenants(
        self, tenants: Iterable[str], active: str | None = None
    ) -> AbstractContextManager[TenantContext]:
        """A block that works in these tenants — reading all of them, writing to `active` —
        validated as an entrance is."""
        return switching.working_in(self.resolution, tenants, active)

    def with_tenant(self, tenant: str) -> AbstractContextManager[TenantContext]:
        """A block that works in one tenant — ABP's `CurrentTenant.Change`."""
        return switching.working_in(self.resolution, [tenant], tenant)

    def each(
        self, run: Callable[[], Any], tenants: Iterable[str] | None = None
    ) -> dict[str, Any]:
        """`run` once per tenant — every active one of the catalog when none are named."""
        return fan_out.each(self.resolution, run, tenants)

    def each_tenant[H: type](self, handler: H) -> H:
        """The use case runs once per active tenant of the catalog, each in its own tenant
        context — a cron's work for every customer. It answers nothing: an answer per tenant is
        `self.tenancy.each(run)`."""
        return _declare(handler, each=True)

    # --- what the bus runs --------------------------------------------------------------
    def _run(
        self,
        declared: TenantDeclaration,
        name: str,
        dto: Any,
        call_next: Callable[[Any], Any],
    ) -> Any:
        """One execution, as its use case declared.

        1. Every tenant: once per tenant, each run as the rest declares.
        2. Across tenants: the tenant filter off, logged.
        3. A tenant required: refused before it runs when none is active.
        4. Isolated: skipped when it skips and no tenant is named; else isolated.
        Final: the use case.
        """
        if declared.each:
            once = replace(declared, each=False)
            answered = self.each(lambda: self._run(once, name, dto, call_next))
            if any(one is not None for one in answered.values()):
                raise ProgrammingError(
                    f"{name} runs once per tenant and answers nothing; collect an answer per "
                    "tenant with self.tenancy.each(...)"
                )
            return None
        if declared.across is not None:
            with crossing.across_tenants(declared.across):
                return call_next(dto)
        if declared.required:
            self.resolution.required(use_context(), name)
        if declared.isolated is not None:
            if isolation.skips(declared.isolated):
                return None
            with self._isolating(declared.isolated):
                return call_next(dto)
        return call_next(dto)

    def _attach(self, bus: "UseFramework", isolated_by_default: bool) -> None:
        """What `on(bus)` wires, and every generation of the bus wires again: the interceptor
        that resolves the tenant and runs what the use case declared, and `self.tenancy`."""

        def resolve_tenant(dto: Any, call_next: Callable[[Any], Any]) -> Any:
            handler = bus.handler_of(type(dto))
            declared = getattr(handler, DECLARED, None)
            if declared is None and isolated_by_default and not isolation.decided():
                declared = TenantDeclaration(isolated=WhenMissing.RAISE)
            self.resolution.resolved(use_context())
            if declared is None:
                return call_next(dto)
            name = getattr(handler, "__name__", "the use case")
            return self._run(declared, name, dto, call_next)

        bus.interceptor(sequence=TENANCY_SEQUENCE)(resolve_tenant)
        bus.add_dependency("tenancy", self)

    def on(self, bus: "UseFramework", isolated: bool = False) -> "Tenancy":
        """Resolves and validates the tenant context of every execution of `bus`, runs what its
        use cases declare, and injects this object in its handlers as `self.tenancy`.
        `isolated=True` isolates every use case nobody wrapped — `without_tenant` to step out.
        """
        bus.extend(lambda one: self._attach(one, isolated))
        return self
