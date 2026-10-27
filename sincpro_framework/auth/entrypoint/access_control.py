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

Context: a use case or hook that declares nothing is not checked — `strict=True` refuses the
use cases among them entered from outside, `enabled=False` checks nothing at all. Who acts is
`security.identity`; an entrypoint that verified the caller with `authenticate(...)` opens
`security.as_identity(...)`. The provider that authenticated an identity is the one asked about it; an
identity opened by hand — a test, in-process code — answers with its `permissions`.
"""

import functools
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar
from weakref import WeakKeyDictionary, WeakSet

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.identity import Credentials, Identity, IdentityKind
from sincpro_framework.auth.domain.permissions import (
    AccessDeclaration,
    AnyOf,
    ByField,
    Permission,
    Requirement,
    WhenDenied,
)
from sincpro_framework.auth.domain.provider import AuthProvider
from sincpro_framework.auth.infrastructure.security_context import current_identity
from sincpro_framework.auth.services.guard import (
    GUARD_SEQUENCE,
    BusGuard,
    Declarations,
    HookGuard,
)
from sincpro_framework.context import context_scopes
from sincpro_framework.ddd.repositories.hooks import Hooks
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.ddd.criteria import Criteria
    from sincpro_framework.use_bus import UseFramework


class AccessDescription(DataTransferObject):
    """What `describe()` answers — for an admin screen, an audit, an agent writing code."""

    enabled: bool
    strict: bool
    providers: list[str]
    use_cases: dict[str, str]
    """Each use case of the guarded buses, by its DTO's name: what it declared, or `unchecked`."""
    hooks: dict[str, str]
    """Each hook of the guarded collections that declared something, by its name."""


class AccessControl[P: Permission]:
    """The one object a bounded context guards itself with — declare, authenticate, decide.

    Context: `P` is the context's own `Permission` enum, so `requires`, `allows` and `check`
    accept only its members and a typo is a type error. The object is the decorator source
    (`requires`, `authenticated`, `public`), the entrypoints' authenticator (`authenticate`,
    `challenges`, `credentials_for`), the handlers' `self.auth` (`allows`, `check`,
    `permitted`, `scope_of`) and the auditor (`verify`, `describe`).

        auth = AccessControl[BillingPermission](providers=[jwt, api_keys], strict=True)
        auth.on(billing)
        auth.verify()     ->  []    # everything declared runs, nothing undeclared

    Context: the class itself knows which object guards each bus (`of`, `guarding`), so an
    entrypoint finds the one of the bus it dispatches to with no argument to pass.
    """

    _guarding: ClassVar["WeakKeyDictionary[UseFramework, AccessControl[Any]]"] = (
        WeakKeyDictionary()
    )
    """Which `AccessControl` guards each bus — and each generation made of it."""

    def __init__(
        self,
        providers: AuthProvider | Sequence[AuthProvider] = (),
        strict: bool = False,
        enabled: bool = True,
    ) -> None:
        """`providers` are tried in order to authenticate a call; `strict` refuses a use case
        entered from outside that declares nothing; `enabled=False` checks nothing — a local
        environment, a test that is not about access.

            AccessControl(providers=jwt).providers            ->  (jwt,)
            AccessControl(providers=[jwt, api_keys]).providers  ->  (jwt, api_keys)
            AccessControl(enabled=False)    ->  guards nothing; `on` warns it is disabled

        It starts with no declaration, no bus and no hooks guarded — `on` adds them.
        """
        self.providers: tuple[AuthProvider, ...] = (
            (providers,) if isinstance(providers, AuthProvider) else tuple(providers)
        )
        self.strict = strict
        self.enabled = enabled
        self.declarations = Declarations()
        self._buses: WeakSet["UseFramework"] = WeakSet()
        """Every bus guarded — those given to `on` and the generations made of them."""
        self._guards: "WeakKeyDictionary[UseFramework, BusGuard]" = WeakKeyDictionary()
        """The guard this object put on each bus — what `on(bus, replaces=this)` replaces."""
        self._roots: list["UseFramework"] = []
        self._hooks: list[Hooks] = []
        self._hook_guard = HookGuard(self)

    @classmethod
    def of(cls, bus: "UseFramework") -> "AccessControl[Any] | None":
        """The `AccessControl` guarding `bus`, or `None` when nothing does — an entrypoint then
        leaves the identity as it found it."""
        return cls._guarding.get(bus)

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
            cls._guarding.items(), key=lambda item: item[0].name != context
        ):
            if access not in found:
                found.append(access)
        return found

    @property
    def identity(self) -> Identity:
        """Who the execution in progress acts as — never `None`."""
        return current_identity()

    # Declaring

    def requires(
        self, *requirements: P | AnyOf | ByField, when_denied: WhenDenied = WhenDenied.RAISE
    ):
        """Every requirement listed, and every other `requires` stacked on the same class, is
        needed — `any_of` for one among several. On a Feature, an ApplicationService or a Hook.

            @auth.requires(BillingPermission.ISSUE_INVOICE)   ->  "billing.invoice.issue"
            @auth.requires(READ, when_denied=WhenDenied.SKIP)
                ->  "billing.read (else skipped)"
            auth.requires()   ->  raises ProgrammingError("requires() names at least one ...")

        The class is returned as it was, its declaration stored in `declarations`.
        """
        if not requirements:
            raise exceptions.ProgrammingError("requires() names at least one permission")

        def declared[T: type](cls: T) -> T:
            return self.declarations.add(
                cls, AccessDeclaration("requires", tuple(requirements), when_denied)
            )

        return declared

    def by_field(self, field: str, permissions: Mapping[Any, P | AnyOf]) -> ByField:
        """A requirement the Command's own `field` chooses — for `requires`: a value it does not
        name is refused; asked with no Command (`may_run`, a catalog), it is one among them.

            @auth.requires(auth.by_field("direction", {
                Direction.INBOUND: FinancePermission.RECEIVE,
                Direction.OUTBOUND: FinancePermission.PAY,
            }))
        """
        if not permissions:
            raise exceptions.ProgrammingError("by_field() names at least one permission")
        return ByField(field, tuple(permissions.items()))

    def any_of(self, *permissions: P) -> AnyOf:
        """A requirement met by one of `permissions` — for `requires`.

        @auth.requires(auth.any_of(BillingPermission.ISSUE, BillingPermission.ADMIN))
        """
        return AnyOf(tuple(permissions))

    def authenticated[T: type](self, cls: T) -> T:
        """Anyone authenticated, whatever they may do."""
        return self.declarations.add(cls, AccessDeclaration("authenticated"))

    def public[T: type](self, cls: T) -> T:
        """Anyone, authenticated or not — what a strict `AccessControl` needs to be told."""
        return self.declarations.add(cls, AccessDeclaration("public"))

    # Authenticating

    def _provider_of(self, identity: Identity) -> AuthProvider | None:
        """The provider that vouched for `identity` — the one asked about its permissions.

        Context: `None` for an identity opened by hand; one vouched for by a provider this
        does not hold is refused — its permissions would otherwise be taken on its word."""
        if identity.provider is None:
            return None
        for provider in self.providers:
            if provider.name == identity.provider:
                return provider
        raise exceptions.ProgrammingError(
            f"{identity.subject} was vouched for by '{identity.provider}', a provider this "
            "AccessControl does not hold"
        )

    def authenticate(self, credentials: Credentials) -> Identity:
        """Who the credentials say is calling — the first provider that recognizes them, or
        `Identity.anonymous()` when none does. `Unauthenticated` when one recognizes them and
        they fail.

            # providers=[jwt, api_keys]
            x-api-key: sk_3f9…      ->  Identity("apikey:ops-bot", provider="api-keys")
            no credentials at all   ->  Identity.anonymous()
            Authorization: Bearer <expired>  ->  raises Unauthenticated, api_keys not asked

        Context: the identity is stamped with the name of the provider that answered, even
        when the provider left `provider` unset — that is the one later asked about it.
        """
        for provider in self.providers:
            identity = provider.authenticate(credentials)
            if identity is not None:
                if identity.provider != provider.name:
                    identity = identity.model_copy(update={"provider": provider.name})
                return identity
        return Identity.anonymous()

    def challenges(self) -> list[str]:
        """The `WWW-Authenticate` values a 401 answers with, one per provider that has one."""
        return [challenge for one in self.providers if (challenge := one.challenge())]

    def credentials_for(self, identity: Identity | None = None) -> Credentials | None:
        """What to send another service so it knows who this call acts for — asked of the
        identity's provider first.

            # acting as user:42, vouched for by "service-tokens"
            auth.credentials_for()
                ->  Credentials(transport="service", headers={"x-sp-service-token": "eyJ…"})
            # a provider that issues nothing for the identity — and no other does either
            auth.credentials_for(Identity.anonymous())   ->  None

        Context: the identity in play when none is given; the other providers are then asked
        in order, the first that issues something wins.
        """
        acting = identity or current_identity()
        own = self._provider_of(acting)
        for provider in (own, *(one for one in self.providers if one is not own)):
            if provider is not None and (issued := provider.credentials_for(acting)):
                return issued
        return None

    # Deciding

    def _has(
        self,
        identity: Identity,
        permission: str,
        resource: Any,
        context: Mapping[str, Any] | None,
    ) -> exceptions.PermissionDenied | None:
        """The refusal of one permission — `None` when `identity` holds it.

        Context: answered, not raised, so `_denial` can weigh the members of an `AnyOf` and
        `allows` can say no without a traceback. A provider that raises `PermissionDenied` to
        say why has that very refusal answered.

        1. An identity opened by hand: its `permissions` are the answer.
        2. Else its provider's `has_permission`, with `resource` and `context`.
        Final: `None` when allowed, else the provider's `PermissionDenied` or a plain one.
        """
        provider = self._provider_of(identity)
        try:
            if provider is None:
                allowed = permission in identity.permissions
            else:
                allowed = provider.has_permission(identity, permission, resource, context)
        except exceptions.PermissionDenied as denied:
            return denied
        return None if allowed else exceptions.PermissionDenied(identity.subject, permission)

    def _denial(
        self,
        identity: Identity,
        requirement: Requirement,
        resource: Any,
        context: Mapping[str, Any] | None,
        dto: Any = None,
    ) -> exceptions.PermissionDenied | None:
        """The refusal of a requirement — one permission, or an `AnyOf` refused only when
        every member is.

            AnyOf((READ, ADMIN)), identity holds READ  ->  None
            AnyOf((READ, ADMIN)), identity holds none  ->  PermissionDenied("user:1",
                "billing.read or billing.admin", "lacks billing.read and lacks billing.admin")

        Context: every member of an `AnyOf` is asked, even after one is granted — the provider
        may be remote, but the answer, and the reasons joined, stay complete and in order; a
        provider that words the whole refusal (`refusing_any`) is told it instead.
        """
        if isinstance(requirement, ByField):
            chosen = requirement.every() if dto is None else requirement.chosen(dto)
            if chosen is None:
                value = getattr(dto, requirement.field, None)
                return exceptions.PermissionDenied(
                    identity.subject,
                    str(requirement),
                    f"{requirement.field}={value!r} names no permission",
                )
            return self._denial(identity, chosen, resource, context)
        if not isinstance(requirement, AnyOf):
            return self._has(identity, requirement.value, resource, context)
        denials = [
            self._has(identity, one.value, resource, context)
            for one in requirement.permissions
        ]
        if any(one is None for one in denials):
            return None
        each = [one.reason for one in denials if one is not None]
        provider = self._provider_of(identity)
        said = (
            None
            if provider is None
            else provider.refusing_any(
                identity, [one.value for one in requirement.permissions], each
            )
        )
        return exceptions.PermissionDenied(
            identity.subject, str(requirement), said or " and ".join(each)
        )

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

            refusal(anyone, AccessDeclaration("public"), "IssueInvoice")          ->  None
            refusal(ANONYMOUS, AccessDeclaration("authenticated"), "IssueInvoice")
                ->  Unauthenticated("authenticate to run IssueInvoice")
            refusal(user_without_issue, AccessDeclaration("requires", (ISSUE,)), "IssueInvoice")
                ->  PermissionDenied("user:42", "billing.invoice.issue")

        Context: public and the system pass first; nobody is refused before any provider is
        asked; then each requirement in order, the first refused answering — a `by_field` one
        chosen by `dto`, the Command being run, or one among its permissions without it.
        """
        if declaration.kind == "public" or identity.kind == IdentityKind.SYSTEM:
            return None
        if identity.is_anonymous:
            return exceptions.Unauthenticated(f"authenticate to run {what}")
        for requirement in declaration.requirements:
            denied = self._denial(identity, requirement, resource, context, dto)
            if denied is not None:
                return denied
        return None

    def allows(self, permission: P, resource: Any = None) -> bool:
        """Whether the identity in play may `permission` — on `resource`, when given. What an
        ApplicationService asks before an optional step."""
        identity = current_identity()
        if identity.kind == IdentityKind.SYSTEM:
            return True
        return self._denial(identity, permission, resource, context_scopes.live()) is None

    def check(self, permission: P, resource: Any = None) -> None:
        """`allows`, raising `PermissionDenied` — or `Unauthenticated` for nobody — when not.

            self.auth.check(BillingPermission.ISSUE_INVOICE)    # holds it  ->  None
            # lacks it   ->  raises PermissionDenied("user:42", "billing.invoice.issue")
            # nobody     ->  raises Unauthenticated("authenticate to billing.invoice.issue")

        The system passes without asking any provider.
        """
        identity = current_identity()
        if identity.kind == IdentityKind.SYSTEM:
            return
        if identity.is_anonymous:
            raise exceptions.Unauthenticated(f"authenticate to {permission.value}")
        denied = self._denial(identity, permission, resource, context_scopes.live())
        if denied is not None:
            raise denied

    def permitted(self, permission: P, resources: Sequence[Any]) -> list[bool]:
        """`allows` for many resources — in one call to the provider when it answers batches.
        A provider that refuses the whole batch with `PermissionDenied` refuses each.

            self.auth.permitted(BillingPermission.READ, [invoice_1, invoice_2])
                ->  [True, False]      # as the provider answered each
            # the system  ->  [True, True]
            # opened by hand: holding READ  ->  [True, True]; not  ->  [False, False]

        Context: an identity opened by hand answers with its `permissions`, the same for each.
        """
        identity = current_identity()
        if identity.kind == IdentityKind.SYSTEM:
            return [True] * len(resources)
        provider = self._provider_of(identity)
        if provider is None:
            return [permission.value in identity.permissions] * len(resources)
        try:
            return provider.permitted(
                identity, permission.value, resources, context_scopes.live()
            )
        except exceptions.PermissionDenied:
            return [False] * len(resources)

    def scope_of(self, aggregate: type) -> "Criteria | None":
        """Which records of `aggregate` the identity in play may read, as its provider filters
        them — `search(Aggregate, criteria)` or a SQL `narrowed(...)` takes it.

        Context: `None` means every record, and only two identities read every record: the
        system, and one opened by hand — a test, in-process code. Nobody calling is refused:
        a read scoped by identity with no identity would otherwise read everything.

            self.auth.scope_of(Invoice)   # user:42 of tenant bo, its provider scoping
                ->  Criteria.where("tenant", "=", "bo")
            # the system, or an identity opened by hand  ->  None
            # nobody  ->  raises Unauthenticated("authenticate to read Invoice")

        The criteria is the provider's `scope(identity, aggregate)`, taken as it answers.
        """
        identity = current_identity()
        if identity.kind == IdentityKind.SYSTEM:
            return None
        if identity.is_anonymous:
            raise exceptions.Unauthenticated(f"authenticate to read {aggregate.__name__}")
        provider = self._provider_of(identity)
        return None if provider is None else provider.scope(identity, aggregate)

    # Wiring

    def _identity_for_logs(self) -> dict[str, str]:
        """The `identity` field every log line of a guarded bus carries — none for nobody."""
        identity = current_identity()
        return {} if identity.is_anonymous else {"identity": identity.subject}

    def attach(self, bus: "UseFramework") -> None:
        """What `on(bus)` wires, and what every generation `fresh()` makes of the bus wires
        again against itself: the guard, and this object as the handlers' `auth`."""
        guard = BusGuard(self, bus)
        bus.interceptor(sequence=GUARD_SEQUENCE)(guard)
        bus.add_dependency("auth", self)
        self._guards[bus] = guard
        self._buses.add(bus)
        AccessControl._guarding[bus] = self

    def _replacing(self, replaced: "AccessControl[Any]", bus: "UseFramework") -> None:
        """What `on(bus, replaces=replaced)` wires, and every generation `fresh()` makes of the
        bus wires again: this object's guard in the place of `replaced`'s, and this object as
        the handlers' `auth` — `replaced` guards the bus no more.

        A bus `replaced` does not guard has nothing to replace: `ProgrammingError`."""
        old = replaced._guards.get(bus)
        if old is None:
            raise exceptions.ProgrammingError(
                f"the AccessControl replaced does not guard '{bus.name}': guard it with on() "
                "instead"
            )
        guard = BusGuard(self, bus)
        bus.interceptor(sequence=GUARD_SEQUENCE, replaces=old)(guard)
        bus.add_dependency("auth", self, replaces=replaced)
        self._guards[bus] = guard
        self._buses.add(bus)
        replaced._buses.discard(bus)
        AccessControl._guarding[bus] = self

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

        A bus this object does not guard: `ProgrammingError`."""
        guard = self._guards.get(bus)
        if guard is None:
            raise exceptions.ProgrammingError(
                f"this AccessControl does not guard '{bus.name}'"
            )
        return guard.refusal_for(command, identity or current_identity(), dto)

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
        """This object with other providers — the same declarations, `strict` and `enabled`:
        what a test puts on a `fresh()` bus, so the rules of production are checked against
        providers of its own.

            test = billing.fresh()
            auth.using(providers=[StaticProvider(...)]).on(test, replaces=auth)
        """
        other: AccessControl[P] = AccessControl(providers, self.strict, self.enabled)
        other.declarations = self.declarations
        return other

    def on(
        self, target: "UseFramework | Hooks", replaces: "AccessControl[Any] | None" = None
    ) -> None:
        """Guard every use case of a bus — the bus not built yet — and inject this object in its
        handlers as `auth`; or every hook of a collection, and of every copy made of it.

            auth.on(billing)          # billing and each generation `fresh()` makes of it
            auth.on(billing_hooks)    # every hook of the collection is gated
            auth.on(billing)   # again  ->  raises ProgrammingError("... guards 'billing'")
            auth.using(providers=[fake]).on(test, replaces=auth)   # a fresh() bus, in a test

        `replaces` names the `AccessControl` guarding the bus now — what `fresh()` repeats from
        the bus it was made of — and this one takes its place there: its guard and its `auth`.

        1. Disabled: a warning is logged — the target is wired anyway, and checks nothing.
        2. A `Hooks` collection:
           2.1 one already guarded is a `ProgrammingError`;
           2.2 else the hook guard is set as its gate and the collection stored.
        3. A bus already guarded is a `ProgrammingError`.
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
                raise exceptions.ProgrammingError("AccessControl already guards this Hooks")
            target.gate(self._hook_guard)
            self._hooks.append(target)
            return
        if target in self._roots:
            raise exceptions.ProgrammingError(f"AccessControl already guards '{target.name}'")
        target.extend(self.attach)
        target.logger.add_context_source(self._identity_for_logs)
        self._roots.append(target)

    # Describing — read without building a bus nor closing a collection

    def _use_cases(self) -> dict[str, AccessDeclaration | None]:
        """What each use case of every guarded bus declared, by its Command's dotted name —
        `None` for one that declared nothing; a replaced use case answers its replacement's.
        """
        declared: dict[str, AccessDeclaration | None] = {}
        for bus in list(self._buses):
            for command, handler in bus.handlers().items():
                declared[f"{command.__module__}.{command.__qualname__}"] = (
                    self.declarations.of(handler, bus.replaced_for(command))
                )
        return declared

    def requirements_of(
        self, cls: type, bus: "UseFramework | None" = None
    ) -> AccessDeclaration | None:
        """What a Command's use case, a handler or a hook declared — `None` for nothing.

            auth.requirements_of(CommandIssueInvoice)  ->  AccessDeclaration("requires", (ISSUE,))
            auth.requirements_of(IssueInvoice)         ->  the same, asked of the handler
            auth.requirements_of(QueryHealth)          ->  None    # declared nothing
            auth.requirements_of(CommandIssueInvoice, bus=billing)   on that bus only

        Context: a Command a guarded bus runs answers its handler's declaration — its
        replacement's when replaced; with `bus`, the handler that bus answers it with; any other
        class is looked up as it is.
        """
        if bus is not None:
            handler = bus.handlers().get(cls)
            return (
                None
                if handler is None
                else self.declarations.of(handler, bus.replaced_for(cls))
            )
        for guarded in list(self._buses):
            handler = guarded.handlers().get(cls)
            if handler is not None:
                return self.declarations.of(handler, guarded.replaced_for(cls))
        return self.declarations.of(cls)

    def verify(self) -> list[str]:
        """What would not work as declared — for a test, or a check once everything is
        registered. It builds no bus and closes no collection, so calling it early changes
        nothing; it only sees less.

            auth.verify()   ->  []
            AccessControl(strict=True, enabled=False).verify()   # once on a bus
                ->  ["access control is disabled",
                     "billing.Orphan declares access but runs on no bus or hooks this ...",
                     "billing.QueryHealth declares nothing and is refused (strict)"]

        1. Disabled: said first — at runtime nothing is checked; the rest is still listed.
        2. A class declared that no guarded bus or collection runs — its declaration never holds.
        Final: strict, a use case that declares nothing — refused when entered from outside.
        """
        problems = [] if self.enabled else ["access control is disabled"]
        running: set[type] = set()
        replaced: set[str] = set()
        for bus in list(self._buses):
            for command, handler in bus.handlers().items():
                running.add(handler)
                replaced.update(bus.replaced_for(command))
        for hooks in self._hooks:
            running.update(hooks.registered())
        problems += sorted(
            f"{f"{cls.__module__}.{cls.__qualname__}"} declares access but runs on no bus or hooks this AccessControl "
            "guards"
            for cls in self.declarations.declared()
            if cls not in running and f"{cls.__module__}.{cls.__qualname__}" not in replaced
        )
        if self.strict:
            problems += sorted(
                f"{name} declares nothing and is refused (strict)"
                for name, declaration in self._use_cases().items()
                if declaration is None
            )
        return problems

    def describe(self) -> AccessDescription:
        """Everything this object guards and how — read without building a bus.

            auth.describe()
                ->  AccessDescription(enabled=True, strict=False, providers=["odoo"],
                        use_cases={"billing.CommandIssueInvoice": "billing.invoice.issue",
                                   "billing.QueryHealth": "unchecked"},
                        hooks={"billing.hooks.NotifyCustomer": "authenticated"})

        Context: every use case is listed, `unchecked` when it declared nothing; only the
        hooks that declared something are.
        """
        return AccessDescription(
            enabled=self.enabled,
            strict=self.strict,
            providers=[one.name for one in self.providers],
            use_cases={
                name: str(declaration) if declaration else "unchecked"
                for name, declaration in self._use_cases().items()
            },
            hooks={
                f"{hook.__module__}.{hook.__qualname__}": str(declaration)
                for collection in self._hooks
                for hook in collection.registered()
                if (declaration := self.declarations.of(hook)) is not None
            },
        )
