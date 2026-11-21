"""How this service authenticates as an OAuth client, and how a provider's identities are
resolved further — each written once, never showing a secret."""

import base64
from urllib.parse import unquote

from pydantic import SecretStr

from sincpro_framework.auth import (
    AccessControl,
    AuthProvider,
    Credentials,
    Identity,
    Permission,
)
from sincpro_framework.auth.client import (
    ClientSecretBasic,
    ClientSecretJwt,
    ClientSecretPost,
    PublicClient,
)
from sincpro_framework.auth.client.adapters.client_authentication import CLIENT_ASSERTION_TYPE
from sincpro_framework.auth.grants import RolePermissions
from sincpro_framework.auth.infrastructure import jws
from sincpro_framework.auth.server import Resolving, StaticProvider

ENDPOINT = "https://auth.acme.bo/token"


def test_basic_form_encodes_the_id_and_secret_before_base64() -> None:
    fields, headers = ClientSecretBasic("billing api", SecretStr("a:b%c")).applied(
        [("grant_type", "client_credentials")], ENDPOINT
    )
    scheme, encoded = headers["authorization"].split(" ")
    client, secret = base64.b64decode(encoded).decode().split(":")
    assert scheme == "Basic" and (client, secret) == ("billing%20api", "a%3Ab%25c")
    assert (unquote(client), unquote(secret)) == ("billing api", "a:b%c")
    assert fields == [("grant_type", "client_credentials")]


def test_post_puts_the_id_and_secret_in_the_form() -> None:
    fields, headers = ClientSecretPost("billing", "s3cret").applied([("a", "1")], ENDPOINT)
    assert fields == [("a", "1"), ("client_id", "billing"), ("client_secret", "s3cret")]
    assert headers == {}


def test_a_jwt_assertion_is_for_the_endpoint_and_signed_with_the_secret() -> None:
    fields, _ = ClientSecretJwt(
        "billing", "a long shared secret", clock=lambda: 1_900_000_000
    ).applied([], ENDPOINT)
    named = dict(fields)
    assert named["client_assertion_type"] == CLIENT_ASSERTION_TYPE
    assertion = jws.parse(named["client_assertion"])
    assert jws.HmacKey.of(None, "a long shared secret").verifies(assertion)
    assert (assertion.claims["iss"], assertion.claims["sub"], assertion.claims["aud"]) == (
        "billing",
        "billing",
        ENDPOINT,
    )
    assert assertion.claims["exp"] - assertion.claims["iat"] == 60 and assertion.claims["jti"]


def test_no_client_shows_its_secret() -> None:
    for client in (
        ClientSecretBasic("c", "s3cret"),
        ClientSecretPost("c", "s3cret"),
        ClientSecretJwt("c", "s3cret"),
        PublicClient("c"),
    ):
        assert "s3cret" not in repr(client) and "'c'" in repr(client)
    assert PublicClient("cli").applied([], ENDPOINT) == ([("client_id", "cli")], {})


class Perm(Permission):
    READ = "books.read"
    WRITE = "books.write"


class Tagging:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def resolve(self, identity: Identity) -> Identity:
        return identity.model_copy(update={"roles": identity.roles | {self.tag}})


def test_a_project_provider_is_resolved_in_order_and_keeps_its_name() -> None:
    base = StaticProvider({"t-1": Identity.user("user:1", roles={"reader"})}, name="sessions")
    provider = Resolving(
        base,
        Tagging("editor"),
        RolePermissions({"reader": {Perm.READ}, "editor": {Perm.WRITE}}),
    )
    assert provider.name == "sessions"
    identity = provider.authenticate(
        Credentials(transport="http", headers={"authorization": "Bearer t-1"})
    )
    assert identity is not None and identity.permissions == {Perm.READ, Perm.WRITE}
    assert provider.authenticate(Credentials(transport="http")) is None
    auth = AccessControl[Perm](providers=[provider])
    vouched = auth.authenticate(
        Credentials(transport="http", headers={"authorization": "Bearer t-1"})
    )
    assert vouched.provider == "sessions" and isinstance(provider, AuthProvider)
