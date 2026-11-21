"""What a use case or a hook may require: a permission of the bounded context's own, one among
several, a role, a rule of the project's own, and what happens when it is missing.

    class BillingPermission(Permission):
        ISSUE_INVOICE = "billing.invoice.issue"

    RoleRequirement(("billing.manager",))             Odoo's `groups=` — a role held
    Rule("its own invoice", lambda call: call.dto.owner == call.identity.subject)

Context: the member is for code — "find usages" shows every handler that requires it, and
renaming it is a safe refactor; the value is for the wire — tokens, the IdP, `describe()` carry
it, so changing it is a breaking change. The base is empty on purpose: Python lets an enum with
no members be extended.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import Any, Literal

from sincpro_framework.auth.domain.identity import Identity


class Permission(StrEnum):
    """Written `context.aggregate.action`: `billing.invoice.issue`."""


class WhenDenied(StrEnum):
    """What a guarded use case or hook does when the identity lacks what it requires."""

    RAISE = "raise"
    """`PermissionDenied`, or `Unauthenticated` when nobody is calling."""
    SKIP = "skip"
    """It does not run: a use case answers `None`, a hook's moment is left out — an optional
    step of an orchestration."""


@dataclass(frozen=True)
class AnyOf:
    """Met when one of its members is — `auth.any_of(...)`: permissions, roles, rules, any
    requirement — "its owner, or an admin".

        AnyOf((Rule("its owner", is_owner), RoleRequirement(("billing.admin",))))
    """

    members: "tuple[Requirement, ...]"

    def __str__(self) -> str:
        """Its members joined by `or` — `billing.read or billing.admin`."""
        return " or ".join(str(one) for one in self.members)


@dataclass(frozen=True)
class ByField:
    """Met as the requirement the Command's own `field` names — `auth.by_field(...)`: what a
    use case needs depends on what it was asked. Each value names any requirement — a
    permission, a role, a rule, an `any_of`.

        ByField("direction", (("inbound", RECEIVE), ("outbound", PAY)))
            CommandRegisterPayment(direction="outbound")   ->  needs PAY
            no Command — a catalog asking                  ->  RECEIVE or PAY
    """

    field: str
    permissions: "tuple[tuple[object, Requirement], ...]"

    def chosen(self, dto: object) -> "Requirement | None":
        """The requirement `dto`'s field names — `None` when its value names none."""
        value = getattr(dto, self.field, None)
        return next((needed for one, needed in self.permissions if one == value), None)

    def every(self) -> AnyOf:
        """One among every requirement it may name — what is asked with no Command at hand.
        Two rules are the same only when they are the same object: rules compare by name, and
        two of one name may check different things."""
        found: list[Requirement] = []
        for _, needed in self.permissions:
            for one in needed.members if isinstance(needed, AnyOf) else (needed,):
                seen = (
                    any(one is held for held in found)
                    if isinstance(one, Rule)
                    else one in found
                )
                if not seen:
                    found.append(one)
        return AnyOf(tuple(found))

    def __str__(self) -> str:
        """`direction: inbound → finance.receive, outbound → finance.pay`."""
        named = ", ".join(f"{value} → {needed}" for value, needed in self.permissions)
        return f"{self.field}: {named}"


@dataclass(frozen=True)
class AssuranceRequirement:
    """Met when the caller authenticated strongly enough, recently enough — `auth.assurance(...)`:
    a step-up (RFC 9470). Refused with `Unauthenticated(step_up=…)`, so the client authenticates
    again as asked and comes back.

        AssuranceRequirement(methods=frozenset({"mfa"}), max_age=timedelta(minutes=5))
            an identity whose amr lacks mfa, or who authenticated an hour ago  ->  refused
    """

    acr: tuple[str, ...] = ()
    """One of these authentication context classes (`acr`), when named."""
    methods: frozenset[str] = field(default_factory=frozenset)
    """Every one of these methods (`amr`: `mfa`, `otp`, `hwk`…)."""
    max_age: timedelta | None = None
    """Authenticated at most this long ago (`auth_time`)."""

    @property
    def step_up(self) -> str:
        """What the client has to come back with — the classes asked, else the methods, else a
        fresh authentication."""
        if self.acr:
            return " ".join(self.acr)
        if self.methods:
            return " ".join(sorted(self.methods))
        return "reauthenticate"

    def __str__(self) -> str:
        """`assurance(mfa, 5m)` — how `describe()` and the logs say it."""
        said = [*self.acr, *sorted(self.methods)]
        if self.max_age is not None:
            said.append(f"{int(self.max_age.total_seconds() // 60)}m")
        return f"assurance({', '.join(said)})"


@dataclass(frozen=True)
class AccessCall:
    """What a rule is asked about: who calls, which use case, with what, on what — and the
    execution's context, where the runtime says the rest (the tenant, the language, the flow,
    what a provider or an entrance put there).

        AccessCall(identity=ana, use_case="CommandApprove", dto=CommandApprove(...),
                   context={"tenant_id": "bo", ...})
    """

    identity: Identity
    use_case: str = ""
    """The DTO's name of the use case run — empty when a handler asks `allows` by hand."""
    dto: Any = None
    """The Command being run — `None` when nothing is run: `may_run` for a catalog, `allows`,
    a hook."""
    resource: Any = None
    """What a handler named in `allows(requirement, resource)` / `check(...)` — `None` when the
    guard asks before a use case runs: there, what is acted on is the Command, `dto`."""
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoleRequirement:
    """Met when the identity holds one of `roles` — `auth.role(...)`: Odoo's `groups=`. Read off
    the roles the identity carries, which a `GrantResolver` fills with their closure — a manager
    holds what implies it.

        RoleRequirement(("billing.manager", "billing.admin"))   an accountant  ->  refused
    """

    roles: tuple[str, ...]

    def __str__(self) -> str:
        """`role billing.manager or role billing.admin` — each named a role, so beside
        permissions in an `any_of` none reads as one."""
        return " or ".join(f"role {one}" for one in self.roles)


@dataclass(frozen=True)
class Rule:
    """Met when `check` says so of the call — `auth.rule(...)`: whatever the project decides
    with the identity, the Command, the resource or the context. `check` may raise an
    `AuthError` of its own (a step-up, `Unauthenticated`) to answer otherwise than 403.

        Rule("its own invoice", lambda call: call.dto.owner == call.identity.subject)
    """

    name: str
    check: Callable[[AccessCall], bool] = field(compare=False)

    def __str__(self) -> str:
        """Its name — what `describe()`, a refusal and the documents say."""
        return self.name


type Requirement = Permission | AnyOf | ByField | AssuranceRequirement | RoleRequirement | Rule


OPEN = "open"
"""How a use case that declares nothing is described — in `describe()`, the manifest, OpenAPI
and the logs: anyone may run it."""


@dataclass(frozen=True)
class AccessDeclaration:
    """What a use case or a hook declared: `authenticated`, or the requirements it needs, and
    what it does when they are missing. Declaring nothing is open — anyone may run it."""

    kind: Literal["authenticated", "requires"]
    requirements: tuple[Requirement, ...] = ()
    when_denied: WhenDenied = WhenDenied.RAISE

    def __str__(self) -> str:
        """How `describe()` and the logs say it.

        AccessDeclaration("authenticated")                            ->  "authenticated"
        AccessDeclaration("requires", (A, AnyOf((B, C))))             ->  "a and b or c"
        AccessDeclaration("requires", (A,), WhenDenied.SKIP)          ->  "a (else skipped)"
        """
        if self.kind != "requires":
            return self.kind
        needed = " and ".join(str(one) for one in self.requirements)
        return needed if self.when_denied == WhenDenied.RAISE else f"{needed} (else skipped)"
