"""The tokens a service that manages its own accounts hands out: a short access token its own
provider accepts, an opaque refresh token rotated on every use — a replay revoking the session —
a logout and "sign out everywhere" holding at once, and other services verifying with the JWKS.
"""

import threading
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest

from sincpro_framework.auth import (
    Assurance,
    Credentials,
    Identity,
    IdentityKind,
    InMemoryRefreshTokens,
    InMemoryRevocations,
    IssuedTokens,
    Issuer,
    JwtProvider,
    KeyValueRefreshTokens,
    PrivateKey,
    PublicKeys,
    RefreshTokens,
    SigningKeys,
    TokenIssuer,
)
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.data_layer.store.adapters.redis import RedisKeyValue
from sincpro_framework.runtime.testing.contracts.refresh_tokens import RefreshTokensContract

SECRET = "a token secret of thirty-two bytes!"


class Clock:
    def __init__(self) -> None:
        self.now = 1_900_000_000.0

    def __call__(self) -> float:
        return self.now


def issuer(clock: Clock | None = None, **options) -> TokenIssuer:
    clock = clock or Clock()
    moment = lambda: datetime.fromtimestamp(clock.now, UTC)  # noqa: E731 — the test's clock
    return TokenIssuer(
        "https://colegio.bo",
        audience="colegio",
        keys=SigningKeys.hmac({"2026-09": SECRET}),
        refresh_tokens=InMemoryRefreshTokens(now=moment),
        revocations=InMemoryRevocations(now=moment),
        clock=clock,
        **options,
    )


def refresh_of(tokens: IssuedTokens) -> str:
    assert tokens.refresh_token is not None
    return tokens.refresh_token.get_secret_value()


def bearer(tokens) -> Credentials:
    return Credentials(
        transport="http",
        headers={"authorization": f"Bearer {tokens.access_token.get_secret_value()}"},
    )


ANA = Identity.user("user:MFAQ0123", tenant="cauor", roles={"colegiado"})


def test_what_the_issuer_signs_its_provider_reads_as_the_same_person() -> None:
    clock = Clock()
    sessions = issuer(clock)
    tokens = sessions.issue(
        ANA, scopes={"dues"}, assurance=Assurance(methods=frozenset({"pwd"}))
    )
    identity = sessions.provider().authenticate(bearer(tokens))
    assert identity is not None
    assert (identity.subject, identity.kind, identity.tenant) == (
        "user:MFAQ0123",
        IdentityKind.USER,
        "cauor",
    )
    assert identity.roles == {"colegiado"} and identity.scopes == {"dues"}
    assert identity.session == tokens.session and identity.assurance.methods == {"pwd"}
    answer = tokens.answer()
    assert answer["token_type"] == "Bearer" and answer["expires_in"] == 600
    assert answer["refresh_token"].startswith("rt_")
    assert "rt_" not in repr(tokens) and tokens.access_token.get_secret_value() not in repr(
        tokens
    )


def test_a_refresh_spends_the_token_and_keeps_the_session() -> None:
    sessions = issuer()
    first = sessions.issue(ANA)
    second = sessions.refresh(refresh_of(first))
    assert second.session == first.session and second.refresh_token != first.refresh_token
    assert sessions.provider().authenticate(bearer(second)) is not None


def test_a_refresh_token_used_twice_at_once_is_refused_the_session_kept() -> None:
    clock = Clock()
    sessions = issuer(clock)
    first = sessions.issue(ANA)
    spent = refresh_of(first)
    second = sessions.refresh(spent)
    clock.now += 5
    with pytest.raises(exceptions.Unauthenticated, match="already used"):
        sessions.refresh(spent)
    assert sessions.refresh(refresh_of(second))


def test_a_refresh_token_replayed_later_revokes_the_whole_session() -> None:
    clock = Clock()
    sessions = issuer(clock)
    first = sessions.issue(ANA)
    spent = refresh_of(first)
    second = sessions.refresh(spent)
    clock.now += 60
    with pytest.raises(exceptions.Unauthenticated, match="session was revoked"):
        sessions.refresh(spent)
    with pytest.raises(exceptions.Unauthenticated, match="session revoked"):
        sessions.refresh(refresh_of(second))
    with pytest.raises(exceptions.Unauthenticated, match="session revoked"):
        sessions.provider().authenticate(bearer(second))


def test_racing_refreshes_with_one_token_give_one_new_pair() -> None:
    sessions = issuer()
    spent = refresh_of(sessions.issue(ANA))
    barrier = threading.Barrier(16)
    outcomes: list[bool] = []

    def refresh() -> None:
        barrier.wait()
        try:
            sessions.refresh(spent)
            outcomes.append(True)
        except exceptions.Unauthenticated:
            outcomes.append(False)

    threads = [threading.Thread(target=refresh) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count(True) == 1


def test_a_logout_refuses_the_access_token_at_once() -> None:
    sessions = issuer()
    tokens, other = sessions.issue(ANA), sessions.issue(ANA)
    sessions.revoke(tokens.access_token.get_secret_value())
    with pytest.raises(exceptions.Unauthenticated, match="session revoked"):
        sessions.provider().authenticate(bearer(tokens))
    assert sessions.provider().authenticate(bearer(other)) is not None
    sessions.revoke(refresh_of(other))
    with pytest.raises(exceptions.Unauthenticated):
        sessions.provider().authenticate(bearer(other))


def test_signing_out_everywhere_ends_every_session_and_a_new_sign_in_works() -> None:
    clock = Clock()
    sessions = issuer(clock)
    before = sessions.issue(ANA)
    clock.now += 1.7  # a fraction into a second, as a real clock is
    sessions.revoke_subject(ANA.subject)
    with pytest.raises(exceptions.Unauthenticated, match="session revoked"):
        sessions.provider().authenticate(bearer(before))
    with pytest.raises(exceptions.Unauthenticated, match="session revoked"):
        sessions.refresh(refresh_of(before))
    again = sessions.issue(ANA)  # the same second: a password changed, then a new sign-in
    assert sessions.provider().authenticate(bearer(again)) is not None


def test_a_token_of_an_identity_provider_signed_out_everywhere_is_refused_by_the_second() -> (
    None
):
    clock = Clock()
    revocations = InMemoryRevocations(now=lambda: datetime.fromtimestamp(clock.now, UTC))
    clock.now += 0.6
    revocations.revoke_subject(ANA.subject, until=datetime.fromtimestamp(clock.now + 60, UTC))
    same_second = datetime.fromtimestamp(int(clock.now), UTC)
    second_before = datetime.fromtimestamp(int(clock.now) - 1, UTC)
    assert revocations.state(ANA.subject).refusal(same_second) is None
    assert revocations.state(ANA.subject).refusal(second_before) == "signed out"


def test_a_session_ends_idle_or_at_most() -> None:
    clock = Clock()
    sessions = issuer(clock, idle_for=timedelta(hours=1), at_most=timedelta(hours=3))
    tokens = sessions.issue(ANA)
    clock.now += 3601
    with pytest.raises(exceptions.Unauthenticated, match="session expired"):
        sessions.refresh(refresh_of(tokens))
    kept = sessions.issue(ANA)
    for _ in range(5):
        clock.now += 3000
        try:
            kept = sessions.refresh(refresh_of(kept))
        except exceptions.Unauthenticated as ended:
            assert "expired" in ended.reason
            break
    else:
        raise AssertionError("a session outlived at_most")


def test_the_sessions_a_person_is_signed_in_on() -> None:
    sessions = issuer()
    one, two = sessions.issue(ANA), sessions.issue(ANA)
    sessions.refresh(refresh_of(one))
    assert sorted(one.family for one in sessions.sessions_of(ANA.subject)) == sorted(
        [one.session, two.session]
    )


def test_other_services_verify_with_the_published_keys() -> None:
    keys = SigningKeys.private({"2026-09": PrivateKey.generate("ES256")}, "ES256")
    sessions = TokenIssuer("https://colegio.bo", audience="colegio", keys=keys)
    tokens = sessions.issue(ANA)
    published = PublicKeys(sessions.jwks()["keys"])
    elsewhere = JwtProvider(
        Issuer(
            "https://colegio.bo",
            audience="colegio",
            keys=published,
            mapping=TokenIssuer.mapping,
        )
    )
    identity = elsewhere.authenticate(bearer(tokens))
    assert identity is not None and identity.subject == "user:MFAQ0123"
    with pytest.raises(exceptions.ProgrammingError, match="no JWKS"):
        issuer().jwks()


def test_embedded_permissions_are_refused_once_they_changed() -> None:
    revocations = InMemoryRevocations()
    sessions = TokenIssuer(
        "https://colegio.bo",
        audience="colegio",
        keys=SigningKeys.hmac({"k": SECRET}),
        revocations=revocations,
        embed_permissions=True,
    )
    tokens = sessions.issue(ANA.model_copy(update={"permissions": frozenset({"dues.pay"})}))
    identity = sessions.provider().authenticate(bearer(tokens))
    assert identity is not None and identity.permissions == {"dues.pay"}
    revocations.bump_permissions(ANA.subject)
    with pytest.raises(exceptions.Unauthenticated, match="permissions changed"):
        sessions.provider().authenticate(bearer(tokens))


class TestInMemoryRefreshTokens(RefreshTokensContract):
    def make_store(self) -> RefreshTokens:
        return InMemoryRefreshTokens()


class TestKeyValueRefreshTokens(RefreshTokensContract):
    def make_store(self) -> RefreshTokens:
        return KeyValueRefreshTokens(InMemoryKeyValue())


class TestRefreshTokensOnRedis(RefreshTokensContract):
    def make_store(self) -> RefreshTokens:
        return KeyValueRefreshTokens(RedisKeyValue(fakeredis.FakeRedis()))


class Granted:
    """A resolver whose grants a test changes — the project's role table."""

    def __init__(self, permissions: set[str]) -> None:
        self.permissions = permissions

    def resolve(self, identity: Identity) -> Identity:
        return identity.model_copy(
            update={"permissions": identity.permissions | frozenset(self.permissions)}
        )


def test_a_refreshed_token_keeps_its_roles_and_resolves_its_permissions_anew() -> None:
    clock = Clock()
    granted = Granted({"dues.pay"})
    sessions = issuer(clock, embed_permissions=True, resolvers=[granted])
    tokens = sessions.issue(ANA)
    first = sessions.provider().authenticate(bearer(tokens))
    assert first is not None
    assert (first.roles, first.permissions) == ({"colegiado"}, {"dues.pay"})
    granted.permissions = set()
    assert sessions.revocations is not None
    sessions.revocations.bump_permissions(ANA.subject)  # what GrantAdministration does
    with pytest.raises(exceptions.Unauthenticated, match="permissions changed"):
        sessions.provider().authenticate(bearer(tokens))
    refreshed = sessions.provider().authenticate(bearer(sessions.refresh(refresh_of(tokens))))
    assert refreshed is not None
    assert (refreshed.roles, refreshed.permissions) == ({"colegiado"}, frozenset())


def test_embedding_permissions_with_refresh_tokens_needs_resolvers() -> None:
    with pytest.raises(exceptions.ProgrammingError, match="needs resolvers="):
        issuer(embed_permissions=True)
