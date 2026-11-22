"""`AccessControl`: who is calling, and what they may do — declared on the use cases and hooks,
decided by the providers, applied by the guard.

    auth = AccessControl[BillingPermission](providers=[OdooProvider(...)])
    auth.on(billing)                   # every use case of the bus; `self.auth` in its handlers
    auth.on(billing_hooks)             # every hook of the collection

    @billing.feature(CommandIssueInvoice)
    @auth.requires(BillingPermission.ISSUE_INVOICE)
    class IssueInvoice(Feature):
        auth: AccessControl[BillingPermission]

        def execute(self, dto):
            if self.auth.allows(BillingPermission.READ_CREDIT):      # an optional step
                ...

Context: open unless it says otherwise — a use case or hook that declares nothing runs for
anyone; `authenticated` and `requires` restrict it, and `@entrypoints.internal` keeps a use case
off every entrypoint. `enabled=False` checks nothing at all. Who acts is
`security.identity`; an entrypoint that verified the caller with `authenticate(...)` opens
`security.as_identity(...)`. The provider that authenticated an identity is the one asked about it; an
identity opened by hand — a test, in-process code — answers with its `permissions`.
"""

import functools
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary, WeakSet

from sincpro_framework.auth.adapters.added_requirements import KeyValueAddedRequirements
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.added_requirements import AddedRequirementStore
from sincpro_framework.auth.domain.administration import AdministrationPolicy
from sincpro_framework.auth.domain.events import EventPublisher
from sincpro_framework.auth.domain.identity import Credentials, Identity
from sincpro_framework.auth.domain.permissions import (
    OPEN,
    AccessCall,
    AccessDeclaration,
    AccessDescription,
    AnyOf,
    AssuranceRequirement,
    ByField,
    DeclarationKind,
    Permission,
    Requirement,
    RoleRequirement,
    Rule,
    WhenDenied,
)
from sincpro_framework.auth.domain.provider import AuthProvider
from sincpro_framework.auth.entrypoint.runtime_requirements import RuntimeRequirements
from sincpro_framework.auth.infrastructure.security_context import current_identity
from sincpro_framework.auth.services import authentication, description
from sincpro_framework.auth.services.decisions import Decisions
from sincpro_framework.auth.services.guard import (
    GUARD_SEQUENCE,
    BusGuard,
    Declarations,
    HookGuard,
    guarded_buses,
)
from sincpro_framework.ddd.repositories.hooks import Hooks
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.ddd.criteria import Criteria
    from sincpro_framework.use_bus import UseFramework


RUNTIME_CHECK = "requirements added at runtime"
"""The name `describe()` and a refusal give the check of `runtime`."""


class AccessControl[P: Permission]:
    """The one object a bounded context guards itself with — declare, authenticate, decide.

    Context: `P` is the context's own `Permission` enum, so `requires`, `allows` and `check`
    accept only its members and a typo is a type error. The object is the decorator source
    (`requires`, `authenticated`), the entrypoints' authenticator (`authenticate`,
    `challenges`, `credentials_for`), the handlers' `self.auth` (`allows`, `check`,
    `permitted`, `scope_of`) and the auditor (`verify`, `describe`).

        auth = AccessControl[BillingPermission](providers=[jwt, api_keys])
        auth.on(billing)
        auth.verify()     ->  []    # everything declared runs on a bus it guards

    Context: the class itself knows which object guards each bus (`of`, `guarding`), so an
    entrypoint finds the one of the bus it dispatches to with no argument to pass.
    """

    def __init__(
        self,
        providers: AuthProvider | Sequence[AuthProvider] = (),
        enabled: bool = True,
        clock: Callable[[], float] = time.time,
        publisher: EventPublisher | None = None,
        runtime: bool | AddedRequirementStore = False,
        administration: AdministrationPolicy | None = None,
    ) -> None:
        """`providers` are tried in order to authenticate a call; `enabled=False` checks
        nothing — a local environment, a test that is not about access. `publisher` is told
        every refused call (`AccessRefused`) and every requirement added or released at
        runtime.

        `runtime=True` lets administrators require more of a use case without a deploy —
        `auth.runtime` — kept on the context's store, or on the `AddedRequirementStore` given;
        `administration` bounds who may (`BoundedAdministration()` when not given).

            AccessControl(providers=jwt).providers            ->  (jwt,)
            AccessControl(providers=[jwt, api_keys]).providers  ->  (jwt, api_keys)
            AccessControl(enabled=False)    ->  guards nothing; `on` warns it is disabled

        It starts with no declaration, no bus and no hooks guarded — `on` adds them.
        """
        self.providers: tuple[AuthProvider, ...] = (
            (providers,) if isinstance(providers, AuthProvider) else tuple(providers)
        )
        self.enabled = enabled
        self.clock = clock
        self.publisher = publisher
        self.declarations = Declarations()
        self.checks: list[Rule] = []
        """What `every_call` registered — asked on every call of every bus guarded."""
        self._decisions = Decisions(self)
        self._runtime: RuntimeRequirements | None = None
        if runtime:
            self._runtime = RuntimeRequirements(
                self,
                KeyValueAddedRequirements() if runtime is True else runtime,
                policy=administration,
                publisher=publisher,
            )
            self.every_call(self._runtime.check, name=RUNTIME_CHECK)
        self._buses: WeakSet["UseFramework"] = WeakSet()
        """Every bus guarded — those given to `on` and the generations made of them."""
        self._guards: "WeakKeyDictionary[UseFramework, BusGuard]" = WeakKeyDictionary()
        """The guard this object put on each bus — what `on(bus, replaces=this)` replaces."""
        self._roots: list["UseFramework"] = []
        self._hooks: list[Hooks] = []
        self._hook_guard = HookGuard(self)
        self._noted: set[str] = set()
        """The provider names a clash was already warned about."""

    @classmethod
    def of(cls, bus: "UseFramework") -> "AccessControl[Any] | None":
        """The `AccessControl` guarding `bus`, or `None` when nothing does — an entrypoint then
        leaves the identity as it found it."""
        found = guarded_buses.get(bus)
        return found if isinstance(found, AccessControl) else None

    @classmethod
    def guarding(cls, context: str) -> "list[AccessControl[Any]]":
        """Every `AccessControl` of this process, those guarding a bus of `context` first —
        what a call to the service hosting `context` asks for the credentials it sends.

            # auth_b guards the "billing" bus, auth_s the "sales" bus and its generations
            AccessControl.guarding("sales")     ->  [auth_s, auth_b]
            AccessControl.guarding("payroll")   ->  [auth_b, auth_s]    # as registered

        Context: an `AccessControl` guarding several buses — the generations `fresh()` makes —
        is listed once, where it first appears.
        """
        found: list[AccessControl[Any]] = []
        for _, access in sorted(
            guarded_buses.items(), key=lambda item: item[0].name != context
        ):
            if isinstance(access, AccessControl) and access not in found:
                found.append(access)
        return found

    @property
    def needs_body(self) -> bool:
        """Whether a provider reads the request's raw body — a webhook signature — so the
        entrypoints hand it over; no wire reads it otherwise."""
        return any(one.needs_body for one in self.providers)

    @classmethod
    def any_needs_body(cls) -> bool:
        """Whether any `AccessControl` of this process reads bodies — what an entrypoint that
        does not know its bus yet asks before it reads one."""
        return any(access.needs_body for access in list(guarded_buses.values()))

    @property
    def identity(self) -> Identity:
        """Who the execution in progress acts as — never `None`."""
        return current_identity()

    # Declaring

    def requires(
        self,
        *requirements: P | Requirement,
        when_denied: WhenDenied = WhenDenied.RAISE,
    ):
        """Every requirement listed, and every other `requires` stacked on the same class, is
        needed — `any_of` for one among several, `role` for a group, `rule` for a check of the
        project's own. On a Feature, an ApplicationService or a Hook.

            @auth.requires(BillingPermission.ISSUE_INVOICE)   ->  "billing.invoice.issue"
            @auth.requires(auth.role("billing.manager"), auth.rule("own", is_own))
                ->  "role billing.manager and own"
            @auth.requires(READ, when_denied=WhenDenied.SKIP)
                ->  "billing.read (else skipped)"
            auth.requires()   ->  raises ProgrammingError("requires() names at least one ...")

        The class is returned as it was, its declaration stored in `declarations`. A requirement
        that reads `open` or `authenticated` is declared all the same, with a warning: the
        descriptions (`describe()`, the manifest, OpenAPI) would read it as declaring nothing,
        or anybody authenticated.
        """
        if not requirements:
            raise exceptions.ProgrammingError("requires() names at least one requirement")
        said = " and ".join(str(one) for one in requirements)
        if said in (OPEN, DeclarationKind.AUTHENTICATED):
            logger.warning(
                "a requirement reads as what a use case that declares it is described: rename "
                "the permission or the rule",
                requirement=said,
            )

        def declared[T: type](cls: T) -> T:
            return self.declarations.add(
                cls,
                AccessDeclaration(DeclarationKind.REQUIRES, tuple(requirements), when_denied),
            )

        return declared

    def by_field(self, field: str, permissions: Mapping[Any, P | Requirement]) -> ByField:
        """A requirement the Command's own `field` chooses — for `requires`: a value it does not
        name is refused; asked with no Command (`may_run`, a catalog), it is one among them.
        A value may name any requirement: a permission, a role, a rule, an `any_of`.

            @auth.requires(auth.by_field("direction", {
                Direction.INBOUND: FinancePermission.RECEIVE,
                Direction.OUTBOUND: auth.any_of(FinancePermission.PAY, own_payment),
            }))
        """
        if not permissions:
            raise exceptions.ProgrammingError("by_field() names at least one requirement")
        return ByField(field, tuple(permissions.items()))

    def assurance(
        self,
        acr: Iterable[str] = (),
        methods: Iterable[str] = (),
        max_age: timedelta | None = None,
    ) -> AssuranceRequirement:
        """A requirement on how the caller authenticated — for `requires`: a step-up (RFC 9470).
        Refused with `Unauthenticated(step_up=…, max_age=…)`, never 403: the caller can meet it
        by authenticating again.

            @auth.requires(BillingPermission.APPROVE_PAYMENT,
                           auth.assurance(methods={"mfa"}, max_age=timedelta(minutes=5)))
        """
        return AssuranceRequirement(tuple(acr), frozenset(methods), max_age)

    def any_of(self, *requirements: P | Requirement) -> AnyOf:
        """A requirement met by one of `requirements` — permissions, roles, rules — for
        `requires`, `allows` and `check`.

            @auth.requires(auth.any_of(BillingPermission.ISSUE, BillingPermission.ADMIN))
            @auth.requires(auth.any_of(auth.rule("its owner", is_owner), auth.role("admin")))
        """
        if not requirements:
            raise exceptions.ProgrammingError("any_of() names at least one requirement")
        return AnyOf(tuple(requirements))

    def authenticated[T: type](self, cls: T) -> T:
        """Anyone authenticated, whatever they may do."""
        return self.declarations.add(cls, AccessDeclaration(DeclarationKind.AUTHENTICATED))

    def role(self, *roles: str) -> RoleRequirement:
        """A requirement met by holding one of `roles` — Odoo's `groups=`, for `requires`,
        `allows` and `check`. The roles are the identity's, their closure when a `GrantResolver`
        resolved them: a manager holds the clerk role its level implies.

            @auth.requires(auth.role("billing.manager", "billing.admin"))
        """
        if not roles:
            raise exceptions.ProgrammingError("role() names at least one role")
        return RoleRequirement(tuple(roles))

    def rule(self, name: str, check: Callable[[AccessCall], bool]) -> Rule:
        """A requirement of the project's own — `check` is asked with the call (`AccessCall`:
        the identity, the Command, the resource, the execution's context) and holds when it
        answers true; refused, `PermissionDenied` names it. `check` may raise an `AuthError` of
        its own to answer otherwise.

            is_own = auth.rule("its own invoice",
                               lambda call: call.dto.owner == call.identity.subject)
            @auth.requires(BillingPermission.READ, is_own)

        A rule named `open` is made all the same, with a warning: `open` is what declaring
        nothing is called, and the descriptions would read the rule as no requirement.
        """
        if name == OPEN:
            logger.warning(
                "a rule named 'open' reads as a use case that declares nothing: rename it"
            )
        return Rule(name, check)

    @property
    def runtime(self) -> RuntimeRequirements:
        """What administrators require of a use case at runtime, beside its code's declaration
        — `require`, `release`, `required` — asked on every call as the `every_call` check
        `requirements added at runtime`. Only with `AccessControl(runtime=True)`.

            auth.runtime.require(CommandRefund, BillingPermission.APPROVE, tenant="bo")
        """
        if self._runtime is None:
            raise exceptions.ProgrammingError(
                "requirements are added at runtime with AccessControl(runtime=True)"
            )
        return self._runtime

    def every_call[F: Callable[[AccessCall], bool]](
        self, check: F | None = None, name: str | None = None
    ) -> Any:
        """Ask `check` on every call of every bus this object guards — declared or open, before
        it runs: an interceptor of the project's own, beside the declarations. Refused, the call
        is refused (`PermissionDenied`, or the `AuthError` the check raises); the system passes.
        A decorator, bare or named:

            @auth.every_call
            def tenant_active(call: AccessCall) -> bool:
                return call.context.get("tenant_id") not in suspended

            auth.every_call(lambda call: not maintenance(), name="not in maintenance")

        The check is answered as it was given, so it stays callable — and testable — alone.
        """

        def registered(given: F) -> F:
            self.checks.append(Rule(name or getattr(given, "__name__", repr(given)), given))
            return given

        return registered if check is None else registered(check)

    # Authenticating

    def authenticate(self, credentials: Credentials) -> Identity:
        """Who the credentials say is calling — the first provider that recognizes them, or
        `Identity.anonymous()` when none does; `Unauthenticated` when one recognizes them and
        they fail. The identity names the provider that answered: the one asked about it."""
        return authentication.authenticated(self.providers, credentials)

    def challenges(self, refusal: exceptions.Unauthenticated | None = None) -> list[str]:
        """The `WWW-Authenticate` values a 401 answers with, one per provider that has one —
        for `refusal` when the wire knows it, so a step-up names what to come back with."""
        return authentication.challenges(self.providers, refusal)

    def credentials_for(
        self, identity: Identity | None = None, audience: str | None = None
    ) -> Credentials | None:
        """What to send another service — `audience`, when named — so it knows who this call
        acts for, the identity in play when none is given: asked of its provider first.

            auth.credentials_for()
                ->  Credentials(transport="service", headers={"x-sp-service-token": "eyJ…"})
            auth.credentials_for(Identity.anonymous())   ->  None
        """
        return authentication.credentials_for(
            self.providers, identity or current_identity(), audience
        )

    # Deciding

    def refusal(
        self,
        identity: Identity,
        declaration: AccessDeclaration,
        what: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
        dto: Any = None,
    ) -> exceptions.AuthError | None:
        """Why `identity` may not run what `declaration` guards — `None` when it may.

        refusal(ANONYMOUS, AccessDeclaration(DeclarationKind.AUTHENTICATED), "IssueInvoice")
            ->  Unauthenticated("authenticate to run IssueInvoice")
        """
        return self._decisions.refusal(identity, declaration, what, resource, context, dto)

    def allows(self, requirement: P | Requirement, resource: Any = None) -> bool:
        """Whether the identity in play meets `requirement` — a permission, a role, a rule,
        `any_of`… — on `resource`, when given. What a handler asks before an optional step.
        Nobody meets none: a rule is never asked for an anonymous caller, as in `requires`.

            self.auth.allows(BillingPermission.READ_CREDIT)
            self.auth.allows(self.auth.role("billing.manager"))
        """
        return self._decisions.in_play(requirement, resource) is None

    def has_role(self, *roles: str) -> bool:
        """Whether the identity in play holds one of `roles` — `allows(role(...))`, said short."""
        return self.allows(self.role(*roles))

    def check(self, requirement: P | Requirement, resource: Any = None) -> None:
        """`allows`, raising `PermissionDenied` — or `Unauthenticated` for nobody — when not.

        self.auth.check(BillingPermission.ISSUE_INVOICE)    # holds it  ->  None
        # lacks it   ->  raises PermissionDenied("user:42", "billing.invoice.issue")
        # nobody     ->  raises Unauthenticated("authenticate to billing.invoice.issue")
        """
        denied = self._decisions.in_play(requirement, resource)
        if denied is not None:
            raise denied

    def every_call_refusal(
        self,
        identity: Identity,
        what: str,
        dto: Any,
        context: Mapping[str, Any],
        command: str = "",
    ) -> exceptions.AuthError | None:
        """Why one of the `every_call` checks refuses this call — `None` when every one lets it
        through, or the system acts. What the guard asks after the declaration."""
        return self._decisions.every_call_refusal(identity, what, dto, context, command)

    def refused(self, identity: Identity, what: str, refusal: exceptions.AuthError) -> None:
        """Tell the publisher a call was refused — what the guard does before it raises."""
        self._decisions.refused(identity, what, refusal)

    def permitted(self, permission: P, resources: Sequence[Any]) -> list[bool]:
        """`allows` for many resources — in one call to the provider when it answers batches.

        self.auth.permitted(BillingPermission.READ, [invoice_1, invoice_2])
            ->  [True, False]      # as the provider answered each
        """
        return self._decisions.permitted(permission, resources)

    def scope_of(self, aggregate: type) -> "Criteria | None":
        """Which records of `aggregate` the identity in play may read, as its provider filters
        them — `search(Aggregate, criteria)` or a SQL `narrowed(...)` takes it; `None` for every
        record (the system, an identity opened by hand); `Unauthenticated` for nobody.

            self.auth.scope_of(Invoice)   ->  Criteria.where("tenant", "=", "bo")
        """
        return self._decisions.scope_of(aggregate)

    # Wiring

    def _identity_for_logs(self) -> dict[str, str]:
        """The `identity` field every log line of a guarded bus carries — none for nobody."""
        identity = current_identity()
        return {} if identity.is_anonymous else {"identity": identity.subject}

    def attach(self, bus: "UseFramework") -> None:
        """What `on(bus)` wires, and what every generation `fresh()` makes of the bus wires
        again against itself: the guard, and this object as the handlers' `auth` — and, once
        per name, a warning when another `AccessControl` of the process gives a provider of
        another kind the name of one of these."""
        for name in sorted(authentication.clashing_names(self.providers) - self._noted):
            self._noted.add(name)
            logger.warning(
                "two AccessControls of the process hold providers of different kinds under one "
                "name: an identity vouched for by one may be asked of the other — name them "
                "apart",
                provider=name,
            )
        guard = BusGuard(self, bus)
        bus.interceptor(sequence=GUARD_SEQUENCE)(guard)
        bus.add_dependency("auth", self)
        self._guards[bus] = guard
        self._buses.add(bus)
        guarded_buses[bus] = self

    def _replacing(self, replaced: "AccessControl[Any]", bus: "UseFramework") -> None:
        """What `on(bus, replaces=replaced)` wires, and every generation `fresh()` makes of the
        bus wires again: this object's guard in the place of the one guarding the bus, and this
        object as the handlers' `auth`.

        1. `replaced` guards the bus: its guard and its `auth` are replaced.
        2. Another `AccessControl` guards it: that one is replaced, with a warning.
        3. Nothing guards it: guarded as `on(bus)` guards, with a warning.
        Final: the one replaced guards the bus no more.
        """
        guarding = replaced if bus in replaced._guards else guarded_buses.get(bus)
        if not isinstance(guarding, AccessControl) or bus not in guarding._guards:
            logger.warning(
                "replaces= names an AccessControl and nothing guards the bus: guarded as on() "
                "guards it",
                bus=bus.name,
            )
            self.attach(bus)
            return
        if guarding is not replaced:
            logger.warning(
                "replaces= names an AccessControl that does not guard the bus: the one "
                "guarding it is replaced",
                bus=bus.name,
            )
        guard = BusGuard(self, bus)
        bus.interceptor(sequence=GUARD_SEQUENCE, replaces=guarding._guards[bus])(guard)
        bus.add_dependency("auth", self, replaces=guarding)
        self._guards[bus] = guard
        self._buses.add(bus)
        guarding._buses.discard(bus)
        guarded_buses[bus] = self

    def refusal_of(
        self,
        bus: "UseFramework",
        command: type,
        identity: Identity | None = None,
        dto: Any = None,
    ) -> exceptions.AuthError | None:
        """Why `identity` — the one in play when none is given — may not run `command` on
        `bus`, without running it: the very decision the bus's guard takes. `None` when it may.

            auth.refusal_of(billing, CommandIssueInvoice)
                ->  PermissionDenied("user:42", "billing.invoice.issue")   or None

        Context: a bus another `AccessControl` guards is answered by that one, and a bus nobody
        guards runs anything — `None`.
        """
        guard = self._guards.get(bus)
        if guard is not None:
            return guard.refusal_for(command, identity or current_identity(), dto)
        guarding = AccessControl.of(bus)
        return None if guarding is None else guarding.refusal_of(bus, command, identity, dto)

    def may_run(
        self,
        bus: "UseFramework",
        command: type,
        identity: Identity | None = None,
        dto: Any = None,
    ) -> bool:
        """Whether `identity` — the one in play when none is given — may run `command` on
        `bus`: what a catalog asks to list only what its caller may call, with the same
        declaration that guards the run.

            [tool for tool in tools if auth.may_run(sales, tool.command)]
        """
        return self.refusal_of(bus, command, identity, dto) is None

    def using(self, providers: AuthProvider | Sequence[AuthProvider]) -> "AccessControl[P]":
        """This object with other providers — the same declarations, `enabled` and publisher, a
        copy of the `every_call` checks (one added to either is the other's no more), the
        requirements added at runtime judged by the copy:
        what a test puts on a `fresh()` bus, so the rules of production are checked against
        providers of its own.

            test = billing.fresh()
            auth.using(providers=[StaticProvider(...)]).on(test, replaces=auth)
        """
        other: AccessControl[P] = AccessControl(
            providers, enabled=self.enabled, clock=self.clock, publisher=self.publisher
        )
        other.declarations = self.declarations
        other.checks = list(self.checks)
        if self._runtime is not None:
            other._runtime = self._runtime.judged_by(other)
            other.checks = [
                Rule(one.name, other._runtime.check) if one.name == RUNTIME_CHECK else one
                for one in other.checks
            ]
        return other

    def on(
        self, target: "UseFramework | Hooks", replaces: "AccessControl[Any] | None" = None
    ) -> None:
        """Guard every use case of a bus — the bus not built yet — and inject this object in its
        handlers as `auth`; or every hook of a collection, and of every copy made of it.

            auth.on(billing)          # billing and each generation `fresh()` makes of it
            auth.on(billing_hooks)    # every hook of the collection is gated
            auth.on(billing)   # again  ->  a warning; guarded once
            auth.using(providers=[fake]).on(test, replaces=auth)   # a fresh() bus, in a test

        `replaces` names the `AccessControl` guarding the bus now — what `fresh()` repeats from
        the bus it was made of — and this one takes its place there: its guard and its `auth`.

        1. Disabled: a warning is logged — the target is wired anyway, and checks nothing.
        2. A `Hooks` collection:
           2.1 one already guarded by this object: a warning, guarded once;
           2.2 else the hook guard is set as its gate and the collection stored.
        3. A bus already guarded by this object: a warning, guarded once.
        4. `attach` is registered as an extension of the bus — run on it and on every
           generation made of it, so the guard and `auth` reach each.
        5. Each log line of the bus gets the `identity` of who acts.
        Final: the bus is stored among the roots this object guards.
        """
        if not self.enabled:
            logger.warning("access control is disabled: nothing it guards is checked")
        if replaces is not None:
            if isinstance(target, Hooks):
                raise exceptions.ProgrammingError(
                    "replaces= is for a bus: a Hooks collection is gated by one AccessControl"
                )
            target.extend(functools.partial(self._replacing, replaces))
            self._roots.append(target)
            return
        if isinstance(target, Hooks):
            if any(target is one for one in self._hooks):
                logger.warning("AccessControl already guards this Hooks: guarded once")
                return
            target.gate(self._hook_guard)
            self._hooks.append(target)
            return
        if target in self._roots:
            logger.warning(
                "AccessControl already guards the bus: guarded once", bus=target.name
            )
            return
        target.extend(self.attach)
        target.logger.add_context_source(self._identity_for_logs)
        self._roots.append(target)

    # Describing — read without building a bus nor closing a collection

    def guarded_use_cases(self) -> set[str]:
        """The dotted name of every Command of every bus this object guards — what a screen
        offers to require more of."""
        return set(description.declared_use_cases(self.declarations, list(self._buses)))

    def requirements_of(
        self, cls: type, bus: "UseFramework | None" = None
    ) -> AccessDeclaration | None:
        """What a Command's use case, a handler or a hook declared — `None` for nothing; with
        `bus`, the handler that bus answers it with.

            auth.requirements_of(CommandIssueInvoice)  ->  AccessDeclaration(DeclarationKind.REQUIRES, (ISSUE,))
            auth.requirements_of(QueryHealth)          ->  None    # declared nothing
        """
        return description.requirements_of(self.declarations, list(self._buses), cls, bus)

    def verify(self) -> list[str]:
        """What would not work as declared — for a test, or a check once everything is
        registered. It builds no bus and closes no collection, so calling it early changes
        nothing; it only sees less.

            auth.verify()   ->  []
            AccessControl(enabled=False).verify()   # once on a bus
                ->  ["access control is disabled",
                     "billing.Orphan declares access but runs on no bus or hooks this ..."]
        """
        return description.problems(
            self.declarations, list(self._buses), self._hooks, self.enabled
        )

    def describe(self) -> AccessDescription:
        """Everything this object guards and how — read without building a bus.

        auth.describe()
            ->  AccessDescription(enabled=True, providers=["odoo"],
                    use_cases={"billing.CommandIssueInvoice": "billing.invoice.issue",
                               "billing.QueryHealth": "open"},
                    hooks={"billing.hooks.NotifyCustomer": "authenticated"})
        """
        return description.description(
            self.declarations,
            list(self._buses),
            self._hooks,
            self.enabled,
            self.providers,
            self.checks,
        )
