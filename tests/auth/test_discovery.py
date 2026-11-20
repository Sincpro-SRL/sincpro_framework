"""An authorization server's metadata is discovered once and cached: OpenID Connect Discovery
first, RFC 8414 next, a document naming another issuer refused, the last good one served while
the server is down — and nothing fetched until it is asked for."""

from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework.auth import EndpointUnavailable, OidcDiscovery
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.data_layer.caching import Cache
from sincpro_framework.runtime.testing import ManualClock, ScriptedHttp

ISSUER = "https://auth.acme.bo/realms/acme"
OIDC = f"{ISSUER}/.well-known/openid-configuration"
OAUTH = "https://auth.acme.bo/.well-known/oauth-authorization-server/realms/acme"
DOCUMENT = {
    "issuer": ISSUER,
    "jwks_uri": f"{ISSUER}/certs",
    "token_endpoint": f"{ISSUER}/token",
    "introspection_endpoint": f"{ISSUER}/introspect",
}


def test_the_openid_document_is_read_once_and_cached() -> None:
    http = ScriptedHttp({OIDC: DOCUMENT})
    discovery = OidcDiscovery(http=http)
    assert http.sent == []
    first, again = discovery.of(ISSUER), discovery.of(ISSUER)
    assert first == again and first.jwks_uri == f"{ISSUER}/certs"
    assert first.endpoint("token_endpoint") == f"{ISSUER}/token"
    assert http.count(OIDC) == 1


def test_rfc_8414_is_asked_when_there_is_no_openid_document() -> None:
    http = ScriptedHttp({OAUTH: DOCUMENT})
    assert OidcDiscovery(http=http).of(ISSUER).token_endpoint == f"{ISSUER}/token"
    assert [one.target for one in http.sent] == [OIDC, OAUTH]


def test_a_document_naming_another_issuer_is_refused() -> None:
    http = ScriptedHttp({OIDC: {**DOCUMENT, "issuer": "https://evil.example"}})
    with pytest.raises(EndpointUnavailable, match="names issuer"):
        OidcDiscovery(http=http).of(ISSUER)


def test_what_the_server_does_not_publish_is_a_programming_error() -> None:
    metadata = OidcDiscovery(http=ScriptedHttp({OIDC: DOCUMENT})).of(ISSUER)
    with pytest.raises(exceptions.ProgrammingError, match="device_authorization_endpoint"):
        metadata.endpoint("device_authorization_endpoint")


def test_the_last_good_document_stands_in_while_the_server_is_down() -> None:
    clock = ManualClock(datetime(2026, 11, 20, tzinfo=UTC))
    http = ScriptedHttp({OIDC: DOCUMENT})
    discovery = OidcDiscovery(
        http=http,
        cache=Cache(now=clock.now),
        ttl=timedelta(hours=1),
        serve_stale_for=timedelta(days=1),
    )
    assert discovery.of(ISSUER).issuer == ISSUER
    http.route(OIDC, EndpointUnavailable(OIDC, "could not be reached"))
    clock.advance(hours=2)
    assert discovery.of(ISSUER).issuer == ISSUER


def test_with_nothing_cached_a_server_down_is_unavailable() -> None:
    http = ScriptedHttp(
        {OIDC: ScriptedHttp.answer({}, status=503, headers={"retry-after": "7"})}
    )
    with pytest.raises(EndpointUnavailable) as raised:
        OidcDiscovery(http=http).of(ISSUER)
    assert raised.value.retry_after == timedelta(seconds=7)
