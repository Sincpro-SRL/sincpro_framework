"""`TokenIssuer`: the tokens a service that manages its own accounts hands out once a person
signed in — a short access token, an opaque refresh token rotated on every use — and what takes
them back: a logout, "sign out everywhere", a permission changed.

    issuer = TokenIssuer("https://colegio.bo", audience="colegio",
                         keys=SigningKeys.hmac({"2026-09": settings.token_secret}),
                         refresh_tokens=KeyValueRefreshTokens(), revocations=KeyValueRevocations())
    tokens = issuer.issue(Identity.user("user:MFAQ0123"), assurance=Assurance(methods={"pwd"}))
    tokens.answer()        ->  {"access_token": …, "refresh_token": …, "expires_in": 600, …}
    issuer.refresh(refresh_token)                  a new pair, the old refresh token spent
    issuer.revoke(access_or_refresh_token)         the session ends, its access token at once
    auth = AccessControl(providers=[issuer.provider(resolvers=[GrantResolver(...)])])

Context: the access token lives ten minutes and carries who and how — no permissions by default:
the provider resolves them on every call, so a role taken away holds at once. A refresh token is
random, stored by its digest, and rotated: used twice past `reuse_grace` (ten seconds — two tabs
refreshing at once), it is a stolen token being replayed, and the whole session is revoked.
Sessions end `idle_for` after the last refresh and `at_most` after they began, whatever is done.
"""

import time
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ConfigDict, SecretStr

from sincpro_framework.auth.adapters.jwt.mapping import ClaimMapping, ClaimTest
from sincpro_framework.auth.adapters.jwt.provider import Issuer, JwtProvider
from sincpro_framework.auth.adapters.jwt.signing import SigningKeys
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.accounts import RefreshRecord, RefreshTokens
from sincpro_framework.auth.domain.identity import Assurance, Identity, IdentityKind
from sincpro_framework.auth.domain.resolver import IdentityResolver
from sincpro_framework.auth.domain.revocations import Revocations
from sincpro_framework.auth.infrastructure import jws
from sincpro_framework.auth.infrastructure.digests import digest, random_token
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger

OWN_MAPPING = ClaimMapping(
    subject_prefix="",
    service_prefix="",
    service_when=ClaimTest(path="kind", equals="service"),
    tenant="tenant",
    roles=("roles",),
    scopes=("scope",),
    permissions=("permissions",),
    client=("client_id",),
)
"""How the tokens a `TokenIssuer` signs read: `sub` is the subject as given (`user:…`)."""


class IssuedTokens(DataTransferObject):
    """What was handed out — its secrets as `SecretStr`, shown by no `repr`."""

    model_config = ConfigDict(frozen=True)

    access_token: SecretStr
    refresh_token: SecretStr | None = None
    expires_in: int
    session: str
    scope: frozenset[str] = frozenset()

    def answer(self) -> dict[str, Any]:
        """The OAuth token response a client is sent — the one place the secrets are revealed."""
        said: dict[str, Any] = {
            "access_token": self.access_token.get_secret_value(),
            "token_type": "Bearer",
            "expires_in": self.expires_in,
        }
        if self.refresh_token is not None:
            said["refresh_token"] = self.refresh_token.get_secret_value()
        if self.scope:
            said["scope"] = " ".join(sorted(self.scope))
        return said


class TokenIssuer:
    """Access tokens signed by `keys`, refresh tokens kept in `refresh_tokens`, taken back
    through `revocations`."""

    mapping = OWN_MAPPING
    """How another service's `Issuer` reads these tokens — `Issuer(..., mapping=TokenIssuer.mapping)`."""

    def __init__(
        self,
        issuer: str,
        audience: str,
        keys: SigningKeys,
        refresh_tokens: RefreshTokens | None = None,
        revocations: Revocations | None = None,
        access_for: timedelta = timedelta(minutes=10),
        idle_for: timedelta = timedelta(days=14),
        at_most: timedelta = timedelta(days=30),
        reuse_grace: timedelta = timedelta(seconds=10),
        embed_permissions: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """`embed_permissions` puts the permissions in the access token with the subject's
        permission version — for a verifier that resolves nothing; a version bumped refuses it.
        """
        self.issuer = issuer
        self.audience = audience
        self.keys = keys
        self.refresh_tokens = refresh_tokens
        self.revocations = revocations
        self.access_for = access_for
        self.idle_for = idle_for
        self.at_most = at_most
        self.reuse_grace = reuse_grace
        self.embed_permissions = embed_permissions
        self.clock = clock

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), UTC)

    def _access(
        self,
        identity: Identity,
        session: str,
        authenticated_at: datetime,
        assurance: Assurance,
        scopes: frozenset[str],
        client: str | None,
    ) -> str:
        """An access token: who, which session, when and how they authenticated, what scopes."""
        now = int(self.clock())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": identity.subject,
            "kind": identity.kind.value,
            "sid": session,
            "jti": random_token(length=12),
            "iat": now,
            "exp": now + int(self.access_for.total_seconds()),
            "auth_time": int(authenticated_at.timestamp()),
        }
        if identity.tenant is not None:
            claims["tenant"] = identity.tenant
        if identity.roles:
            claims["roles"] = sorted(identity.roles)
        if scopes:
            claims["scope"] = " ".join(sorted(scopes))
        if client is not None:
            claims["client_id"] = client
        if assurance.level is not None:
            claims["acr"] = assurance.level
        if assurance.methods:
            claims["amr"] = sorted(assurance.methods)
        if self.embed_permissions:
            claims["permissions"] = sorted(identity.permissions)
            claims["pv"] = (
                0
                if self.revocations is None
                else self.revocations.permission_version(identity.subject)
            )
        return self.keys.sign(claims)

    def _refresh_record(
        self, token: str, base: RefreshRecord, issued_at: datetime
    ) -> RefreshRecord:
        """A record for `token`, of `base`'s session, alive `idle_for` — never past its end."""
        return base.model_copy(
            update={
                "digest": digest(token),
                "issued_at": issued_at,
                "expires_at": min(issued_at + self.idle_for, base.session_ends),
                "rotated_at": None,
            }
        )

    def issue(
        self,
        identity: Identity,
        scopes: Iterable[str] = (),
        assurance: Assurance | None = None,
        client: str | None = None,
    ) -> IssuedTokens:
        """A new session for `identity` — just authenticated: an access token, and a refresh
        token when refresh tokens are kept."""
        now = self._now()
        session = random_token("sid_", 16)
        how = assurance or identity.assurance
        chosen = frozenset(scopes)
        access = self._access(identity, session, now, how, chosen, client)
        refresh: str | None = None
        if self.refresh_tokens is not None:
            refresh = random_token("rt_")
            first = RefreshRecord(
                digest=digest(refresh),
                family=session,
                subject=identity.subject,
                kind=identity.kind.value,
                tenant=identity.tenant,
                client=client,
                scopes=chosen,
                assurance=how,
                authenticated_at=now,
                issued_at=now,
                expires_at=min(now + self.idle_for, now + self.at_most),
                session_ends=now + self.at_most,
            )
            self.refresh_tokens.save(first)
        return IssuedTokens(
            access_token=SecretStr(access),
            refresh_token=SecretStr(refresh) if refresh else None,
            expires_in=int(self.access_for.total_seconds()),
            session=session,
            scope=chosen,
        )

    def _reused(self, record: RefreshRecord, now: datetime) -> exceptions.Unauthenticated:
        """A refresh token used again: within `reuse_grace` a race of the same client — refused,
        the session kept; past it a replay — the whole session revoked."""
        assert record.rotated_at is not None
        if now - record.rotated_at <= self.reuse_grace:
            return exceptions.Unauthenticated("refresh token already used")
        logger.warning(
            "a refresh token was reused: its session is revoked",
            subject=record.subject,
            session=record.family[:12],
        )
        if self.revocations is not None:
            self.revocations.revoke_session(record.family, record.session_ends)
        return exceptions.Unauthenticated("refresh token reused: the session was revoked")

    def refresh(self, refresh_token: str) -> IssuedTokens:
        """A new pair for a refresh token, which is spent.

        1. Unknown, past its idle time or its session's end: `Unauthenticated`.
        2. Its session revoked, or its subject signed out everywhere since: `Unauthenticated`.
        3. Already rotated: refused — and past `reuse_grace`, the session revoked.
        4. Rotated atomically; losing the race is step 3.
        Final: a new access token of the same session and a new refresh token.
        """
        if self.refresh_tokens is None:
            raise exceptions.ProgrammingError("this TokenIssuer keeps no refresh tokens")
        now = self._now()
        record = self.refresh_tokens.find(digest(refresh_token))
        if record is None:
            raise exceptions.Unauthenticated("unknown refresh token")
        if now >= record.expires_at or now >= record.session_ends:
            raise exceptions.Unauthenticated("session expired")
        if self.revocations is not None:
            refusal = self.revocations.state(record.subject, session=record.family).refusal(
                record.authenticated_at
            )
            if refusal is not None:
                raise exceptions.Unauthenticated(refusal)
        if record.rotated_at is not None:
            raise self._reused(record, now)
        successor_token = random_token("rt_")
        successor = self._refresh_record(successor_token, record, now)
        if not self.refresh_tokens.rotate(record.digest, successor, now):
            again = self.refresh_tokens.find(record.digest)
            if again is None or again.rotated_at is None:
                raise exceptions.Unauthenticated("unknown refresh token")
            raise self._reused(again, now)
        identity = Identity(
            subject=record.subject,
            kind=IdentityKind(record.kind),
            tenant=record.tenant,
        )
        access = self._access(
            identity,
            record.family,
            record.authenticated_at,
            record.assurance,
            record.scopes,
            record.client,
        )
        return IssuedTokens(
            access_token=SecretStr(access),
            refresh_token=SecretStr(successor_token),
            expires_in=int(self.access_for.total_seconds()),
            session=record.family,
            scope=record.scopes,
        )

    def _revocations(self) -> Revocations:
        if self.revocations is None:
            raise exceptions.ProgrammingError("taking tokens back needs revocations=")
        return self.revocations

    def revoke(self, token: str) -> None:
        """A logout: the session of `token` — an access or a refresh token — ends now, its
        access tokens refused at once. A token this issuer did not issue is left alone."""
        revocations = self._revocations()
        if self.refresh_tokens is not None and not jws.looks_like_jws(token):
            record = self.refresh_tokens.find(digest(token))
            if record is not None:
                revocations.revoke_session(record.family, record.session_ends)
            return
        try:
            parsed = jws.parse(token)
            key = self.keys.verification().key_for(parsed.key_id, parsed.algorithm)
        except exceptions.Unauthenticated:
            return
        if (
            not key.verify(parsed.signing_input, parsed.signature)
            or parsed.claims.get("iss") != self.issuer
        ):
            return
        session = parsed.claims.get("sid")
        if isinstance(session, str):
            revocations.revoke_session(session, self._now() + self.at_most)

    def revoke_subject(self, subject: str) -> None:
        """ "Sign out everywhere": every token of `subject` issued until now is refused — a
        password changed, an account disabled."""
        self._revocations().revoke_subject(subject, self._now() + self.at_most)

    def sessions_of(self, subject: str) -> list[RefreshRecord]:
        """The sessions `subject` is signed in on."""
        return [] if self.refresh_tokens is None else self.refresh_tokens.sessions_of(subject)

    def provider(
        self, resolvers: Sequence[IdentityResolver] = (), name: str = "sessions"
    ) -> JwtProvider:
        """The `JwtProvider` that accepts what this issuer signed — checking revocations, and the
        permission version when permissions are embedded."""
        return JwtProvider(
            Issuer(
                self.issuer,
                audience=self.audience,
                keys=self.keys.verification(),
                mapping=OWN_MAPPING,
                required=frozenset({"exp", "sub", "sid"}),
            ),
            resolvers=resolvers,
            revocations=self.revocations,
            name=name,
            clock=self.clock,
        )

    def jwks(self) -> dict[str, Any]:
        """The public keys other services verify these tokens with — asymmetric keys only."""
        return self.keys.jwks()
