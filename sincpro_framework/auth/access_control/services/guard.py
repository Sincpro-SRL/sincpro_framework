"""The guard: what `AccessControl` runs around a use case — the outermost interceptor of a bus,
and of every generation `fresh()` makes of it — and before a hook's moment — a gate of its
`Hooks`, and of every copy or combination made of it.

Context: the decorators only mark classes; the marks are read here, at a use case's or a hook's
first run, so a handler registered after `on(...)` is guarded all the same. A mark belongs to the
class object, not to its name: a use case stored as source and loaded again is a new class, and
declares anew. A `replaces=` handler or hook that declares nothing inherits the declaration of the
one it replaces — replacing never opens a hole. A use case that declares nothing is open: it runs
for anyone, entered from outside or nested. The `every_call` checks of the `AccessControl` run on
every use case, declared or open — the project's own interceptor, in the guard's place.
"""

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary

from sincpro_framework.auth.access_control.domain.access import Access
from sincpro_framework.auth.access_control.services.decisions import Decisions
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.identity import Identity, IdentityKind
from sincpro_framework.auth.common.domain.permissions import (
    AccessDeclaration,
    WhenDenied,
    command_name,
)
from sincpro_framework.auth.common.infrastructure.security_context import (
    as_access_control,
    current_identity,
)
from sincpro_framework.context import (
    ContextKey,
    context_scopes,
    said_by_caller,
    use_context,
)
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.ddd.repositories.hooks import Hook, Hooks
    from sincpro_framework.use_bus import UseFramework

GUARD_SEQUENCE = -(10**9)
"""Lower than any a project writes: the guard wraps every other interceptor of a bus, so a
cached or retried answer is never given without asking."""

ACTOR_KEYS = (ContextKey.USER_ID, ContextKey.TENANT_ID, ContextKey.TENANT_IDS)
"""The keys of the context that say who acts and where — what a draft, a cached answer, an
idempotent write and a fact's `actor` are owned by."""


def _tenant_refusal(identity: Identity, context: Any) -> exceptions.PermissionDenied | None:
    """The refusal of a call whose caller names a tenant that is not its identity's own
    (`tenant_id`, `tenant_ids`) — an identity of a tenant acts in it, whatever the headers say.
    `None` for an identity of no tenant, or one the caller names nothing else for."""
    if identity.is_anonymous or identity.tenant is None:
        return None
    named = (
        [context.get(ContextKey.TENANT_ID)]
        if said_by_caller(context, ContextKey.TENANT_ID)
        else []
    )
    if said_by_caller(context, ContextKey.TENANT_IDS):
        named.extend(context.get(ContextKey.TENANT_IDS) or ())
    other = next((one for one in named if one not in (None, identity.tenant)), None)
    if other is None:
        return None
    return exceptions.PermissionDenied(
        identity.subject,
        ContextKey.TENANT_ID,
        f"acts in tenant {identity.tenant!r}, the call names {other!r}",
    )


def _bind_identity_to_context(identity: Identity, context: Any) -> None:
    """Who acts and where, as the identity a provider verified says — never as the caller's
    headers say.

    Context: the context is a dict a caller writes (`baggage`, `sincpro-context`); what a draft,
    a cached answer, an audit trail or a project's query keys on (`Owner.BY_TENANT_AND_USER`)
    is read from it. Code, a provider of the project's and a scope still say what they say.

    1. Nobody verified: what the caller sent about who acts and where is dropped — an
       anonymous caller never owns the draft, the cached answer or the write of whom it names.
    2. `user_id`: unsaid, the identity's subject; sent by the caller, a person's subject in
       its place — a service acts for whom its call names.
    Final: an identity of a tenant acts in it — `tenant_id` unsaid is its tenant.
    """
    if identity.is_anonymous:
        for key in ACTOR_KEYS:
            if said_by_caller(context, key):
                context.set(key, None)
        return
    said = context.get(ContextKey.USER_ID)
    if said is None or (
        identity.kind == IdentityKind.USER
        and said != identity.subject
        and said_by_caller(context, ContextKey.USER_ID)
    ):
        context.set(ContextKey.USER_ID, identity.subject)
    if identity.tenant is not None and context.get(ContextKey.TENANT_ID) is None:
        context.set(ContextKey.TENANT_ID, identity.tenant)


def _reject(
    decisions: Decisions,
    refusal: exceptions.AuthError,
    when_denied: WhenDenied,
    identity: Identity,
    use_case: str,
) -> None:
    """Act on a refusal: log and return for `WhenDenied.SKIP`; log, publish it and raise it for
    `WhenDenied.RAISE` — the caller then answers `None` or leaves the hook's moment out."""
    if when_denied == WhenDenied.SKIP:
        logger.debug("access skipped", use_case=use_case, reason=str(refusal))
        return
    logger.warning(
        "access refused", subject=identity.subject, use_case=use_case, reason=str(refusal)
    )
    decisions.publish_refusal(identity, use_case, refusal)
    raise refusal


class BusGuard:
    """The interceptor one bus runs — built for the bus `on(...)` was given and for every
    generation `fresh()` makes of it, so each resolves its own handlers and reads its own
    context."""

    def __init__(self, access: Access, bus: "UseFramework") -> None:
        """The guard of `bus` for `access`, with an empty cache of declarations.

            BusGuard(auth, billing).__qualname__   ->  "AccessControl.guard[billing]"

        Context: the qualified name is set so the build log and introspection name the
        interceptor after the bus it guards.
        """
        self.access = access
        self.decisions = Decisions(access)
        self.bus = bus
        self._declared: WeakKeyDictionary[type, WeakKeyDictionary[type, Any]]
        self._declared = WeakKeyDictionary()
        """By Command, then by the handler answering it — a handler registered later with
        `replaces=` is read on its own; weak on both, so a use case loaded again lets its
        classes go."""
        self.__qualname__ = f"AccessControl.guard[{bus.name}]"
        """What the build log and introspection call the interceptor."""

    def find_declaration(self, command: type) -> AccessDeclaration | None:
        """What the handler answering `command` on this bus declared — read once per pair of
        Command and handler, then cached; `None` for no handler or nothing declared."""
        handler = self.bus.handler_of(command)
        if handler is None:
            return None
        by_handler = self._declared.setdefault(command, WeakKeyDictionary())
        if handler not in by_handler:
            by_handler[handler] = self.access.declarations.find(
                handler, self.bus.replaced_for(command)
            )
        return by_handler[handler]

    def _refusals(
        self, command: type, identity: Identity, dto: Any, context: Any
    ) -> Iterator[tuple[exceptions.AuthError, WhenDenied]]:
        """Every reason `identity` may not run `command`, in the order the guard asks them, each
        with what its refusal does — asked lazily, so the first stops the rest.

        1. A tenant the caller named that is not the identity's own: always raised.
        2. What the handler declared, asked of the providers: as it says (`when_denied`).
        Final: the `every_call` checks, open or declared: always raised — `SKIP` speaks for
        the declaration only, never for the project's interceptor.
        """
        tenant = _tenant_refusal(identity, context)
        if tenant is not None:
            yield tenant, WhenDenied.RAISE
        declaration = self.find_declaration(command)
        if declaration is not None:
            refused = self.decisions.evaluate_declaration(
                identity, declaration, command.__name__, None, context, dto
            )
            if refused is not None:
                yield refused, declaration.when_denied
        intercepted = self.decisions.evaluate_every_call(
            identity, command.__name__, dto, context, command_name(command)
        )
        if intercepted is not None:
            yield intercepted, WhenDenied.RAISE

    def find_refusal(
        self, command: type, identity: Identity, dto: Any = None
    ) -> exceptions.AuthError | None:
        """Why `identity` may not run `command` on this bus — `None` when it may: the decision
        the guard runs on, asked without running — `AccessControl.may_run`. Disabled, it may.
        Nothing of the context is changed."""
        if not self.access.enabled:
            return None
        context = self.bus.current_context()
        found = next(self._refusals(command, identity, dto, context), None)
        return None if found is None else found[0]

    def __call__(self, dto: Any, call_next: Callable[[Any], Any]) -> Any:
        """Decide whether the identity in play may run the use case `dto` enters, and run it.

        1. Who acts and where: the identity in play over what its caller sent
           (`_bind_identity_to_context`) — a fact the use case records names its `actor`, and
           nobody verified owns nothing the caller named. Disabled, an anonymous caller's
           context is left as it came.
        2. Disabled: it runs.
        3. The first refusal (`_refusals`): skipped (`None`) when its declaration said
           `WhenDenied.SKIP`, else raised.
        Final: it runs, this `AccessControl` in play.
        """
        identity = current_identity()
        context = use_context()
        if self.access.enabled or not identity.is_anonymous:
            _bind_identity_to_context(identity, context)
        if not self.access.enabled:
            return call_next(dto)
        command = type(dto)
        found = next(self._refusals(command, identity, dto, context), None)
        if found is not None:
            _reject(self.decisions, found[0], found[1], identity, command.__name__)
            return None
        with as_access_control(self.access):
            return call_next(dto)


class HookGuard:
    """The gate a guarded `Hooks` asks before each moment — handed the collection running the
    hook, so a replacement made in a combination inherits from what it replaces there."""

    def __init__(self, access: Access) -> None:
        """The gate of every collection `access` guards — one for all of them."""
        self.access = access
        self.decisions = Decisions(access)

    def find_declaration(self, hooks: "Hooks", hook: type) -> AccessDeclaration | None:
        """What `hook` declared — or, when nothing, what the hooks it replaces there did."""
        return self.access.declarations.find(
            hook, hooks.replacements.get(command_name(hook), ())
        )

    def __call__(self, hooks: "Hooks", hook: type["Hook"], moment: str, record: Any) -> bool:
        """Whether `hook` may run its `moment` for the identity in play.

        Context: `False` leaves the moment out; raising refuses the write it is part of. The
        record is the resource asked about — except at `after_search`, where there is no
        single record yet.

        1. Disabled, or nothing declared: `True`.
        2. The refusal of the declaration, with the record and the executing context — the
           system passes.
        Final: `True` when none; else skipped (`False`) or raised, as the declaration says.
        """
        if not self.access.enabled:
            return True
        declaration = self.find_declaration(hooks, hook)
        if declaration is None:
            return True
        identity = current_identity()
        use_case = f"{hook.__name__}.{moment}"
        resource = None if moment == "after_search" else record
        refusal = self.decisions.evaluate_declaration(
            identity, declaration, use_case, resource, context_scopes.live()
        )
        if refusal is None:
            return True
        _reject(self.decisions, refusal, declaration.when_denied, identity, use_case)
        return False
