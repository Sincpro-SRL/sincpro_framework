"""`Revocations`: what was taken back before it ran out — a token, a session, every session a
subject had, and the permissions a subject held when its credential was issued.

    revocations.revoke_session("sid-7", until=session_ends)      a logout
    revocations.revoke_subject("user:42", until=now + longest)   "sign out everywhere"
    revocations.bump_permissions("user:42")                      a role taken away: the next
                                                                 call resolves them anew
    revocations.state("user:42", token_id="jti-1", session="sid-7").refusal(issued_at)
        ->  "session revoked"   or None

Context: a JWT is checked by its signature and lives until it expires; this is what lets one be
refused earlier, with one read per call: `state` reads the credential's revocations and the
permission versions of its subject and its tenant at once, and inside `remembering_versions()` —
which a provider opens around its authentication — the resolvers that ask a version afterwards
are answered from that read, never the store again. An entry lives only until the last moment a
credential it covers could still be accepted — `until` — so the store holds what matters and no
more. A permission version, bumped by every change of a subject's roles, keys the cache of
resolved permissions: the next call on any replica resolves them anew.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime

from pydantic import ConfigDict, Field

from sincpro_framework.auth.domain.identity import Identity
from sincpro_framework.sincpro_abstractions import DataTransferObject

_VERSIONS_READ: ContextVar[dict[tuple[int, str], int] | None] = ContextVar(
    "sincpro_auth_versions_read", default=None
)


def tenant_subject(tenant: str) -> str:
    """The subject a tenant's permission version is kept under — bumped when its entitlements
    change: `tenant_subject("bo")` -> `"tenant:bo"`."""
    return f"tenant:{tenant}"


def related_of(identity: Identity) -> tuple[str, ...]:
    """The subjects whose permission versions a credential's check reads with its own: its
    tenant's, when it has one."""
    return (tenant_subject(identity.tenant),) if identity.tenant else ()


@contextmanager
def remembering_versions() -> Iterator[None]:
    """Within it, every permission version a `Revocations` reads is remembered — asked again,
    it is answered without the store. A provider opens it around one authentication, so its
    revocation check and its resolvers share one read.

        with remembering_versions():
            revocations.state("user:42", related=["tenant:bo"])     one get_many
            revocations.permission_version("tenant:bo")             none
    """
    token = _VERSIONS_READ.set({})
    try:
        yield
    finally:
        _VERSIONS_READ.reset(token)


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
    """The permission versions of the related subjects read with it — a tenant's."""

    def refusal(self, issued_at: datetime | None) -> str | None:
        """Why a credential issued at `issued_at` is no longer good — `None` when it is.

            RevocationState(not_before=10:00).refusal(issued_at=09:59)   ->  "signed out"
            RevocationState(not_before=10:00).refusal(issued_at=None)    ->  "signed out"

        Context: a credential that cannot say when it was issued is refused once its subject
        signed out everywhere — it cannot prove it is newer.
        """
        if self.token_revoked:
            return "token revoked"
        if self.session_revoked:
            return "session revoked"
        if self.not_before is not None and (issued_at is None or issued_at < self.not_before):
            return "signed out"
        return None


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
        `related` subjects (`tenant:<id>`), in one read — remembered inside
        `remembering_versions()`.

            state("user:42", token_id="jti-1", session="sid-7", related=["tenant:bo"])
                ->  RevocationState(session_revoked=True, permission_version=3,
                                    versions={"tenant:bo": 1})
        """
        state = self.read(subject, token_id, session, tuple(related))
        remembered = _VERSIONS_READ.get()
        if remembered is not None:
            remembered[(id(self), subject)] = state.permission_version
            remembered.update({(id(self), one): v for one, v in state.versions.items()})
        return state

    def permission_versions(self, *subjects: str) -> dict[str, int]:
        """The permission version of each of `subjects` — 0 before any change: what this
        authentication already read, else read now, all at once.

        1. Inside `remembering_versions()`: the versions already read answered from it.
        2. The rest, if any, in one `state` of the first with the others related.
        Final: a version per subject asked.
        """
        remembered = _VERSIONS_READ.get() or {}
        known = {
            one: remembered[(id(self), one)]
            for one in subjects
            if (id(self), one) in remembered
        }
        missing = [one for one in subjects if one not in known]
        if missing:
            first, *others = missing
            state = self.state(first, related=others)
            known[first] = state.permission_version
            known.update({one: state.versions.get(one, 0) for one in others})
        return {one: known[one] for one in subjects}

    def permission_version(self, subject: str) -> int:
        """The permission version of `subject` — 0 before any change."""
        return self.permission_versions(subject)[subject]
