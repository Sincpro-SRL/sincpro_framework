# PRD_35: Auth by job — one door each for the server, the client, grants and accounts

- **Status**: built on `refactor/312-auth-doors` (#312). Breaking: no name is re-exported from
  its old place.
- **Builds on**: PRD_11 (the contract), PRD_34 (the plugin), #309 (open by default).
- **Rule**: one folder = one door = one docs page = one section of the skill. Whether a name
  verifies what arrives or presents what goes out is said by its import.

## Problem

`sincpro_framework.auth` exported 136 names from one door, and its folders were ordered by layer
(`adapters/jwt`, `adapters/outbound`, `adapters/accounts`), not by job. Three things followed:

- **Nothing in an import said which side a name is on.** `ApiKeyProvider` verifies an API key that
  arrives and `ApiKeyHeader` presents one that goes out, from the same door.
- **The folders did not answer "where is it".** The four docs pages were already split by job
  (`server.md`, `client.md`, `grants.md`, `accounts.md`), but the code was not, so a reader had
  to translate between the two.
- **The issuer verified its own tokens from `adapters/jwt`**, beside the provider that verifies
  everybody else's. An account feature sat in the server's folder, and the server's door
  pulled in account code.

## Plans compared

| Plan | Shape | Kept / refused because |
|---|---|---|
| A. Doors over the current folders | `auth/client.py` re-exporting from `adapters/outbound/…` | **Refused**: the folder still says `adapters/outbound`, so a name has two places and the reader translates — what the change is meant to end |
| **B. Doors by direction and job** | the root is the contract; `server/`, `client/`, `grants/`, `accounts/`, each layered inside | **Chosen**: the import says the side (`auth.server.JwtProvider`, `auth.client.ClientCredentials`); the folders are the docs pages; a door's dependencies can be stated and tested |
| C. Doors by technology | `auth.jwt`, `auth.oauth`, `auth.api_keys`, `auth.webhooks`, `auth.mtls` | **Refused**: `auth.jwt` would hold a verifier (`JwtProvider`), an issuer (`TokenIssuer`) and a client assertion (`PrivateKeyJwt`) — the import no longer says the side, and one call between two services spans three doors |
| D. Two doors, server and client | grants and accounts inside the server | **Refused**: a server door of ~65 names; grants are the authorization model a provider merely uses, and accounts are optional and heavy |

## The doors

| Door | Job | Depends on |
|---|---|---|
| `sincpro_framework.auth` | the contract: `AccessControl` and its requirements, `Identity` / `Credentials`, the ports (`AuthProvider`, `IdentityResolver`, `HttpClient`, `ClientAuthentication`), `security`, the ASGI middleware, every auth error, and what the doors share — revocations, discovery, JOSE keys, HMAC signature styles | nothing of `auth` |
| `sincpro_framework.auth.server` | verify what arrives: `JwtProvider` and bearer tokens (introspection, DPoP proofs, the protected resource), API keys, service tokens, webhook signatures, mTLS, policy engines | the root |
| `sincpro_framework.auth.client` | present what goes out: `TokenSource` and every source (static, client credentials, per tenant, refreshing, exchange, device code, request signing, DPoP), the client's authentication to a token endpoint, `call_with_credentials`, `requests_auth`, `httpx_auth` | the root |
| `sincpro_framework.auth.grants` | roles and groups the Odoo 20 way: the catalog, assignments, the resolver, administration | the root |
| `sincpro_framework.auth.accounts` | accounts a service manages itself: passwords, lockout, HTTP Basic against them (`BasicProvider` over the `CredentialVerifier` port), one-time links, TOTP, the token issuer and its keys | the root, the server (the issuer verifies what it issued with a `JwtProvider`) |

The root never imports a door, so no door is loaded by `import sincpro_framework.auth`. The
architecture test states the direction, and `test_core_without_extras` holds that no door loads
PyJWT until a `[jwt]` name is asked for.

HTTP Basic is the accounts': `BasicProvider` verifies a username and a password against the
project's own accounts (`CredentialVerifier`) and locks a guessed one with `LoginAttempts`. In the
server it would make the server depend on accounts while accounts depend on the server.

## Between services

A service in the middle of a chain both verifies and presents, and its setup says which is which:

```python
from sincpro_framework.auth import AccessControl
from sincpro_framework.auth.client import PresentingProvider, TokenExchange
from sincpro_framework.auth.server import JwtProvider, ServiceTokenProvider

auth = AccessControl(providers=[
    JwtProvider.from_issuer(KEYCLOAK, audience="orders"),            # people, through a gateway
    ServiceTokenProvider("orders", keys),                             # this platform's services
    PresentingProvider({"billing": TokenExchange(client, audience="billing-api", issuer=KEYCLOAK)}),
])
```

`ServiceTokenProvider` stays a server piece even though it also says what this service presents
to another of the same keys. That is the `AuthProvider` contract's `outbound_for`, so the
verification and the matching outbound token live together. `remote_execution` and the queue wire
ask `security.identity_headers` and never name a door.

## Out of scope

- Behaviour: no use case, provider or source decides differently. The suites move with the code.
- Record rules and field access (#305), runtime roles and runtime requirements (a follow-up).
