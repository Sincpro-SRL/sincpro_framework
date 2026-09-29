# Auth — who is calling, and what they may do

A use case or a hook declares the permission it requires; a provider says who is calling and
whether they hold it; the guard applies the declaration however the use case is reached — an
entrypoint, a cron, another bus, an ApplicationService. A refusal is one of two errors every
entrypoint already tells the caller: `Unauthenticated` (who are you?) or `PermissionDenied` (you
may not).

| Piece | Where | What it is |
|---|---|---|
| `Permission` | `auth/domain/permissions.py` | the `StrEnum` base a bounded context extends with its permissions |
| `Credentials` → `Identity` | `auth/domain/identity.py` | what the caller presented, and who they turned out to be |
| `AuthProvider` | `auth/domain/provider.py` | **the contract**: authenticate, and answer about permissions |
| `AccessControl` | `auth/access_control.py` | the declarations (`requires`, `authenticated`, `public`) and the checks |
| the guard | `auth/guard.py` | what runs around a use case and before a hook |
| `as_identity`, `as_system` | `auth/security_context.py` | who the execution acts as |
| `StaticProvider`, `RolePermissions`, `ApiKeyProvider`, `ServiceTokenProvider` | `auth/adapters/` | what the framework ships; any class honouring the contract stands beside them |
| `IdentityMiddleware` | `auth/asgi.py` | an ASGI app of the project's — FastAPI, Starlette — acting as its requests |
| the transports | `auth/transports.py` | what every entrypoint hands auth: credentials out of a request, the refusal it answers |

Nothing is forced: a use case or hook that declares nothing is not checked, and a bus with no
`AccessControl` runs as before. JSON-RPC, gRPC, MCP and a context hosted for another service
authenticate by themselves when the bus is guarded — see [Entrypoints](#entrypoints-authenticate-by-themselves).

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## Permissions of a bounded context

```python
from collections.abc import Mapping
from typing import Any

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    AuthProvider,
    Credentials,
    Identity,
    Permission,
    PermissionDenied,
    Unauthenticated,
    WhenDenied,
    as_identity,
    as_system,
)


class BillingPermission(Permission):
    ISSUE_INVOICE = "billing.invoice.issue"
    READ_CREDIT = "billing.credit.read"
    APPROVE_PAYMENT = "billing.payment.approve"
```

The member is for code — "find usages" on `BillingPermission.ISSUE_INVOICE` shows every handler
that requires it, and renaming the member is a refactor. The value is for the wire — tokens, the
IdP, `describe()` carry it — so changing it is a breaking change.
`AccessControl[BillingPermission]` accepts only billing's members in `requires`, `allows` and
`check`: another context's permission is a type error.

## A provider: the one contract

A provider turns `Credentials` into an `Identity`, and answers whether an identity may do
something. One method is required; the rest have defaults and are overridden when the protocol
needs them.

```python
class TenantKeys(AuthProvider):
    """A project's own: an API key header, the permissions asked of an API."""

    name = "tenant-keys"

    def __init__(self, keys: Mapping[str, str], grants: set[tuple[str, str]]) -> None:
        self.keys = keys
        self.grants = grants

    def authenticate(self, credentials: Credentials) -> Identity | None:
        key = credentials.headers.get("x-api-key")
        if key is None:
            return None                                  # not mine: the next provider is tried
        if key not in self.keys:
            raise Unauthenticated("unknown key")         # mine, and it fails
        return Identity.user(self.keys[key], tenant="bo")

    def has_permission(
        self,
        identity: Identity,
        permission: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        if resource is not None and getattr(resource, "amount", 0) > 10_000:
            raise PermissionDenied(identity.subject, permission, "above the approval limit")
        return (identity.subject, permission) in self.grants


keys = TenantKeys(
    {"k-accountant": "user:1", "k-clerk": "user:2"},
    {
        ("user:1", BillingPermission.ISSUE_INVOICE),
        ("user:1", BillingPermission.APPROVE_PAYMENT),
    },
)
auth = AccessControl[BillingPermission](providers=[keys])
```

| Method | Required | Default | Override it for |
|---|---|---|---|
| `authenticate(credentials)` | yes | — | turning a token, key, cookie or certificate into an `Identity`; `None` = not my kind, `Unauthenticated` = mine and it fails |
| `has_permission(identity, permission, resource, context)` | no | `permission in identity.permissions` | asking an API, a policy engine (Cedar, OPA), a relationship store (OpenFGA); `False`, or `PermissionDenied(reason)` to say why |
| `permitted(identity, permission, resources, context)` | no | `has_permission` per resource | answering a batch in one call |
| `scope(identity, aggregate)` | no | `None` — every record | the records an identity may read, as `Criteria` |
| `challenge()` | no | `None` | the `WWW-Authenticate` of a 401 — where to get a credential |
| `credentials_for(identity)` | no | `None` | what to send another service on the identity's behalf |

A JWT issuer (Keycloak, Entra, Google) needs only `authenticate` — verify the token, put its
permissions or roles in the `Identity` — and the default answers the rest. `RolePermissions`
turns roles into permissions, a role granting what the roles it implies grant. A provider of
yours proves itself with `sincpro_framework.testing.AuthProviderContract`.

`Credentials` carries every field a protocol needs, normalized by the entrypoint: `headers`
(lower-case; gRPC metadata and a message's headers too), `cookies`, `method` and `uri` (DPoP,
HTTP signatures), `body` (an HMAC over it, read only when the provider says `needs_body`) and
`peer_certificate` (mTLS). Its `repr` never shows a value.

## Declaring what a use case requires

```python
billing = UseFramework("billing", log_after_execution=False)


class CommandIssueInvoice(DataTransferObject):
    total: int


class CommandCreditReport(DataTransferObject):
    customer: str


class CommandPrice(DataTransferObject):
    total: int


class CommandCheckout(DataTransferObject):
    customer: str
    total: int


class Answer(DataTransferObject):
    by: str = ""
    credit: str = ""


class Payment(DataTransferObject):
    amount: int


@billing.feature(CommandIssueInvoice)
@auth.requires(BillingPermission.ISSUE_INVOICE)
class IssueInvoice(Feature):
    auth: AccessControl[BillingPermission]

    def execute(self, dto: CommandIssueInvoice) -> Answer:
        return Answer(by=self.auth.identity.subject)


@billing.feature(CommandCreditReport)
@auth.requires(BillingPermission.READ_CREDIT, when_denied=WhenDenied.SKIP)
class CreditReport(Feature):
    def execute(self, dto: CommandCreditReport) -> Answer:
        return Answer(credit="AAA")


@billing.feature(CommandPrice)
class Price(Feature):
    def execute(self, dto: CommandPrice) -> Answer:
        return Answer()


@billing.app_service(CommandCheckout)
@auth.requires(BillingPermission.ISSUE_INVOICE)
class Checkout(ApplicationService):
    auth: AccessControl[BillingPermission]

    def execute(self, dto: CommandCheckout) -> Answer:
        self.feature_bus.execute(CommandPrice(total=dto.total))
        credit = self.feature_bus.execute(CommandCreditReport(customer=dto.customer), Answer)
        invoice = self.feature_bus.execute(CommandIssueInvoice(total=dto.total), Answer)
        assert invoice is not None
        if self.auth.allows(BillingPermission.APPROVE_PAYMENT):
            self.auth.check(BillingPermission.APPROVE_PAYMENT, Payment(amount=dto.total))
        return Answer(by=invoice.by, credit=credit.credit if credit else "skipped")


auth.on(billing)
```

- `auth.on(billing)` places the guard — the outermost interceptor of the bus, so a cached or
  retried answer is never given without asking — and injects `auth` in the handlers, typed like
  any dependency. The decorators only mark classes, in any order around `@billing.feature`.
- `@auth.requires(A, B)` and stacked `requires` are *and*; `auth.any_of(A, B)` is *or*;
  `@auth.authenticated` is anybody authenticated; `@auth.public` is anybody.
- `self.auth.allows(...)` asks before an optional step; `self.auth.check(permission, resource)`
  decides on the loaded resource, which no interceptor can see — the provider gets it.

## Acting as someone

```python
def called_with(key: str) -> Identity:
    return auth.authenticate(Credentials(transport="http", headers={"X-Api-Key": key}))


with as_identity(called_with("k-accountant")):
    answer = billing(CommandCheckout(customer="c-1", total=500))
    assert isinstance(answer, Answer)
    assert (answer.by, answer.credit) == ("user:1", "skipped")

with as_identity(called_with("k-clerk")):
    try:
        billing(CommandIssueInvoice(total=10))
    except PermissionDenied as refused:
        assert refused.requirement == BillingPermission.ISSUE_INVOICE

try:
    billing(CommandIssueInvoice(total=10))
except Unauthenticated:
    pass

assert auth.authenticate(Credentials(transport="http")).is_anonymous
```

- `authenticate` tries the providers in order: the first that recognizes the credentials answers,
  and stamps its name on the `Identity` — the provider asked about that identity's permissions.
  Credentials no provider recognizes are `Identity.anonymous()`.
- `CreditReport` requires what the accountant lacks, and said `WhenDenied.SKIP`: inside the
  checkout it answered `None`, logged, and the checkout went on. `Price` declares nothing: it is
  not checked. A nested use case that declares its own requirement is checked again.
- Who acts is a `ContextVar` of its own, never a key of `bus.context(...)` — the context is a
  dict anyone writes, so an identity kept there could be forged by writing it. It follows the
  execution into every bus it calls and into `thread_context()`.

## Skipping, strict, disabled, the system

```python
with as_system("cron: close cash registers"):
    billing(CommandCreditReport(customer="c-1"))

strict = AccessControl[BillingPermission](providers=[keys], strict=True)
unchecked = AccessControl[BillingPermission](enabled=False)
```

| Situation | What happens |
|---|---|
| a use case or hook declares nothing | not checked |
| `strict=True` | a use case entered from outside that declares nothing is refused; a nested one inherits |
| `enabled=False` | nothing is checked; `on(...)` warns once |
| `when_denied=WhenDenied.SKIP` | a use case answers `None`, a hook's moment is left out — logged |
| `as_system(reason)` | every requirement is met; the reason is logged |
| a `replaces=` handler or hook that declares nothing | keeps the declaration of the one it replaces |
| one bus that must not be guarded | no `auth.on(bus)` for it |
| a new generation of the bus — `fresh()`, the runtime registry | guarded as the bus it came from, resolving its own handlers |

## Hooks

```python
from dataclasses import dataclass

from sincpro_framework.ddd import Entity, MemoryRepository
from sincpro_framework.ddd.repositories.hooks import Hook, Hooks


@dataclass
class Invoice(Entity):
    total: int = 0


billing_hooks = Hooks(None)


@billing_hooks.on(Invoice)
@auth.requires(BillingPermission.ISSUE_INVOICE)
class OnlyIssuersWriteInvoices(Hook):
    def before_save(self, invoice: Invoice) -> None: ...


auth.on(billing_hooks)
invoices = MemoryRepository(hooks=billing_hooks)

with as_identity(called_with("k-accountant")):
    invoices.save(Invoice(total=1))

with as_identity(called_with("k-clerk")):
    try:
        invoices.save(Invoice(total=1))
    except PermissionDenied:
        pass
```

A hook's declaration holds whichever use case writes the aggregate: "writing an invoice requires
issuing them", decided before the hook's moment, inside the write. `auth.on(hooks)` registers the
guard as a gate of the collection — `Hooks.gate`, the extension point any component can use — and
the gate holds on every copy or combination made of it, before or after: a `without(...)` built
at import time is guarded all the same, and a replacement made in a `combined_with(...)` keeps
what it replaces required.

## Testing

```python
from sincpro_framework.testing import AuthProviderContract, RecordingProvider, granting

with granting(BillingPermission.ISSUE_INVOICE):
    billing(CommandIssueInvoice(total=1))

recording = RecordingProvider(keys)
recorded = AccessControl[BillingPermission](providers=[recording])
with as_identity(recorded.authenticate(Credentials(transport="http", headers={"x-api-key": "k-accountant"}))):
    recorded.allows(BillingPermission.ISSUE_INVOICE)
assert ("has_permission", "user:1", "billing.invoice.issue") in recording.asked


class TestTenantKeys(AuthProviderContract):
    def make_provider(self) -> AuthProvider:
        return keys

    def accepted(self) -> Credentials:
        return Credentials(transport="http", headers={"x-api-key": "k-accountant"})

    def foreign(self) -> Credentials:
        return Credentials(transport="http", headers={"authorization": "Bearer eyJ"})

    def rejected(self) -> Credentials:
        return Credentials(transport="http", headers={"x-api-key": "stolen"})
```

| In a test I need… | How |
|---|---|
| to be someone, no provider, no bus | `with as_identity(Identity.user("user:1", permissions={...})):` |
| only some permissions, in one line | `with granting(BillingPermission.ISSUE_INVOICE):` |
| an entrypoint with fake tokens | `StaticProvider({"token-a": identity_a})` |
| no access control at all | `AccessControl(enabled=False)`, or no `auth.on(bus)` |
| what the provider was asked | `RecordingProvider(real_or_none)` → `.asked` |
| a provider of mine to behave | inherit `AuthProviderContract` |
| what a use case requires | `auth.requirements_of(CommandIssueInvoice)` |
| a declaration that never runs, or what strict refuses | `assert auth.verify() == []` — it builds no bus and closes no collection |

## What it says about itself

```python
described = auth.describe()
assert described.use_cases[f"{__name__}.CommandIssueInvoice"] == "billing.invoice.issue"
assert described.use_cases[f"{__name__}.CommandPrice"] == "unchecked"
assert auth.verify() == []
```

## Entrypoints authenticate by themselves

A guarded bus needs nothing more on its entrypoints: each builds `Credentials` from what arrived,
the bus's `AccessControl` authenticates them, and the use case runs as that identity.

```python
from sincpro_framework.auth import ApiKey, ApiKeyProvider, InMemoryApiKeys, ServiceTokenProvider
from sincpro_framework.entrypoints.rpc import RpcGateway

api_keys = InMemoryApiKeys()
bot_key = api_keys.issue(
    ApiKey(subject="apikey:ops-bot", permissions=frozenset({BillingPermission.ISSUE_INVOICE}))
)

gated = UseFramework("billing-gated", log_after_execution=False)
gated_auth = AccessControl[BillingPermission](
    providers=[ApiKeyProvider(api_keys), ServiceTokenProvider("billing", {"2026-09": "shared"})]
)
gated.feature(CommandIssueInvoice)(gated_auth.requires(BillingPermission.ISSUE_INVOICE)(IssueInvoice))
gated_auth.on(gated)

rpc = RpcGateway({"billing": gated})
call = {"jsonrpc": "2.0", "id": 1, "method": "billing.features.CommandIssueInvoice", "params": {"total": 1}}

answered = rpc.handle(call, None, Credentials(transport="http", headers={"x-api-key": bot_key}))
assert answered["result"] == {"by": "apikey:ops-bot", "credit": ""}
refused = rpc.handle(call, None, Credentials(transport="http"))
assert refused["error"]["data"]["kind"] == "unauthenticated"
```

| Entrypoint | Credentials from | A refusal is |
|---|---|---|
| JSON-RPC (`RpcGateway`) | headers, cookies, query, method, target | a single call: HTTP 401 with `WWW-Authenticate`; `-32001` / `-32003` with `data.kind`, `data.reason`, `data.requirement` |
| gRPC (`GrpcGateway`) | metadata, the mTLS client certificate | `UNAUTHENTICATED` / `PERMISSION_DENIED`, with `sp-auth-refusal` and `www-authenticate` in the trailing metadata |
| MCP (`build_mcp_server`) | the HTTP request of the tool call; with `auth=`, FastMCP's own bearer check first | a tool error; with `auth=`, FastMCP's 401 and — given `base_url` — the resource metadata (RFC 9728) |
| An ASGI app of the project's | `IdentityMiddleware(app, access=auth)` | 401 with `WWW-Authenticate`, 403 with the reason |
| A context hosted for another service | the caller's `ServiceTokenProvider` token | the refusal raised on the caller as itself |

- **A call to another service** carries who it acts for: the calling process's provider issues a
  short JWT (`ServiceTokenProvider.credentials_for`) in its own header, and the host verifies it
  with the same keys. Only subject, kind, tenant and permissions travel — never the claims — and
  the request context it sends is never read for identity. The system does not cross services.
- **A bus nobody guards** is left alone by every entrypoint: nothing is authenticated, the
  identity is what the host opened.
- **Nothing here needs an extra**: the transports and the middleware are the standard library;
  `grpc`, `fastmcp` and `starlette` are read only by the entrypoints that already need them.

## What no contract here covers

- **Kerberos, NTLM, SPNEGO** — handshakes over several round trips bound to a connection; done at
  the proxy, which hands on a header or a JWT.
- **Interactive login** — passwords, OTP, passkeys, SAML: the identity provider's job; the
  service receives the token it issues.
- **Masking fields of a response** — `WhenDenied.SKIP` leaves out a step, not a field.
- **Revocation in the middle of a long stream** — a WebSocket is authenticated when it connects.
