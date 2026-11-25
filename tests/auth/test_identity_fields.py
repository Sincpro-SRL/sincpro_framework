"""What a credential says about itself is typed on the identity — scopes, client, session, token
id, when the caller authenticated, when it expires, how strongly — and an identity built the old
way, or carried as JSON by MCP, is the same as before."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from sincpro_framework.auth import Assurance, Credentials, Identity
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.auth.server import (
    ApiKey,
    ApiKeyProvider,
    InMemoryApiKeys,
    ServiceTokenProvider,
)
from sincpro_framework.auth.server.adapters.resource_server.service_tokens import HEADER


def test_an_identity_built_the_old_way_has_every_new_field_at_its_default() -> None:
    identity = Identity.user("user:1", tenant="bo", permissions={"a.b.c"})
    assert identity.scopes == frozenset() and identity.assurance == Assurance()
    assert (identity.client, identity.session, identity.token_id) == (None, None, None)
    assert (identity.authenticated_at, identity.expires_at) == (None, None)
    assert Identity.user("user:1", scopes={"openid"}).scopes == {"openid"}


def test_an_identity_carried_as_json_comes_back_whole() -> None:
    identity = Identity.user(
        "user:1", tenant="bo", permissions={"a.b"}, scopes={"s"}
    ).model_copy(
        update={
            "session": "sid-1",
            "token_id": "jti-1",
            "authenticated_at": datetime(2026, 11, 20, 10, tzinfo=UTC),
            "expires_at": datetime(2026, 11, 20, 11, tzinfo=UTC),
            "assurance": Assurance(level="mfa", methods=frozenset({"pwd", "otp"})),
            "actor": Identity.service("billing"),
        }
    )
    assert Identity.model_validate(identity.model_dump(mode="json")) == identity


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_a_service_token_says_its_id_and_expiry_and_ends_on_the_clock_it_is_given() -> None:
    clock = Clock(1_900_000_000.0)
    tokens = ServiceTokenProvider("orders", {"k": SecretStr("shared")}, clock=clock)
    first, second = tokens.token_for(Identity.user("user:1")), tokens.token_for(
        Identity.user("user:1")
    )
    identity = tokens.authenticate(Credentials(transport="service", headers={HEADER: first}))
    other = tokens.authenticate(Credentials(transport="service", headers={HEADER: second}))
    assert identity is not None and other is not None
    assert identity.token_id and identity.token_id != other.token_id
    assert identity.expires_at == datetime.fromtimestamp(1_900_000_060, UTC)
    clock.now += 60 + 5  # past its expiry and the replicas' leeway
    with pytest.raises(exceptions.Unauthenticated, match="service token expired"):
        tokens.authenticate(Credentials(transport="service", headers={HEADER: first}))


def test_an_api_key_carries_its_scopes_and_stops_at_its_expiry() -> None:
    now = datetime(2026, 11, 20, tzinfo=UTC)
    keys = InMemoryApiKeys()
    secret = keys.issue(
        ApiKey(
            subject="apikey:bot",
            scopes=frozenset({"read"}),
            expires_at=now + timedelta(days=1),
        )
    )
    moments = [now]
    provider = ApiKeyProvider(keys, now=lambda: moments[0])
    identity = provider.authenticate(
        Credentials(transport="http", headers={"x-api-key": secret})
    )
    assert identity is not None and identity.scopes == {"read"}
    assert identity.expires_at == now + timedelta(days=1)
    moments[0] = now + timedelta(days=1)
    with pytest.raises(exceptions.Unauthenticated, match="expired api key"):
        provider.authenticate(Credentials(transport="http", headers={"x-api-key": secret}))


def test_a_service_token_signed_a_moment_ahead_by_another_replica_is_accepted() -> None:
    ahead = ServiceTokenProvider("orders", {"k": "shared"}, clock=lambda: 1_900_000_101.1)
    here = ServiceTokenProvider("orders", {"k": "shared"}, clock=lambda: 1_900_000_100.6)
    token = ahead.token_for(Identity.user("user:1"))
    assert here.authenticate(Credentials(transport="service", headers={HEADER: token}))
