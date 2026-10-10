# Entrypoints and testing

## Entrypoints authenticate by themselves

A guarded bus needs nothing more on its entrypoints: each builds `Credentials` from what arrived,
the bus's `AccessControl` authenticates them (`authenticated_as(bus, credentials)`), and the use
case runs as that identity. A bus nobody guards is left alone: nothing is authenticated, the
identity is what the host opened.

| Entrypoint | Credentials from | A refusal is |
|---|---|---|
| REST (`FastApiGateway`) | the ASGI scope of the request | 401 (with `WWW-Authenticate`) / 403 problem responses |
| JSON-RPC (`RpcGateway`) | headers, cookies, query, method, target | `-32001` / `-32003` with `data.kind`, `data.reason`, `data.requirement` |
| gRPC (`GrpcGateway`) | metadata, the mTLS client certificate | `UNAUTHENTICATED` / `PERMISSION_DENIED`, `sp-auth-refusal` and `www-authenticate` in trailing metadata |
| MCP (`build_mcp_server`) | the HTTP request of the tool call | a tool error; with `auth=`, FastMCP's own bearer check and 401 first |
| A project's own ASGI app | `IdentityMiddleware(app, access=auth)` | 401 with `WWW-Authenticate`, 403 with the reason |
| A context hosted for another service | the caller's `ServiceTokenProvider` token | the refusal raised on the caller |

- **Gateway build rule:** a published use case on a guarded bus must declare something
  (`@auth.requires`, `@auth.authenticated` or `@auth.public`), or the gateway build fails. A bus
  with no `AccessControl` needs `unguarded=True` on the gateway.
- **Order:** `auth.on(bus)` before the bus is built — a first call and `bus.with_trace(...)` build
  it; afterwards `auth.on` raises `BusAlreadyBuilt`. And before any gateway: a gateway reads the
  bus's `AccessControl` when it is built, so one built earlier sees an unguarded bus.
- **A call to another service** carries who it acts for: the caller's provider issues a short JWT
  (`credentials_for`) in its own header; the host verifies it with the same keys. Only subject,
  kind, tenant and permissions travel — never claims. The system identity does not cross services,
  and the request context is never read for identity.
- The transports and the middleware are standard library; `grpc`, `fastmcp` and `starlette` are
  read only by the entrypoints that already need them.

## Testing

| Need | How |
|---|---|
| to be someone, no provider, no bus | `with as_identity(Identity.user("user:1", permissions={...})):` |
| only some permissions, one line | `with granting(BillingPermission.ISSUE_INVOICE):` (`sincpro_framework.runtime.testing`) |
| an entrypoint with fake tokens | `StaticProvider({"token-a": identity_a})` |
| no access control at all | `AccessControl(enabled=False)`, or no `auth.on(bus)` |
| what the provider was asked | `RecordingProvider(real_or_none)` → `.asked` |
| a provider of mine to behave | inherit `AuthProviderContract` |
| what a use case requires | `auth.requirements_of(CommandIssueInvoice)` |
| a declaration that never runs / what strict refuses | `assert auth.verify() == []` — builds no bus |
| the whole map | `auth.describe()` → `AccessDescription` (`use_cases`: declaration or `unchecked`) |

Test through the bus with an identity open — never by calling `execute` directly, which skips the
guard.
