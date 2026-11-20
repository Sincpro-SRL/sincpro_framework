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

## Acting for the caller: token exchange — RFC 8693

A call to another service can carry a token for that service acting for the caller in play —
exchanged for the bearer the caller presented, which the entrypoint holds while the call runs
(`security.presented`), never on the identity. `PresentingProvider` names the source per service,
so `remote_execution` — which sends `security.identity_headers(context)` — carries it with no
change to its transports.

```python
from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    ClientSecretBasic,
    Credentials,
    PresentingProvider,
    StaticProvider,
    TokenExchange,
)

orders_token = TokenExchange(
    ClientSecretBasic("billing", "s3cret"),
    audience="orders-api",
    issuer="https://auth.bank.bo",
    http=server,
    cache=TokenCache(InMemoryKeyValue()),
)
exchange_auth = AccessControl(providers=[
    StaticProvider({"ana-token": Identity.user("user:ana", tenant="bo-001")}),
    PresentingProvider({"orders": orders_token}),        # calls to the "orders" context
])
billing = UseFramework("billing-exchanges", log_after_execution=False)


class CommandShip(DataTransferObject):
    order: str


class Carried(DataTransferObject):
    headers: dict[str, str]


@billing.feature(CommandShip)
@exchange_auth.authenticated
class Ship(Feature):
    def execute(self, dto: CommandShip) -> Carried:
        return Carried(headers=security.identity_headers("orders"))   # what remote_execution sends


exchange_auth.on(billing)
with security.authenticated_as(billing, Credentials(transport="http",
                                                    headers={"authorization": "Bearer ana-token"})):
    carried = billing(CommandShip(order="O-1"), Carried)
assert carried.headers["authorization"].startswith("Bearer at-")
```

A caller's token the server will not exchange is the caller's to fix: `Unauthenticated`. Outside
an authenticated call there is nothing to exchange: `ProgrammingError`.

## Holding consent: refresh tokens and device code

`RefreshingToken` keeps the grant of an API that issues refresh tokens — a tenant's Odoo consented
once, a CLI authorized by device code (`DeviceCode`, RFC 8628) — in `HeldGrants`
(`KeyValueHeldGrants` on the context's store, sealed when given a `Sealer`). A refresh runs under a
lease, so one replica refreshes while the others wait for its answer — past `wait` with the
refresh still running elsewhere, they answer `EndpointUnavailable` (503) and never refresh without
the lease — and it is stored by compare-and-set: a server that rotates refresh tokens never sees
one used twice (and revokes the grant for it). `invalid_grant` drops the grant: `GrantRejected`, consent to be given again.

```python
from sincpro_framework.auth import DeviceCode, InMemoryHeldGrants, PublicClient, RefreshingToken

device_server = FakeTokenEndpoint("https://auth.sincpro.dev", clients={"sincpro-cli": ""})
shown = []
issued = DeviceCode(
    PublicClient("sincpro-cli"),
    show=shown.append,                                   # print(prompt.verification_uri, …)
    issuer="https://auth.sincpro.dev",
    http=device_server,
    sleep=lambda seconds: device_server.approve(shown[0].user_code),   # the person approves
).authorize()

cli = RefreshingToken(PublicClient("sincpro-cli"), InMemoryHeldGrants(), key="sincpro-cli",
                      issuer="https://auth.sincpro.dev", http=device_server,
                      store=InMemoryKeyValue())
cli.seed(issued)
assert cli.headers_for(request)["authorization"].startswith("Bearer at-")
```
