---
name: sincpro-framework-auth
description: Guard a sincpro_framework service — declare permissions on use cases and hooks (@auth.requires, @auth.authenticated, @auth.public, when_denied=SKIP), implement an AuthProvider (JWT, API key, service token), and let every entrypoint authenticate by itself. Use whenever a task adds authorization, permission checks, an identity, a token/API-key provider, or asks who is calling / what they may do.
---

# sincpro-framework-auth

A use case or a hook declares the permission it requires; a provider says who is calling and whether
they hold it; the guard applies the declaration **however** the use case is reached — an entrypoint,
a cron, another bus, an ApplicationService. A refusal is one of two errors every entrypoint already
tells the caller: `Unauthenticated` or `PermissionDenied`. This skill stands alone; the long form is
`docs/auth/README.md` and PRD_11 in the framework repo.

## Context

- **Problem it solves:** one place, next to the use case, that says who may run it, held on every
  path into the bus — so an RPC method, a cron and a nested call cannot disagree.
- **The framework facilitates; security decisions are the service's.** Which provider, how a token
  is verified, which keys exist, what is public, how strict to be: the project chooses. Nothing is
  forced — a use case/hook that declares nothing is not checked, and a bus with no `AccessControl`
  checks nothing.
- **It is not** an identity provider, a login flow, a user store, a token issuer for users, or field
  masking. The IdP issues tokens; a provider of the project verifies them.
- **Do not use it** for business validation ("amount above limit" is a domain rule, unless it is a
  permission decision the provider owns), nor duplicate it with checks in FastAPI routes.

## Abstractions

Every name below imports from `sincpro_framework.auth` unless the Import column says otherwise.

| Term | What it is | Kind | Import |
|---|---|---|---|
| `Permission` | `StrEnum` base a context extends; value `context.aggregate.action` | DTO (enum) | `sincpro_framework.auth` |
| `Credentials` | what arrived, normalized by the entrypoint; **not verified** | DTO | same |
| `Identity` | who the caller turned out to be: subject, kind, tenant, permissions, roles, provider | DTO | same |
| `IdentityKind` | `user` · `service` · `system` · `anonymous` | DTO (enum) | same |
| `AuthProvider` | the one contract: `authenticate` (+ optional `has_permission`, `scope`, …) | port | same |
| `StaticProvider` | fixed bearer tokens → identities (tests, local) | adapter | same |
| `ApiKeyProvider` / `ApiKeyStore` / `InMemoryApiKeys` / `ApiKey` | keys kept by SHA-256; store is the port | adapter / port / adapter / DTO | same |
| `ServiceTokenProvider` | service-to-service HS256 JWT, stdlib only, header `x-sp-service-token` | adapter | same |
| `RolePermissions` | roles → permissions, with implied roles | adapter (helper) | same |
| `AccessControl[P]` | holds providers + declarations, decides, `on(bus)` wires the guard | registry | same |
| `@auth.requires(...)` / `@auth.authenticated` / `@auth.public` | mark a Feature, ApplicationService or Hook | decorator | methods of `AccessControl` |
| `auth.any_of(A, B)` → `AnyOf` | one among several | function / DTO | same |
| `WhenDenied.RAISE` / `.SKIP` | refuse, or answer `None` and log | setting | same |
| `strict=` / `enabled=` | refuse undeclared use cases entered from outside / check nothing | setting | `AccessControl(...)` |
| `self.auth.allows` / `check` / `permitted` / `scope_of` | in-handler decisions on a loaded resource | function | injected `auth` |
| `as_identity` / `as_system` / `current_identity` | who the execution acts as (a `ContextVar`) | function | same |
| `AuthError` → `Unauthenticated` (401) / `PermissionDenied` (403) | the two refusals, `DomainError`s | DTO (exception) | same |
| `IdentityMiddleware` | ASGI middleware for a project's own app | adapter | same |
| `credentials_from_asgi` / `credentials_from_headers` / `authenticated_as` | what an entrypoint does | function | same |
| `describe()` / `verify()` / `requirements_of()` | introspection, test-time checks | function | `AccessControl` methods |
| `granting` / `RecordingProvider` / `AuthProviderContract` | test helpers | function / adapter / port | `sincpro_framework.runtime.testing` |

Look-alikes: `auth.Identity` is **who calls**; `observability.ObservabilityIdentity` is **who the
service is** (release, version). `PermissionDenied` is not Python's `PermissionError` (an `OSError`).
`auth.Declaration` is not `observability.metrics.Declaration`. `@auth.public` admits anybody;
`@auth.authenticated` anybody *known*. `as_system(reason)` passes every check for one block;
`enabled=False` turns the whole `AccessControl` off.

## Architecture

**Inside the framework** — package `sincpro_framework/auth/`, standard library only:

- `domain/` the vocabulary and the `AuthProvider` port; `adapters/` the shipped providers (all
  stdlib — `ServiceTokenProvider` signs with `hmac`); `security_context.py` the identity
  `ContextVar`; `guard.py` the bus interceptor (`BusGuard`, outermost) and the hook gate
  (`HookGuard`, via `Hooks.gate`); `access_control.py` declarations and decisions;
  `transports.py` + `asgi.py` what entrypoints call.
- No JWT/OIDC library is shipped. A provider for Keycloak/Entra/Google is the project's own adapter,
  and its library (e.g. PyJWT) is the project's dependency.

**Inside a consumer service** (one `UseFramework` per bounded context, created in
`infrastructure/framework.py` before `services/` is imported; `auth.on(bus)` before any gateway):

```
billing/
  __init__.py                  # billing = config_billing_framework("billing"); then `from . import services`
  domain/permissions.py        # class BillingPermission(Permission)
  adapters/keycloak.py         # class KeycloakProvider(AuthProvider) — the project's own
  infrastructure/
    auth.py                    # auth = AccessControl[BillingPermission](providers=[...])
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
                         as_identity(identity) ─► bus(dto) ─► BusGuard (outermost interceptor)
                                              │  declaration of the handler: public / authenticated / requires
                                              │  requires → identity's provider.has_permission(...)
                                              ▼
                         other interceptors ─► execute() ─► self.auth.check(perm, resource)
                                              └► repository.save ─► HookGuard before the hook's moment
```

## Mistakes an agent makes

Silent ones first — the runtime says nothing.

- **Leaving a use case undeclared on a non-strict bus.** It runs for anybody, anonymous included.
  Declare every use case; in production use `AccessControl(..., strict=True)`; assert
  `auth.verify() == []` in a test. (Gateways refuse an undeclared *published* use case, not a
  nested, cron or internal one.)
- **Putting the identity in `bus.context({...})` or `self.context`.** Ignored — the guard reads only
  `as_identity`. Open `as_identity(...)` or let the entrypoint do it.
- **A provider answering `None` for a credential of its own kind that fails.** The caller becomes
  anonymous and every public/undeclared use case runs. `None` means "not my kind"; a bad token of
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
- **`as_system(...)` on a request path.** Every requirement is met. It is for crons, migrations and
  consumers, with a reason that is logged.
- **A handwritten FastAPI route calling `bus(dto)`.** No credentials reach the bus. Use the gateway,
  or wrap the app in `IdentityMiddleware(app, access=auth)`.
- **Refusals counted as failures.** They reach GlitchTip as errors unless the bus is told they are
  traffic: `billing.ignore_sentry_exceptions(AuthError)`.

## Permissions and a provider

```python
from sincpro_framework.auth import (
    AccessControl, AuthProvider, Credentials, Identity, Permission, Unauthenticated, WhenDenied,
)


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
            raise Unauthenticated("unknown key")         # mine, and it fails
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


auth.on(billing)                                     # before the bus is built
```

- The decorators only mark classes, in any order around `@billing.feature`; the marks are read at
  first run, so a handler registered after `auth.on(...)` is guarded too.
- `@auth.requires(A, B)` and stacked `requires` are *and*; `auth.any_of(A, B)` is *or*.
- `self.auth.check(permission, resource)` decides on the loaded resource (no interceptor can see
  it — the provider gets it); `self.auth.permitted(permission, resources)` answers a batch.
- A nested use case that declares something is checked again; one that declares nothing inherits
  the decision of the use case entered from outside. A `replaces=` handler or hook that declares
  nothing keeps the declaration of the one it replaces.

## Acting as someone

```python
from sincpro_framework.auth import as_identity, as_system

with as_identity(auth.authenticate(credentials)):    # what an entrypoint does
    billing(CommandIssueInvoice(...))

with as_system("cron: close cash registers"):
    billing(CommandCreditReport(...))
```

Who acts follows the execution into every bus it calls and into `thread_context()`; it does not
cross to another service by itself — `ServiceTokenProvider.credentials_for` does that.

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
| a use case/hook declares nothing | not checked (strict: refused when entered from outside) |
| `enabled=False` | nothing is checked; `on(...)` logs a warning |
| `WhenDenied.SKIP` | a use case answers `None`, a hook's moment is left out — logged |
| `as_system(reason)` | every requirement is met; the reason is logged |

Entrypoints, refusal mapping per wire and testing helpers:
[references/entrypoints-and-testing.md](references/entrypoints-and-testing.md).

Not covered by any contract: Kerberos/NTLM/SPNEGO (the proxy does it), interactive login (the IdP),
field masking (`SKIP` leaves out a step, not a field), revocation mid-stream.

## Related

- The bus, interceptors, error handlers, the context manager: `sincpro-framework`, `sincpro-framework-core`
- Entrypoints and declared exposure: `sincpro-framework-entrypoints`
- Refusals on metrics and GlitchTip: `sincpro-framework-observability`
