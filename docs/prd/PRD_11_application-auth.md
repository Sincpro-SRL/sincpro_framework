# PRD_11: Authentication and authorization, from the application's point of view

- **Status**: phase 1 built — `sincpro_framework.auth`, documented in
  [docs/auth](../auth/README.md). Phases 2–5 proposed, to iterate.
- **Depends on**: interceptors (`bus.interceptor`, ordered), dependencies (`add_dependency`,
  `DependencyContextType`), repository hooks (`Hooks.gate`), `Criteria`, `KeyValueStore`, domain
  events, settings (`SincproConfig`, `Secret`), the entrypoints (RPC, MCP, gRPC, HTTP Open Host),
  `remote_execution`.
- **Philosophy**: a toolkit, not a gate. The framework ships one contract, the errors every layer
  understands, a typed way to declare permissions and a few ready providers; the project decides
  where to declare — a Feature, an ApplicationService, a Hook — and writes its own provider for
  whatever protocol it speaks. What declares nothing is not checked. A bus with no auth component
  behaves as today.

## Problem

Every Sincpro service answers "who is calling, and may they do this?" on its own: an MCP server
checks an API key, an Odoo SDK trusts Odoo's session, a REST app writes a FastAPI dependency, and
the use case underneath has no standard way to know who asked nor a standard way to say "no".
`sincpro_mcp_odoo` passes the tenant's access token as an argument to every tool so each use case
resolves the tenant on its own. The same questions come back each time — where the token is
verified, where permissions come from, what a refusal raises, how an optional step is skipped,
how one service proves itself to another, how a cron skips the check — and are answered
differently in each repository, with permissions written as strings nobody can navigate.

## Background — what mature frameworks settled on

| Framework / library | Its shape | What this design takes |
|---|---|---|
| **Starlette** | `AuthenticationBackend.authenticate(conn) -> (AuthCredentials, BaseUser) \| None` | `AuthProvider.authenticate`, `None` = not my kind of credential |
| **Django** | `AnonymousUser`: `request.user` is never null | `Identity.anonymous()`: `current_identity()` is never `None` |
| **Spring Security** | `ProviderManager` walks `AuthenticationProvider`s; `SecurityContextHolder`; `@PreAuthorize` | providers tried in order; `security_context`; method-level declaration — typed, not SpEL strings |
| **ASP.NET Core** | declarative `[Authorize(Policy)]` plus imperative `AuthorizeAsync(user, resource, requirement)` | two doors: the guard before the use case, `check(permission, resource)` on the loaded resource |
| **NestJS** | guards reading `@Roles(Role.Admin)` metadata | enum permissions; a decorator that only marks; a guard that reads the mark |
| **Encore** | one auth handler returning typed `AuthData`; `api({auth: true})` | one provider contract; `@auth.authenticated` is one line |
| **GraphQL / Hasura** | a field the caller may not see answers `null`, the rest of the response goes on | `WhenDenied.SKIP`: an optional step answers `None` |
| **Cedar, OPA, OpenFGA, Casbin, Cerbos** | policy engines behind SDKs; OpenID AuthZEN's subject · action · resource · context | `has_permission(identity, permission, resource, context)` — each engine is a provider |
| **Cedar / OPA partial evaluation, OpenFGA `ListObjects`** | answer "which records may they see", not only yes/no | `AuthProvider.scope(...) -> Criteria` |

The convergence: **declarative metadata on the handler + a guard that reads it + an imperative
check on the loaded resource**, with a typed vocabulary instead of strings. Engines come and go —
Oso's open-source engine was retired — so the contract is what lasts.

## Shapes — which Python form each piece takes

| Form | For | Auth pieces |
|---|---|---|
| **DTO** (`DataTransferObject`) | what crosses a boundary and validates input | `Identity`, `Credentials`, `AccessDescription` |
| **`@dataclass(frozen=True)`** | internal runtime values, never serialized | `Declaration`, `AnyOf` |
| **`StrEnum`** | a closed, navigable vocabulary | `Permission` (extended per context), `WhenDenied`, `IdentityKind` |
| **abstract class** | a contract adapters implement | `AuthProvider` |
| **ordinary dependency** | what a handler calls | `AccessControl`, injected as `auth` |
| **`SincproConfig`** with `Secret` | a provider's configuration per environment | issuer, audience, client secret, key material (phase 2) |
| **`DomainEvent`** | what is persisted and fanned out | `SessionsRevoked`, `ApiKeyRevoked` (phase 3) |

No auth table ships: a store a provider needs is a port with an in-memory adapter; a project maps
its own `Entity` when it wants SQL.

## The pieces

```text
sincpro_framework/auth/
├── domain/
│   ├── identity.py          Credentials (what was presented), Identity (who it is), IdentityKind
│   ├── permissions.py       Permission (StrEnum base), AnyOf, WhenDenied
│   ├── provider.py          AuthProvider — the contract
│   └── exceptions.py        AuthError, Unauthenticated, PermissionDenied
├── adapters/
│   ├── static_provider.py   StaticProvider — identities by token, for tests and local runs
│   └── role_permissions.py  RolePermissions — roles to permissions, implied roles
├── security_context.py      current_identity, as_identity, as_system
├── guard.py                 Declaration, Declarations; BusGuard — one per bus and generation;
│                            HookGuard — the gate of every guarded collection
└── access_control.py        AccessControl: on · requires · any_of · authenticated · public ·
                             authenticate · allows · check · permitted · scope_of · challenges ·
                             credentials_for · requirements_of · verify · describe
sincpro_framework/testing/
└── auth.py                  granting, RecordingProvider, AuthProviderContract
```

Additions outside the package, all generic:

| Where | What | Why auth needs it |
|---|---|---|
| `UseFramework.extend(extension)` | wire a component into a bus and into every generation `fresh()` makes of it, as one step | the guard of a generation reads the generation's handlers and context, not the first bus's |
| `UseFramework.handlers()` | every DTO answered → its handler class, without building the bus | `verify`, `describe`, `requirements_of` change nothing |
| `UseFramework.replaced_for(dto)` | which handlers a `replaces=` took the place of | a replacement inherits a declaration |
| `Hooks.gate(gate)` | a function asked before each hook's moment, handed the running collection — `False` skips it, raising refuses it; held live by every copy and combination, one gate run once | the hook guard |
| `Hooks.registered()` | the hooks registered so far, without walking nor closing the collection | describing changes nothing |
| `context.mixin.executing_context()` | the context of the execution in progress, from outside a bus | the `context` a provider decides with |
| `deprecations.PositionalFields` | a DTO that was a dataclass still takes its fields positionally, with a `DeprecationWarning`, until the next major version | `Run` and `RuntimeUseCase` became DTOs within a minor version |

## Credentials and identity

`Credentials` is what the caller presented, not yet verified; `Identity` is who they turned out to
be. An `AuthProvider` turns one into the other.

```python
class Credentials(DataTransferObject):          # normalized by the entrypoint, whatever the transport
    transport: str                              # "http" · "grpc" · "mcp" · "queue"
    headers: Mapping[str, str]                  # lower-case; gRPC metadata; a message's headers
    cookies: Mapping[str, str]                  # a session cookie — Odoo's session_id
    method: str | None                          # DPoP (RFC 9449), HTTP message signatures
    uri: str | None
    body: bytes | None                          # HMAC over the body — only when a provider needs_body
    peer_certificate: bytes | None              # mTLS, SPIFFE
    bearer: str | None                          # property: the token of Authorization: Bearer
                                                # repr never shows a value


class Identity(DataTransferObject):
    subject: str                                # "user:42", "service:billing", "apikey:ops-bot"
    kind: IdentityKind                          # USER · SERVICE · SYSTEM · ANONYMOUS
    tenant: str | None
    permissions: frozenset[str]                 # as the provider resolved them
    roles: frozenset[str]                       # what the provider found, for whoever asks
    provider: str | None                        # the provider that vouched — the one asked about it
    actor: Identity | None                      # the service acting on the subject's behalf
    claims: Mapping[str, Any]                   # the rest of the credential; claims_as(Shape)
    reason: str | None                          # why the system acts
```

An identity opened by hand — `as_identity(...)`, `granting(...)` — has no provider, and its
`permissions` are the answer.

## The contract — `AuthProvider`

```python
class AuthProvider(ABC):
    name: str
    needs_body: bool = False

    @abstractmethod
    def authenticate(self, credentials) -> Identity | None: ...
    # None = not my kind of credential, the next provider is tried; raise Unauthenticated = mine, and it fails

    def has_permission(self, identity, permission, resource=None, context=None) -> bool:
        return permission in identity.permissions            # False, or raise PermissionDenied(reason)

    def permitted(self, identity, permission, resources, context=None) -> list[bool]: ...   # a batch
    def scope(self, identity, aggregate) -> Criteria | None: return None                    # filter reads
    def challenge(self) -> str | None: return None                                          # WWW-Authenticate
    def credentials_for(self, identity) -> Credentials | None: return None                  # outgoing
```

One method is required. A provider is whatever the project needs — a wrapper of Keycloak, of
Odoo, of an internal API, of a policy engine — as long as it honours the contract, which it proves
with `AuthProviderContract`: accepted credentials are someone and the same someone twice, foreign
ones answer `None`, failing ones raise `Unauthenticated` and nothing else, an anonymous identity is
granted nothing, a batch answers one decision per resource.

The contract is synchronous, like the bus: an entrypoint runs the bus on a worker thread, so a
provider that calls the network blocks that thread only.

### Protocols it covers

| Protocol / case | Covered by |
|---|---|
| OAuth 2.1 / OIDC JWT — Keycloak, Entra, Auth0, Google | `authenticate` verifies signature (JWKS), `exp`, `aud`; permissions from a claim or roles |
| Opaque token + introspection (RFC 7662) | `authenticate` calls the introspection endpoint, caches in `KeyValueStore` |
| API key, HTTP Basic | `headers` |
| Session cookie — Odoo | `cookies` |
| mTLS, SPIFFE | `peer_certificate` |
| DPoP (RFC 9449) | `method`, `uri`, `headers["dpop"]` |
| HMAC signatures — webhooks, SigV4 | `body` with `needs_body` |
| gRPC | metadata as `headers`, the peer certificate |
| MCP remote servers (OAuth 2.1) | `authenticate` for the bearer; `challenge()` points to the Protected Resource Metadata (RFC 9728) |
| Step-up (RFC 9470) | `Unauthenticated(step_up=...)` with the provider's `challenge()` |
| Service to service on a user's behalf (RFC 8693) | `credentials_for(identity)`, verified by the host's provider |
| Queue consumers | the message's headers as `headers` |
| Several providers — JWT *or* API key | `AccessControl(providers=[...])`, tried in order |
| RBAC, permissions in the token | `RolePermissions`, or the claim |
| ABAC, policy engines — Cedar, OPA | `has_permission(..., resource, context)` |
| ReBAC — OpenFGA, SpiceDB | `has_permission`, `permitted` in a batch, `scope` for listings |

### Out of scope, on purpose

- **Kerberos, NTLM, SPNEGO** — handshakes over several round trips bound to a connection: done at
  the proxy, which hands on a header or a JWT.
- **Interactive login** — passwords, OTP, passkeys, SAML: the identity provider's job. No user
  table, no login UI, no token issuing for users.
- **Masking fields of a response** — `WhenDenied.SKIP` leaves out a step, not a field.
- **Revocation in the middle of a long stream** — authenticated when it connects.
- **Asynchronous providers** — until a case needs one.

## The errors

```text
DomainError
└── AuthError                      catch this to catch any auth refusal
    ├── Unauthenticated            who are you? — reason, step_up
    └── PermissionDenied           you may not — subject, requirement, reason
```

Two, not a taxonomy (Sincpro's coding style: one error per layer, its context in fields): a
missing, invalid, expired or revoked credential is `Unauthenticated` with its `reason`, because the
caller does the same for each. `PermissionDenied`, not `PermissionError` — Python's builtin is an
`OSError`. Every provider raises only these; every entrypoint maps them the same (phase 2):

| Error | RPC / OpenRPC | HTTP | gRPC | MCP |
|---|---|---|---|---|
| `Unauthenticated` | code, `data.kind = "unauthenticated"` | 401 + `WWW-Authenticate` from `challenges()` | `UNAUTHENTICATED` | 401 + Protected Resource Metadata |
| `Unauthenticated(step_up=…)` | `data.step_up` | 401, `error="insufficient_user_authentication"` | `UNAUTHENTICATED` + detail | 401, same challenge |
| `PermissionDenied` | code, `data.kind = "permission_denied"`, `data.requirement` | 403 | `PERMISSION_DENIED` | 403 / tool error |

## Declaring

```python
class BillingPermission(Permission):
    ISSUE_INVOICE = "billing.invoice.issue"
    READ_CREDIT = "billing.credit.read"

auth = AccessControl[BillingPermission](providers=[OdooProvider(...)])
auth.on(billing)                  # every use case of the bus; `auth` injected in its handlers
auth.on(billing_hooks)            # every hook of the collection

@billing.feature(CommandIssueInvoice)
@auth.requires(BillingPermission.ISSUE_INVOICE)
class IssueInvoice(Feature): ...

@billing.feature(CommandCreditReport)
@auth.requires(BillingPermission.READ_CREDIT, when_denied=WhenDenied.SKIP)
class CreditReport(Feature): ...

@billing_hooks.on(Invoice)
@auth.requires(BillingPermission.ISSUE_INVOICE)
class OnlyIssuersWriteInvoices(Hook): ...
```

- The member is for code, the value for the wire; `AccessControl[BillingPermission]` accepts only
  billing's members.
- `requires(A, B)` and stacked `requires` are *and*; `any_of(A, B)` is *or*; `authenticated` is
  anybody authenticated; `public` is anybody.
- The decorators only mark classes — the marks are kept by `AccessControl`, never written onto the
  class — in any order around the registration, read at the first run.
- A `replaces=` handler or hook that declares nothing keeps the declaration of the one it replaces.

## Where it is decided

| Door | How | For |
|---|---|---|
| **Guard around a use case** | the outermost interceptor of the bus — placed by the framework, so a caching or retry interceptor never answers without asking | what the use case declared |
| **Gate before a hook** | `Hooks.gate`: the declaration holds whichever use case writes the aggregate; the record is the resource | "writing an invoice requires issuing them" |
| **Inside a handler** | `self.auth.allows(permission)` before an optional step; `self.auth.check(permission, resource)` on the loaded resource; `permitted` for many | decisions only the handler can make |
| **Reads** | `self.auth.scope_of(Invoice)` → `Criteria` → `search(...)` or `narrowed(...)`; nobody calling is `Unauthenticated`, never "every record" | which records |

The identity's own provider — the one that authenticated it — is asked. An identity opened by hand
answers with its `permissions`.

## Skipping — what is not checked, and when a refusal is not a failure

| Situation | What happens |
|---|---|
| a use case or hook declares nothing | not checked |
| `AccessControl(strict=True)` | a use case entered from outside that declares nothing is refused; a nested one inherits |
| `AccessControl(enabled=False)` | nothing is checked; `on(...)` warns once |
| `when_denied=WhenDenied.SKIP` | a use case answers `None`, a hook's moment is left out; logged at info |
| an ApplicationService orchestrating Features | a nested use case that declares nothing inherits the decision; one that declares is checked again |
| `as_system(reason)` — a cron, a migration | every requirement met; the reason logged |
| one bus that must not be guarded | no `auth.on(bus)` for it |
| a new generation of the bus — `fresh()`, the runtime registry | guarded as the bus it came from; a stored use case declares anew on every version |

## The identity cannot be forged

- **Not in the request context.** Who acts is a `ContextVar` of its own (`security_context`); only
  `as_identity` and `as_system` set it. The context dict stays a dict anyone writes — writing
  `"identity"` into it changes nobody's identity. The variable follows the execution into every
  bus it calls and into `thread_context()`; log lines carry the subject as `identity`.
- **Across services.** `remote_execution` adopts the request context from a header as it came; the
  identity never travels there. The caller sends `credentials_for(identity)` — signed, short-lived,
  the calling service as `actor` — and the host's provider verifies it (phase 2).
- **Caching.** The guard wraps the cache interceptor; a cached Query whose answer depends on the
  identity varies by subject or tenant.

## Debugging and navigation

- Everything by reference: a permission, a provider, a declaration — "go to definition" lands on
  it. The provider is passed explicitly where the context is composed.
- A refusal logs identity, what was refused and the reason; a skip logs at info; an inherited
  decision at debug. A provider's `PermissionDenied(reason)` reaches the caller as it said it.
- `auth.verify()`, `describe()`, `requirements_of()` build no bus and close no collection — called
  early they only see less.
- `auth.verify()` — a declaration on a class no guarded bus or collection runs, and, when strict,
  every use case that declares nothing. `auth.describe()` — every use case and declared hook with
  what it requires. `auth.requirements_of(CommandX)` — one of them.

## Testing

| I need… | How |
|---|---|
| to be someone, no provider, no bus | `with as_identity(Identity.user("user:1", permissions={...})):` |
| only some permissions, one line | `with granting(BillingPermission.ISSUE_INVOICE):` |
| an entrypoint with fake tokens | `StaticProvider({"token-a": identity_a})` |
| no access control | `AccessControl(enabled=False)`, or no `auth.on(bus)` |
| a real provider swapped | `override_dependencies(bus, auth=...)` — it is a dependency |
| what a provider was asked | `RecordingProvider(real_or_none).asked` |
| a provider of mine to behave | inherit `AuthProviderContract` |
| what a use case requires | `auth.requirements_of(CommandX)`; `assert auth.verify() == []` |
| a cron without an identity | `with as_system("test"):` |

## How a project plugs its protocol in

**Keycloak, Entra, Google — the identity provider issues a JWT.** Configuration, no code of the
project's: `JwtProvider(settings.keycloak, roles=RolePermissions(...))` (phase 3) verifies the
token and fills the identity's permissions; the default `has_permission` answers the rest.

**ChatGPT, Claude — MCP clients.** They are OAuth *clients* and need an authorization server
where the user logs in. Either the MCP server points them to Keycloak — its Protected Resource
Metadata says so, and it is the Keycloak case — or the service is its own authorization server, as
`sincpro_mcp_odoo` is on FastMCP. Issuing tokens stays outside the framework; verifying them is a
provider.

**`sincpro_mcp_odoo` — its own token, resolved against Odoo.** Its authorization server stays as it
is. What its `McpAccessTokenVerifier` does becomes a provider:

```python
class SincproTokens(AuthProvider):
    name = "sincpro"

    def __init__(self, common_mcp: UseFramework) -> None:
        self.common_mcp = common_mcp

    def authenticate(self, credentials: Credentials) -> Identity | None:
        if credentials.bearer is None:
            return None
        access = self.common_mcp(CommandResolveCredential(access_token=credentials.bearer),
                                 SincproMcpAccess)
        return Identity.user(f"user:{access.email}", tenant=access.tenant)

    def has_permission(self, identity, permission, resource=None, context=None) -> bool:
        return self.odoo_groups.holds(identity, permission)        # its permission API
```

The use cases stop receiving the access token as an argument and read `self.auth.identity.tenant`.
The tenant's Odoo token is not identity — it is the credential to call the customer's Odoo — so it
stays out of the `Identity`, resolved from the tenant by the adapter that already does.

## How this maps to Odoo

| Odoo | Here |
|---|---|
| `res.users` | `Identity` (subject, tenant = company / database) |
| `res.groups` (and implied groups) | roles, `RolePermissions(implies=...)`, or a provider asking Odoo |
| `ir.model.access` — CRUD per model per group | `@auth.requires` on a use case — finer than per model — or on a hook, per aggregate |
| `ir.rule` — record rules as domains | `AuthProvider.scope(identity, aggregate) -> Criteria` |
| `sudo()` | `as_system(reason)`, logged |
| API keys / session | a provider reading `headers` / `cookies` |
| `auth_oauth` | `JwtProvider` against the same IdP |

## Decisions to iterate

1. **The service-to-service credential** — proposed a signed short-lived token first
   (`credentials_for` on a `ServiceTokenProvider`); mTLS is the deployment's and arrives as
   `peer_certificate`.
2. **Odoo as the identity provider** — should the Odoo SDKs issue OIDC tokens for Odoo users?
3. **The JWT library of the `[jwt]` extra** — joserfc or PyJWT (Authlib's stack is joserfc).
4. **Reads filtered by default** — should a repository apply `scope_of` by itself while an identity
   is in play, or stay explicit as in phase 1?

## Phases

1. **Built.** `Permission`, `AnyOf`, `WhenDenied`; `Credentials` and `Identity` as DTOs; the two
   errors; `AuthProvider` with its six methods; `StaticProvider`, `RolePermissions`;
   `current_identity`, `as_identity`, `as_system` on a `ContextVar` of their own; `AccessControl`
   with `requires` / `any_of` / `authenticated` / `public` / `when_denied`, skip by default,
   `strict`, `enabled`, the outermost guard, nested and `replaces` inheritance, the hook gate,
   `authenticate` over several providers, `allows` / `check` / `permitted` / `scope_of` /
   `challenges` / `credentials_for`, `requirements_of` / `verify` / `describe`; `granting`,
   `RecordingProvider`, `AuthProviderContract`; `UseFramework.extend` / `handlers` /
   `replaced_for`, `Hooks.gate` / `registered`, `executing_context`, `PositionalFields` for `Run`
   and `RuntimeUseCase`. A review of the phase closed ten findings — generations of the bus,
   replacements in combined hooks, copies made before the gate, stored use cases declaring anew,
   reads that failed open, batches, checks that built the bus, strict answering nobody with 403,
   a gate run twice — each with its test.
2. The entrypoints authenticate: RPC, gRPC, MCP and the Open Host build `Credentials`, call
   `auth.authenticate`, open `as_identity`, and map the errors; `ApiKeyProvider`; a
   `ServiceTokenProvider` in `remote_execution` and the Open Host, closing its missing guard.
3. `JwtProvider` behind `[jwt]` — JWKS cached, refetched on an unknown `kid`; revocation by a deny
   list in `KeyValueStore` or introspection, cached; MCP's OAuth metadata.
4. Reads filtered by `scope_of` where the project asks for it, per decision 4.
5. An Odoo SDK provider: `res.groups` as permissions, the session cookie, API keys.
