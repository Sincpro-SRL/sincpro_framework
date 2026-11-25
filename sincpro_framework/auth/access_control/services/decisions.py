"""What an identity may do: a declaration's requirements, the `every_call` checks, and what a
handler asks by hand (`allows`, `check`, `permitted`, `scope_of`) — one decision, whoever asks.

Context: a refusal is answered, never raised here, so an `AnyOf` weighs its members and `allows`
says no without a traceback; the guard and the facade raise it. Who is asked about an identity
is its provider (`find_provider`); an identity opened by hand answers with its own permissions.
A permission is a `Permission` member or its value as text — a screen, a policy file.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sincpro_framework.auth.access_control.domain.access import Access
from sincpro_framework.auth.access_control.services.authentication import find_provider
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.events import SUBJECT, AccessRefused
from sincpro_framework.auth.common.domain.identity import Identity, IdentityKind
from sincpro_framework.auth.common.domain.permissions import (
    AccessCall,
    AccessDeclaration,
    AssuranceRequirement,
    ByField,
    Requirement,
    RoleRequirement,
    Rule,
    command_name,
)
from sincpro_framework.auth.common.infrastructure.event_publishing import publish_event
from sincpro_framework.auth.common.infrastructure.security_context import current_identity
from sincpro_framework.context import context_scopes

if TYPE_CHECKING:
    from sincpro_framework.ddd.criteria import Criteria

UNJUDGED_WITHOUT_COMMAND = (AttributeError, TypeError, LookupError)
"""What a rule that reads the Command raises when asked with none (`call.dto.owner` on `None`)
— refused as "cannot be judged". Anything else it raises — a store down (503), a bug — is let
through: a rule that could not be asked is never a refusal."""


class Decisions:
    """The decisions of one `AccessControl`."""

    def __init__(self, access: Access) -> None:
        self.access = access

    def _evaluate_permission(
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
        provider = find_provider(self.access.providers, identity)
        try:
            if provider is None:
                allowed = permission in identity.permissions
            else:
                allowed = provider.has_permission(identity, permission, resource, context)
        except exceptions.PermissionDenied as denied:
            return denied
        return None if allowed else exceptions.PermissionDenied(identity.subject, permission)

    def _evaluate_assurance(
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

    def _evaluate_rule(self, rule: Rule, call: AccessCall) -> exceptions.AuthError | None:
        """The refusal of a rule that does not hold — `PermissionDenied` naming it, or the
        `AuthError` its check raised to answer otherwise.

        1. The check holds: `None`.
        2. It raised an `AuthError`: that refusal.
        3. Asked with no Command (`may_run`, `allows`, a hook) and it failed reading one
           (`UNJUDGED_WITHOUT_COMMAND`): refused — what cannot be judged is never granted. A
           rule that may be judged without it says so:
           `lambda call: call.dto is None or call.dto.owner == ...`.
        Final: `PermissionDenied` naming the rule; anything else the check raised propagates.
        """
        try:
            if rule.check(call):
                return None
        except exceptions.AuthError as refused:
            return refused
        except UNJUDGED_WITHOUT_COMMAND:
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

    def evaluate_requirement(
        self,
        identity: Identity,
        requirement: str | Requirement,
        resource: Any,
        context: Mapping[str, Any] | None,
        dto: Any = None,
        use_case: str = "",
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
        if isinstance(requirement, str):
            return self._evaluate_permission(identity, str(requirement), resource, context)
        if isinstance(requirement, AssuranceRequirement):
            return self._evaluate_assurance(identity, requirement)
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
                use_case,
                dto,
                resource,
                dict(context or {}),
                "" if dto is None else command_name(type(dto)),
            )
            return self._evaluate_rule(requirement, call)
        if isinstance(requirement, ByField):
            chosen = requirement.every() if dto is None else requirement.chosen(dto)
            if chosen is None:
                value = getattr(dto, requirement.field, None)
                return exceptions.PermissionDenied(
                    identity.subject,
                    str(requirement),
                    f"{requirement.field}={value!r} names no requirement",
                )
            return self.evaluate_requirement(
                identity, chosen, resource, context, dto, use_case
            )
        refused: list[exceptions.AuthError] = []
        for one in requirement.members:
            denied = self.evaluate_requirement(
                identity, one, resource, context, dto, use_case
            )
            if denied is None:
                return None
            refused.append(denied)
        step_up = next(
            (one for one in refused if isinstance(one, exceptions.Unauthenticated)), None
        )
        if step_up is not None:
            return step_up
        reasons = [getattr(one, "reason", None) or str(one) for one in refused]
        provider = find_provider(self.access.providers, identity)
        permissions = [str(one) for one in requirement.members if isinstance(one, str)]
        worded = (
            provider.refusing_any(identity, permissions, reasons)
            if provider is not None and len(permissions) == len(requirement.members)
            else None
        )
        return exceptions.PermissionDenied(
            identity.subject, str(requirement), worded or " and ".join(reasons)
        )

    def _evaluate_caller(
        self,
        identity: Identity,
        action: str,
        evaluate: Callable[[], exceptions.AuthError | None],
    ) -> exceptions.AuthError | None:
        """The one exemption every requirement shares: the system passes; nobody is refused
        before any provider or rule is asked (`authenticate to <action>`); anyone else is
        answered by `evaluate`."""
        if identity.kind == IdentityKind.SYSTEM:
            return None
        if identity.is_anonymous:
            return exceptions.Unauthenticated(f"authenticate to {action}", anonymous=True)
        return evaluate()

    def evaluate_declaration(
        self,
        identity: Identity,
        declaration: AccessDeclaration,
        use_case: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
        dto: Any = None,
    ) -> exceptions.AuthError | None:
        """Why `identity` may not run what `declaration` guards — `None` when it may.

            evaluate_declaration(ANONYMOUS, AccessDeclaration(DeclarationKind.AUTHENTICATED),
                                 "IssueInvoice")
                ->  Unauthenticated("authenticate to run IssueInvoice")
            evaluate_declaration(user, AccessDeclaration(DeclarationKind.REQUIRES, (ISSUE,)),
                                 "IssueInvoice")
                ->  PermissionDenied("user:42", "billing.invoice.issue")

        Context: the system passes first; nobody is refused before any provider is asked; then
        each requirement in order, the first refused answering — a `by_field` one chosen by
        `dto`, the Command being run, or one among its permissions without it.
        """

        def first_refusal() -> exceptions.AuthError | None:
            for requirement in declaration.requirements:
                denied = self.evaluate_requirement(
                    identity, requirement, resource, context, dto, use_case
                )
                if denied is not None:
                    return denied
            return None

        return self._evaluate_caller(identity, f"run {use_case}", first_refusal)

    def evaluate_current(
        self, requirement: str | Requirement, resource: Any
    ) -> exceptions.AuthError | None:
        """What `allows` and `check` decide for the identity in play — as the guard would: the
        system passes, nobody is refused before any provider or rule is asked, then the
        requirement's own refusal."""
        identity = current_identity()
        return self._evaluate_caller(
            identity,
            str(requirement),
            lambda: self.evaluate_requirement(
                identity, requirement, resource, context_scopes.live()
            ),
        )

    def evaluate_every_call(
        self,
        identity: Identity,
        use_case: str,
        dto: Any,
        context: Mapping[str, Any],
        command: str = "",
    ) -> exceptions.AuthError | None:
        """Why one of the `every_call` checks refuses this call — `None` when every one lets it
        through, or the system acts. Nobody is asked about too: a check speaks for anyone."""
        if identity.kind == IdentityKind.SYSTEM:
            return None
        call = AccessCall(identity, use_case, dto, None, dict(context), command)
        for check in self.access.checks:
            refused = self._evaluate_rule(check, call)
            if refused is not None:
                return refused
        return None

    def publish_refusal(
        self, identity: Identity, use_case: str, refusal: exceptions.AuthError
    ) -> None:
        """Tell the publisher a call was refused — what the guard does before it raises."""
        body = refusal.refusal_body()
        publish_event(
            self.access.publisher,
            AccessRefused(
                entity_type=SUBJECT,
                entity_id=identity.subject,
                use_case=use_case,
                kind=refusal.failure_kind,
                reason=str(body.get("reason", "")),
                requirement=str(body.get("requirement", "")),
                tenant=identity.tenant,
            ),
        )

    def permitted(self, permission: str, resources: Sequence[Any]) -> list[bool]:
        """`allows` for many resources — in one call to the provider when it answers batches.
        A provider that refuses the whole batch with `PermissionDenied` refuses each.

            permitted(BillingPermission.READ, [invoice_1, invoice_2])
                ->  [True, False]      # as the provider answered each
            permitted("billing.read", [invoice_1])   # a permission's value, the same
            # the system  ->  [True, True]
            # opened by hand: holding READ  ->  [True, True]; not  ->  [False, False]
        """
        identity = current_identity()
        if identity.kind == IdentityKind.SYSTEM:
            return [True] * len(resources)
        provider = find_provider(self.access.providers, identity)
        if provider is None:
            return [str(permission) in identity.permissions] * len(resources)
        try:
            return provider.permitted(
                identity, str(permission), resources, context_scopes.live()
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
        refused = self._evaluate_caller(identity, f"read {aggregate.__name__}", lambda: None)
        if refused is not None:
            raise refused
        provider = (
            None
            if identity.kind == IdentityKind.SYSTEM
            else find_provider(self.access.providers, identity)
        )
        return None if provider is None else provider.scope(identity, aggregate)
