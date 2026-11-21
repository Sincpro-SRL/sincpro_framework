---
name: sincpro-framework-auth
description: Guard a sincpro_framework service — declare permissions on use cases and hooks (@auth.requires, @auth.authenticated, auth.assurance, when_denied=SKIP; open when nothing is declared), verify what arrives (JwtProvider for Keycloak/Entra/Google, introspection, API keys, webhook signatures, DPoP, mTLS, policy engines), resolve roles into permissions (GrantCatalog, GrantResolver, revocations), present what goes out (TokenSource — client credentials, token exchange, refresh, per tenant), and manage accounts (PasswordHasher, LoginAttempts, OneTimeTokens, Totp, TokenIssuer). Use whenever a task adds authorization, an identity, a token, a login, a role, or a call to another API that needs credentials.
---

# sincpro-framework-auth

A use case or a hook declares the permission it requires; a provider says who is calling and whether
they hold it; the guard applies the declaration **however** the use case is reached — an entrypoint,
a cron, another bus, an ApplicationService. A refusal is one of two errors every entrypoint already
tells the caller: `Unauthenticated` or `PermissionDenied`. This skill stands alone; the long form is
`docs/auth/README.md`, PRD_11, PRD_34 and PRD_35 in the framework repo.

## Context

- **Problem it solves:** one place, next to the use case, that says who may run it, held on every
  path into the bus — so an RPC method, a cron and a nested call cannot disagree.
- **The framework facilitates; security decisions are the service's.** Which provider, how a token
  is verified, which keys exist, what is open: the project chooses. Nothing is forced — a use
  case/hook that declares nothing is open, and a bus with no `AccessControl` checks nothing.
- **It is not** a login screen nor a user table — those are the IdP's or the project's own entity —
  nor field masking (#305). It ships the building blocks a self-managed project needs (hashing,
  lockout, one-time links, TOTP, `TokenIssuer`) and verifies what an IdP issues (`JwtProvider`).
- **Two roles, one vocabulary.** *Server*: providers verify what arrives. *Client*: a `TokenSource`
  says what an outgoing request presents — injected in the adapter that calls the API, never in a
  use case. Long form: `docs/auth/{server,grants,client,accounts}.md`, PRD_34.
- **Do not use it** for business validation ("amount above limit" is a domain rule, unless it is a
  permission decision the provider owns), nor duplicate it with checks in FastAPI routes.

## Abstractions

Every name below imports from `sincpro_framework.auth` — the contract — unless the Import column
names its door: `auth.server` verifies what arrives, `auth.client` presents what goes out,
`auth.grants` holds roles, `auth.accounts` the service's own accounts. One name, one door.

| Term | What it is | Kind | Import |
|---|---|---|---|
| `Permission` | `StrEnum` base a context extends; value `context.aggregate.action` | DTO (enum) | `sincpro_framework.auth` |
| `Credentials` | what arrived, normalized by the entrypoint; **not verified** | DTO | same |
| `Identity` | who the caller turned out to be: subject, kind, tenant, permissions, roles, provider | DTO | same |
| `IdentityKind` | `user` · `service` · `system` · `anonymous` | DTO (enum) | same |
| `AuthProvider` | the one contract: `authenticate` (+ optional `has_permission`, `scope`, …) | port | same |
| `StaticProvider` | fixed bearer tokens → identities (tests, local) | adapter | `sincpro_framework.auth.server` |
| `ApiKeyProvider` / `ApiKeyStore` / `InMemoryApiKeys` / `ApiKey` | keys stored by SHA-256; store is the port | adapter / port / adapter / DTO | `sincpro_framework.auth.server` |
| `ServiceTokenProvider` | service-to-service HS256 JWT, stdlib only, header `x-sp-service-token` | adapter | `sincpro_framework.auth.server` |
| `RolePermissions` | roles → permissions, with implied roles | adapter (helper) | `sincpro_framework.auth.grants` |
| `AccessControl[P]` | holds providers + declarations, decides, `on(bus)` wires the guard; `AccessControl.of(bus)` the one guarding a bus | registry | same |
| `@auth.requires(...)` / `@auth.authenticated` | restrict a Feature, ApplicationService or Hook — undeclared is open | decorator | methods of `AccessControl` |
| `auth.any_of(A, B)` → `AnyOf` | one among several — permissions, roles, rules mixed ("its owner, or an admin") | function / DTO | same |
| `auth.role(*roles)` → `RoleRequirement` | Odoo's `groups=`: one of the roles held, read off their closure | function / DTO | same |
| `auth.rule(name, check)` → `Rule` / `AccessCall` | a requirement of the project's own: `check(call) -> bool` with the identity, use case, Command, resource and the execution's context | function / DTO | same |
| `@auth.every_call` | an interceptor of the project's own, asked on every use case of every guarded bus — open or declared | decorator | same |
| `WhenDenied.RAISE` / `.SKIP` | refuse, or answer `None` and log | setting | same |
| `enabled=` | `False` checks nothing | setting | `AccessControl(...)` |
| `self.auth.allows` / `check` / `has_role` / `identity` / `permitted` / `scope_of` | in-handler decisions — `allows`/`check` take any requirement (permission, role, rule, `any_of`) | function | injected `auth` |
| `security.identity` / `security.as_identity` / `security.as_system` | who the execution acts as (a `ContextVar`) | facade | `security` |
| `AuthError` → `Unauthenticated` (401) / `PermissionDenied` (403) | the two refusals, `ClientError`s; `.http_status`, `.refusal_body()` (`anonymous: true` when nothing was presented); `refusal_from(body)` rebuilds one from an answer | DTO (exception) | same |
| `IdentityMiddleware` | ASGI middleware for a project's own app | adapter | same |
| `Credentials.from_asgi` / `Credentials.from_headers` / `security.authenticated_as` / `security.challenges` / `security.identity_headers` | what an entrypoint does | classmethod / facade | same |
| `describe()` / `verify()` / `requirements_of()` | introspection, test-time checks | function | `AccessControl` methods |
| `JwtProvider` / `TrustedIssuer` / `ClaimMapping.keycloak()` / `.entra()` / `.google()` / `SharedSecrets` | bearer JWTs of the issuers trusted; claims → identity; HMAC keys (stdlib) | adapter / config | `sincpro_framework.auth.server` |
| `Jwks` / `PublicKeys` / `JwtProvider.from_issuer` | public keys by OIDC discovery, cached, rotated — `[jwt]`, imported on first use | adapter | `sincpro_framework.auth.server` |
| `IntrospectionProvider` | opaque tokens (RFC 7662), cached by digest | adapter | `sincpro_framework.auth.server` |
| `SignatureProvider` | a webhook signed with HMAC, verified | adapter | `sincpro_framework.auth.server` |
| `RequestSigner` | a request this service signs | adapter | `sincpro_framework.auth.client` |
| `HexDigest` / `TimestampedSignature` | the HMAC styles both sides share | config | same |
| `CertificateProvider` / `DpopProofs` | mTLS + SPIFFE (`[jwt]`; with `trust_domain`, SPIFFE IDs only — never a bare CN); DPoP proofs (`[jwt]`) | adapter | `sincpro_framework.auth.server` |
| `BasicProvider` / `CredentialVerifier` / `HashedCredentials` | HTTP Basic against the service's own accounts | adapter / port | `sincpro_framework.auth.accounts` |
| `GrantCatalog` / `Category` / `Privilege` / `Role` / `Entitlement` | roles in code, Odoo 20's shape: implies, exclusive levels, disjoint, `describe()` | domain | `sincpro_framework.auth.grants` |
| `GrantStore` (`InMemoryGrants`, `KeyValueGrants`, `DatabaseGrants`) / `GrantAdministration` / `GrantResolver` / `RoleMapping` / `ScopeCeiling` | who holds which role per tenant; checked changes; resolved per call | port / adapter / service | `sincpro_framework.auth.grants` (`DatabaseGrants`: `data_layer.orm`) |
| `LiveCatalog` / `RoleAdministration` / `RoleDefinition` / `RoleDefinitions` (`InMemoryRoleDefinitions`, `KeyValueRoleDefinitions`) / `live.problems(tenant)` | roles an administrator defines at runtime beside the code's — for every tenant (`tenant=None`, the platform's) or one tenant: only adding, checked inside the write against what is stored now, an assigned role keeping its shape, a removed key retired, a tenant's own role winning in its tenant, a broken one granting nothing; `LiveCatalog` only reads (current within `check_every`), `RoleAdministration` defines and removes | adapter / service / DTO / port | `sincpro_framework.auth.grants` |
| `AdministrationPolicy` / `BoundedAdministration(superadmin=None)` / `TrustedAdministration()` | who administers: by default the system and a superadmin anything; anyone else only in their own tenant, never on themselves, and only give or take away a role they hold (or their tenant defined from what they hold); entitlements and every tenant's roles are the platform's; nobody known nothing — a seed runs `security.as_system` | port / policy | same (`policy=` on `GrantAdministration`, `RoleAdministration`) |
| `Revocations` (`KeyValueRevocations`) / `remembering_versions()` | a token, a session, a subject signed out (to the second, as `iat`), a permission version — one read per authentication inside `remembering_versions()` | port / adapter / function | same |
| `auth.assurance(acr=, methods=, max_age=)` | step-up requirement (RFC 9470) | requirement | `AccessControl` method |
| `TokenSource` / `StaticToken` / `ApiKeyHeader` / `BasicCredentials` / `ClientCredentials` / `TokenSources` | what an outgoing request presents | port / adapter | `sincpro_framework.auth.client` |
| `TokenExchange` + `PresentingProvider` / `RefreshingToken` + `HeldGrants` / `DeviceCode` / `DpopProver` | act for the caller (RFC 8693); hold consent; RFC 8628; DPoP client | adapter | `sincpro_framework.auth.client` |
| `call_with_credentials` / `requests_auth` / `httpx_auth` | send, and once more after a 401 | function | `sincpro_framework.auth.client` |
| `PasswordHasher` / `LoginAttempts` / `OneTimeTokens` / `Totp` / `TokenIssuer` / `SigningKeys` | accounts the service manages itself | adapter | `sincpro_framework.auth.accounts` |
| `AuthEvent` / `AccessRefused` / `EventPublisher` | the audit trail: `publisher=` on `AccessControl`, `GrantAdministration`, `TokenIssuer`, `LoginAttempts`, `OneTimeTokens` publishes versioned `DomainEvent`s through the framework's `Publisher` — grants' `RoleAssigned`…, accounts' `SignedIn`, `RefreshTokenReused`, `AccountLocked`… | DTO (event) / port | events of a door from that door |
| `ScriptedHttp` / `FakeTokenEndpoint` / `TokenSourceContract` / `RevocationsContract` / `GrantStoreContract` / `RefreshTokensContract` / `ApiKeyStoreContract` | test doubles and contracts | double / contract | `sincpro_framework.runtime.testing` |
| `granting` / `RecordingProvider` / `AuthProviderContract` | test helpers | function / adapter / port | `sincpro_framework.runtime.testing` |

Look-alikes: `auth.Identity` is **who calls**; `observability.ObservabilityIdentity` is **who the
service is** (release, version). `PermissionDenied` is not Python's `PermissionError` (an `OSError`).
`auth.AccessDeclaration` is what a use case declared about who may call it. Declaring nothing admits
anybody (`open`); `@auth.authenticated` anybody *known*. `security.as_system(reason)` passes every check for one block;
`enabled=False` turns the whole `AccessControl` off.

## Architecture

**Inside the framework** — package `sincpro_framework/auth/`, standard library only:

- One folder per job, each its own door (PRD_35):

  ```
  auth/            the contract — domain/ (Identity, AuthProvider, permissions, errors, revocations),
                   entrypoint/ (AccessControl, security, asgi), services/guard.py (BusGuard, HookGuard),
                   infrastructure/ (the identity ContextVar, jws, JOSE keys, HTTP, discovery)
  auth/server/     verify what arrives — adapters/bearer_tokens/ (JwtProvider, introspection, DPoP
                   proofs), api_keys, service_tokens, webhook_signatures, certificates, policy/
  auth/client/     present what goes out — domain/ (TokenSource), adapters/ (every source),
                   infrastructure/ (token endpoint, token cache), entrypoint/ (call_with_credentials)
  auth/grants/     roles — domain/ (catalog, assignments), adapters/ (resolver, stores), services/
  auth/accounts/   the service's own accounts — adapters/passwords/ (hasher, lockout, HTTP Basic),
                   adapters/issuing/ (TokenIssuer, SigningKeys, refresh tokens), one-time tokens, TOTP
  ```

  The root never imports a door; `server`, `client`, `grants` build on the root alone, `accounts`
  on the root and the server (`tests/auth/test_doors.py`).
- HS256 and HMAC, scrypt, TOTP and the HTTP client (`UrllibHttp`) are the standard library;
  asymmetric JOSE (RS/PS/ES/EdDSA, JWKS, DPoP, mTLS, AES-GCM) is the `[jwt]` extra (PyJWT +
  cryptography), argon2 the `[argon2]` one. PyJWT only signs and verifies: claims are always
  checked by `auth/infrastructure/jws.py`. `import sincpro_framework.auth` never loads an extra.

**Inside a consumer service** (one `UseFramework` per bounded context, created in
`infrastructure/framework.py` before `services/` is imported; `auth.on(bus)` before any gateway):

```
billing/
  __init__.py                  # billing = config_billing_framework("billing"); then `from . import services`
  domain/permissions.py        # class BillingPermission(Permission)
  adapters/bank_api.py         # the API client; its TokenSource injected, never in a use case
  infrastructure/
    auth.py                    # auth = AccessControl[BillingPermission](providers=[JwtProvider.from_issuer(
                               #     KEYCLOAK, audience="billing-api", mapping=ClaimMapping.keycloak(client=…),
                               #     resolvers=[GrantResolver(catalog, KeyValueGrants(), revocations=…)])])
    dependencies.py            # DependencyContextType: auth: AccessControl[BillingPermission]  (typing only)
    framework.py               # UseFramework(...); register_dependencies(...); auth.on(instance); auth.on(hooks)
  services/issue_invoice.py    # @billing.feature(Cmd) + @auth.requires(BillingPermission.ISSUE_INVOICE)
  entrypoints/                 # gateways — they authenticate by themselves
```

`auth.on(bus)` registers the `auth` dependency itself; `add_dependency("auth", ...)` again raises.

**One call:**

```
request ─► entrypoint ─► Credentials ─► AccessControl.authenticate ─► providers, in order
                                              │  first non-None wins, stamps Identity.provider
                                              │  none recognizes → Identity.anonymous()
                                              ▼
                         security.as_identity(identity) ─► bus(dto) ─► BusGuard (outermost interceptor)
                                              │  declaration of the handler: open / authenticated / requires
                                              │  requires → identity's provider.has_permission(...)
                                              ▼
                         other interceptors ─► execute() ─► self.auth.check(perm, resource)
                                              └► repository.save ─► HookGuard before the hook's moment
```

## Mistakes an agent makes

Silent ones first — the runtime says nothing.

- **Forgetting to restrict a use case that must be.** Undeclared is open — it runs for anybody,
  anonymous included, and nothing refuses the build. Read the gateway's log of what it publishes
  open (or `manifest()` / `auth.describe()`, where it says `open`) and assert it in a test;
  `@entrypoints.internal` for what must not be reached from outside.
- **Putting the identity in `bus.context({...})` or `self.context`.** Ignored — the guard reads only
  `security.as_identity`. Open `security.as_identity(...)` or let the entrypoint do it.
- **A provider answering `None` for a credential of its own kind that fails.** The caller becomes
  anonymous and every open use case runs. `None` means "not my kind"; a bad token of
  your kind raises `Unauthenticated`.
- **`@auth.requires` on a Hook without `auth.on(hooks)`** (or on a use case of a bus never given to
  `auth.on`). Never checked; only `auth.verify()` reports it.
- **Implementing `scope()` and expecting the repository to filter.** Nothing applies it by itself:
  call `self.auth.scope_of(Invoice)` and add the `Criteria` to the read.
- **Handing work to a raw thread** (`threading.Thread`, `executor.submit(self.feature_bus.execute,
  …)`). The thread acts as anonymous. In an ApplicationService submit
  `self.feature_bus.thread_context().execute`; elsewhere run it under `contextvars.copy_context()`.
- **`WhenDenied.SKIP` on a use case a client calls directly.** The client gets `None`, not a 403.
  SKIP is for an optional nested step; the caller must handle `None`.
- **`security.as_system(...)` on a request path.** Every requirement is met. It is for crons, migrations and
  consumers, with a reason that is logged.
- **A handwritten FastAPI route calling `bus(dto)`.** No credentials reach the bus. Use the gateway,
  or wrap the app in `IdentityMiddleware(app, access=auth)`.
- **A `TrustedIssuer` with no audience, or a mapping that forgets Keycloak's `typ`.** The first is refused
  at construction; use `ClaimMapping.keycloak(...)`, which refuses ID and refresh tokens.
- **Resolvers on `AccessControl`.** They belong to a provider (`resolvers=` or `Resolving`): a
  service token or an API key arrives resolved, a global resolver grants twice.
- **Permissions frozen in a self-issued token.** `TokenIssuer` resolves them per call by default;
  a role taken away needs `GrantAdministration` (it bumps the version) to hold at once. With
  `embed_permissions=True` and refresh tokens, give the issuer `resolvers=`: a refresh resolves
  the permissions again (without them the issuer refuses to be built).
- **A provider of the project's that checks revocations, then resolves grants.** Two reads of the
  store per call. Open `remembering_versions()` around both, as `JwtProvider` does: the resolver's
  permission versions come from the revocation check's one read.
- **A wire of the project's answering a 401 from a JSON body with `security.challenges(buses)`.**
  The refusal is lost: an expired token or a step-up gets a bare `Bearer`. Pass
  `refusal_from(body)` as the refusal.
- **A `TokenSource` in a use case, or a token in the context.** The context travels to other
  services; a source belongs to the adapter that calls the API (`dependencies.py`).
- **An outbound misconfiguration raised as `Unauthenticated`.** The inbound caller would log in
  again in a loop; the framework raises `CredentialsRejected` / `EndpointUnavailable`.
- **A cache or a store keyed by a raw token.** Every store here keys by digest; do the same.
- **Refusals counted as failures.** An `AuthError` is a `ClientError`: left out of GlitchTip by
  default, but counted as its kind in the metrics unless the bus is told it is traffic:
  `billing.ignore_sentry_exceptions(AuthError)`.

## Permissions and a provider

```python
from sincpro_framework.auth import (
    AccessControl,
    AuthProvider,
    Credentials,
    Identity,
    Permission,
    WhenDenied,
)
from sincpro_framework.auth.domain import exceptions


class BillingPermission(Permission):
    ISSUE_INVOICE = "billing.invoice.issue"
    READ_CREDIT = "billing.credit.read"


class TenantKeys(AuthProvider):
    name = "tenant-keys"

    def __init__(self, keys: dict[str, str]) -> None:
        self.keys = keys

    def authenticate(self, credentials: Credentials) -> Identity | None:
        key = credentials.headers.get("x-api-key")      # header names are lower-case
        if key is None:
            return None                                  # not mine: the next provider is tried
        if key not in self.keys:
            raise exceptions.Unauthenticated("unknown key")         # mine, and it fails
        return Identity.user(self.keys[key], tenant="bo", permissions={BillingPermission.ISSUE_INVOICE})


auth = AccessControl[BillingPermission](providers=[TenantKeys({"k-1": "user:1"})])
```

The member is for code (rename freely); the value is for the wire (tokens, the IdP) — changing it
is a breaking change. Provider contract, shipped adapters, `Credentials` fields and a JWT provider:
[references/providers.md](references/providers.md).

## Declaring what a use case requires

```python
@billing.feature(CommandIssueInvoice)
@auth.requires(BillingPermission.ISSUE_INVOICE)
class IssueInvoice(Feature):
    auth: AccessControl[BillingPermission]           # injected by auth.on(billing)

    def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
        if self.auth.allows(BillingPermission.READ_CREDIT):     # an optional step
            ...
        return ResponseIssueInvoice(by=self.auth.identity.subject)


@billing.feature(CommandCreditReport)
@auth.requires(BillingPermission.READ_CREDIT, when_denied=WhenDenied.SKIP)
class CreditReport(Feature): ...


auth.on(billing)                                     # before any gateway
```

- The decorators only mark classes, in any order around `@billing.feature`; the marks are read at
  first run, so a handler registered after `auth.on(...)` is guarded too.
- `@auth.requires(A, B)` and stacked `requires` are *and*; `auth.any_of(A, B)` is *or*;
  `auth.role(...)` is a group; `auth.rule(name, check)` is anything else, decided with the call
  and its context. A rule that must hold for every use case is `@auth.every_call`, not a
  `requires` repeated on each.
- `self.auth.check(permission, resource)` decides on the loaded resource (no interceptor can see
  it — the provider gets it); `self.auth.permitted(permission, resources)` answers a batch.
- A nested use case that declares something is checked again; one that declares nothing inherits
  the decision of the use case entered from outside. A `replaces=` handler or hook that declares
  nothing inherits the declaration of the one it replaces.

## Acting as someone

```python
from sincpro_framework.auth import security

with security.as_identity(auth.authenticate(credentials)):    # what an entrypoint does
    billing(CommandIssueInvoice(...))

with security.as_system("cron: close cash registers"):
    billing(CommandCreditReport(...))
```

Who acts follows the execution into every bus it calls and into `thread_context()`; it does not
cross to another service by itself — `ServiceTokenProvider.credentials_for` does that.

On a guarded bus, the identity let in (unless anonymous) is the execution's `user_id` when
nothing said one. That is what `Entity.record()` stamps as `actor`, and what an `AuditedMixin` record stamps as
`created_by`. Don't write a `context_provider` that copies `security.identity.subject` into
`user_id`. Write one only when `user_id` is something else: a profile id, say. Yours wins.

## Hooks

```python
@billing_hooks.on(Invoice)
@auth.requires(BillingPermission.ISSUE_INVOICE)
class OnlyIssuersWriteInvoices(Hook):
    def before_save(self, invoice: Invoice) -> None: ...

auth.on(billing_hooks)
```

The hook's declaration holds whichever use case writes the aggregate, decided before the hook's
moment. The gate holds on every copy or combination of the `Hooks`.

## Modes

| Situation | What happens |
|---|---|
| a use case/hook declares nothing | open — it runs for anybody |
| `enabled=False` | nothing is checked; `on(...)` logs a warning |
| `WhenDenied.SKIP` | a use case answers `None`, a hook's moment is left out — logged |
| `security.as_system(reason)` | every requirement is met; the reason is logged |

Entrypoints, refusal mapping per wire and testing helpers:
[references/entrypoints-and-testing.md](references/entrypoints-and-testing.md).

Not covered by any contract: Kerberos/NTLM/SPNEGO (the proxy does it), interactive login (the IdP),
field masking (`SKIP` leaves out a step, not a field), revocation mid-stream.

## Related

- The bus, interceptors, error handlers, the context manager: `sincpro-framework`, `sincpro-framework-core`
- Entrypoints and declared exposure: `sincpro-framework-entrypoints`
- Refusals on metrics and GlitchTip: `sincpro-framework-observability`

**Testing on a `fresh()` bus.** `fresh()` keeps the same `AccessControl` — and its providers, which may
hold production's bus. A test swaps them, keeping production's declarations:
`auth.using(providers=[StaticProvider({...})]).on(test, replaces=auth)` — `AccessControl.of(test)` is
the new one, on every bus made of it too. **A provider whose backend does not answer** raises
`ServiceUnavailableError` (503 + `Retry-After`, never 401 nor 500); `AuthProviderContract` checks it
with `make_provider_whose_backend_is_down()`. A provider should depend on an adapter (with its own
cache), not run a use case on the bus it guards: that call re-enters the guard as anonymous.

**A requirement the Command chooses, and asking without running.** `@auth.requires(auth.by_field("direction",
{Direction.INBOUND: P.RECEIVE, Direction.OUTBOUND: P.PAY}))` — never a check inside the Feature
for that. `auth.may_run(bus, Command, identity=None, dto=None)` / `auth.refusal_of(...)` /
`auth.requirements_of(Command, bus=bus)` answer with the guard's own decision — what a catalog
(`tools/list`) filters with, so the catalog never lists what the run refuses. A provider words an
`any_of` refusal with `refusing_any(identity, permissions, reasons)`.
