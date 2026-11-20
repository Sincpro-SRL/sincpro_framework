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

from sincpro_framework.auth import (
    ClaimMapping,
    Credentials,
    Issuer,
    JwtProvider,
    SharedSecrets,
)
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
| revocation | `revocations=`: the token, its session, its subject signed out everywhere — one read |

A known issuer's token that fails is `Unauthenticated` with its reason; keys that cannot be
fetched are `EndpointUnavailable` (503) — the caller's token did not fail.

**How claims read.** `ClaimMapping` says which claim is the subject, the tenant, the roles, the
scopes and the client. The presets:

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

from sincpro_framework.auth import InMemoryRevocations, Permission, RolePermissions


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
(`on_unavailable="admit"`).

## Where to get a token

A 401 says where to get a credential this service accepts — RFC 6750's `WWW-Authenticate`, with
the reason of the refusal and, given a `ProtectedResource`, RFC 9728's metadata URL, which an MCP
client follows to find the authorization server.

```python
from sincpro_framework.auth import ProtectedResource

resource = ProtectedResource("https://billing.sincpro.com.bo/mcp", authorization_servers=[KEYCLOAK])
provider = JwtProvider(keycloak, resource=resource, realm="billing")
assert provider.challenge_for(exceptions.Unauthenticated("token expired")) == (
    'Bearer realm="billing", error="invalid_token", error_description="token expired", '
    'resource_metadata="https://billing.sincpro.com.bo/.well-known/oauth-protected-resource/mcp"'
)
```

## Public keys: an identity provider's, by discovery — `[jwt]`

Keycloak, Entra and Google sign with private keys and publish the public ones as a JWKS.
`JwtProvider.from_issuer` finds the JWKS by OIDC discovery — on the first token, never when the
service starts — and `Jwks` keeps it cached.

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
