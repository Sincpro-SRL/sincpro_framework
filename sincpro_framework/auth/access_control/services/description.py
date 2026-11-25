"""What an `AccessControl` says about itself — read off the buses and collections it guards,
without building a bus nor closing a collection.

Context: a use case is named by its Command's dotted name, and a replaced handler answers what
its replacement declared — or, declaring nothing, what the one it replaced did. Calling any of
these early changes nothing; it only sees less.
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING

from sincpro_framework.auth.access_control.services.guard import Declarations
from sincpro_framework.auth.common.domain.permissions import (
    OPEN,
    AccessDeclaration,
    AccessDescription,
    Rule,
    command_name,
)
from sincpro_framework.auth.common.domain.provider import AuthProvider

if TYPE_CHECKING:
    from sincpro_framework.ddd.repositories.hooks import Hooks
    from sincpro_framework.use_bus import UseFramework


def declared_use_cases(
    declarations: Declarations, buses: Iterable["UseFramework"]
) -> dict[str, AccessDeclaration | None]:
    """What each use case of `buses` declared, by its Command's dotted name — `None` for one
    that declared nothing; a replaced use case answers its replacement's."""
    declared: dict[str, AccessDeclaration | None] = {}
    for bus in buses:
        for command, handler in bus.handlers().items():
            declared[command_name(command)] = declarations.of(
                handler, bus.replaced_for(command)
            )
    return declared


def requirements_of(
    declarations: Declarations,
    buses: Iterable["UseFramework"],
    cls: type,
    bus: "UseFramework | None" = None,
) -> AccessDeclaration | None:
    """What a Command's use case, a handler or a hook declared — `None` for nothing.

        requirements_of(declarations, buses, CommandIssueInvoice)  ->  AccessDeclaration(...)
        requirements_of(declarations, buses, QueryHealth)          ->  None

    1. With `bus`: the handler that bus answers `cls` with.
    2. A Command a guarded bus runs: its handler's declaration — its replacement's when
       replaced.
    Final: any other class, looked up as it is.
    """
    if bus is not None:
        handler = bus.handlers().get(cls)
        return None if handler is None else declarations.of(handler, bus.replaced_for(cls))
    for guarded in buses:
        handler = guarded.handlers().get(cls)
        if handler is not None:
            return declarations.of(handler, guarded.replaced_for(cls))
    return declarations.of(cls)


def problems(
    declarations: Declarations,
    buses: Iterable["UseFramework"],
    hooks: Iterable["Hooks"],
    enabled: bool,
) -> list[str]:
    """What would not work as declared.

        problems(...)   ->  []
        # disabled, and a class declared that no guarded bus runs
            ->  ["access control is disabled",
                 "billing.Orphan declares access but runs on no bus or hooks this ..."]

    1. Disabled: said first — at runtime nothing is checked; the rest is still listed.
    Final: a class declared that no guarded bus or collection runs — its declaration never
    holds.
    """
    running: set[type] = set()
    replaced: set[str] = set()
    for bus in buses:
        for command, handler in bus.handlers().items():
            running.add(handler)
            replaced.update(bus.replaced_for(command))
    for collection in hooks:
        running.update(collection.registered())
    orphans = []
    for cls in declarations.declared():
        name = f"{cls.__module__}.{cls.__qualname__}"
        if cls not in running and name not in replaced:
            orphans.append(
                f"{name} declares access but runs on no bus or hooks this AccessControl guards"
            )
    return ([] if enabled else ["access control is disabled"]) + sorted(orphans)


def description(
    declarations: Declarations,
    buses: Iterable["UseFramework"],
    hooks: Iterable["Hooks"],
    enabled: bool,
    providers: Iterable[AuthProvider],
    checks: Iterable[Rule],
) -> AccessDescription:
    """Everything an `AccessControl` guards and how.

        description(...)
            ->  AccessDescription(enabled=True, providers=["odoo"],
                    use_cases={"billing.CommandIssueInvoice": "billing.invoice.issue",
                               "billing.QueryHealth": "open"},
                    hooks={"billing.hooks.NotifyCustomer": "authenticated"})

    Context: every use case is listed, `open` when it declared nothing — anyone may run it;
    only the hooks that declared something are.
    """
    return AccessDescription(
        enabled=enabled,
        providers=[one.name for one in providers],
        every_call=[one.name for one in checks],
        use_cases={
            name: str(declaration) if declaration else OPEN
            for name, declaration in declared_use_cases(declarations, buses).items()
        },
        hooks={
            f"{hook.__module__}.{hook.__qualname__}": str(declaration)
            for collection in hooks
            for hook in collection.registered()
            if (declaration := declarations.of(hook)) is not None
        },
    )
