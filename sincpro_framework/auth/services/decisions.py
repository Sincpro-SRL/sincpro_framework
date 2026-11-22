"""What an identity may do: a declaration's requirements, the `every_call` checks, and what a
handler asks by hand (`allows`, `check`, `permitted`, `scope_of`) — one decision, whoever asks.

Context: a refusal is answered, never raised here, so an `AnyOf` weighs its members and `allows`
says no without a traceback; the guard and the facade raise it. Who is asked about an identity
is its provider (`vouched_by`); an identity opened by hand answers with its own permissions.
"""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.events import SUBJECT, AccessRefused
from sincpro_framework.auth.domain.identity import Identity, IdentityKind
from sincpro_framework.auth.domain.permissions import (
    AccessCall,
    AccessDeclaration,
    AnyOf,
    AssuranceRequirement,
    ByField,
    Permission,
    Requirement,
    RoleRequirement,
    Rule,
    command_name,
)
from sincpro_framework.auth.infrastructure.announcing import announce
from sincpro_framework.auth.infrastructure.security_context import current_identity
from sincpro_framework.auth.services.authentication import vouched_by
from sincpro_framework.auth.services.guard import Access
from sincpro_framework.context import context_scopes

if TYPE_CHECKING:
    from sincpro_framework.ddd.criteria import Criteria


class Decisions:
    """The decisions of one `AccessControl`."""

    def __init__(self, access: Access) -> None:
        self.access = access

    def _has(
        self,
        identity: Identity,
        permission: str,
        resource: Any,
        context: Mapping[str, Any] | None,
    ) -> exceptions.PermissionDenied | None:
        """The refusal of one permission — `None` when `identity` holds it.

        Context: a provider that raises `PermissionDenied` to say why has that very refusal
        answered.

        1. No provider vouched for it: its `permissions` are the answer.
        2. Else its provider's `has_permission`, with `resource` and `context`.
        Final: `None` when allowed, else the provider's `PermissionDenied` or a plain one.
        """
        provider = vouched_by(self.access.providers, identity)
        try:
            if provider is None:
                allowed = permission in identity.permissions
            else:
                allowed = provider.has_permission(identity, permission, resource, context)
        except exceptions.PermissionDenied as denied:
            return denied
        return None if allowed else exceptions.PermissionDenied(identity.subject, permission)

    def _assured(
        self, identity: Identity, requirement: AssuranceRequirement
    ) -> exceptions.Unauthenticated | None:
        """The step-up refusal when `identity` authenticated too weakly or too long ago.

            assurance(methods={"mfa"}), amr ["pwd"]   ->  Unauthenticated("mfa required",
                                                               step_up="mfa")
            assurance(max_age=5m), authenticated 1h ago  ->  Unauthenticated("a recent
                                                               authentication required", …)

        Context: judged on the identity itself — its `assurance` and `authenticated_at`, as
        the provider read them — with the `AccessControl`'s clock; no provider is asked.
        """
        reasons = []
        if requirement.acr and identity.assurance.level not in requirement.acr:
            reasons.append(f"authentication level {' or '.join(requirement.acr)} required")
        missing = requirement.methods - identity.assurance.methods
        if missing:
            reasons.append(f"{', '.join(sorted(missing))} required")
        if requirement.max_age is not None and (
            identity.authenticated_at is None
            or self.access.clock() - identity.authenticated_at.timestamp()
            > requirement.max_age.total_seconds()
        ):
            reasons.append("a recent authentication required")
        if not reasons:
            return None
        return exceptions.Unauthenticated(
            "; ".join(reasons),
            step_up=requirement.step_up,
            max_age=(
                None
                if requirement.max_age is None
                else int(requirement.max_age.total_seconds())
            ),
        )

    def _ruled(self, rule: Rule, call: AccessCall) -> exceptions.AuthError | None:
        """The refusal of a rule that does not hold — `PermissionDenied` naming it, or the
        `AuthError` its check raised to answer otherwise.

        1. The check holds: `None`.
        2. It raised an `AuthError`: that refusal.
        3. Asked with no Command (`may_run`, `allows`, a hook) and it failed reading one:
           refused — what cannot be judged is never granted. A rule that may be judged
           without it says so: `lambda call: call.dto is None or call.dto.owner == ...`.
        Final: `PermissionDenied` naming the rule.
        """
        try:
            if rule.check(call):
                return None
        except exceptions.AuthError as refused:
            return refused
        except Exception:
            if call.dto is not None:
                raise
            return exceptions.PermissionDenied(
                call.identity.subject,
                rule.name,
                f"{rule.name} cannot be judged without the Command",
            )
        return exceptions.PermissionDenied(
            call.identity.subject, rule.name, f"{rule.name} does not hold"
        )

    def denial(
        self,
        identity: Identity,
        requirement: Permission | Requirement,
        resource: Any,
        context: Mapping[str, Any] | None,
        dto: Any = None,
        what: str = "",
    ) -> exceptions.AuthError | None:
        """The refusal of a requirement — one permission, a role, a rule, or an `AnyOf` refused
        only when every member is.

            AnyOf((READ, ADMIN)), identity holds READ  ->  None
            AnyOf((READ, ADMIN)), identity holds none  ->  PermissionDenied("user:1",
                "billing.read or billing.admin", "lacks billing.read and lacks billing.admin")

        Context: the members of an `AnyOf` are asked in order, and the first that holds grants
        it — none after it is asked, so a remote provider is asked no more than needed and a
        rule placed after a role is never judged for whom the role admits. Refused, the reasons
        of every member are joined in order; when every member is a permission, a provider that
        words the whole refusal (`refusing_any`) is told it instead. A member refused with
        `Unauthenticated` — a step-up — answers for the whole: authenticating again may meet
        it, a 403 would not say so.
        """
        if isinstance(requirement, AssuranceRequirement):
            return self._assured(identity, requirement)
        if isinstance(requirement, RoleRequirement):
            if identity.roles & set(requirement.roles):
                return None
            return exceptions.PermissionDenied(
                identity.subject,
                str(requirement),
                f"holds no {' or '.join(requirement.roles)}",
            )
        if isinstance(requirement, Rule):
            call = AccessCall(
                identity,
                what,
                dto,
                resource,
                dict(context or {}),
                "" if dto is None else command_name(type(dto)),
            )
            return self._ruled(requirement, call)
        if isinstance(requirement, ByField):
            chosen = requirement.every() if dto is None else requirement.chosen(dto)
            if chosen is None:
                value = getattr(dto, requirement.field, None)
                return exceptions.PermissionDenied(
                    identity.subject,
                    str(requirement),
                    f"{requirement.field}={value!r} names no requirement",
                )
            return self.denial(identity, chosen, resource, context, dto, what)
        if not isinstance(requirement, AnyOf):
            return self._has(identity, requirement.value, resource, context)
        refused: list[exceptions.AuthError] = []
        for one in requirement.members:
            denied = self.denial(identity, one, resource, context, dto, what)
            if denied is None:
                return None
            refused.append(denied)
        step_up = next(
            (one for one in refused if isinstance(one, exceptions.Unauthenticated)), None
        )
        if step_up is not None:
            return step_up
        each = [getattr(one, "reason", None) or str(one) for one in refused]
        provider = vouched_by(self.access.providers, identity)
        permissions = [
            one.value for one in requirement.members if isinstance(one, Permission)
        ]
        said = (
            provider.refusing_any(identity, permissions, each)
            if provider is not None and len(permissions) == len(requirement.members)
            else None
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

            refusal(ANONYMOUS, AccessDeclaration(DeclarationKind.AUTHENTICATED), "IssueInvoice")
                ->  Unauthenticated("authenticate to run IssueInvoice")
            refusal(user, AccessDeclaration(DeclarationKind.REQUIRES, (ISSUE,)), "IssueInvoice")
                ->  PermissionDenied("user:42", "billing.invoice.issue")

        Context: the system passes first; nobody is refused before any provider is asked; then
        each requirement in order, the first refused answering — a `by_field` one chosen by
        `dto`, the Command being run, or one among its permissions without it.
        """
        if identity.kind == IdentityKind.SYSTEM:
            return None
        if identity.is_anonymous:
            return exceptions.Unauthenticated(f"authenticate to run {what}", anonymous=True)
        for requirement in declaration.requirements:
            denied = self.denial(identity, requirement, resource, context, dto, what)
            if denied is not None:
                return denied
        return None

    def in_play(
        self, requirement: Permission | Requirement, resource: Any
    ) -> exceptions.AuthError | None:
        """What `allows` and `check` decide for the identity in play — as the guard would: the
        system passes, nobody is refused before any provider or rule is asked, then the
        requirement's own refusal."""
        identity = current_identity()
        if identity.kind == IdentityKind.SYSTEM:
            return None
        if identity.is_anonymous:
            return exceptions.Unauthenticated(
                f"authenticate to {requirement}", anonymous=True
            )
        return self.denial(identity, requirement, resource, context_scopes.live())

    def every_call_refusal(
        self,
        identity: Identity,
        what: str,
        dto: Any,
        context: Mapping[str, Any],
        command: str = "",
    ) -> exceptions.AuthError | None:
        """Why one of the `every_call` checks refuses this call — `None` when every one lets it
        through, or the system acts."""
        if identity.kind == IdentityKind.SYSTEM:
            return None
        call = AccessCall(identity, what, dto, None, dict(context), command)
        for check in self.access.checks:
            refused = self._ruled(check, call)
            if refused is not None:
                return refused
        return None

    def refused(self, identity: Identity, what: str, refusal: exceptions.AuthError) -> None:
        """Tell the publisher a call was refused — what the guard does before it raises."""
        said = refusal.refusal_body()
        announce(
            self.access.publisher,
            AccessRefused(
                entity_type=SUBJECT,
                entity_id=identity.subject,
                use_case=what,
                kind=str(said.get("kind", "")),
                reason=str(said.get("reason", "")),
                requirement=str(said.get("requirement", "")),
                tenant=identity.tenant,
            ),
        )

    def permitted(self, permission: Permission, resources: Sequence[Any]) -> list[bool]:
        """`allows` for many resources — in one call to the provider when it answers batches.
        A provider that refuses the whole batch with `PermissionDenied` refuses each.

            permitted(BillingPermission.READ, [invoice_1, invoice_2])
                ->  [True, False]      # as the provider answered each
            # the system  ->  [True, True]
            # opened by hand: holding READ  ->  [True, True]; not  ->  [False, False]
        """
        identity = current_identity()
        if identity.kind == IdentityKind.SYSTEM:
            return [True] * len(resources)
        provider = vouched_by(self.access.providers, identity)
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
        them — `None` for every record.

            scope_of(Invoice)   # user:42 of tenant bo, its provider scoping
                ->  Criteria.where("tenant", "=", "bo")
            # the system, or an identity opened by hand  ->  None
            # nobody  ->  raises Unauthenticated("authenticate to read Invoice")

        Context: only two identities read every record — the system, and one no provider
        vouched for (a test, in-process code). Nobody calling is refused: a read scoped by
        identity with no identity would otherwise read everything. An identity another bounded
        context let in is scoped by the provider that let it in.
        """
        identity = current_identity()
        if identity.kind == IdentityKind.SYSTEM:
            return None
        if identity.is_anonymous:
            raise exceptions.Unauthenticated(
                f"authenticate to read {aggregate.__name__}", anonymous=True
            )
        provider = vouched_by(self.access.providers, identity)
        return None if provider is None else provider.scope(identity, aggregate)
