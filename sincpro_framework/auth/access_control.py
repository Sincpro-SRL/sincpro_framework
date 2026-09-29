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
from weakref import WeakSet

from sincpro_framework.auth.domain import (
    AnyOf,
    AuthError,
    AuthProvider,
    Credentials,
    Identity,
    IdentityKind,
    Permission,
    PermissionDenied,
    Requirement,
    Unauthenticated,
    WhenDenied,
)
from sincpro_framework.auth.guard import (
    GUARD_SEQUENCE,
    BusGuard,
    Declaration,
    Declarations,
    HookGuard,
    qualified,
)
from sincpro_framework.auth.security_context import current_identity
from sincpro_framework.context.mixin import executing_context
from sincpro_framework.ddd.repositories.hooks import Hooks
from sincpro_framework.exceptions import ExtensionRefused
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
    def __init__(
        self,
        providers: AuthProvider | Sequence[AuthProvider] = (),
        strict: bool = False,
        enabled: bool = True,
    ) -> None:
        """`providers` are tried in order to authenticate a call; `strict` refuses a use case
        entered from outside that declares nothing; `enabled=False` checks nothing — a local
        environment, a test that is not about access."""
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
        """
        if not requirements:
            raise ExtensionRefused("requires() names at least one permission")

        def declared[T: type](cls: T) -> T:
            return self.declarations.add(
                cls, Declaration("requires", tuple(requirements), when_denied)
            )

        return declared

    def any_of(self, *permissions: P) -> AnyOf:
        return AnyOf(tuple(permissions))

    def authenticated[T: type](self, cls: T) -> T:
        """Anyone authenticated, whatever they may do."""
        return self.declarations.add(cls, Declaration("authenticated"))

    def public[T: type](self, cls: T) -> T:
        """Anyone, authenticated or not — what a strict `AccessControl` needs to be told."""
        return self.declarations.add(cls, Declaration("public"))

    # Authenticating

    def _provider_of(self, identity: Identity) -> AuthProvider | None:
        """Context: `None` for an identity opened by hand; one vouched for by a provider this
        does not hold is refused — its permissions would otherwise be taken on its word."""
        if identity.provider is None:
            return None
        for provider in self.providers:
            if provider.name == identity.provider:
                return provider
        raise ExtensionRefused(
            f"{identity.subject} was vouched for by '{identity.provider}', a provider this "
            "AccessControl does not hold"
        )

    def authenticate(self, credentials: Credentials) -> Identity:
        """Who the credentials say is calling — the first provider that recognizes them, or
        `Identity.anonymous()` when none does. `Unauthenticated` when one recognizes them and
        they fail."""
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
        identity's provider first."""
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
    ) -> PermissionDenied | None:
        provider = self._provider_of(identity)
        try:
            if provider is None:
                allowed = permission in identity.permissions
            else:
                allowed = provider.has_permission(identity, permission, resource, context)
        except PermissionDenied as denied:
            return denied
        return None if allowed else PermissionDenied(identity.subject, permission)

    def _denial(
        self,
        identity: Identity,
        requirement: Requirement,
        resource: Any,
        context: Mapping[str, Any] | None,
    ) -> PermissionDenied | None:
        if not isinstance(requirement, AnyOf):
            return self._has(identity, requirement.value, resource, context)
        denials = [
            self._has(identity, one.value, resource, context)
            for one in requirement.permissions
        ]
        if any(one is None for one in denials):
            return None
        reasons = " and ".join(one.reason for one in denials if one is not None)
        return PermissionDenied(identity.subject, str(requirement), reasons)

    def refusal(
        self,
        identity: Identity,
        declaration: Declaration,
        what: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> AuthError | None:
        """Why `identity` may not run what `declaration` guards — `None` when it may."""
        if declaration.kind == "public" or identity.kind == IdentityKind.SYSTEM:
            return None
        if identity.is_anonymous:
            return Unauthenticated(f"authenticate to run {what}")
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
        """`allows`, raising `PermissionDenied` — or `Unauthenticated` for nobody — when not."""
        identity = current_identity()
        if identity.kind == IdentityKind.SYSTEM:
            return
        if identity.is_anonymous:
            raise Unauthenticated(f"authenticate to {permission.value}")
        denied = self._denial(identity, permission, resource, executing_context())
        if denied is not None:
            raise denied

    def permitted(self, permission: P, resources: Sequence[Any]) -> list[bool]:
        """`allows` for many resources — in one call to the provider when it answers batches.
        A provider that refuses the whole batch with `PermissionDenied` refuses each."""
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
        except PermissionDenied:
            return [False] * len(resources)

    def scope_of(self, aggregate: type) -> "Criteria | None":
        """Which records of `aggregate` the identity in play may read, as its provider filters
        them — `search(Aggregate, criteria)` or a SQL `narrowed(...)` takes it.

        Context: `None` means every record, and only two identities read every record: the
        system, and one opened by hand — a test, in-process code. Nobody calling is refused:
        a read scoped by identity with no identity would otherwise read everything.
        """
        identity = current_identity()
        if identity.kind == IdentityKind.SYSTEM:
            return None
        if identity.is_anonymous:
            raise Unauthenticated(f"authenticate to read {aggregate.__name__}")
        provider = self._provider_of(identity)
        return None if provider is None else provider.scope(identity, aggregate)

    # Wiring

    def _identity_for_logs(self) -> dict[str, str]:
        identity = current_identity()
        return {} if identity.is_anonymous else {"identity": identity.subject}

    def attach(self, bus: "UseFramework") -> None:
        """What `on(bus)` wires, and what every generation `fresh()` makes of the bus wires
        again against itself: the guard, and this object as the handlers' `auth`."""
        bus.interceptor(sequence=GUARD_SEQUENCE)(BusGuard(self, bus))
        bus.add_dependency("auth", self)
        self._buses.add(bus)

    def on(self, target: "UseFramework | Hooks") -> None:
        """Guard every use case of a bus — the bus not built yet — and inject this object in its
        handlers as `auth`; or every hook of a collection, and of every copy made of it."""
        if not self.enabled:
            logger.warning("access control is disabled: nothing it guards is checked")
        if isinstance(target, Hooks):
            if any(target is one for one in self._hooks):
                raise ExtensionRefused("AccessControl already guards this Hooks")
            target.gate(self._hook_guard)
            self._hooks.append(target)
            return
        if target in self._roots:
            raise ExtensionRefused(f"AccessControl already guards '{target.name}'")
        target.extend(self.attach)
        target.logger.add_context_source(self._identity_for_logs)
        self._roots.append(target)

    # Describing — read without building a bus nor closing a collection

    def _use_cases(self) -> dict[str, Declaration | None]:
        declared: dict[str, Declaration | None] = {}
        for bus in list(self._buses):
            for command, handler in bus.handlers().items():
                declared[qualified(command)] = self.declarations.of(
                    handler, bus.replaced_for(command)
                )
        return declared

    def requirements_of(self, cls: type) -> Declaration | None:
        """What a Command's use case, a handler or a hook declared — `None` for nothing."""
        for bus in list(self._buses):
            handler = bus.handlers().get(cls)
            if handler is not None:
                return self.declarations.of(handler, bus.replaced_for(cls))
        return self.declarations.of(cls)

    def verify(self) -> list[str]:
        """What would not work as declared — for a test, or a check once everything is
        registered. It builds no bus and closes no collection, so calling it early changes
        nothing; it only sees less.

        1. Disabled: said first, nothing is checked.
        2. A class declared that no guarded bus or collection runs — its declaration never holds.
        3. Final: strict, a use case that declares nothing — refused when entered from outside.
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
            f"{qualified(cls)} declares access but runs on no bus or hooks this AccessControl "
            "guards"
            for cls in self.declarations.declared()
            if cls not in running and qualified(cls) not in replaced
        )
        if self.strict:
            problems += sorted(
                f"{name} declares nothing and is refused (strict)"
                for name, declaration in self._use_cases().items()
                if declaration is None
            )
        return problems

    def describe(self) -> AccessDescription:
        return AccessDescription(
            enabled=self.enabled,
            strict=self.strict,
            providers=[one.name for one in self.providers],
            use_cases={
                name: str(declaration) if declaration else "unchecked"
                for name, declaration in self._use_cases().items()
            },
            hooks={
                qualified(hook): str(declaration)
                for collection in self._hooks
                for hook in collection.registered()
                if (declaration := self.declarations.of(hook)) is not None
            },
        )
