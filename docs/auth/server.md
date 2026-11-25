# Verifying what arrives, issuing to OAuth clients — `sincpro_framework.auth.server`

**Door**: `from sincpro_framework.auth.server import …` — folder `auth/server/`, two sub-domains in
each of its layers: `resource_server` verifies a credential that arrives, and
`authorization_server` issues tokens to OAuth clients ([below](#issuing-to-oauth-clients-the-authorization-server)).
What goes out is [`auth.client`](client.md).

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

A `TrustedIssuer` is one identity provider this service trusts: its `iss`, the audience its tokens must
name, where its keys are, how its claims read. `JwtProvider` routes a token by its `iss` — read
before it is verified, only to route — and checks everything else against that issuer, never
against what the token says about itself.

```python
import time

from sincpro_framework.auth import Credentials
from sincpro_framework.auth.server import JwtProvider
from sincpro_framework.auth import ClaimMapping, SharedSecrets, TrustedIssuer
from sincpro_framework.auth import Unauthenticated
from sincpro_framework.runtime.testing import signed_token

KEYCLOAK = "https://auth.sincpro.com.bo/realms/sincpro"
SECRET = "a shared secret of thirty-two by!"

keycloak = TrustedIssuer(
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
    return signed_token(payload, secret=SECRET, kid="2026-09")


def bearer(value: str) -> Credentials:
    return Credentials(transport="http", headers={"authorization": f"Bearer {value}"})


ana = provider.authenticate(bearer(token()))
assert ana is not None and ana.subject == "user:4c1d"
assert ana.roles == {"app:billing", "accountant"} and ana.scopes == {"openid"}
assert (ana.client, ana.session, ana.token_id) == ("billing-web", "s-1", "j-1")

try:
    provider.authenticate(bearer(token(aud="another-api")))
except Unauthenticated as refused:
    assert refused.reason == "token is for another audience"

assert provider.authenticate(bearer(token(iss="https://elsewhere"))) is None   # not its kind
```

| Checked | How |
|---|---|
| the issuer | the `iss` names one this provider trusts; another is `None`, the next provider's |
| the algorithm | one the issuer's keys verify — never `none`, never HMAC for public keys nor the reverse (checked when the `TrustedIssuer` is built) |
| the key | the issuer's, by `kid`; a `jwk`, `jku` or `x5u` in the header is ignored; a `crit` header is refused |
| the claims | `iss` exact, `aud` names one of the audiences (a `TrustedIssuer` with none is a `ProgrammingError`), `exp`, `nbf`, `iat` with `leeway` (60 s), `required` present |
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
own = TrustedIssuer("billing", audience="billing", keys=SharedSecrets({"own": SECRET}))
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
except Unauthenticated as refused:
    assert refused.reason == "session revoked"
```

| Revoked | Call | Refuses |
|---|---|---|
| one token | `revoke_token(jti, until)` | that token |
| a session — a logout | `revoke_session(sid, until)` | every token of the session |
| a subject — "sign out everywhere", a password changed | `revoke_subject(subject, until)` | every token issued before now |
| a subject's permissions | `bump_permissions(subject)` | nothing — the resolved permissions are resolved anew |

`until` is when the last token it covers expires: an entry lives no longer. `KeyValueRevocations`
stores them on the running context's store — Redis in production — under digested keys; a store
that does not answer is refused (503) by default, or admitted with a warning
(`on_unavailable=WhenUnavailable.ADMIT`). One authentication reads the store once: the token, its session, its
subject's and its tenant's permission versions in one `get_many`, and a `GrantResolver` on the
same `Revocations` is answered from that read (`cached_permission_versions()`).

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
assert provider.challenge_for(Unauthenticated("token expired")) == (
    'Bearer realm="billing", error="invalid_token", error_description="token expired", '
    'resource_metadata="https://billing.sincpro.com.bo/.well-known/oauth-protected-resource/mcp"'
)
```

The body of a 401 says `"anonymous": true` when the caller presented nothing. A wire of the
project's own that turned the refusal into JSON before answering rebuilds it with `Unauthenticated.from_error_body`
— from a REST problem body, a JSON-RPC error's `data` or a `refusal_body()` — so the challenge is
still the refusal's own:

```python
from sincpro_framework.auth import Unauthenticated

nobody = Unauthenticated("authenticate to run IssueInvoice", anonymous=True)
assert nobody.refusal_body() == {
    "kind": "unauthenticated", "reason": "authenticate to run IssueInvoice", "anonymous": True,
}
assert "error=" not in provider.challenge_for(Unauthenticated.from_error_body(nobody.refusal_body()))
expired = Unauthenticated.from_error_body({"kind": "unauthenticated", "reason": "UNAUTHENTICATED",
                        "detail": "token expired"})                 # a REST problem body
assert 'error="invalid_token", error_description="token expired"' in provider.challenge_for(expired)
assert Unauthenticated.from_error_body({"kind": "permission_denied", "detail": "lacks billing.invoice.issue"}) is None
```

## Public keys: an identity provider's, by discovery — `[jwt]`

Keycloak, Entra and Google sign with private keys and publish the public ones as a JWKS.
`JwtProvider.from_issuer` finds the JWKS by OIDC discovery — on the first token, never when the
service starts — and `Jwks` caches it. Every component built without `http=` nor
`discovery=` — this one, `IntrospectionProvider`, `ClientCredentials`, `TokenExchange`,
`RefreshTokenSource`, `DeviceAuthorization` — asks the process's one `OidcDiscovery`: an issuer's document is
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

signed = signed_token(
    {"iss": KEYCLOAK, "aud": "billing-api", "sub": "4c1d", "exp": time.time() + 300},
    key=signing,
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
    except Unauthenticated as refused:
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
`exp`; an inactive answer is remembered for `inactive_cache_ttl`, so a stream of bad tokens is not a
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
bound = signed_token(
    {"iss": "billing", "aud": "billing", "sub": "ana", "exp": time.time() + 300,
     "cnf": {"jkt": client_key.thumbprint}},
    secret=SECRET,
    kid="own",
)
api = JwtProvider(own, dpop=DpopProofs("https://billing.sincpro.com.bo"))
with_proof = Credentials(
    transport="http", method="GET", uri="/invoices",
    headers={"authorization": f"DPoP {bound}",
             "dpop": client_key.create_proof("GET", "https://billing.sincpro.com.bo/invoices", bound)},
)
assert api.authenticate(with_proof).subject == "user:ana"
try:
    api.authenticate(Credentials(transport="http", headers={"authorization": f"Bearer {bound}"}))
except Unauthenticated as refused:
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
in OpenID AuthZEN's terms: a subject, an action, a resource, a context. `PolicyEngineProvider` preserves the
provider's name and answers `has_permission`, `permitted` and `scope` from the engine.

```python
from sincpro_framework.auth.server import AuthZenEngine, PolicyEngineProvider, OpaEngine, OpenFgaEngine

pdp = ScriptedHttp({"POST https://pdp.internal/access/v1/evaluation": {"decision": True}})
decided = PolicyEngineProvider(provider, AuthZenEngine("https://pdp.internal", http=pdp))
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

## Issuing to OAuth clients: the authorization server

The other half of `server`: instead of verifying what an identity provider issued, the service
**is** the authorization server — an MCP host, a partner's app or a CLI gets a token by the
person signing in on the project's own page, never by handing over a password. It issues with the
service's own [`TokenIssuer`](accounts.md#issuing-tokens), so what it hands out is verified by
`JwtProvider.for_token_issuer` like any session of the service.

```python
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

from sincpro_framework.auth import Identity, InMemoryRevocations
from sincpro_framework.auth.accounts import InMemoryRefreshTokens, SigningKeys, TokenIssuer
from sincpro_framework.auth.server import (
    AuthorizationRequest,
    AuthorizationServer,
    InMemoryOAuthClients,
)

issuer = TokenIssuer(
    "https://auth.colegio.bo",
    audience="colegio",
    keys=SigningKeys.hmac({"2026-11": "a token secret of thirty-two bytes!"}),
    refresh_tokens=InMemoryRefreshTokens(),
    revocations=InMemoryRevocations(),
)
oauth = AuthorizationServer(
    "https://auth.colegio.bo", token_issuer=issuer, client_registry=InMemoryOAuthClients()
)

registered = oauth.register_from_metadata({                                      # /register
    "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],               # RFC 7591
    "client_name": "Claude",
})
client = registered.client

verifier = "the-client-stores-this-secret-verifier-0123456789"                  # PKCE, RFC 7636
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
pending = oauth.authorize(AuthorizationRequest(                                  # /authorize
    client_id=client.client_id,
    redirect_uri="https://claude.ai/api/mcp/auth_callback",
    code_challenge=challenge,
    state="xyz",
))
# … the project's login and consent page shows pending.client_name, the person signs in …
redirect = oauth.complete(pending.id, Identity.user("user:MFAQ0123", tenant="cauor"))
code = parse_qs(urlsplit(redirect).query)["code"][0]

tokens = oauth.token({                                                           # /token
    "grant_type": "authorization_code",
    "code": code,
    "code_verifier": verifier,
    "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
    "client_id": client.client_id,
})
renewed = oauth.token({                                                          # its client only
    "grant_type": "refresh_token",
    "refresh_token": tokens.answer()["refresh_token"],
    "client_id": client.client_id,
})
oauth.revoke(renewed.answer()["refresh_token"], client.client_id)                # RFC 7009
```

| Step | Method | What it guarantees |
|---|---|---|
| `/authorize` | `authorize(request)` → `PendingAuthorization` | an unknown client or a redirect it did not register is refused, never redirected — no open redirector; past that check, a refusal carries `redirect_uri` with the error for the client (RFC 6749 §4.1.2.1) |
| sign-in | `find_pending(id)`, then `complete(id, identity)` / `deny(id)` | the project's page says who asks and who signed in; the browser goes back with `code`, `state` and `iss` (RFC 9207) |
| `/token` | `token(form, authorization_header)` | dispatches by `grant_type` to the grants composed — `unsupported_grant_type` otherwise; reads `client_secret_basic` or `client_secret_post` |
| code | `exchange_code(code, verifier, client_id, redirect_uri)` | a code works once, for its client, its redirect and its PKCE verifier; it lasts a minute; presented again, the session it opened is revoked |
| refresh | `refresh(token, client_id)` | rotated, a replay revokes the session — and another client's refresh token is refused |
| `/revoke` | `revoke(token, client_id)` | the session ends, its access tokens refused at once — only the client's own tokens (RFC 7009 §2.1) |
| `/register` | `register_from_metadata(body, initial_access_token)` / `register(redirect_uris, …)` | https, loopback http (any port at `/authorize`, RFC 8252) or a private-use scheme, never a fragment nor user information; a confidential client's secret shown once; `answer()` is the RFC 7591 response |
| `/.well-known/oauth-authorization-server` | `metadata(endpoint_urls)` | RFC 8414, listing only the endpoints, grants and methods the server serves |

`AuthorizationRequest.resource` (RFC 8707) becomes the `aud` of every token of the session; the
API that verifies them then expects its own URI as the audience.

**Compose what you serve.** Each capability is a mixin over `AuthorizationServerCore` —
`AuthorizationCodeGrant`, `RefreshTokenGrant`, `TokenRevocation`, `ClientRegistration`,
`AuthorizationServerMetadata` — and `AuthorizationServer` composes them all. Each adds its grant
to `token_grants()`, its endpoints to `served_endpoints()` and its fields to `capabilities()`: a
server without dynamic registration is `class Server(AuthorizationCodeGrant, RefreshTokenGrant,
AuthorizationServerMetadata)`, and its metadata never names a registration endpoint. Refresh and
revocation are served only when the `TokenIssuer` stores refresh tokens and revocations, and the
JWKS only when its keys are asymmetric.

**Policy is an argument, never a fork.** `pkce_policy=PkcePolicy.REQUIRED` (OAuth 2.1, the
default), `PkcePolicy.PUBLIC_CLIENTS` for confidential clients that predate PKCE (RFC 6749 +
7636), or `PkcePolicy.OPTIONAL`; `challenge_methods` adds `CodeChallengeMethod.PLAIN` for a
client that cannot hash; `authorization_ttl` and `code_ttl` how long a sign-in and a code wait;
`registration_token` closes registration behind an initial access token (RFC 7591 §3). Every
decision is a public method to override: `issue_tokens` (what a code grants),
`is_registrable_redirect_uri` (which redirects may be registered), `authenticate_client`, and
`OAuthClient.allows_redirect_uri` (how a redirect matches). Clients live in a `ClientRegistry` —
`InMemoryOAuthClients`, `KeyValueOAuthClients` (the context's store), or the project's own. A
refusal is an `OAuthError` whose `answer()` is the body RFC 6749 §5.2 defines, `status` 400 — 401
for `invalid_client` and `invalid_token`.

**Consent is the project's.** Registration is open by default, so anyone may register a client
and send a person to `/authorize`. When the login page completes at once for someone already
signed in, show `pending.client_name` and let the person accept or `deny` — otherwise a
registered impostor gets a code for a person who never saw it. Or close registration with
`registration_token`.

The transport is the project's: an HTTP route, FastMCP's provider or a test call these methods.
