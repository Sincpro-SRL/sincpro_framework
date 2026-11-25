"""The permission versions one authentication read, answered again without the store —
`cached_permission_versions()` around the authentication, `CachingRevocations` under the store.

    with cached_permission_versions():
        revocations.state("user:42", related=["tenant:bo"])     one get_many
        revocations.permission_version("tenant:bo")             none

Context: a provider opens it around one authentication, so its revocation check and the
resolvers that ask a version afterwards share one read. The cache lives in a `ContextVar`, one
per authentication, and is dropped when it ends: a version is never answered from a read of
another call.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sincpro_framework.auth.common.domain.revocations import Revocations, RevocationState

_VERSIONS_READ: ContextVar[dict[tuple[int, str], int] | None] = ContextVar(
    "sincpro_auth_versions_read", default=None
)


@contextmanager
def cached_permission_versions() -> Iterator[None]:
    """Within it, every permission version a `CachingRevocations` reads is cached — asked
    again, it is answered without the store."""
    token = _VERSIONS_READ.set({})
    try:
        yield
    finally:
        _VERSIONS_READ.reset(token)


class CachingRevocations(Revocations):
    """`Revocations` whose permission versions, once read inside `cached_permission_versions()`, are
    answered from that read — the base of every implementation the framework ships."""

    def state(
        self,
        subject: str,
        token_id: str | None = None,
        session: str | None = None,
        related: Iterable[str] = (),
    ) -> RevocationState:
        """`Revocations.state`, its versions cached inside `cached_permission_versions()`."""
        state = super().state(subject, token_id, session, related)
        cache = _VERSIONS_READ.get()
        if cache is not None:
            cache[(id(self), subject)] = state.permission_version
            cache.update({(id(self), one): v for one, v in state.versions.items()})
        return state

    def permission_versions(self, *subjects: str) -> dict[str, int]:
        """The permission version of each of `subjects` — what this authentication already
        read, else read now, all at once.

        1. Inside `cached_permission_versions()`: the versions already read answered from it.
        2. The rest, if any, in one `state` of the first with the others related.
        Final: a version per subject asked.
        """
        cache = _VERSIONS_READ.get() or {}
        known = {one: cache[(id(self), one)] for one in subjects if (id(self), one) in cache}
        missing = [one for one in subjects if one not in known]
        known.update(super().permission_versions(*missing))
        return {one: known[one] for one in subjects}
