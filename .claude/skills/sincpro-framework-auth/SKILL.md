---
name: sincpro-framework-auth
description: Guard a sincpro_framework service — declare permissions on use cases and hooks (@auth.requires, @auth.authenticated, @auth.public, when_denied=SKIP), implement an AuthProvider (JWT, API key, service token), and let every entrypoint authenticate by itself. Use whenever a task adds authorization, permission checks, an identity, a token/API-key provider, or asks who is calling / what they may do.
---

# sincpro-framework-auth

A use case or a hook declares the permission it requires; a provider says who is calling and whether
they hold it; the guard applies the declaration **however** the use case is reached — an entrypoint,
a cron, another bus, an ApplicationService. A refusal is one of two errors every entrypoint already
tells the caller: `Unauthenticated` or `PermissionDenied`. Depth: `docs/auth/README.md`, PRD_11.

Nothing is forced: a use case/hook that declares nothing is not checked, and a bus with no
`AccessControl` runs as before.

## Permissions and a provider

```python
from sincpro_framework.auth import AccessControl, AuthProvider, Credentials, Identity, Permission


class BillingPermission(Permission):                 # the StrEnum base a context extends
    ISSUE_INVOICE = "billing.invoice.issue"
    READ_CREDIT = "billing.credit.read"


class TenantKeys(AuthProvider):
    name = "tenant-keys"

    def authenticate(self, credentials: Credentials) -> Identity | None:
        key = credentials.headers.get("x-api-key")
        if key is None:
            return None                              # not mine: the next provider is tried
        if key not in self.keys:
            raise Unauthenticated("unknown key")     # mine, and it fails
        return Identity.user(self.keys[key], tenant="bo")


auth = AccessControl[BillingPermission](providers=[TenantKeys(...)])
```

| Method | Required | Default | Override it for |
|---|---|---|---|
| `authenticate(credentials)` | yes | — | a token, key, cookie or certificate into an `Identity`; `None` = not my kind, `Unauthenticated` = mine and it fails |
| `has_permission(identity, permission, resource, context)` | no | `permission in identity.permissions` | an API, a policy engine (Cedar/OPA), a relationship store (OpenFGA) |
| `scope(identity, aggregate)` | no | `None` | the records an identity may read, as `Criteria` |
| `challenge()` | no | `None` | the `WWW-Authenticate` of a 401 |

The shipped adapters: `StaticProvider`, `RolePermissions`, `ApiKeyProvider`, `ServiceTokenProvider`.
A provider of yours proves itself with `sincpro_framework.testing.AuthProviderContract`.

`Credentials` carries every field a protocol needs, normalized by the entrypoint: `headers`,
`cookies`, `method`, `uri`, `body` (only when the provider says `needs_body`), `peer_certificate`.
Its `repr` never shows a value.

## Declaring what a use case requires

```python
@billing.feature(CommandIssueInvoice)
@auth.requires(BillingPermission.ISSUE_INVOICE)
class IssueInvoice(Feature):
    auth: AccessControl[BillingPermission]           # injected like any dependency
    def execute(self, dto):
        return Answer(by=self.auth.identity.subject)


@billing.feature(CommandCreditReport)
@auth.requires(BillingPermission.READ_CREDIT, when_denied=WhenDenied.SKIP)
class CreditReport(Feature): ...
```

- `auth.on(billing)` places the guard — the **outermost** interceptor, so a cached or retried answer
  is never given without asking — and injects `auth` in the handlers, typed.
- `@auth.requires(A, B)` and stacked `requires` are *and*; `auth.any_of(A, B)` is *or*;
  `@auth.authenticated` is anybody authenticated; `@auth.public` is anybody.
- `self.auth.allows(...)` asks before an optional step; `self.auth.check(permission, resource)`
  decides on the loaded resource (which no interceptor can see — the provider gets it).
- `when_denied=WhenDenied.SKIP` answers `None` and logs (a nested step left out).
- Nested use cases are checked again; a `replaces=` handler or hook that declares nothing keeps the
  declaration of the one it replaces.

## Acting as someone

```python
from sincpro_framework.auth import as_identity, as_system

with as_identity(identity):
    billing(CommandCheckout(...))

with as_system("cron: close cash registers"):
    billing(CommandCreditReport(...))
```

Who acts is a `ContextVar` of its own — never a key of `bus.context(...)` (that dict is writable, so
an identity there could be forged). It follows the execution into every bus it calls and into
`thread_context()`. `authenticate` tries providers in order; the first that recognizes the
credentials answers and stamps its name on the `Identity`. Credentials no provider recognizes are
`Identity.anonymous()`.

## Hooks

```python
@billing_hooks.on(Invoice)
@auth.requires(BillingPermission.ISSUE_INVOICE)
class OnlyIssuersWriteInvoices(Hook):
    def before_save(self, invoice: Invoice) -> None: ...

auth.on(billing_hooks)
```

A hook's declaration holds whichever use case writes the aggregate, decided before the hook's moment,
inside the write. `auth.on(hooks)` registers the guard as a gate (`Hooks.gate`) that survives every
copy or combination.

## Entrypoints authenticate by themselves

A guarded bus needs nothing more on its entrypoints: each builds `Credentials` from what arrived, the
bus's `AccessControl` authenticates them, and the use case runs as that identity. A refusal becomes
the wire's answer (401/403 for REST, `-32001`/`-32003` for JSON-RPC, `UNAUTHENTICATED`/
`PERMISSION_DENIED` for gRPC, a tool error for MCP). A call to another service carries who it acts
for (`ServiceTokenProvider.credentials_for`) — only subject, kind, tenant and permissions travel.

## Testing

| Need | How |
|---|---|
| to be someone, no provider, no bus | `with as_identity(Identity.user("user:1", permissions={...})):` |
| only some permissions, one line | `with granting(BillingPermission.ISSUE_INVOICE):` |
| what the provider was asked | `RecordingProvider(real_or_none)` → `.asked` |
| what a use case requires | `auth.requirements_of(CommandIssueInvoice)` |
| a declaration that never runs / what strict refuses | `assert auth.verify() == []` |

**Entrypoints and strict mode:** a published use case on a guarded bus must declare access or the
gateway build fails (`unguarded=True` to allow a bus with no `AccessControl`). `strict=True` refuses
a use case entered from outside that declares nothing; `enabled=False` checks nothing.

## What no contract covers

Kerberos/NTLM/SPNEGO (the proxy does it), interactive login (the IdP does it), field masking of a
response (`WhenDenied.SKIP` leaves out a step, not a field), revocation mid-stream.

## Related

- The bus, interceptors and error handlers: `sincpro-framework`
- Entrypoints and declared exposure: `sincpro-framework-entrypoints`
