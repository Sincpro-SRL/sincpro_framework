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
    second = sessions.refresh(first.refresh_token.get_secret_value())  # type: ignore[union-attr]
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
    assert sessions.refresh(second.refresh_token.get_secret_value())  # type: ignore[union-attr]


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
        sessions.refresh(second.refresh_token.get_secret_value())  # type: ignore[union-attr]
    with pytest.raises(exceptions.Unauthenticated, match="session revoked"):
        sessions.provider().authenticate(bearer(second))


def test_racing_refreshes_with_one_token_give_one_new_pair() -> None:
    sessions = issuer()
    spent = sessions.issue(ANA).refresh_token.get_secret_value()  # type: ignore[union-attr]
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
    sessions.revoke(other.refresh_token.get_secret_value())  # type: ignore[union-attr]
    with pytest.raises(exceptions.Unauthenticated):
        sessions.provider().authenticate(bearer(other))


def test_signing_out_everywhere_ends_every_session_and_a_new_sign_in_works() -> None:
    clock = Clock()
    sessions = issuer(clock)
    before = sessions.issue(ANA)
    clock.now += 1
    sessions.revoke_subject(ANA.subject)
    with pytest.raises(exceptions.Unauthenticated, match="signed out"):
        sessions.provider().authenticate(bearer(before))
    with pytest.raises(exceptions.Unauthenticated, match="signed out"):
        sessions.refresh(before.refresh_token.get_secret_value())  # type: ignore[union-attr]
    clock.now += 1
    assert sessions.provider().authenticate(bearer(sessions.issue(ANA))) is not None


def test_a_session_ends_idle_or_at_most() -> None:
    clock = Clock()
    sessions = issuer(clock, idle_for=timedelta(hours=1), at_most=timedelta(hours=3))
    tokens = sessions.issue(ANA)
    clock.now += 3601
    with pytest.raises(exceptions.Unauthenticated, match="session expired"):
        sessions.refresh(tokens.refresh_token.get_secret_value())  # type: ignore[union-attr]
    kept = sessions.issue(ANA)
    for _ in range(5):
        clock.now += 3000
        try:
            kept = sessions.refresh(kept.refresh_token.get_secret_value())  # type: ignore[union-attr]
        except exceptions.Unauthenticated as ended:
            assert "expired" in ended.reason
            break
    else:
        raise AssertionError("a session outlived at_most")


def test_the_sessions_a_person_is_signed_in_on() -> None:
    sessions = issuer()
    one, two = sessions.issue(ANA), sessions.issue(ANA)
    sessions.refresh(one.refresh_token.get_secret_value())  # type: ignore[union-attr]
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
