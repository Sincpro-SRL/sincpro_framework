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

from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any, Protocol
from weakref import WeakKeyDictionary, WeakValueDictionary

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.identity import Identity, IdentityKind
from sincpro_framework.auth.domain.permissions import AccessDeclaration, WhenDenied
from sincpro_framework.auth.infrastructure.security_context import (
    _access_in_play,
    current_identity,
)
from sincpro_framework.context import ContextKey, context_scopes, use_context
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.ddd.repositories.hooks import Hook, Hooks
    from sincpro_framework.use_bus import UseFramework

GUARD_SEQUENCE = -(10**9)
"""Lower than any a project writes: the guard wraps every other interceptor of a bus, so a
cached or retried answer is never given without asking."""


class Declarations:
    """Every declaration, by the class it marks — never written onto it — and the latest class
    declared under each name, which is how a replaced handler named by `module.Class` is found.
    Weak on both sides: a use case loaded again lets its old class go."""

    def __init__(self) -> None:
        """Nothing declared yet."""
        self._by_class: WeakKeyDictionary[type, AccessDeclaration] = WeakKeyDictionary()
        self._by_name: WeakValueDictionary[str, type] = WeakValueDictionary()

    def add[T: type](self, cls: T, declaration: AccessDeclaration) -> T:
        """Mark `cls` with `declaration` and answer `cls` — what every decorator ends in.

            @auth.requires(A)
            @auth.requires(B)          ->  AccessDeclaration("requires", (B, A))
            class IssueInvoice(Feature): ...

        Context: stacked decorators on one class add up; the same class declared two
        different ways is refused, since only one of them could hold.

        1. Nothing held yet: the declaration as given.
        2. Two `requires`: their requirements joined — refused when they disagree on
           `when_denied`.
        3. Any other pair (`authenticated` and `requires`, twice `authenticated`): refused.
        Final: stored by the class and by its dotted name; `cls` is answered unchanged.
        """
        held = self._by_class.get(cls)
        if held is None:
            merged = declaration
        elif held.kind == declaration.kind == "requires":
            if held.when_denied != declaration.when_denied:
                raise exceptions.ProgrammingError(
                    f"{cls.__name__} requires with when_denied={held.when_denied} and "
                    f"{declaration.when_denied}: declare one"
                )
            merged = AccessDeclaration(
                "requires", held.requirements + declaration.requirements, held.when_denied
            )
        else:
            raise exceptions.ProgrammingError(
                f"{cls.__name__} is declared both {held} and {declaration}"
            )
        self._by_class[cls] = merged
        self._by_name[f"{cls.__module__}.{cls.__qualname__}"] = cls
        return cls

    def of(self, cls: type, replaced: Iterable[str] = ()) -> AccessDeclaration | None:
        """What `cls` declared — or, when it declared nothing, what the newest of the handlers
        it replaced did, `replaced` given oldest first.

            declarations.of(IssueInvoice)    ->  AccessDeclaration("requires", (ISSUE,))
            declarations.of(IssueV3, ["billing.IssueInvoice", "billing.IssueInvoiceV2"])
                ->  what IssueInvoiceV2 declared — or IssueInvoice's, when V2 declared nothing
            declarations.of(QueryHealth)     ->  None

        Context: a replaced handler is found by its dotted name, and only while its class is
        still declared.
        """
        own = self._by_class.get(cls)
        if own is not None:
            return own
        for name in reversed(tuple(replaced)):
            found = self._by_name.get(name)
            if found is not None and found in self._by_class:
                return self._by_class[found]
        return None

    def declared(self) -> list[type]:
        """Every class declared and still alive — the newest under each dotted name."""
        return list(self._by_name.values())


class Access(Protocol):
    """What a guard asks of the `AccessControl` that built it — whether it checks, what was
    declared, and the decision itself.

    Context: a port, so the guard below the entrypoint never imports the `AccessControl` above
    it; that one object answers it as it is.
    """

    enabled: bool
    declarations: Declarations

    def refusal(
        self,
        identity: Identity,
        declaration: AccessDeclaration,
        what: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
        dto: Any = None,
    ) -> exceptions.AuthError | None:
        """Why `identity` may not run what `declaration` guards — `None` when it may; `dto`, the
        Command being run, chooses a `by_field` requirement."""
        ...

    def every_call_refusal(
        self, identity: Identity, what: str, dto: Any, context: Mapping[str, Any]
    ) -> exceptions.AuthError | None:
        """Why one of the `every_call` checks refuses this call — `None` when none does."""
        ...


def _refused_or_skipped(
    refusal: exceptions.AuthError,
    declaration: AccessDeclaration,
    identity: Identity,
    what: str,
) -> None:
    """Act on a refusal as the declaration says: log and return for `WhenDenied.SKIP`, log
    and raise it for `WhenDenied.RAISE` — the caller then answers `None` or leaves the hook's
    moment out."""
    if declaration.when_denied == WhenDenied.SKIP:
        logger.info(f"access: {what} skipped — {refusal}")
        return
    logger.warning(f"access refused: {identity.subject} → {what} — {refusal}")
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
        self.bus = bus
        self._declared: dict[tuple[type, type | None], AccessDeclaration | None] = {}
        """By Command and the handler answering it — a handler registered later with `replaces=`
        is read on its own."""
        self.__qualname__ = f"AccessControl.guard[{bus.name}]"
        """What the build log and introspection call the interceptor."""

    def declaration_of(self, command: type) -> AccessDeclaration | None:
        """What the handler answering `command` on this bus declared — read once per pair of
        Command and handler, then cached; `None` for no handler or nothing declared."""
        handler = self.bus.handler_of(command)
        if (command, handler) not in self._declared:
            self._declared[command, handler] = (
                None
                if handler is None
                else self.access.declarations.of(handler, self.bus.replaced_for(command))
            )
        return self._declared[command, handler]

    def _run_in_play(self, call_next: Callable[[Any], Any], dto: Any) -> Any:
        """Run the use case with this `AccessControl` in play — what `security.identity_headers`
        asks first for the credentials of a call onwards; undone once it answers."""
        guarding = _access_in_play.set(self.access)
        try:
            return call_next(dto)
        finally:
            _access_in_play.reset(guarding)

    def refusal_for(
        self, command: type, identity: Identity, dto: Any = None
    ) -> exceptions.AuthError | None:
        """Why `identity` may not run `command` on this bus — `None` when it may: the one
        decision the guard runs on, and what `AccessControl.may_run` asks without running.

        1. Disabled, or the system acting: it may.
        2. Declared something: what it requires, asked of the providers — the first refusal.
        Final: the `every_call` checks, open or declared — the first refusal; else it may.
        """
        if not self.access.enabled or identity.kind == IdentityKind.SYSTEM:
            return None
        declaration = self.declaration_of(command)
        context = self.bus.current_context()
        if declaration is not None:
            refused = self.access.refusal(
                identity, declaration, command.__name__, None, context, dto
            )
            if refused is not None:
                return refused
        return self.access.every_call_refusal(identity, command.__name__, dto, context)

    def __call__(self, dto: Any, call_next: Callable[[Any], Any]) -> Any:
        """Decide whether the identity in play may run the use case `dto` enters, and run it.

        1. Who acts: the identity in play, unless anonymous, is the execution's `user_id` when
           nothing said one — not a provider of the project's, not a scope —, so a fact the use
           case records names its `actor`.
        2. Disabled: it runs.
        3. Declared something the identity lacks: refused, or skipped (`None`) when it said
           `WhenDenied.SKIP`.
        4. An `every_call` check refuses: always raised — `SKIP` speaks for the declaration
           only, never for the project's interceptor.
        Final: it runs, this `AccessControl` in play.
        """
        identity = current_identity()
        context = use_context()
        if not identity.is_anonymous and context.get(ContextKey.USER_ID) is None:
            context.set(ContextKey.USER_ID, identity.subject)
        if not self.access.enabled:
            return call_next(dto)
        command = type(dto)
        if identity.kind == IdentityKind.SYSTEM:
            return self._run_in_play(call_next, dto)
        declaration = self.declaration_of(command)
        bus_context = self.bus.current_context()
        if declaration is not None:
            refused = self.access.refusal(
                identity, declaration, command.__name__, None, bus_context, dto
            )
            if refused is not None:
                _refused_or_skipped(refused, declaration, identity, command.__name__)
                return None
        intercepted = self.access.every_call_refusal(
            identity, command.__name__, dto, bus_context
        )
        if intercepted is not None:
            logger.warning(
                f"access refused: {identity.subject} → {command.__name__} — {intercepted}"
            )
            raise intercepted
        return self._run_in_play(call_next, dto)


class HookGuard:
    """The gate a guarded `Hooks` asks before each moment — handed the collection running the
    hook, so a replacement made in a combination inherits from what it replaces there."""

    def __init__(self, access: Access) -> None:
        """The gate of every collection `access` guards — one for all of them."""
        self.access = access

    def declaration_of(self, hooks: "Hooks", hook: type) -> AccessDeclaration | None:
        """What `hook` declared — or, when nothing, what the hooks it replaces there did."""
        return self.access.declarations.of(
            hook, hooks.replacements.get(f"{hook.__module__}.{hook.__qualname__}", ())
        )

    def __call__(self, hooks: "Hooks", hook: type["Hook"], moment: str, record: Any) -> bool:
        """Whether `hook` may run its `moment` for the identity in play.

        Context: `False` leaves the moment out; raising refuses the write it is part of. The
        record is the resource asked about — except at `after_search`, where there is no
        single record yet.

        1. Disabled, nothing declared, or the system acting: `True`.
        2. The refusal of the declaration, with the record and the executing context.
        Final: `True` when none; else skipped (`False`) or raised, as the declaration says.
        """
        if not self.access.enabled:
            return True
        declaration = self.declaration_of(hooks, hook)
        identity = current_identity()
        if declaration is None or identity.kind == IdentityKind.SYSTEM:
            return True
        what = f"{hook.__name__}.{moment}"
        resource = None if moment == "after_search" else record
        refusal = self.access.refusal(
            identity, declaration, what, resource, context_scopes.live()
        )
        if refusal is None:
            return True
        _refused_or_skipped(refusal, declaration, identity, what)
        return False
