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
the provider resolves them on every call, so a role taken away holds at once. A session keeps
the identity as the sign-in gave it, never what `resolvers` added from a store: a role assigned
there is resolved again by whoever reads the token, and one taken away is gone at once. With
`embed_permissions`, the issuer's own `resolvers` resolve them at sign-in and again at every
refresh — never copied from the token before, which a role taken away would outlive. A refresh token is
random, stored by its digest, and rotated: used again within `reuse_grace` (ten seconds — two
tabs refreshing at once) it is answered with a pair of the same session; past it, it is a stolen
token being replayed, and the whole session is revoked.
Sessions end `idle_for` after the last refresh and `at_most` after they began, whatever is done.
"""

import time
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ConfigDict, SecretStr

from sincpro_framework.auth.accounts.adapters.signing_keys import SigningKeys
from sincpro_framework.auth.accounts.domain.events import (
    RefreshTokenReused,
    SignedIn,
    SignedOut,
    SignedOutEverywhere,
)
from sincpro_framework.auth.accounts.domain.refresh_tokens import RefreshRecord, RefreshTokens
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.events import SUBJECT, EventPublisher
from sincpro_framework.auth.common.domain.identity import Assurance, Identity, IdentityKind
from sincpro_framework.auth.common.domain.resolver import IdentityResolver, resolved
from sincpro_framework.auth.common.domain.revocations import Revocations
from sincpro_framework.auth.common.infrastructure import jws
from sincpro_framework.auth.common.infrastructure.announcing import announce
from sincpro_framework.auth.common.infrastructure.digests import digest, random_token
from sincpro_framework.auth.server import ClaimMapping, ClaimTest, JwtProvider, TrustedIssuer
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
    """How another service's `TrustedIssuer` reads these tokens — `TrustedIssuer(..., mapping=TokenIssuer.mapping)`."""

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
        resolvers: Sequence[IdentityResolver] = (),
        clock: Callable[[], float] = time.time,
        publisher: EventPublisher | None = None,
    ) -> None:
        """`embed_permissions` puts the permissions in the access token with the subject's
        permission version — for a verifier that resolves nothing; a version bumped refuses it.
        `resolvers` resolve what a token carries when it is issued — at sign-in and at every
        refresh; embedding permissions with refresh tokens needs them (`ProgrammingError`
        otherwise): a refresh resolves the permissions again, it never copies the old ones.
        """
        if embed_permissions and refresh_tokens is not None and not resolvers:
            raise exceptions.ProgrammingError(
                "embed_permissions with refresh tokens needs resolvers=: a refresh resolves "
                "the permissions again, it never copies the old ones"
            )
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
        self.resolvers = tuple(resolvers)
        self.clock = clock
        self.publisher = publisher

    def _now(self) -> datetime:
        """This moment, by `clock`."""
        return datetime.fromtimestamp(self.clock(), UTC)

    def _carried(self, signed_in: Identity) -> Identity:
        """What a token of `signed_in` carries: resolved by `resolvers` — its roles as the
        sign-in gave them unless permissions are embedded. A role a store assigns is resolved
        again by whoever verifies the token, so one taken away there is gone at once; embedded,
        the permission version refuses the token instead."""
        issued = resolved(signed_in, self.resolvers)
        if self.embed_permissions:
            return issued
        return issued.model_copy(update={"roles": signed_in.roles})

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
                0 if self.revocations is None else self.revocations.permission_epoch(identity)
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
        token when refresh tokens are kept, remembering the identity as the sign-in gave it.

        Context: what `resolvers` see is what a verifier and every refresh see — the scopes
        chosen and the client, the identity's own when `client` is not given — so a scope
        ceiling decides the same at sign-in and after.
        """
        chosen = frozenset(scopes)
        client = identity.client if client is None else client
        signed_in = identity.model_copy(update={"scopes": chosen, "client": client})
        now = self._now()
        session = random_token("sid_", 16)
        how = assurance or identity.assurance
        access = self._access(self._carried(signed_in), session, now, how, chosen, client)
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
                roles=identity.roles,
                scopes=chosen,
                assurance=how,
                authenticated_at=now,
                issued_at=now,
                expires_at=min(now + self.idle_for, now + self.at_most),
                session_ends=now + self.at_most,
            )
            self.refresh_tokens.save(first)
        announce(
            self.publisher,
            SignedIn(
                entity_type=SUBJECT,
                entity_id=identity.subject,
                tenant=identity.tenant,
                session=session,
                client=client,
                methods=sorted(how.methods),
                scopes=sorted(chosen),
            ),
        )
        return IssuedTokens(
            access_token=SecretStr(access),
            refresh_token=SecretStr(refresh) if refresh else None,
            expires_in=int(self.access_for.total_seconds()),
            session=session,
            scope=chosen,
        )

    def _replayed(self, record: RefreshRecord) -> exceptions.Unauthenticated:
        """A refresh token used again past `reuse_grace` — stolen or leaked: its whole session
        revoked, logged and announced."""
        logger.warning(
            "a refresh token was reused: its session is revoked",
            subject=record.subject,
            session=record.family[:12],
        )
        if self.revocations is not None:
            self.revocations.revoke_session(record.family, record.session_ends)
        announce(
            self.publisher,
            RefreshTokenReused(
                entity_type=SUBJECT, entity_id=record.subject, session=record.family
            ),
        )
        return exceptions.Unauthenticated("refresh token reused: the session was revoked")

    def _spent(
        self,
        refresh_tokens: RefreshTokens,
        record: RefreshRecord,
        successor: RefreshRecord,
        now: datetime,
    ) -> None:
        """`record` spent for `successor`.

        1. Not rotated yet: rotated atomically, `successor` kept.
        2. Rotated — earlier, or by a refresh racing this one —: within `reuse_grace` of it,
           one client refreshing twice at once (two tabs, requests in parallel), so
           `successor` is kept beside the token the first refresh got, in the same session.
        3. Past `reuse_grace`: a replay — the session revoked.
        """
        if record.rotated_at is None and refresh_tokens.rotate(record.digest, successor, now):
            return
        spent = (
            record if record.rotated_at is not None else refresh_tokens.find(record.digest)
        )
        if spent is None or spent.rotated_at is None:
            raise exceptions.Unauthenticated("unknown refresh token")
        if now - spent.rotated_at > self.reuse_grace:
            raise self._replayed(spent)
        refresh_tokens.save(successor)

    def refresh(self, refresh_token: str) -> IssuedTokens:
        """A new pair for a refresh token, which is spent.

        1. Unknown, past its idle time or its session's end: `Unauthenticated`.
        2. Its session revoked, or its subject signed out everywhere since: `Unauthenticated`.
        3. Spent: rotated — or, used again within `reuse_grace`, a token beside the first
           refresh's — and past it, a replay that revokes the session.
        Final: a new access token of the same session — the identity of its sign-in, resolved
        again by `resolvers` — and a new refresh token.
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
        successor_token = random_token("rt_")
        successor = self._refresh_record(successor_token, record, now)
        self._spent(self.refresh_tokens, record, successor, now)
        signed_in = Identity(
            subject=record.subject,
            kind=IdentityKind(record.kind),
            tenant=record.tenant,
            roles=record.roles,
            scopes=record.scopes,
            client=record.client,
        )
        access = self._access(
            self._carried(signed_in),
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
        """The revocations given — `ProgrammingError` without them: nothing can be taken back."""
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
                announce(
                    self.publisher,
                    SignedOut(
                        entity_type=SUBJECT, entity_id=record.subject, session=record.family
                    ),
                )
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
            announce(
                self.publisher,
                SignedOut(
                    entity_type=SUBJECT,
                    entity_id=str(parsed.claims.get("sub", "")),
                    session=session,
                ),
            )

    def sessions_of(self, subject: str) -> list[RefreshRecord]:
        """The sessions `subject` is signed in on — one each, its newest token; none it signed
        out of, nor any begun before it signed out everywhere — oldest first."""
        if self.refresh_tokens is None:
            return []
        newest: dict[str, RefreshRecord] = {}
        for one in self.refresh_tokens.sessions_of(subject):
            held = newest.get(one.family)
            if held is None or one.issued_at >= held.issued_at:
                newest[one.family] = one
        revocations = self.revocations
        return sorted(
            (
                one
                for one in newest.values()
                if revocations is None
                or revocations.state(subject, session=one.family).refusal(
                    one.authenticated_at
                )
                is None
            ),
            key=lambda one: one.authenticated_at,
        )

    def revoke_subject(self, subject: str) -> None:
        """ "Sign out everywhere": every token of `subject` issued until now is refused — a
        password changed, an account disabled.

        1. The subject revoked from this second on — what a token's `iat` can say.
        Final: each of its sessions revoked too, so one begun earlier in that same second ends.
        """
        revocations = self._revocations()
        revocations.revoke_subject(subject, self._now() + self.at_most)
        held = [] if self.refresh_tokens is None else self.refresh_tokens.sessions_of(subject)
        for record in held:
            revocations.revoke_session(record.family, record.session_ends)
        announce(
            self.publisher,
            SignedOutEverywhere(entity_type=SUBJECT, entity_id=subject),
        )

    def provider(
        self, resolvers: Sequence[IdentityResolver] | None = None, name: str = "sessions"
    ) -> JwtProvider:
        """The `JwtProvider` that accepts what this issuer signed — checking revocations, and the
        permission version when permissions are embedded. `resolvers` are the issuer's own when
        not given: what a store grants is resolved on every call, as it was at sign-in."""
        return JwtProvider(
            TrustedIssuer(
                self.issuer,
                audience=self.audience,
                keys=self.keys.verification(),
                mapping=OWN_MAPPING,
                required=frozenset({"exp", "sub", "sid"}),
            ),
            resolvers=self.resolvers if resolvers is None else resolvers,
            revocations=self.revocations,
            name=name,
            clock=self.clock,
        )

    def jwks(self) -> dict[str, Any]:
        """The public keys other services verify these tokens with — asymmetric keys only."""
        return self.keys.jwks()
