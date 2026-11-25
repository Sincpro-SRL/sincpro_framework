"""`Revocations`: what was taken back before it ran out — a token, a session, every session a
subject had, and the permissions a subject held when its credential was issued.

    revocations.revoke_session("sid-7", until=session_ends)      a logout
    revocations.revoke_subject("user:42", until=now + longest)   "sign out everywhere"
    revocations.bump_permissions("user:42")                      a role taken away: the next
                                                                 call resolves them anew
    revocations.state("user:42", token_id="jti-1", session="sid-7").revocation_reason(issued_at)
        ->  "session revoked"   or None

Context: a JWT is checked by its signature and lives until it expires; this is what lets one be
refused earlier, with one read per call: `state` reads the credential's revocations and the
permission versions of its subject and its tenant at once (an implementation on
`CachingRevocations` answers the versions asked again inside `cached_permission_versions()` from that
read). An entry lives only until the last moment a credential it covers could still be accepted
— `until` — so the store holds what matters and no more. A permission version, bumped by every
change of a subject's roles, keys the cache of resolved permissions: the next call on any
replica resolves them anew.

The permission versions are counters that only grow, and a token carries their sum (`pv`): a
store that evicts one sets it back to 0, and a token of an older sum could match again. Store
them where nothing is evicted — Redis with `maxmemory-policy noeviction` or a `volatile-*`
policy (the counters carry no expiry, so a `volatile-*` one never picks them). A token whose
`pv` is greater than the current sum can only come from such an eviction: it is refused and
logged (`RevocationState.refusal`).
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from pydantic import ConfigDict, Field

from sincpro_framework.auth.common.domain.identity import Identity
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger


class WhenUnavailable(StrEnum):
    """What a credential gets while the revocations store does not answer."""

    REFUSE = "refuse"
    """Refused (503): nothing revoked is ever let through."""
    ADMIT = "admit"
    """Admitted unchecked, with a warning — availability over revocation."""


def tenant_subject(tenant: str) -> str:
    """The subject a tenant's permission version is stored under — bumped when its entitlements
    change: `tenant_subject("bo")` -> `"tenant:bo"`."""
    return f"tenant:{tenant}"


EVERY_TENANT = tenant_subject("*")
"""The subject whose permission version every tenant shares — bumped when a role every tenant
shares is changed or removed."""


def related_subjects(identity: Identity) -> tuple[str, ...]:
    """The subjects whose permission versions a credential's check reads with its own: its
    tenant's, when it has one, and every tenant's."""
    return (
        (tenant_subject(identity.tenant), EVERY_TENANT)
        if identity.tenant
        else (EVERY_TENANT,)
    )


class RevocationState(DataTransferObject):
    """What `Revocations` says about one credential of one subject, read at once."""

    model_config = ConfigDict(frozen=True)

    token_revoked: bool = False
    session_revoked: bool = False
    not_before: datetime | None = None
    """Credentials of the subject issued before this second are revoked — "sign out everywhere";
    to the second, as a JWT's `iat` is."""
    permission_version: int = 0
    versions: dict[str, int] = Field(default_factory=dict)
    """The permission versions of the related subjects read with it — a tenant's, every
    tenant's."""

    @property
    def permission_epoch(self) -> int:
        """The subject's permission version and its related subjects', summed — what a token
        carrying permissions carries (`pv`): any of them moving refuses it. They only grow, so
        the sum moves whenever one does."""
        return self.permission_version + sum(self.versions.values())

    def revocation_reason(
        self, issued_at: datetime | None, permission_epoch: int | None = None
    ) -> str | None:
        """Why a credential issued at `issued_at`, carrying the permissions of
        `permission_epoch` (`pv`) when it carries any, is no longer good — `None` when it is.

            RevocationState(not_before=10:00).revocation_reason(issued_at=09:59)   ->  "signed out"
            RevocationState(not_before=10:00).revocation_reason(issued_at=None)    ->  "signed out"
            RevocationState(permission_version=2).revocation_reason(now, permission_epoch=1)
                ->  "permissions changed: refresh the token"

        Context: a credential that cannot say when it was issued is refused once its subject
        signed out everywhere — it cannot prove it is newer. A `permission_epoch` greater than
        the current one is impossible unless the store evicted a counter (see the module): it
        is refused too, and logged as an error to fix the store's eviction policy.
        """
        if self.token_revoked:
            return "token revoked"
        if self.session_revoked:
            return "session revoked"
        if self.not_before is not None and (issued_at is None or issued_at < self.not_before):
            return "signed out"
        if permission_epoch is None or permission_epoch == self.permission_epoch:
            return None
        if permission_epoch > self.permission_epoch:
            logger.error(
                "a token carries permissions of a version newer than the store holds: the "
                "store evicted a permission version — store them with no eviction "
                "(maxmemory-policy noeviction or volatile-*)",
                carried=permission_epoch,
                current=self.permission_epoch,
            )
        return "permissions changed: refresh the token"


class Revocations(ABC):
    """The port: take credentials back, and say what was taken back — atomically, on whatever
    store every replica shares."""

    @abstractmethod
    def revoke_token(self, token_id: str, until: datetime) -> None:
        """Refuse the credential `token_id` (`jti`) until `until` — when it would have expired."""

    @abstractmethod
    def revoke_session(self, session: str, until: datetime) -> None:
        """Refuse every credential of `session` (`sid`) until `until` — a logout."""

    @abstractmethod
    def revoke_subject(self, subject: str, until: datetime) -> None:
        """Refuse every credential of `subject` issued until now, for as long as one could live
        — `until`: "sign out everywhere", a password changed, an account disabled."""

    @abstractmethod
    def bump_permissions(self, subject: str) -> int:
        """A new permission version for `subject`, atomically — what every change of its roles
        calls so the next call resolves them anew."""

    @abstractmethod
    def read(
        self,
        subject: str,
        token_id: str | None,
        session: str | None,
        related: tuple[str, ...],
    ) -> RevocationState:
        """Everything revoked about this credential of `subject`, and the permission versions of
        the `related` subjects, as the store holds them — in one read."""

    def state(
        self,
        subject: str,
        token_id: str | None = None,
        session: str | None = None,
        related: Iterable[str] = (),
    ) -> RevocationState:
        """Everything revoked about this credential of `subject`, and the permission versions of
        `related` subjects (`tenant:<id>`), in one read.

            state("user:42", token_id="jti-1", session="sid-7", related=["tenant:bo"])
                ->  RevocationState(session_revoked=True, permission_version=3,
                                    versions={"tenant:bo": 1})
        """
        return self.read(subject, token_id, session, tuple(related))

    def permission_versions(self, *subjects: str) -> dict[str, int]:
        """The permission version of each of `subjects` — 0 before any change — in one `state`
        of the first with the others related."""
        if not subjects:
            return {}
        first, *others = subjects
        state = self.state(first, related=others)
        return {first: state.permission_version} | {
            one: state.versions.get(one, 0) for one in others
        }

    def permission_version(self, subject: str) -> int:
        """The permission version of `subject` — 0 before any change."""
        return self.permission_versions(subject)[subject]

    def permission_epoch(self, identity: Identity) -> int:
        """What a token carrying `identity`'s permissions carries (`pv`): its version, its
        tenant's and every tenant's, summed — as `RevocationState.permission_epoch` reads it.
        """
        return sum(
            self.permission_versions(identity.subject, *related_subjects(identity)).values()
        )
