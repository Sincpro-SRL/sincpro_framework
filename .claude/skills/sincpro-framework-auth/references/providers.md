# Providers

## The contract — `AuthProvider` (port)

One method is required; every other has a default that holds for the common case. Synchronous,
like the bus: an entrypoint runs the bus on a worker thread, so a provider that calls the network
blocks that thread only.

| Member | Required | Default | Override it for |
|---|---|---|---|
| `name` | yes (class attr) | `"provider"` | what `Identity.provider`, logs and `describe()` show — unique per `AccessControl` |
| `needs_body` | no | `False` | an HMAC over the raw body (webhooks, SigV4) |
| `authenticate(credentials)` | yes | — | `None` = not my kind (next provider); raise `Unauthenticated` = mine and it fails |
| `has_permission(identity, permission, resource, context)` | no | `permission in identity.permissions` | an API, a policy engine (Cedar/OPA), a relationship store; `False`, or raise `PermissionDenied(subject, permission, reason)` to say why |
| `permitted(identity, permission, resources, context)` | no | `has_permission` per resource | a batch in one call |
| `scope(identity, aggregate)` | no | `None` (every record) | the records an identity may read, as `Criteria` — applied only where a use case calls `self.auth.scope_of(Aggregate)` |
| `security_scheme()` | no | `None` | how OpenAPI describes it |
| `challenge()` | no | `None` | the `WWW-Authenticate` of a 401 |
| `credentials_for(identity)` | no | `None` | what to send another service on the identity's behalf |

The provider that authenticated an identity (`Identity.provider`) is the one asked about it. An
identity opened by hand (`provider=None` — a test, in-process code) answers with its own
`permissions`. An identity stamped with a provider name the `AccessControl` does not hold is
refused (`ExtensionRefused`, from `sincpro_framework.exceptions`), so its permissions are never taken on its word.

Prove a provider with `sincpro_framework.runtime.testing.AuthProviderContract`: inherit it, answer
`make_provider`, `accepted`, `foreign` and `rejected`, run with pytest.

## `Credentials` (DTO, frozen)

| Field | What |
|---|---|
| `transport` | `http`, `grpc`, `mcp`, `queue`, `service` — required |
| `headers` | lower-case names: HTTP headers, gRPC metadata (text only, never `-bin`), message headers |
| `cookies` | parsed from the `cookie` header |
| `query` | first value per name — `?access_token=` of a legacy client |
| `method`, `uri` | for a proof bound to the request (DPoP, HTTP message signatures) |
| `body` | raw bytes, only when the provider says `needs_body` |
| `peer_certificate` | the mTLS client certificate, DER |
| `.bearer` | the token of `Authorization: Bearer <token>`, or `None` |

Its `repr` never shows a value.

## `Identity` (DTO, frozen)

`subject` (`user:42`, `service:billing`, `apikey:ops-bot`), `kind`, `tenant`, `permissions`
(what the guard reads), `roles` (for providers and screens), `provider`, `actor` (the service acting
for the subject), `claims` (read-only, typed with `identity.claims_as(Shape)`), `reason` (SYSTEM).
Builders: `Identity.user(subject, tenant, permissions, roles)`, `Identity.service(name,
permissions)`, `Identity.system(reason)`, `Identity.anonymous()`. Never `None`.

## Shipped adapters (all standard library)

| Adapter | Reads | Answers |
|---|---|---|
| `StaticProvider({token: identity})` | `Authorization: Bearer` | the mapped identity; unknown token → `Unauthenticated`. Tests, local, fixed-token scripts |
| `ApiKeyProvider(store, header="x-api-key", query=None)` | `x-api-key`, `Authorization: ApiKey <k>`, optional query param | a SERVICE identity of the stored `ApiKey`; unknown/revoked → `Unauthenticated` |
| `ServiceTokenProvider(issuer, keys={kid: secret}, audience="sincpro", ttl_seconds=60)` | `x-sp-service-token` | HS256 JWT between Sincpro services: `sub`, `kind`, `tenant`, `permissions`, `act`; newest key signs, every key verifies (rotation) |
| `RolePermissions(roles, implies)` | — | `.resolve(identity)` adds what its roles (and implied roles) grant |

`ApiKeyStore` is the port for a project's own key table (`find`, `keep`); `issue(ApiKey(...))`
returns the secret once and keeps only its SHA-256; `revoke(secret_or_digest)`.
`InMemoryApiKeys` is the in-memory adapter.

## A provider for an OIDC/JWT issuer (project code)

The framework ships no JWT/OIDC library. The project's adapter brings its own (PyJWT here, a
dependency of the project):

```python
import jwt                                            # the project's dependency

from sincpro_framework.auth import AuthProvider, Credentials, Identity, RolePermissions, Unauthenticated


class KeycloakProvider(AuthProvider):
    name = "keycloak"

    def __init__(self, jwks_url: str, audience: str, grants: RolePermissions) -> None:
        self.keys = jwt.PyJWKClient(jwks_url)
        self.audience = audience
        self.grants = grants

    def authenticate(self, credentials: Credentials) -> Identity | None:
        token = credentials.bearer
        if token is None:
            return None
        try:
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.audience)
        except jwt.PyJWTError as error:
            raise Unauthenticated(f"invalid token: {error}") from error
        identity = Identity.user(
            f"user:{claims['sub']}",
            tenant=claims.get("tenant"),
            roles=claims.get("realm_access", {}).get("roles", ()),
        )
        return self.grants.resolve(identity)
```

Which claims, which algorithm, which audience and how roles map are the project's decisions.
