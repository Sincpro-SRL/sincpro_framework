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
`current_identity()`; an entrypoint that verified the caller with `authenticate(...)` opens
`as_identity(...)`. The provider that authenticated an identity is the one asked about it; an
identity opened by hand — a test, in-process code — answers with its `permissions`.
"""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary, WeakSet

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.identity import Credentials, Identity, IdentityKind
from sincpro_framework.auth.domain.permissions import (
    AnyOf,
    Declaration,
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
from sincpro_framework.context.infrastructure.tree import executing_context
from sincpro_framework.ddd.repositories.hooks import Hooks
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.ddd.criteria import Criteria
    from sincpro_framework.use_bus import UseFramework


_guarding: "WeakKeyDictionary[UseFramework, AccessControl[Any]]" = WeakKeyDictionary()
"""Which `AccessControl` guards each bus — and each generation made of it — so an entrypoint
finds the one of the bus it dispatches to, with no argument to pass."""


def access_control_of(bus: "UseFramework") -> "AccessControl[Any] | None":
    """The `AccessControl` guarding `bus`, or `None` when nothing does — an entrypoint then
    leaves the identity as it found it."""
    return _guarding.get(bus)


def access_controls_for(context: str) -> "list[AccessControl[Any]]":
    """Every `AccessControl` of this process, those guarding a bus of `context` first — what a
    call to the service hosting `context` asks for the credentials it sends.

        # auth_b guards the "billing" bus, auth_s the "sales" bus and its generations
        access_controls_for("sales")     ->  [auth_s, auth_b]
        access_controls_for("payroll")   ->  [auth_b, auth_s]    # as registered

    Context: an `AccessControl` guarding several buses — the generations `fresh()` makes —
    is listed once, where it first appears.
    """
    found: list[AccessControl[Any]] = []
    for bus, access in sorted(_guarding.items(), key=lambda item: item[0].name != context):
        if access not in found:
            found.append(access)
    return found


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
    """

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
        self._roots: list["UseFramework"] = []
        self._hooks: list[Hooks] = []
        self._hook_guard = HookGuard(self)

    @property
    def identity(self) -> Identity:
        """Who the execution in progress acts as — never `None`."""
        return current_identity()

    # Declaring

    def requires(self, *requirements: P | AnyOf, when_denied: WhenDenied = WhenDenied.RAISE):
        """Every requirement listed, and every other `requires` stacked on the same class, is
        needed — `any_of` for one among several. On a Feature, an ApplicationService or a Hook.

            @auth.requires(BillingPermission.ISSUE_INVOICE)   ->  "billing.invoice.issue"
            @auth.requires(READ, when_denied=WhenDenied.SKIP)
                ->  "billing.read (else skipped)"
            auth.requires()   ->  raises ProgrammingError("requires() names at least one ...")

        The class is returned as it was, its declaration kept in `declarations`.
        """
        if not requirements:
            raise exceptions.ProgrammingError("requires() names at least one permission")

        def declared[T: type](cls: T) -> T:
            return self.declarations.add(
                cls, Declaration("requires", tuple(requirements), when_denied)
            )

        return declared

    def any_of(self, *permissions: P) -> AnyOf:
        """A requirement met by one of `permissions` — for `requires`.

        @auth.requires(auth.any_of(BillingPermission.ISSUE, BillingPermission.ADMIN))
        """
        return AnyOf(tuple(permissions))

    def authenticated[T: type](self, cls: T) -> T:
        """Anyone authenticated, whatever they may do."""
        return self.declarations.add(cls, Declaration("authenticated"))

    def public[T: type](self, cls: T) -> T:
        """Anyone, authenticated or not — what a strict `AccessControl` needs to be told."""
        return self.declarations.add(cls, Declaration("public"))

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
    ) -> exceptions.PermissionDenied | None:
        """The refusal of a requirement — one permission, or an `AnyOf` refused only when
        every member is.

            AnyOf((READ, ADMIN)), identity holds READ  ->  None
            AnyOf((READ, ADMIN)), identity holds none  ->  PermissionDenied("user:1",
                "billing.read or billing.admin", "lacks billing.read and lacks billing.admin")

        Context: every member of an `AnyOf` is asked, even after one is granted — the provider
        may be remote, but the answer, and the reasons joined, stay complete and in order.
        """
        if not isinstance(requirement, AnyOf):
            return self._has(identity, requirement.value, resource, context)
        denials = [
            self._has(identity, one.value, resource, context)
            for one in requirement.permissions
        ]
        if any(one is None for one in denials):
            return None
        reasons = " and ".join(one.reason for one in denials if one is not None)
        return exceptions.PermissionDenied(identity.subject, str(requirement), reasons)

    def refusal(
        self,
        identity: Identity,
        declaration: Declaration,
        what: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> exceptions.AuthError | None:
        """Why `identity` may not run what `declaration` guards — `None` when it may.

            refusal(anyone, Declaration("public"), "IssueInvoice")          ->  None
            refusal(ANONYMOUS, Declaration("authenticated"), "IssueInvoice")
                ->  Unauthenticated("authenticate to run IssueInvoice")
            refusal(user_without_issue, Declaration("requires", (ISSUE,)), "IssueInvoice")
                ->  PermissionDenied("user:42", "billing.invoice.issue")

        Context: public and the system pass first; nobody is refused before any provider is
        asked; then each requirement in order, the first refused answering.
        """
        if declaration.kind == "public" or identity.kind == IdentityKind.SYSTEM:
            return None
        if identity.is_anonymous:
            return exceptions.Unauthenticated(f"authenticate to run {what}")
        for requirement in declaration.requirements:
            denied = self._denial(identity, requirement, resource, context)
            if denied is not None:
                return denied
        return None

    def allows(self, permission: P, resource: Any = None) -> bool:
        """Whether the identity in play may `permission` — on `resource`, when given. What an
        ApplicationService asks before an optional step."""
        identity = current_identity()
        if identity.kind == IdentityKind.SYSTEM:
            return True
        return self._denial(identity, permission, resource, executing_context()) is None

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
        denied = self._denial(identity, permission, resource, executing_context())
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
                identity, permission.value, resources, executing_context()
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
        bus.interceptor(sequence=GUARD_SEQUENCE)(BusGuard(self, bus))
        bus.add_dependency("auth", self)
        self._buses.add(bus)
        _guarding[bus] = self

    def on(self, target: "UseFramework | Hooks") -> None:
        """Guard every use case of a bus — the bus not built yet — and inject this object in its
        handlers as `auth`; or every hook of a collection, and of every copy made of it.

            auth.on(billing)          # billing and each generation `fresh()` makes of it
            auth.on(billing_hooks)    # every hook of the collection is gated
            auth.on(billing)   # again  ->  raises ProgrammingError("... guards 'billing'")

        1. Disabled: a warning is logged — the target is wired anyway, and checks nothing.
        2. A `Hooks` collection:
           2.1 one already guarded is a `ProgrammingError`;
           2.2 else the hook guard is set as its gate and the collection kept.
        3. A bus already guarded is a `ProgrammingError`.
        4. `attach` is registered as an extension of the bus — run on it and on every
           generation made of it, so the guard and `auth` reach each.
        5. Each log line of the bus gets the `identity` of who acts.
        Final: the bus is kept among the roots this object guards.
        """
        if not self.enabled:
            logger.warning("access control is disabled: nothing it guards is checked")
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

    def _use_cases(self) -> dict[str, Declaration | None]:
        """What each use case of every guarded bus declared, by its Command's dotted name —
        `None` for one that declared nothing; a replaced use case answers its replacement's.
        """
        declared: dict[str, Declaration | None] = {}
        for bus in list(self._buses):
            for command, handler in bus.handlers().items():
                declared[f"{command.__module__}.{command.__qualname__}"] = (
                    self.declarations.of(handler, bus.replaced_for(command))
                )
        return declared

    def requirements_of(self, cls: type) -> Declaration | None:
        """What a Command's use case, a handler or a hook declared — `None` for nothing.

            auth.requirements_of(CommandIssueInvoice)  ->  Declaration("requires", (ISSUE,))
            auth.requirements_of(IssueInvoice)         ->  the same, asked of the handler
            auth.requirements_of(QueryHealth)          ->  None    # declared nothing

        Context: a Command a guarded bus runs answers its handler's declaration — its
        replacement's when replaced; any other class is looked up as it is.
        """
        for bus in list(self._buses):
            handler = bus.handlers().get(cls)
            if handler is not None:
                return self.declarations.of(handler, bus.replaced_for(cls))
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
