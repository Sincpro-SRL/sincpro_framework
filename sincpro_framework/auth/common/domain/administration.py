"""Who may administer what — the policy every administration of auth asks before it writes:
assigning a role, defining one, requiring more of a use case.

    BoundedAdministration()                                   # the default
    BoundedAdministration(superadmin="platform.grants.manage")
    TrustedAdministration()                                   # every administrator trusted

| Who acts | `BoundedAdministration` lets them |
|---|---|
| the system (`security.as_system`), a holder of `superadmin` | anything, in any tenant, and what every tenant shares |
| anyone else known | only in their own tenant, never on themselves, and only give or take what they hold |
| nobody known | nothing |

Context: without a bound, whoever may assign or define a role gives themselves anything — a role
granting every permission, assigned to themselves; a weaker administrator strips a stronger one.
Enterprise IAM bounds a grantor by what it holds (Keycloak's fine-grained admin, AWS permission
boundaries); a tenant's administrator never reaches another tenant, nor what all of them share.
A project with rules of its own implements `AdministrationPolicy`.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable

from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.identity import Identity, IdentityKind


class AdministrationPolicy(ABC):
    """The port: whether who acts may administer a scope, a subject, what they would give.

    Context: a template — whoever is `is_unrestricted` passes every rule, nobody known passes none
    (`Unauthenticated`), and anyone else is asked the policy's own rules, each a no-op until
    overridden: `_validate_tenant`, `_validate_subject`, `_validate_holds`.
    """

    @abstractmethod
    def is_unrestricted(self, actor: Identity) -> bool:
        """Whether `actor` may do anything — no further rule is asked of them."""

    def _is_bounded(self, actor: Identity, action: str) -> bool:
        """Whether the policy's rules are asked of `actor` — `False` for whom is unbounded;
        `Unauthenticated` for nobody known: whoever administers is somebody."""
        if self.is_unrestricted(actor):
            return False
        if actor.is_anonymous:
            raise exceptions.Unauthenticated(f"authenticate to {action}", anonymous=True)
        return True

    def _validate_tenant(self, actor: Identity, tenant: str | None, action: str) -> None:
        """Raise unless `actor` may administer `tenant` — nothing, unless overridden."""

    def _validate_subject(self, actor: Identity, subject: str, action: str) -> None:
        """Raise unless `actor` may administer what `subject` holds — nothing, unless
        overridden."""

    def _validate_holds(
        self, actor: Identity, action: str, permissions: Iterable[str], roles: Iterable[str]
    ) -> None:
        """Raise unless `actor` holds what it would give — nothing, unless overridden."""

    def validate_tenant(self, actor: Identity, tenant: str | None, action: str) -> None:
        """Raise unless `actor` may administer `tenant` — `None` is what every tenant shares."""
        if self._is_bounded(actor, action):
            self._validate_tenant(actor, tenant, action)

    def validate_subject(self, actor: Identity, subject: str, action: str) -> None:
        """Raise unless `actor` may administer what `subject` holds."""
        if self._is_bounded(actor, action):
            self._validate_subject(actor, subject, action)

    def validate_holds(
        self,
        actor: Identity,
        action: str,
        permissions: Iterable[str] = (),
        roles: Iterable[str] = (),
    ) -> None:
        """Raise unless `actor` holds every one of `permissions` and of `roles` — what it may
        give, take away or require."""
        if self._is_bounded(actor, action):
            self._validate_holds(actor, action, permissions, roles)


class BoundedAdministration(AdministrationPolicy):
    """The system and a `superadmin` anything; anyone else in their own tenant, never on
    themselves, and only with what they hold."""

    def __init__(self, superadmin: str | None = None) -> None:
        """`superadmin` — a permission whose holders are the platform's own administrators."""
        self.superadmin = superadmin

    def is_unrestricted(self, actor: Identity) -> bool:
        """The system, or a holder of `superadmin`."""
        return actor.kind == IdentityKind.SYSTEM or (
            self.superadmin is not None and self.superadmin in actor.permissions
        )

    def _validate_tenant(self, actor: Identity, tenant: str | None, action: str) -> None:
        """Their own tenant only; what every tenant shares is the platform's."""
        if tenant is None:
            raise exceptions.PermissionDenied(
                actor.subject,
                action,
                f"{action} is the platform's: it applies to every tenant",
            )
        if actor.tenant != tenant:
            raise exceptions.PermissionDenied(
                actor.subject, action, f"{action} in tenant {tenant}, not {actor.tenant}"
            )

    def _validate_subject(self, actor: Identity, subject: str, action: str) -> None:
        """Never on themselves: what they hold is somebody else's to change."""
        if actor.subject == subject:
            raise exceptions.PermissionDenied(
                actor.subject,
                action,
                f"{action} for {subject}: nobody administers themselves",
            )

    def _validate_holds(
        self, actor: Identity, action: str, permissions: Iterable[str], roles: Iterable[str]
    ) -> None:
        """Every permission and role named among the actor's own — roles as resolved, their
        closure."""
        missing = sorted(
            {*(set(permissions) - actor.permissions), *(set(roles) - actor.roles)}
        )
        if missing:
            raise exceptions.PermissionDenied(
                actor.subject,
                action,
                f"{action} needs {', '.join(missing)}, which {actor.subject} does not hold",
            )


class TrustedAdministration(AdministrationPolicy):
    """Whoever is known may administer anything — the administrators are trusted, as Odoo trusts
    its access-rights group. Nobody known still administers nothing."""

    def is_unrestricted(self, actor: Identity) -> bool:
        """Anyone known."""
        return not actor.is_anonymous
