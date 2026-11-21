# Verifying what arrives — the providers

[The overview](README.md) says how a use case declares what it requires and how a provider
answers. This page is the providers the framework ships for what a caller presents, and what
each checks. Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

| A caller presents… | Provider | Extra |
|---|---|---|
| a JWT of Keycloak, Entra, Google, or of this service's own issuer | `JwtProvider` | asymmetric keys: `[jwt]` |
| an API key | `ApiKeyProvider` | — |
| a peer Sincpro service's token | `ServiceTokenProvider` | — |
| a fixed token, in a test | `StaticProvider` | — |

## JWTs: the issuers a service trusts

An `Issuer` is one identity provider this service trusts: its `iss`, the audience its tokens must
name, where its keys are, how its claims read. `JwtProvider` routes a token by its `iss` — read
before it is verified, only to route — and checks everything else against that issuer, never
against what the token says about itself.

```python
import time

from sincpro_framework.auth import Credentials
from sincpro_framework.auth.server import ClaimMapping, Issuer, JwtProvider, SharedSecrets
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.infrastructure import jws

KEYCLOAK = "https://auth.sincpro.com.bo/realms/sincpro"
SECRET = "a shared secret of thirty-two by!"

keycloak = Issuer(
    KEYCLOAK,
    audience="billing-api",
    keys=SharedSecrets({"2026-09": SECRET}),       # in production: Jwks(...) — below
    mapping=ClaimMapping.keycloak(client="billing-api"),
)
provider = JwtProvider(keycloak)


def token(**claims) -> str:
    """A token as Keycloak would sign it — what a test hands the provider."""
    now = time.time()
    payload = {"iss": KEYCLOAK, "aud": "billing-api", "sub": "4c1d", "exp": now + 300,
               "azp": "billing-web", "sid": "s-1", "jti": "j-1", "scope": "openid",
               "realm_access": {"roles": ["app:billing"]},
               "resource_access": {"billing-api": {"roles": ["accountant"]}}, **claims}
    return jws.encode({"alg": "HS256", "kid": "2026-09"}, payload,
                      jws.HmacKey.of("2026-09", SECRET).sign)


def bearer(value: str) -> Credentials:
    return Credentials(transport="http", headers={"authorization": f"Bearer {value}"})


ana = provider.authenticate(bearer(token()))
assert ana is not None and ana.subject == "user:4c1d"
assert ana.roles == {"app:billing", "accountant"} and ana.scopes == {"openid"}
assert (ana.client, ana.session, ana.token_id) == ("billing-web", "s-1", "j-1")

try:
    provider.authenticate(bearer(token(aud="another-api")))
except exceptions.Unauthenticated as refused:
    assert refused.reason == "token is for another audience"

assert provider.authenticate(bearer(token(iss="https://elsewhere"))) is None   # not its kind
```

| Checked | How |
|---|---|
| the issuer | the `iss` names one this provider trusts; another is `None`, the next provider's |
| the algorithm | one the issuer's keys verify — never `none`, never HMAC for public keys nor the reverse (checked when the `Issuer` is built) |
| the key | the issuer's, by `kid`; a `jwk`, `jku` or `x5u` in the header is ignored; a `crit` header is refused |
| the claims | `iss` exact, `aud` names one of the audiences (an `Issuer` with none is a `ProgrammingError`), `exp`, `nbf`, `iat` with `leeway` (60 s), `required` present |
| the kind of token | the mapping's `refused_types`: Keycloak's ID and refresh tokens are JWTs signed with the same keys |
| the client | `authorized_parties`, when given: the `azp` allowed |
| revocation | `revocations=`: the token, its session, its subject signed out everywhere — one read, which also brings the permission versions the resolvers key their cache by |

A known issuer's token that fails is `Unauthenticated` with its reason; keys that cannot be
fetched are `EndpointUnavailable` (503) — the caller's token did not fail.

**How claims read.** `ClaimMapping` says which claim is the subject, the tenant, the roles, the
scopes and the client. A token whose subject claim is missing or empty is refused — never a
`user:None` every such token would share. The presets:

| Preset | Subject | Tenant | Roles | Scopes | A service when |
|---|---|---|---|---|---|
| `ClaimMapping()` | `sub` | — | `roles` | `scope`, `scp` | — |
| `ClaimMapping.keycloak(client=…)` | `sub` | `tenant=` path | `realm_access.roles`, `resource_access.<client>.roles` | `scope` | `preferred_username` starts with `service-account-` |
| `ClaimMapping.entra()` | `oid` | `tid` | `roles` | `scp` | `idtyp` is `app` |
| `ClaimMapping.google()` | `sub` | `hd` | — | `scope` | — |

The subject is prefixed — `user:<sub>`, `service:<sub>` — so a person and a service never share
one. `sid`, `jti`, `auth_time`, `exp`, `acr` and `amr` land in the identity's typed fields; every
claim stays in `identity.claims`.

## Several issuers, revocations, resolvers

One provider trusts several issuers — Keycloak for people, this service's own for its sessions.
`resolvers` turn the roles a token carries into permissions, once, when it authenticates;
`revocations` refuse a token before it expires.

```python
from datetime import UTC, datetime, timedelta

from sincpro_framework.auth import InMemoryRevocations, Permission
from sincpro_framework.auth.grants import RolePermissions


class BillingPermission(Permission):
    ISSUE_INVOICE = "billing.invoice.issue"


revocations = InMemoryRevocations()               # KeyValueRevocations() in production
own = Issuer("billing", audience="billing", keys=SharedSecrets({"own": SECRET}))
provider = JwtProvider(
    [keycloak, own],
    resolvers=[RolePermissions({"accountant": {BillingPermission.ISSUE_INVOICE}})],
    revocations=revocations,
)

ana = provider.authenticate(bearer(token()))
assert ana is not None and ana.permissions == {BillingPermission.ISSUE_INVOICE}

revocations.revoke_session("s-1", until=datetime.now(UTC) + timedelta(hours=1))   # a logout
try:
    provider.authenticate(bearer(token()))
except exceptions.Unauthenticated as refused:
    assert refused.reason == "session revoked"
```

| Revoked | Call | Refuses |
|---|---|---|
| one token | `revoke_token(jti, until)` | that token |
| a session — a logout | `revoke_session(sid, until)` | every token of the session |
| a subject — "sign out everywhere", a password changed | `revoke_subject(subject, until)` | every token issued before now |
| a subject's permissions | `bump_permissions(subject)` | nothing — the resolved permissions are resolved anew |

`until` is when the last token it covers expires: an entry lives no longer. `KeyValueRevocations`
keeps them on the running context's store — Redis in production — under digested keys; a store
that does not answer is refused (503) by default, or admitted with a warning
(`on_unavailable="admit"`). One authentication reads the store once: the token, its session, its
subject's and its tenant's permission versions in one `get_many`, and a `GrantResolver` on the
same `Revocations` is answered from that read (`remembering_versions()`).

## Where to get a token

A 401 says where to get a credential this service accepts — RFC 6750's `WWW-Authenticate`, with
the reason of the refusal and, given a `ProtectedResource`, RFC 9728's metadata URL, which an MCP
client follows to find the authorization server. A caller that presented nothing gets no error
code (RFC 6750 §3.1): there was no token to call invalid. FastAPI, gRPC (`www-authenticate` in the
trailing metadata), JSON-RPC, REST and `IdentityMiddleware` answer the same challenge.

```python
from sincpro_framework.auth.server import ProtectedResource

resource = ProtectedResource("https://billing.sincpro.com.bo/mcp", authorization_servers=[KEYCLOAK])
provider = JwtProvider(keycloak, resource=resource, realm="billing")
assert provider.challenge_for(exceptions.Unauthenticated("token expired")) == (
    'Bearer realm="billing", error="invalid_token", error_description="token expired", '
    'resource_metadata="https://billing.sincpro.com.bo/.well-known/oauth-protected-resource/mcp"'
)
```

The body of a 401 says `"anonymous": true` when the caller presented nothing. A wire of the
project's own that turned the refusal into JSON before answering rebuilds it with `refusal_from`
— from a REST problem body, a JSON-RPC error's `data` or a `refusal_body()` — so the challenge is
still the refusal's own:

```python
from sincpro_framework.auth import refusal_from

nobody = exceptions.Unauthenticated("authenticate to run IssueInvoice", anonymous=True)
assert nobody.refusal_body() == {
    "kind": "unauthenticated", "reason": "authenticate to run IssueInvoice", "anonymous": True,
}
assert "error=" not in provider.challenge_for(refusal_from(nobody.refusal_body()))
expired = refusal_from({"kind": "unauthenticated", "reason": "UNAUTHENTICATED",
                        "detail": "token expired"})                 # a REST problem body
assert 'error="invalid_token", error_description="token expired"' in provider.challenge_for(expired)
assert refusal_from({"kind": "permission_denied", "detail": "lacks billing.invoice.issue"}) is None
```

## Public keys: an identity provider's, by discovery — `[jwt]`

Keycloak, Entra and Google sign with private keys and publish the public ones as a JWKS.
`JwtProvider.from_issuer` finds the JWKS by OIDC discovery — on the first token, never when the
service starts — and `Jwks` keeps it cached. Every component built without `http=` nor
`discovery=` — this one, `IntrospectionProvider`, `ClientCredentials`, `TokenExchange`,
`RefreshingToken`, `DeviceCode` — asks the process's one `OidcDiscovery`: an issuer's document is
fetched once per process, not once per component.

```python
from sincpro_framework.auth import PrivateKey
from sincpro_framework.runtime.testing import ScriptedHttp

signing = PrivateKey.generate("ES256", key_id="kc-1")         # Keycloak's, in this example
http = ScriptedHttp({                                          # what Keycloak would answer
    f"{KEYCLOAK}/.well-known/openid-configuration": {
        "issuer": KEYCLOAK, "jwks_uri": f"{KEYCLOAK}/protocol/openid-connect/certs"},
    f"{KEYCLOAK}/protocol/openid-connect/certs": {"keys": [signing.jwk()]},
})
keycloak_provider = JwtProvider.from_issuer(
    KEYCLOAK, audience="billing-api", mapping=ClaimMapping.keycloak(client="billing-api"), http=http
)
assert http.sent == []                                         # nothing fetched yet

signed = jws.encode(
    {"alg": "ES256", "kid": "kc-1"},
    {"iss": KEYCLOAK, "aud": "billing-api", "sub": "4c1d", "exp": time.time() + 300},
    signing.sign,
)
assert keycloak_provider.authenticate(bearer(signed)).subject == "user:4c1d"
```

| `Jwks(...)` | Default | Why |
|---|---|---|
| `algorithms` | RS256, PS256, ES256, EdDSA | an algorithm outside them is refused before any key is looked up |
| `refresh_every` | 10 minutes | how long the set is believed |
| `unknown_kid_cooldown` | 30 s | a `kid` not in the set refetches it once per cooldown — a key rotated in is found, a forged one never becomes a stream of fetches |
| `serve_stale_for` | 24 hours | while the issuer is down the last good set stands in; with nothing cached it is `EndpointUnavailable` (503) |

A key whose `use` is not `sig` is left out; a JWKS publishing a private part (`d`) is logged as
an error and the key left out. Keys given by hand — a partner's PEM, a JWK from its
documentation — are `PublicKeys([...], algorithm="RS256")`. `import sincpro_framework.auth` never
loads PyJWT: `Jwks`, `PublicKeys`, `PublicKey` and `PrivateKey` are imported when first asked for.

## Step-up: how strongly, how recently — RFC 9470

A use case may require how its caller authenticated — a method (`amr`), a level (`acr`), a recent
authentication (`auth_time`) — beside its permissions. Missing it is `Unauthenticated` with what
to come back with (`step_up`, `max_age`), never `PermissionDenied`: the caller can meet it by
authenticating again, and the challenge says how.

```python
from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import AccessControl, Assurance, Identity, security


class CommandApprovePayment(DataTransferObject):
    amount: int


class PaymentAnswer(DataTransferObject):
    approved_by: str


payments_auth = AccessControl[BillingPermission](providers=[provider])
payments = UseFramework("payments-step-up", log_after_execution=False)


@payments.feature(CommandApprovePayment)
@payments_auth.requires(
    BillingPermission.ISSUE_INVOICE,
    payments_auth.assurance(methods={"mfa"}, max_age=timedelta(minutes=5)),
)
class ApprovePayment(Feature):
    def execute(self, dto: CommandApprovePayment) -> PaymentAnswer:
        return PaymentAnswer(approved_by=security.identity.subject)


payments_auth.on(payments)

with_password_only = Identity.user("user:4c1d", permissions={BillingPermission.ISSUE_INVOICE}).model_copy(
    update={"assurance": Assurance(methods=frozenset({"pwd"})), "authenticated_at": datetime.now(UTC)}
)
with security.as_identity(with_password_only):
    try:
        payments(CommandApprovePayment(amount=900))
    except exceptions.Unauthenticated as refused:
        assert (refused.step_up, refused.max_age) == ("mfa", 300)
        assert 'error="insufficient_user_authentication"' in provider.challenge_for(refused)
```

`auth.assurance(acr=…, methods=…, max_age=…)` is judged on the identity — the `acr`, `amr` and
`auth_time` its provider read — with the `AccessControl`'s `clock`. `describe()` says
`billing.invoice.issue and assurance(mfa, 5m)`; `when_denied=WhenDenied.SKIP` leaves an optional
step out, as for a permission.

## Opaque tokens: introspection — RFC 7662

An opaque token is asked of the server that issued it. `IntrospectionProvider` authenticates as a
client (`ClientSecretBasic`, `ClientSecretPost`, `ClientSecretJwt`, `PrivateKeyJwt` — `[jwt]`),
caches every answer by the token's SHA-256 — never the token — and never past the token's own
`exp`; an inactive answer is remembered for `inactive_for`, so a stream of bad tokens is not a
stream of requests. A JWT is left to `JwtProvider`.

```python
from sincpro_framework.auth.client import ClientSecretBasic
from sincpro_framework.auth.server import IntrospectionProvider

introspect = f"{KEYCLOAK}/protocol/openid-connect/token/introspect"
http = ScriptedHttp({f"POST {introspect}": {"active": True, "sub": "4c1d", "aud": "billing-api",
                                             "exp": time.time() + 300, "scope": "openid"}})
opaque = IntrospectionProvider(
    client=ClientSecretBasic("billing-api", "the client secret"),
    endpoint=introspect,                         # or issuer=… to discover it
    audience="billing-api",
    http=http,
)
assert opaque.authenticate(bearer("an-opaque-token")).subject == "user:4c1d"
assert opaque.authenticate(bearer("an-opaque-token")).subject == "user:4c1d"
assert http.count(introspect) == 1               # the second answer came from the cache
```

The endpoint refusing this service's own client (401, 403) is its misconfiguration, never the
caller's: `EndpointUnavailable` (503), logged as an error.

## Webhooks: a signature over the body

A bank, a payment gateway or GitHub proves it sent a request by signing its body with a secret
both share. `SignatureProvider` reads the raw body (`needs_body`), so the wires — JSON-RPC, REST,
FastAPI, a queue's message, `IdentityMiddleware` — hand it over as it came, only when a provider
of the bus asks for it; the app still reads the whole body after.

```python
from sincpro_framework.auth import HexDigest, TimestampedSignature
from sincpro_framework.auth.client import OutgoingRequest, RequestSigner
from sincpro_framework.auth.server import SignatureProvider
from sincpro_framework.data_layer.store import InMemoryKeyValue

gateway = SignatureProvider(
    {"2026-09": "the gateway secret"},                     # several: the sender rotates
    TimestampedSignature("x-gateway-signature"),           # t=…,v1=… — or HexDigest() (GitHub)
    subject="service:payment-gateway",
    permissions={BillingPermission.ISSUE_INVOICE},
    replay=InMemoryKeyValue(),                             # the context's store in production
)
body = b'{"invoice": "F-7", "paid": 10}'
signature = RequestSigner("the gateway secret", TimestampedSignature("x-gateway-signature"))
signed = signature.headers_for(OutgoingRequest(url="https://billing/notify", body=body))
sender = gateway.authenticate(Credentials(transport="http", headers=signed, body=body))
assert sender.subject == "service:payment-gateway"
```

A timestamped signature older than `tolerance` (five minutes) is refused; with `replay`, one is
accepted once within it. `RequestSigner` is the same signature on the way out — this service
delivering its own webhooks ([client side](client.md)).

## Tokens bound to a key or a certificate — `[jwt]`

A token that leaks from a log or a proxy is of no use when it is bound: to a key the client proves
on every request (DPoP, RFC 9449), or to the client certificate of its mTLS connection (RFC 8705).

```python
from sincpro_framework.auth.client import DpopProver
from sincpro_framework.auth.server import DpopProofs

client_key = DpopProver()                                      # the client's — ClientCredentials(dpop=…)
bound = jws.encode(
    {"alg": "HS256", "kid": "own"},
    {"iss": "billing", "aud": "billing", "sub": "ana", "exp": time.time() + 300,
     "cnf": {"jkt": client_key.thumbprint}},
    jws.HmacKey.of("own", SECRET).sign,
)
api = JwtProvider(own, dpop=DpopProofs("https://billing.sincpro.com.bo"))
with_proof = Credentials(
    transport="http", method="GET", uri="/invoices",
    headers={"authorization": f"DPoP {bound}",
             "dpop": client_key.proof("GET", "https://billing.sincpro.com.bo/invoices", bound)},
)
assert api.authenticate(with_proof).subject == "user:ana"
try:
    api.authenticate(Credentials(transport="http", headers={"authorization": f"Bearer {bound}"}))
except exceptions.Unauthenticated as refused:
    assert refused.reason == "this token must be sent as DPoP"
```

The proof names the method and the URL — compared with `public_base_url`, never the Host header —
when it was made (a minute, `max_age`), the token's hash and, with `replay`, is accepted once. A
server that demands a nonce answers `use_dpop_nonce` with a `DPoP-Nonce`: `DpopProver` learns it —
from an API's 401 and from the token endpoint's 400 — and the request goes once more. A token
naming `cnf.x5t#S256` is accepted only over that client certificate.
`CertificateProvider(trust_domain="sincpro.bo")` authenticates the certificate itself — a SPIFFE
workload (`spiffe://sincpro.bo/billing` → `service:billing`), PEM or DER, its chain checked against
`trusted_roots` when the TLS server did not. With a trust domain only its SPIFFE IDs are accepted —
a certificate naming a common name alone is refused; without one, the common name is the service.

## Decisions taken outside: policy engines

A policy engine or a relationship store decides beside the provider whose identities it judges —
in OpenID AuthZEN's terms: a subject, an action, a resource, a context. `DecidedBy` keeps the
provider's name and answers `has_permission`, `permitted` and `scope` from the engine.

```python
from sincpro_framework.auth.server import AuthZenEngine, DecidedBy, OpaEngine, OpenFgaEngine

pdp = ScriptedHttp({"POST https://pdp.internal/access/v1/evaluation": {"decision": True}})
decided = DecidedBy(provider, AuthZenEngine("https://pdp.internal", http=pdp))
assert decided.has_permission(ana, BillingPermission.ISSUE_INVOICE)
assert decided.name == provider.name
```

| Engine | Asks | Answers |
|---|---|---|
| `AuthZenEngine(url)` | `POST /access/v1/evaluation` and `/evaluations` (a batch in one call) | any AuthZEN PDP — Topaz, Cerbos, … |
| `OpaEngine(url, path)` | `POST /v1/data/<path>` with the decision as `input` | `result: true` allows; an undefined rule refuses |
| `OpenFgaEngine(url, store_id, relations=…, types=…)` | `check`, `batch-check`, `list-objects` | a permission is a relation; `scope(identity, Invoice)` is the ids OpenFGA lists, as a `Criteria` |

No decision is cached — a policy that changes holds at once. An engine that does not answer is
`EndpointUnavailable` (503), never `False`: a refusal the engine did not make would be a lie.
`headers=` authenticates this service to the engine — a dict, or a function of a `TokenSource`.
