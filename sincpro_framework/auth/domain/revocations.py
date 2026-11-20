"""`Revocations`: what was taken back before it ran out — a token, a session, every session a
subject had, and the permissions a subject held when its credential was issued.

    revocations.revoke_session("sid-7", until=session_ends)      a logout
    revocations.revoke_subject("user:42", until=now + longest)   "sign out everywhere"
    revocations.bump_permissions("user:42")                      a role taken away: the next
                                                                 call resolves them anew
    revocations.state("user:42", token_id="jti-1", session="sid-7").refusal(issued_at)
        ->  "session revoked"   or None

Context: a JWT is checked by its signature and lives until it expires; this is what lets one be
refused earlier, with one read per call (`state`). An entry lives only until the last moment a
credential it covers could still be accepted — `until` — so the store holds what matters and no
more. A permission version, bumped by every change of a subject's roles, keys the cache of
resolved permissions: the next call on any replica resolves them anew.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import ConfigDict

from sincpro_framework.sincpro_abstractions import DataTransferObject


class RevocationState(DataTransferObject):
    """What `Revocations` says about one credential of one subject, read at once."""

    model_config = ConfigDict(frozen=True)

    token_revoked: bool = False
    session_revoked: bool = False
    not_before: datetime | None = None
    """Credentials of the subject issued before this moment are revoked — "sign out everywhere"."""
    permission_version: int = 0

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
    def state(
        self, subject: str, token_id: str | None = None, session: str | None = None
    ) -> RevocationState:
        """Everything revoked about this credential of `subject`, in one read."""

    def permission_version(self, subject: str) -> int:
        """The permission version of `subject` — 0 before any change."""
        return self.state(subject).permission_version
