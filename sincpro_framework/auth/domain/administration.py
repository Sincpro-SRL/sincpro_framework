"""Who may change who holds what — the rule every administration of auth applies before it
writes: assigning a role, defining one, requiring more of a use case.

    AdministrationBounds()                                   # the default: bounded
    AdministrationBounds(superadmin="platform.grants.manage")
    AdministrationBounds(enforced=False)                     # every administrator trusted

| Who acts | May |
|---|---|
| the system (`security.as_system`), a holder of `superadmin` | anything, any tenant, and what is global |
| anyone else known | only in their own tenant, and only give what they hold themselves |
| nobody known | nothing |

Context: without the bound, whoever may assign or define a role may give themselves anything —
define a role granting every permission, assign it to themselves. Enterprise IAM bounds a grantor
by what it holds (Keycloak's fine-grained admin, AWS permission boundaries); a tenant's
administrator never reaches another tenant, nor what is shared by all of them.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.identity import Identity, IdentityKind


@dataclass(frozen=True)
class AdministrationBounds:
    """The bound on whoever administers — enforced unless said otherwise."""

    enforced: bool = True
    superadmin: str | None = None
    """A permission whose holders are not bounded — the platform's own administrators."""

    def unbounded(self, actor: Identity) -> bool:
        """Whether `actor` may do anything: the bound off, the system, or a superadmin."""
        return (
            not self.enforced
            or actor.kind == IdentityKind.SYSTEM
            or (self.superadmin is not None and self.superadmin in actor.permissions)
        )

    def in_scope(self, actor: Identity, tenant: str | None, what: str) -> None:
        """`PermissionDenied` unless `actor` may administer `tenant` — `None` is what every
        tenant shares, the platform's alone.

        1. Unbounded: allowed.
        2. Nobody known: `Unauthenticated`.
        3. What is global, or another tenant than the actor's own: refused.
        """
        if self.unbounded(actor):
            return
        if actor.is_anonymous:
            raise exceptions.Unauthenticated(f"authenticate to {what}", anonymous=True)
        if tenant is None:
            raise exceptions.PermissionDenied(
                actor.subject, what, f"{what} is the platform's: it applies to every tenant"
            )
        if actor.tenant != tenant:
            raise exceptions.PermissionDenied(
                actor.subject, what, f"{what} in tenant {tenant}, not {actor.tenant}"
            )

    def holding(self, actor: Identity, permissions: Iterable[str], what: str) -> None:
        """`PermissionDenied` unless `actor` holds every one of `permissions` — what it may
        give. Read off the identity as resolved: the permissions its provider answered."""
        if self.unbounded(actor):
            return
        missing = sorted(set(permissions) - actor.permissions)
        if missing:
            raise exceptions.PermissionDenied(
                actor.subject,
                what,
                f"{what} gives {', '.join(missing)}, which {actor.subject} does not hold",
            )
