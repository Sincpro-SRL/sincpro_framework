"""What the services of access control read of the `AccessControl` above them — `Access` — and
the declarations it holds — `Declarations`.

    declarations.add(IssueInvoice, AccessDeclaration(DeclarationKind.REQUIRES, (ISSUE,)))
    declarations.find(IssueInvoice)   ->  AccessDeclaration(DeclarationKind.REQUIRES, (ISSUE,))

Context: one port, so no service imports the facade; the facade answers it as it is, read on
every call — a provider added to it afterwards is asked too. It holds data only: the decisions
are the services', built over it.
"""

from collections.abc import Callable, Iterable
from typing import Protocol
from weakref import WeakKeyDictionary, WeakValueDictionary

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.events import EventPublisher
from sincpro_framework.auth.common.domain.permissions import (
    AccessDeclaration,
    DeclarationKind,
    Rule,
    command_name,
)
from sincpro_framework.auth.common.domain.provider import AuthProvider


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
            @auth.requires(B)          ->  AccessDeclaration(DeclarationKind.REQUIRES, (B, A))
            class IssueInvoice(Feature): ...

        Context: stacked decorators on one class add up; the same class declared two
        different ways is refused, since only one of them could hold.

        1. Nothing declared yet: the declaration as given.
        2. Two `requires`: their requirements joined — refused when they disagree on
           `when_denied`.
        3. Any other pair (`authenticated` and `requires`, twice `authenticated`): refused.
        Final: stored by the class and by its dotted name; `cls` is answered unchanged.
        """
        current = self._by_class.get(cls)
        if current is None:
            merged = declaration
        elif current.kind == declaration.kind == DeclarationKind.REQUIRES:
            if current.when_denied != declaration.when_denied:
                raise exceptions.ProgrammingError(
                    f"{cls.__name__} requires with when_denied={current.when_denied} and "
                    f"{declaration.when_denied}: declare one"
                )
            merged = AccessDeclaration(
                DeclarationKind.REQUIRES,
                current.requirements + declaration.requirements,
                current.when_denied,
            )
        else:
            raise exceptions.ProgrammingError(
                f"{cls.__name__} is declared both {current} and {declaration}"
            )
        self._by_class[cls] = merged
        self._by_name[command_name(cls)] = cls
        return cls

    def find(self, cls: type, replaced: Iterable[str] = ()) -> AccessDeclaration | None:
        """What `cls` declared — or, when it declared nothing, what the newest of the handlers
        it replaced did, `replaced` given oldest first.

            declarations.find(IssueInvoice)    ->  AccessDeclaration(DeclarationKind.REQUIRES, (ISSUE,))
            declarations.find(IssueV3, ["billing.IssueInvoice", "billing.IssueInvoiceV2"])
                ->  what IssueInvoiceV2 declared — or IssueInvoice's, when V2 declared nothing
            declarations.find(QueryHealth)     ->  None

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

    def classes(self) -> list[type]:
        """Every class declared and still alive — the newest under each dotted name."""
        return list(self._by_name.values())


class Access(Protocol):
    """What every service of access control reads of the `AccessControl` above it — what it
    holds and what was declared."""

    enabled: bool
    declarations: Declarations
    providers: tuple[AuthProvider, ...]
    clock: Callable[[], float]
    publisher: EventPublisher | None
    checks: list[Rule]
    """What `every_call` registered."""

    @property
    def needs_body(self) -> bool:
        """Whether a provider reads the request's raw body — a webhook signature."""
        ...

    def guarded_use_cases(self) -> set[str]:
        """The dotted names of every Command of every bus guarded."""
        ...
