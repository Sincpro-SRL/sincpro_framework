# PRD_34: The auth plugin — verify what arrives, present what goes out, manage accounts

- **Status**: built on `feat/304-auth-plugin` (#304), documented in [docs/auth](../auth/README.md)
  — `server.md`, `grants.md`, `client.md`, `accounts.md`, every page executed by the docs test.
  Record rules applied by the framework's reads and field access by permission are #305.
- **Builds on**: PRD_11 (the contract: `Credentials`, `Identity`, `AuthProvider`,
  `AccessControl`, the guard, `security`), the caching primitives (`Cache`, `KeyValueStore`,
  `Leases`, `StoreBypass`), the template tables of `data_layer.orm`, the entrypoints.
- **Reverses** PRD_11 "Out of scope: interactive login … no token issuing for users": the
  framework ships the building blocks a project that manages its own users needs — never a user
  table, never a login screen.
- **Philosophy**: PRD_11's. What a use case sees does not change — `@auth.requires`,
  `@auth.authenticated`, `@auth.public`, `self.auth.check`, `self.auth.scope_of`. Every protocol
  is an adapter on a port; every component is a plain object a project injects with
  `add_dependency` and calls by hand when it needs to.

## Problem

A survey of the Sincpro repositories (2026-11) found no project using the providers the
framework ships, and every one of them writing the rest by hand, with the same faults:

| Found | Where |
|---|---|
| Self-issued HS256: an unverified decode to pick the provider by `iss`, permissions frozen in a 12 h–30 d token, no refresh, no logout, no revocation, a `""` or dev secret by default, no `exp` at all | quipu, forge, synthesis |
| Passwords hashed by hand; a lockout counter read, changed and saved with no lock; a missing account answered before the hash runs (usernames enumerable) | quipu |
| Tenant permissions cached by the raw token, with the tenant's secrets in plain text; Odoo `has_group` asked over RPC on every check | sincpro_mcp_odoo |
| An OAuth refresh that two replicas can win at once; no revocation | sincpro_mcp_odoo |
| Outbound tokens with no expiry, no cache, no refresh, no retry on 401 — a login before every operation | payments SDK, Odoo consumers |
| HMAC signatures over a shared mutable `requests.AuthBase` — two merchants signed with each other's key | payments SDK |
| A tenant with no credential silently given the server's default token | SIAT |
| A Keycloak token accepted with no audience check, linked to any user by an unverified email | an Odoo addon |

Nobody verifies Keycloak tokens, the corporate identity provider (`sincpro_auth`).

## Decisions

1. **Two extras, the rest standard library.** `[jwt]` = PyJWT with `cryptography`: asymmetric
   signatures (RS/PS/ES/EdDSA), JWKS, DPoP, `private_key_jwt`, mTLS certificates, AES-GCM.
   `[argon2]` = `argon2-cffi`. HS256 and HMAC, scrypt, TOTP and the HTTP client are the
   standard library. PyJWT only signs and verifies asymmetric signatures; **the claims are
   always checked by the framework's own code** — one path, one injected clock — so a project
   issuing its own HS256 tokens needs no extra.
2. **One HTTP port.** `HttpClient.send(HttpRequest) -> HttpAnswer` answers any status — OAuth
   needs the error body — and raises `ServiceUnavailableError` only when nothing answered.
   `UrllibHttp` requires https except on loopback, never follows a redirect (it would resend a
   client's credentials to another host), caps the body. A test injects `ScriptedHttp`; a
   project may hand httpx behind the same port.
3. **Resolvers belong to a provider**, never to `AccessControl`: a service token or an API key
   arrives with its permissions resolved, and a global resolver would grant twice.
4. **Store only what was assigned** (Odoo 20's `group_ids` beside `all_group_ids`): a role
   catalog computes the closure; assignments keep the roles a person was given.
5. **Permissions are resolved per request, not frozen in a token.** A permission version per
   subject, bumped by every assignment, keys the cache: revoking a role holds on the next call,
   on every replica.
6. **Keys and caches never hold a raw secret**: a token, a refresh token, a one-time token and
   a password are looked up by their SHA-256; a log line shows at most eight characters of a
   fingerprint.
7. **An outbound misconfiguration is never `Unauthenticated`** — the inbound caller would be told
   to log in again, and loop. A token endpoint down is `TokenEndpointUnavailable` (503, with
   `Retry-After`); a client the endpoint refuses is `CredentialsRejected` (500).
8. **Every store** is a port with an in-memory adapter, a `KeyValueStore` adapter — the context's
   store, Redis in production — and a `Database*` adapter over a template table the project
   declares in its own metadata and migrations, like `idempotency_table`.

## The catalog

| Category | Pieces | Extra |
|---|---|---|
| **Inbound** — who calls | `JwtProvider` (several issuers, `ClaimMapping` presets for Keycloak, Entra and Google, `from_issuer` by OIDC discovery), `IntrospectionProvider` (RFC 7662), `SignatureProvider` (webhook HMAC), `BasicProvider` (RFC 7617), `CertificateProvider` (mTLS, SPIFFE), DPoP (RFC 9449); `StaticProvider`, `ApiKeyProvider`, `ServiceTokenProvider` as before | asymmetric, DPoP, mTLS: `[jwt]` |
| **Authorization** | `GrantCatalog` (category → privilege → role, implied roles, exclusive levels, disjoint roles, entitlements), `GrantStore`, `GrantResolver` (roles → permissions ∩ scope ceiling − what the tenant is not entitled to), `RoleMapping`, `ScopeCeiling`, step-up `auth.assurance(...)` (RFC 9470), `PolicyEngine` + `DecidedBy` (AuthZEN, OPA, OpenFGA) | — |
| **Lifecycle** | `Revocations`: a token, a session, every session of a subject, a permission version | — |
| **Accounts** — the framework manages them | `PasswordHasher`, `LoginAttempts`, `OneTimeTokens`, `Totp` (RFC 6238), `SigningKeys`, `TokenIssuer` (access JWT, refresh rotation with reuse detection, logout, JWKS) | argon2: `[argon2]`; ES256 keys: `[jwt]` |
| **Outbound** — what this service presents | `TokenSource`, `StaticToken`, `ApiKeyHeader`, `BasicCredentials`, `ClientCredentials` (RFC 6749 §4.4, RFC 8707), `TokenSources` per tenant, `RefreshingToken`, `TokenExchange` (RFC 8693), `DeviceCode` (RFC 8628), `RequestSigner`, `DpopProver`, `call_with_credentials`, `requests_auth`, `httpx_auth` | `private_key_jwt`, DPoP, sealing: `[jwt]` |
| **Storage** | `DatabaseKeyValue` (revocations, lockout, one-time tokens, TOTP replay on SQL), `DatabaseApiKeys`, `DatabaseGrants`, `DatabaseRefreshTokens` | `[sqlalchemy]` |

## Out of scope

- Record rules applied by the framework's reads, field access by permission — #305.
- An OAuth authorization server's own endpoints (authorize page, consent, dynamic client
  registration): Keycloak's, or FastMCP's for an MCP server.
- SAML, LDAP, Kerberos, NTLM: the identity provider's or the proxy's.
- WebAuthn / passkeys, Cedar, Cerbos and SpiceDB SDKs, a Shared Signals (CAEP) receiver, RFC
  9421 message signatures beyond the HMAC styles shipped — follow-ups when a project needs them.

## Phases

All built in one PR, each a commit: the frame; identity and JWS; HTTP, discovery and resolvers;
revocations; `JwtProvider` (HS, then asymmetric); grants; step-up; introspection; the outbound
core; client credentials; signatures; delegation and refresh; accounts (passwords and lockout,
one-time tokens and TOTP, the token issuer); proof of possession and mTLS; the tables; policy
engines; the documentation.
