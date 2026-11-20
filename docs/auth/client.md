# Presenting what goes out — token sources

[The overview](README.md) and [the providers](server.md) are about who calls this service. This
page is the other half: what this service presents when it calls another — a bank, SIAT, a
tenant's Odoo, a peer — fetched once, shared, refreshed, and never another tenant's. Every block
on this page runs, in order, in `tests/docs/test_persistence_guide.py`, against
`runtime.testing.FakeTokenEndpoint`.

| The API asks for… | Source | Extra |
|---|---|---|
| a fixed token, an API key, a username and password | `StaticToken`, `ApiKeyHeader`, `BasicCredentials` | — |
| a token from its authorization server, for this service | `ClientCredentials` (RFC 6749 §4.4) | `private_key_jwt`: `[jwt]` |
| a credential per tenant | `TokenSources` | — |

**Where a source lives.** In the adapter that calls the API, injected with `add_dependency` in
`infrastructure/dependencies.py` — never in a use case, never in the context (whose values
travel to other services). One source per API: the audience is the source's, never read from a
URL, so a token for one API is never sent to another.

## Static credentials

```python
from sincpro_framework.auth import ApiKeyHeader, BasicCredentials, OutgoingRequest, StaticToken

request = OutgoingRequest(url="https://pilotosiatservicios.impuestos.gob.bo/v2/x", method="POST")
siat = StaticToken("the-siat-token", header="apikey", scheme="TokenApi")
assert siat.headers_for(request) == {"apikey": "TokenApi the-siat-token"}
assert ApiKeyHeader("k-1", header="odoo-api-key").headers_for(request) == {"odoo-api-key": "k-1"}
assert BasicCredentials("billing", "pw").headers_for(request)["authorization"].startswith("Basic ")
```

A secret is held as given — text or a pydantic `Secret` from the settings — and read when a request
is made; no `repr` shows it.

## Client credentials: a token for this service, shared

```python
from sincpro_framework.auth import ClientCredentials, TokenCache, call_with_credentials
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.runtime.testing import FakeTokenEndpoint

server = FakeTokenEndpoint("https://auth.bank.bo", clients={"billing": "s3cret"})
bank = ClientCredentials(
    client_id="billing",
    client_secret="s3cret",                    # settings.bank_secret, a Secret
    issuer="https://auth.bank.bo",             # the token endpoint, discovered
    scope=["payments"],
    http=server,                               # UrllibHttp() when not given
    cache=TokenCache(InMemoryKeyValue()),      # the running context's store when not given
)

api = server.resource()                        # the bank's API, as a test sees it
answered = call_with_credentials(bank, OutgoingRequest(url="https://api.bank.bo/qr"), api)
assert answered.status == 200
assert [call_with_credentials(bank, request, api).status for _ in range(3)] == [200, 200, 200]
assert server.grants("client_credentials") == 1                     # fetched once
```

| Behaviour | How |
|---|---|
| one fetch for every thread and replica | `TokenCache` is the framework's `Cache` on the running context's store: one caller fetches, the others wait for it |
| served until shortly before it expires | `expires_at - skew` (30 s, at most half its life); the expiry is when the answer arrived plus `expires_in` — five minutes when not said, never forever |
| fetched again when the API refuses it | `call_with_credentials` (or `requests_auth`, `httpx_auth`) sends once more after a 401 — never after a 403, never in a loop |
| an API refusing every token | a token younger than `min_reuse` (10 s) is not fetched again: one retry per call, not a fetch per request |
| the token endpoint asks to wait | `Retry-After` remembered: the same `EndpointUnavailable` (503) until then, the endpoint not asked |
| this service's client refused | `CredentialsRejected` — this service's configuration, never `Unauthenticated` (the caller would be told to log in again, and loop) |
| cached on Redis | the token itself is stored; `TokenCache(seal=AesGcmSealer(key))` keeps it encrypted (`[jwt]`) |

`client=` takes any `ClientAuthentication`: `ClientSecretBasic` (the default for an id and a
secret), `ClientSecretPost`, `ClientSecretJwt`, `PrivateKeyJwt` (`[jwt]`). `audience=` and
`resources=` (RFC 8707) name the API the token is for.

## Any HTTP client

```python
import httpx

transport = httpx.MockTransport(
    lambda r: httpx.Response(200 if server.accepts(r.headers["authorization"].split()[1]) else 401)
)
from sincpro_framework.auth import httpx_auth

with httpx.Client(transport=transport, auth=httpx_auth(bank)) as client:
    assert client.get("https://api.bank.bo/qr").status_code == 200
```

`requests_auth(bank)` is the same for `requests` (`requests.post(url, auth=requests_auth(bank))`);
`httpx_auth` serves `httpx.AsyncClient` too, the source running on a thread, never on the event
loop. Neither library is imported until its hook is asked for.

## A credential per tenant — never another's

```python
from sincpro_framework.auth import Identity, NoCredentialsFor, TokenSources, security

siat_by_tenant = TokenSources({
    "bo-001": StaticToken("token-of-001", header="apikey", scheme="TokenApi"),
    "bo-002": StaticToken("token-of-002", header="apikey", scheme="TokenApi"),
})
with security.as_identity(Identity.user("user:ana", tenant="bo-002")):
    sent = call_with_credentials(siat_by_tenant, request, lambda headers: headers)
assert sent == {"apikey": "TokenApi token-of-002"}

try:
    siat_by_tenant.headers_for(request.model_copy(update={"tenant": "bo-999"}))
except NoCredentialsFor:
    pass                                       # 403 — never a default token
```

The tenant is the request's, else the identity's in play. `TokenSources(function)` resolves a
source per tenant, keeps the least recently used `max_tenants`, keyed by the tenant — never by a
token.
