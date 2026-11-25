"""`TokenIssuer`: the tokens a service that manages its own accounts hands out once a person
signed in — a short access token, an opaque refresh token rotated on every use — and what takes
them back: a logout, "sign out everywhere", a permission changed.

    issuer = TokenIssuer("https://colegio.bo", audience="colegio",
                         keys=SigningKeys.hmac({"2026-09": settings.token_secret}),
                         refresh_tokens=KeyValueRefreshTokens(), revocations=KeyValueRevocations())
    tokens = issuer.issue(Identity.user("user:MFAQ0123"), assurance=Assurance(methods={"pwd"}))
    tokens.answer()        ->  {"access_token": …, "refresh_token": …, "expires_in": 600, …}
    issuer.refresh(refresh_token)                  a new pair, the old refresh token rotated
    issuer.revoke(access_or_refresh_token)         the session ends, its access token at once
    issuer.revoke_session(session_id)              the same by its id — "sign out this device"
    auth = AccessControl(providers=[JwtProvider.for_token_issuer(issuer, resolvers=[GrantResolver(...)])])

Context: the access token lives ten minutes and carries who and how — no permissions by default:
the provider resolves them on every call, so a role taken away holds at once. A session stores
the identity as the sign-in gave it, never what `resolvers` added from a store: a role assigned
there is resolved again by whoever reads the token, and one taken away is gone at once. With
`embed_permissions`, the issuer's own `resolvers` resolve them at sign-in and again at every
refresh — never copied from the token before, which a role taken away would outlive. A refresh token is
random, stored by its digest, and rotated: used again within `reuse_grace` (ten seconds — two
tabs refreshing at once) it is answered with a pair of the same session; past it, it is a stolen
token being replayed, and the whole session is revoked — which is why refresh tokens need
`revocations=`: without them a replay could revoke nothing (`ProgrammingError`).
Sessions end `idle_ttl` after the last refresh and `session_ttl` after they began, whatever is done
— `session_ttl=None`, only when idle; `lifetime_for` decides per session (#337).

Extend — every decision a public method, asked with the identity (and so its tenant):

    lifetime_for(identity, client)    how long this session lives        SessionLifetime
    kept_claims(identity)             what it keeps on the server        session_attributes=
    serves_audience(audience)         the resources its tokens are for   resources= (RFC 8707)

What a sign-in knew that no token may carry (`Identity.claims`) is kept by `session_attributes`
under the session, given back at every refresh, and at every call by
`JwtProvider.for_token_issuer` (#336).
"""

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
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
from sincpro_framework.auth.accounts.domain.sessions import SessionAttributes, SessionLifetime
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.common.domain.events import SUBJECT, EventPublisher
from sincpro_framework.auth.common.domain.identity import Assurance, Identity, IdentityKind
from sincpro_framework.auth.common.domain.resolver import IdentityResolver, resolved
from sincpro_framework.auth.common.domain.resources import ResourcePolicy
from sincpro_framework.auth.common.domain.revocations import Revocations
from sincpro_framework.auth.common.infrastructure import jws
from sincpro_framework.auth.common.infrastructure.claim_mapping import ClaimMapping, ClaimTest
from sincpro_framework.auth.common.infrastructure.digests import digest, random_token
from sincpro_framework.auth.common.infrastructure.event_publishing import publish_event
from sincpro_framework.auth.common.infrastructure.trusted_issuer import TrustedIssuer
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger

CLAIM_MAPPING = ClaimMapping(
    subject_prefix="",
    service_prefix="",
    service_match=ClaimTest(path="kind", equals="service"),
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
    """Access tokens signed by `keys`, refresh tokens stored in `refresh_tokens`, taken back
    through `revocations`."""

    mapping = CLAIM_MAPPING
    """How another service's `TrustedIssuer` reads these tokens — `TrustedIssuer(..., mapping=TokenIssuer.mapping)`."""

    def __init__(
        self,
        issuer: str,
        audience: str,
        keys: SigningKeys,
        refresh_tokens: RefreshTokens | None = None,
        revocations: Revocations | None = None,
        access_ttl: timedelta = timedelta(minutes=10),
        idle_ttl: timedelta = timedelta(days=14),
        session_ttl: timedelta | None = timedelta(days=30),
        reuse_grace: timedelta = timedelta(seconds=10),
        embed_permissions: bool = False,
        resolvers: Sequence[IdentityResolver] = (),
        clock: Callable[[], float] = time.time,
        publisher: EventPublisher | None = None,
        resources: ResourcePolicy | None = None,
        session_attributes: SessionAttributes | None = None,
    ) -> None:
        """`access_ttl` is how long an access token lives; `idle_ttl` how long a refresh token
        lives unused; `session_ttl` how long a session lasts however it is refreshed — `None`,
        while it is used (`lifetime_for` decides per session). `resources` are the RFC 8707
        resources its tokens may be issued for, besides its `audience`; `session_attributes`
        keep on the server what a sign-in knew that no token carries.
        `embed_permissions` puts the permissions in the access token with the subject's
        permission version — for a verifier that resolves nothing; a version bumped refuses it.
        `resolvers` resolve what a token carries when it is issued — at sign-in and at every
        refresh; embedding permissions with refresh tokens needs them (`ProgrammingError`
        otherwise): a refresh resolves the permissions again, it never copies the old ones.
        Refresh tokens need `revocations` (`ProgrammingError` otherwise): a refresh token
        replayed — stolen — revokes its session there, and without them the thief would go on
        refreshing.
        """
        if refresh_tokens is not None and revocations is None:
            raise exceptions.ProgrammingError(
                "refresh_tokens= needs revocations=: a replayed refresh token revokes its "
                "session there — without them a stolen token goes on refreshing"
            )
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
        self.access_ttl = access_ttl
        self.idle_ttl = idle_ttl
        self.session_ttl = session_ttl
        self.reuse_grace = reuse_grace
        self.embed_permissions = embed_permissions
        self.resolvers = tuple(resolvers)
        self.clock = clock
        self.publisher = publisher
        self.resources = resources
        self.session_attributes = session_attributes

    def _now(self) -> datetime:
        """This moment, by `clock`."""
        return datetime.fromtimestamp(self.clock(), UTC)

    def _resolve_claims(self, signed_in: Identity) -> Identity:
        """What a token of `signed_in` carries: resolved by `resolvers` — its roles as the
        sign-in gave them unless permissions are embedded. A role a store assigns is resolved
        again by whoever verifies the token, so one taken away there is gone at once; embedded,
        the permission version refuses the token instead."""
        issued = resolved(signed_in, self.resolvers)
        if self.embed_permissions:
            return issued
        return issued.model_copy(update={"roles": signed_in.roles})

    def _sign_access_token(
        self,
        identity: Identity,
        session: str,
        authenticated_at: datetime,
        assurance: Assurance,
        scopes: frozenset[str],
        client: str | None,
        audience: str | None,
    ) -> str:
        """An access token: who, which session, when and how they authenticated, what scopes,
        for which audience — the issuer's own unless one was asked for (RFC 8707)."""
        now = int(self.clock())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": audience or self.audience,
            "sub": identity.subject,
            "kind": identity.kind.value,
            "sid": session,
            "jti": random_token(length=12),
            "iat": now,
            "exp": now + int(self.access_ttl.total_seconds()),
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

    def lifetime_for(self, identity: Identity, client: str | None) -> SessionLifetime:
        """How long the session `identity` signs in to with `client` lives — the issuer's
        `idle_ttl` and `session_ttl`. Override to decide per tenant (`identity.tenant`), per
        client or per assurance; the choice is recorded with the session and every refresh
        honours it.

            class Issuer(TokenIssuer):
                def lifetime_for(self, identity, client):
                    if identity.tenant in LONG_LIVED:
                        return SessionLifetime(idle=timedelta(days=30))   # while it is used
                    return super().lifetime_for(identity, client)
        """
        return SessionLifetime(idle=self.idle_ttl, at_most=self.session_ttl)

    def kept_claims(self, identity: Identity) -> Mapping[str, Any]:
        """What the session of `identity` keeps on the server — its `claims`, as the sign-in gave
        them. Override to keep less, or more. Asked only with `session_attributes`."""
        return dict(identity.claims)

    def serves_audience(self, audience: str) -> bool:
        """Whether a token whose `aud` is `audience` is one this issuer's verifier accepts: its
        own `audience`, or a resource its `resources` serve (RFC 8707)."""
        return audience == self.audience or (
            self.resources is not None and self.resources.serves(audience)
        )

    def verification_resolvers(self) -> tuple[IdentityResolver, ...]:
        """What a verifier of these tokens runs before any resolver of the project's — the
        session's kept claims, when `session_attributes` keep them (#336)."""
        if self.session_attributes is None:
            return ()
        return (KeptClaims(self.session_attributes),)

    def _kept_for(self, ends_at: datetime | None) -> timedelta:
        """How long what a session keeps is stored from now: while a token of it may still be
        presented — its refresh token's end, then one more access token."""
        if ends_at is None:
            return self.access_ttl
        return ends_at - self._now() + self.access_ttl

    def _build_refresh_record(
        self, token: str, base: RefreshRecord, issued_at: datetime
    ) -> RefreshRecord:
        """A record for `token`, of `base`'s session, alive its idle lifetime — never past its
        end."""
        idle = base.idle_ttl or self.idle_ttl
        expires_at = issued_at + idle
        if base.session_ends is not None:
            expires_at = min(expires_at, base.session_ends)
        return base.model_copy(
            update={
                "digest": digest(token),
                "issued_at": issued_at,
                "expires_at": expires_at,
                "rotated_at": None,
            }
        )

    def issue(
        self,
        identity: Identity,
        scopes: Iterable[str] = (),
        assurance: Assurance | None = None,
        client: str | None = None,
        audience: str | None = None,
    ) -> IssuedTokens:
        """A new session for `identity` — just authenticated: an access token, and a refresh
        token when there is a refresh token store, which stores the identity as the sign-in gave
        it; what it keeps on the server, with `session_attributes`.

        Context: what `resolvers` see is what a verifier and every refresh see — the scopes
        chosen, the client (the identity's own when `client` is not given) and the session — so
        a scope ceiling decides the same at sign-in and after. `audience` is the resource the
        tokens are for (RFC 8707) — every refresh of the session carries it; the issuer's own
        when `None`.

        1. The session opened: its id, its lifetime (`lifetime_for`).
        2. The access token signed for the identity resolved.
        3. With a refresh token store, its first refresh token, recording the lifetime chosen.
        4. With `session_attributes`, what `kept_claims` answers, until the session ends.
        Final: `SignedIn` announced, the tokens answered.
        """
        chosen = frozenset(scopes)
        client = identity.client if client is None else client
        session = random_token("sid_", 16)
        signed_in = identity.model_copy(
            update={"scopes": chosen, "client": client, "session": session}
        )
        lifetime = self.lifetime_for(signed_in, client)
        now = self._now()
        session_ends = None if lifetime.at_most is None else now + lifetime.at_most
        expires_at = now + lifetime.idle
        if session_ends is not None:
            expires_at = min(expires_at, session_ends)
        how = assurance or identity.assurance
        access = self._sign_access_token(
            self._resolve_claims(signed_in), session, now, how, chosen, client, audience
        )
        refresh: str | None = None
        if self.refresh_tokens is not None:
            refresh = random_token("rt_")
            first = RefreshRecord(
                digest=digest(refresh),
                session=session,
                subject=identity.subject,
                kind=identity.kind.value,
                tenant=identity.tenant,
                client=client,
                audience=audience,
                roles=identity.roles,
                scopes=chosen,
                assurance=how,
                authenticated_at=now,
                issued_at=now,
                expires_at=expires_at,
                session_ends=session_ends,
                idle_ttl=lifetime.idle,
            )
            self.refresh_tokens.save(first)
        if self.session_attributes is not None:
            self.session_attributes.save(
                session,
                self.kept_claims(signed_in),
                self._kept_for(expires_at if self.refresh_tokens is not None else None),
            )
        publish_event(
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
            expires_in=int(self.access_ttl.total_seconds()),
            session=session,
            scope=chosen,
        )

    def _require_revocations(self) -> Revocations:
        """The revocations given — `ProgrammingError` without them: nothing can be taken back."""
        if self.revocations is None:
            raise exceptions.ProgrammingError("taking tokens back needs revocations=")
        return self.revocations

    def _longest_session(self) -> timedelta:
        """The longest a session of the issuer's own lifetime may still last from now."""
        return (self.session_ttl or self.idle_ttl) + self.access_ttl

    def _revoked_until(self, session: str, subject: str) -> datetime:
        """How long a revocation of `session` must hold: until its live token stops working,
        then one more access token — read from its subject's sessions; the issuer's longest
        session when it is not found there."""
        if self.refresh_tokens is not None and subject:
            for record in self.refresh_tokens.sessions_of(subject):
                if record.session == session:
                    return record.ends_at + self.access_ttl
        return self._now() + self._longest_session()

    def _end_session(self, session: str, until: datetime) -> None:
        """`session` refused until `until`, and what it kept forgotten."""
        self._require_revocations().revoke_session(session, until)
        if self.session_attributes is not None:
            self.session_attributes.delete(session)

    def revoke_session(self, session: str, subject: str = "") -> None:
        """End `session` now — a logout, "sign out this device" from `sessions_of`, an
        authorization code replayed: its refresh tokens and access tokens refused at once, what
        it kept forgotten. `subject` names whose it was in the `SignedOut` announced, and finds
        how long the revocation must hold."""
        self._end_session(session, self._revoked_until(session, subject))
        publish_event(
            self.publisher,
            SignedOut(entity_type=SUBJECT, entity_id=subject, session=session),
        )

    def _revoke_replayed_session(self, record: RefreshRecord) -> exceptions.Unauthenticated:
        """A refresh token used again past `reuse_grace` — stolen or leaked: its whole session
        revoked, logged and announced."""
        logger.warning(
            "a refresh token was reused: its session is revoked",
            subject=record.subject,
            session=record.session[:12],
        )
        self._end_session(record.session, record.ends_at + self.access_ttl)
        publish_event(
            self.publisher,
            RefreshTokenReused(
                entity_type=SUBJECT, entity_id=record.subject, session=record.session
            ),
        )
        return exceptions.Unauthenticated("refresh token reused: the session was revoked")

    def _rotate(
        self,
        refresh_tokens: RefreshTokens,
        record: RefreshRecord,
        successor: RefreshRecord,
        now: datetime,
    ) -> None:
        """`record` rotated to `successor`.

        1. Not rotated yet: rotated atomically, `successor` stored.
        2. Rotated — earlier, or by a refresh racing this one —: within `reuse_grace` of it,
           one client refreshing twice at once (two tabs, requests in parallel), so
           `successor` is stored beside the token the first refresh got, in the same session.
        3. Past `reuse_grace`: a replay — the session revoked.
        """
        if record.rotated_at is None and refresh_tokens.rotate(record.digest, successor, now):
            return
        rotated = (
            record if record.rotated_at is not None else refresh_tokens.find(record.digest)
        )
        if rotated is None or rotated.rotated_at is None:
            raise exceptions.Unauthenticated("unknown refresh token")
        if now - rotated.rotated_at > self.reuse_grace:
            raise self._revoke_replayed_session(rotated)
        refresh_tokens.save(successor)

    def refresh(
        self,
        refresh_token: str,
        client: str | None = None,
        scopes: Iterable[str] | None = None,
    ) -> IssuedTokens:
        """A new pair for a refresh token, which is rotated — for `client` alone when it is
        named: a refresh token issued to one client is never another's (RFC 6749 §6). `scopes`,
        when given, narrows the access token to a subset of what the session was granted; the
        refresh token keeps the whole grant.

        1. Unknown, past its idle time or its session's end, or issued to another client than
           the one named: `Unauthenticated`, the token left unused.
        2. Its session revoked, or its subject signed out everywhere since: `Unauthenticated`.
        3. `scopes` beyond the grant: `OAuthError(invalid_scope)`, the token left unused.
        4. With `session_attributes`, what the session kept — gone, the session ended:
           `Unauthenticated`.
        5. Rotated — or, used again within `reuse_grace`, a token beside the first refresh's —
           and past it, a replay that revokes the session.
        Final: a new access token of the same session — the identity of its sign-in, with what
        it kept, resolved again by `resolvers` — and a new refresh token; what the session keeps
        lives as long as the new one.
        """
        if self.refresh_tokens is None:
            raise exceptions.ProgrammingError(
                "this TokenIssuer has no refresh token store: give it refresh_tokens="
            )
        now = self._now()
        record = self.refresh_tokens.find(digest(refresh_token))
        if record is None:
            raise exceptions.Unauthenticated("unknown refresh token")
        if now >= record.ends_at:
            raise exceptions.Unauthenticated("session expired")
        if client is not None and record.client != client:
            raise exceptions.Unauthenticated("refresh token issued to another client")
        refusal = (
            self._require_revocations()
            .state(record.subject, session=record.session)
            .revocation_reason(record.authenticated_at)
        )
        if refusal is not None:
            raise exceptions.Unauthenticated(refusal)
        granted = record.scopes if scopes is None else frozenset(scopes)
        if not granted <= record.scopes:
            raise exceptions.OAuthError(
                exceptions.OAuthErrorCode.INVALID_SCOPE,
                "a scope the session was not granted",
            )
        kept: Mapping[str, Any] = {}
        if self.session_attributes is not None:
            found = self.session_attributes.find(record.session)
            if found is None:
                raise exceptions.Unauthenticated("the session ended")
            kept = found
        successor_token = random_token("rt_")
        successor = self._build_refresh_record(successor_token, record, now)
        self._rotate(self.refresh_tokens, record, successor, now)
        if self.session_attributes is not None:
            self.session_attributes.save(
                record.session, kept, self._kept_for(successor.ends_at)
            )
        signed_in = Identity(
            subject=record.subject,
            kind=IdentityKind(record.kind),
            tenant=record.tenant,
            roles=record.roles,
            scopes=granted,
            client=record.client,
            claims=kept,
            session=record.session,
        )
        access = self._sign_access_token(
            self._resolve_claims(signed_in),
            record.session,
            record.authenticated_at,
            record.assurance,
            granted,
            record.client,
            record.audience,
        )
        return IssuedTokens(
            access_token=SecretStr(access),
            refresh_token=SecretStr(successor_token),
            expires_in=int(self.access_ttl.total_seconds()),
            session=record.session,
            scope=granted,
        )

    def revoke(self, token: str, client: str | None = None) -> None:
        """A logout: the session of `token` — an access or a refresh token — ends now, its
        access tokens refused at once (`revoke_session`). A token this issuer did not issue is
        left alone, and so is one issued to another client than `client` when it is named (RFC
        7009 §2.1): a client takes back only its own tokens, and nobody learns which tokens
        exist."""
        self._require_revocations()
        if self.refresh_tokens is not None and not jws.looks_like_jws(token):
            record = self.refresh_tokens.find(digest(token))
            if record is not None and (client is None or record.client == client):
                self.revoke_session(record.session, record.subject)
            return
        try:
            parsed = jws.parse(token)
            key = self.keys.verification().key_for(parsed.key_id, parsed.algorithm)
        except exceptions.Unauthenticated:
            return
        if (
            not key.verify(parsed.signing_input, parsed.signature)
            or parsed.claims.get("iss") != self.issuer
            or (client is not None and parsed.claims.get("client_id") != client)
        ):
            return
        session = parsed.claims.get("sid")
        if isinstance(session, str):
            self.revoke_session(session, str(parsed.claims.get("sub", "")))

    def sessions_of(self, subject: str) -> list[RefreshRecord]:
        """The sessions `subject` is signed in on — one each, its newest token; none it signed
        out of, nor any begun before it signed out everywhere — oldest first."""
        if self.refresh_tokens is None:
            return []
        newest: dict[str, RefreshRecord] = {}
        for one in self.refresh_tokens.sessions_of(subject):
            found = newest.get(one.session)
            if found is None or one.issued_at >= found.issued_at:
                newest[one.session] = one
        revocations = self._require_revocations()
        return sorted(
            (
                one
                for one in newest.values()
                if revocations.state(subject, session=one.session).revocation_reason(
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
        revocations = self._require_revocations()
        sessions = (
            [] if self.refresh_tokens is None else self.refresh_tokens.sessions_of(subject)
        )
        until = max(
            [self._now() + self._longest_session()]
            + [record.ends_at + self.access_ttl for record in sessions]
        )
        revocations.revoke_subject(subject, until)
        for record in sessions:
            self._end_session(record.session, record.ends_at + self.access_ttl)
        publish_event(
            self.publisher,
            SignedOutEverywhere(entity_type=SUBJECT, entity_id=subject),
        )

    def trusted_issuer(self) -> TrustedIssuer:
        """What a verifier trusts about this issuer — its name, audience and the resources it
        serves, keys and how its claims
        read: `JwtProvider.for_token_issuer(issuer)` verifies by it, and another service of the
        platform by `TrustedIssuer(..., keys=PublicKeys(issuer.jwks()["keys"]), mapping=TokenIssuer.mapping)`.
        """
        return TrustedIssuer(
            self.issuer,
            audience=self.audience,
            keys=self.keys.verification(),
            mapping=CLAIM_MAPPING,
            required=frozenset({"exp", "sub", "sid"}),
            accepts_audience=self.serves_audience if self.resources is not None else None,
        )

    def jwks(self) -> dict[str, Any]:
        """The public keys other services verify these tokens with — asymmetric keys only."""
        return self.keys.jwks()


class KeptClaims:
    """The resolver that gives a verified identity of a session what the session keeps on the
    server — the token's own claims win a clash (#336).

    Context: a token whose session keeps nothing any more was revoked, expired or never kept
    anything under these attributes: `Unauthenticated`, so the client refreshes — and signs in
    again when its session is gone.
    """

    def __init__(self, attributes: SessionAttributes) -> None:
        self.attributes = attributes

    def resolve(self, identity: Identity) -> Identity:
        if identity.session is None:
            return identity
        kept = self.attributes.find(identity.session)
        if kept is None:
            raise exceptions.Unauthenticated("the session ended")
        return identity.model_copy(update={"claims": {**kept, **identity.claims}})
