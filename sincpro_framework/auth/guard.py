"""The guard: what `AccessControl` runs around a use case — the outermost interceptor of a bus,
and of every generation `fresh()` makes of it — and before a hook's moment — a gate of its
`Hooks`, and of every copy or combination made of it.

Context: the decorators only mark classes; the marks are read here, at a use case's or a hook's
first run, so a handler registered after `on(...)` is guarded all the same. A mark belongs to the
class object, not to its name: a use case stored as source and loaded again is a new class, and
declares anew. A `replaces=` handler or hook that declares nothing inherits the declaration of the
one it replaces — replacing never opens a hole. A use case that declares nothing runs unchecked,
unless the `AccessControl` is strict and the use case was entered from outside; a nested one
inherits the decision of the use case that was.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from weakref import WeakKeyDictionary, WeakValueDictionary

from sincpro_framework.auth.domain import (
    AuthError,
    Identity,
    IdentityKind,
    PermissionDenied,
    Requirement,
    Unauthenticated,
    WhenDenied,
)
from sincpro_framework.auth.security_context import _decided, current_identity
from sincpro_framework.context.mixin import executing_context
from sincpro_framework.exceptions import ExtensionRefused
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.auth.access_control import AccessControl
    from sincpro_framework.ddd.repositories.hooks import Hook, Hooks
    from sincpro_framework.use_bus import UseFramework

GUARD_SEQUENCE = -(10**9)
"""Lower than any a project writes: the guard wraps every other interceptor of a bus, so a
cached or retried answer is never given without asking."""


def qualified(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


@dataclass(frozen=True)
class Declaration:
    """What a use case or a hook declared: `public`, `authenticated`, or the requirements it
    needs, and what it does when they are missing."""

    kind: Literal["public", "authenticated", "requires"]
    requirements: tuple[Requirement, ...] = ()
    when_denied: WhenDenied = WhenDenied.RAISE

    def __str__(self) -> str:
        if self.kind != "requires":
            return self.kind
        needed = " and ".join(str(one) for one in self.requirements)
        return needed if self.when_denied == WhenDenied.RAISE else f"{needed} (else skipped)"


class Declarations:
    """Every declaration, by the class it marks — never written onto it — and the latest class
    declared under each name, which is how a replaced handler named by `module.Class` is found.
    Weak on both sides: a use case loaded again lets its old class go."""

    def __init__(self) -> None:
        self._by_class: WeakKeyDictionary[type, Declaration] = WeakKeyDictionary()
        self._by_name: WeakValueDictionary[str, type] = WeakValueDictionary()

    def add[T: type](self, cls: T, declaration: Declaration) -> T:
        """Context: stacked decorators on one class add up; the same class declared two
        different ways is refused, since only one of them could hold."""
        held = self._by_class.get(cls)
        if held is None:
            merged = declaration
        elif held.kind == declaration.kind == "requires":
            if held.when_denied != declaration.when_denied:
                raise ExtensionRefused(
                    f"{cls.__name__} requires with when_denied={held.when_denied} and "
                    f"{declaration.when_denied}: declare one"
                )
            merged = Declaration(
                "requires", held.requirements + declaration.requirements, held.when_denied
            )
        else:
            raise ExtensionRefused(
                f"{cls.__name__} is declared both {held} and {declaration}"
            )
        self._by_class[cls] = merged
        self._by_name[qualified(cls)] = cls
        return cls

    def of(self, cls: type, replaced: Iterable[str] = ()) -> Declaration | None:
        """What `cls` declared — or, when it declared nothing, what the newest of the handlers
        it replaced did, `replaced` given oldest first."""
        own = self._by_class.get(cls)
        if own is not None:
            return own
        for name in reversed(tuple(replaced)):
            found = self._by_name.get(name)
            if found is not None and found in self._by_class:
                return self._by_class[found]
        return None

    def declared(self) -> list[type]:
        return list(self._by_name.values())


def _refused_or_skipped(
    refusal: AuthError, declaration: Declaration, identity: Identity, what: str
) -> None:
    if declaration.when_denied == WhenDenied.SKIP:
        logger.info(f"access: {what} skipped — {refusal}")
        return
    logger.warning(f"access refused: {identity.subject} → {what} — {refusal}")
    raise refusal


class BusGuard:
    """The interceptor one bus runs — built for the bus `on(...)` was given and for every
    generation `fresh()` makes of it, so each resolves its own handlers and reads its own
    context."""

    def __init__(self, access: "AccessControl[Any]", bus: "UseFramework") -> None:
        self.access = access
        self.bus = bus
        self._declared: dict[type, Declaration | None] = {}
        self.__qualname__ = f"AccessControl.guard[{bus.name}]"
        """What the build log and introspection call the interceptor."""

    def declaration_of(self, command: type) -> Declaration | None:
        if command not in self._declared:
            handler = self.bus.handler_of(command)
            self._declared[command] = (
                None
                if handler is None
                else self.access.declarations.of(handler, self.bus.replaced_for(command))
            )
        return self._declared[command]

    def _decided_run(self, command: type, call_next: Callable[[Any], Any], dto: Any) -> Any:
        token = _decided.set(command.__name__)
        try:
            return call_next(dto)
        finally:
            _decided.reset(token)

    def _undeclared(self, command: type, identity: Identity) -> None:
        """Context: refused only when strict and entered from outside — nobody calling is asked
        to authenticate, a known identity is told the use case declares nothing."""
        entered_by = _decided.get()
        if entered_by is not None:
            logger.debug(f"access: {command.__name__} inherits the decision of {entered_by}")
            return
        if not self.access.strict:
            return
        refusal: AuthError = (
            Unauthenticated(f"authenticate to run {command.__name__}")
            if identity.is_anonymous
            else PermissionDenied(
                identity.subject, command.__name__, f"{command.__name__} declares no access"
            )
        )
        logger.warning(f"access refused: {identity.subject} → {command.__name__} — {refusal}")
        raise refusal

    def __call__(self, dto: Any, call_next: Callable[[Any], Any]) -> Any:
        """1. Disabled, or the system acting: it runs.
        2. Declared nothing: it runs, unless strict and entered from outside.
        3. Final: declared something the identity lacks — refused, or skipped (`None`) when it
           said `WhenDenied.SKIP`; else it runs, and a use case it calls inherits the decision.
        """
        if not self.access.enabled:
            return call_next(dto)
        command = type(dto)
        identity = current_identity()
        declaration = (
            None if identity.kind == IdentityKind.SYSTEM else self.declaration_of(command)
        )
        if identity.kind != IdentityKind.SYSTEM and declaration is None:
            self._undeclared(command, identity)
        elif declaration is not None:
            refusal = self.access.refusal(
                identity, declaration, command.__name__, None, self.bus.current_context()
            )
            if refusal is not None:
                _refused_or_skipped(refusal, declaration, identity, command.__name__)
                return None
        return self._decided_run(command, call_next, dto)


class HookGuard:
    """The gate a guarded `Hooks` asks before each moment — handed the collection running the
    hook, so a replacement made in a combination inherits from what it replaces there."""

    def __init__(self, access: "AccessControl[Any]") -> None:
        self.access = access

    def declaration_of(self, hooks: "Hooks", hook: type) -> Declaration | None:
        return self.access.declarations.of(hook, hooks.replacements.get(qualified(hook), ()))

    def __call__(self, hooks: "Hooks", hook: type["Hook"], moment: str, record: Any) -> bool:
        """`False` leaves the moment out; raising refuses the write it is part of."""
        if not self.access.enabled:
            return True
        declaration = self.declaration_of(hooks, hook)
        identity = current_identity()
        if declaration is None or identity.kind == IdentityKind.SYSTEM:
            return True
        what = f"{hook.__name__}.{moment}"
        resource = None if moment == "after_search" else record
        refusal = self.access.refusal(
            identity, declaration, what, resource, executing_context()
        )
        if refusal is None:
            return True
        _refused_or_skipped(refusal, declaration, identity, what)
        return False
