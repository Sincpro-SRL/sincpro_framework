# Auth — who is calling, and what they may do

A use case or a hook declares the permission it requires; a provider says who is calling and
whether they hold it; the guard applies the declaration however the use case is reached — an
entrypoint, a cron, another bus, an ApplicationService. A refusal is one of two errors every
entrypoint already tells the caller: `Unauthenticated` (who are you?) or `PermissionDenied` (you
may not).

## One door per job

The plugin is one folder per job, each its own door and its own page. The import says which side
a name is on: `auth.server.JwtProvider` verifies a token that arrives, `auth.client.ClientCredentials`
presents one that goes out.

| Door | Folder | Job | Page | Extra |
|---|---|---|---|---|
| `sincpro_framework.auth` | `auth/` | **the contract** — what a use case declares and asks, who calls, the ports a project implements, every error, and what the doors share | this page | — |
| `sincpro_framework.auth.server` | `auth/server/` | verify what arrives: `JwtProvider` (Keycloak, Entra, Google, several issuers, JWKS by discovery), introspection (RFC 7662), API keys, service tokens, webhook signatures, DPoP proofs (RFC 9449), mTLS and SPIFFE, policy engines (AuthZEN, OPA, OpenFGA) | [Verifying what arrives](server.md) | asymmetric keys, DPoP, mTLS: `[jwt]` |
| `sincpro_framework.auth.client` | `auth/client/` | present what goes out: static credentials, client credentials shared by every replica, a credential per tenant, token exchange (RFC 8693), refresh tokens held and rotated, device code (RFC 8628), request signing, `call_with_credentials`, `requests`/`httpx` hooks | [Presenting what goes out](client.md) | `private_key_jwt`, DPoP: `[jwt]` |
| `sincpro_framework.auth.grants` | `auth/grants/` | roles declared in code the Odoo 20 way — categories, privileges, exclusive levels, disjoint roles, entitlements — assignments per tenant, resolved per call | [Grants](grants.md) | — |
| `sincpro_framework.auth.accounts` | `auth/accounts/` | accounts the service manages itself: password hashing, lockout, HTTP Basic, one-time links, TOTP, the token issuer with refresh rotation and logout | [Accounts](accounts.md) | argon2: `[argon2]` |

The root never imports a door; `server`, `client` and `grants` build on the root alone, and
`accounts` on the root and the server (its issuer verifies what it issued with a `JwtProvider`).
`tests/auth/test_doors.py` holds it.

**In the root** — the rest of this page:

| Piece | What it is |
|---|---|
| `Permission` | the `StrEnum` base a bounded context extends with its permissions |
| `Credentials` → `Identity` | what the caller presented, and who they turned out to be |
| `AuthProvider`, `IdentityResolver` | **the contract**: authenticate, and answer about permissions; resolve what an identity holds |
| `AccessControl` | the declarations (`requires`, `authenticated`), what `requires` takes besides a permission (`role`, `rule`, `any_of`, `by_field`, `assurance`), the checks asked on every call (`every_call`) and by hand (`check`, `allows`, `has_role`, `scope_of`); `AccessControl.of(bus)` is the one guarding a bus |
| `security` | who the execution acts as (`identity`, `as_identity`, `as_system`), and what every entrypoint hands auth (`authenticated_as`, `challenges`, `identity_headers`) |
| `IdentityMiddleware` | an ASGI app of the project's — FastAPI, Starlette — acting as its requests |
| `AuthError` and its kinds | `Unauthenticated`, `PermissionDenied`, and the refusals of the doors (`EndpointUnavailable`, `CredentialsRejected`, `InvalidGrant`, …) — caught from one place |
| shared by the doors | `Revocations` (`InMemoryRevocations`, `KeyValueRevocations`), `HttpClient` / `UrllibHttp`, `OidcDiscovery`, `ClientAuthentication`, `PublicKey` / `PrivateKey` (`[jwt]`), `HexDigest` / `TimestampedSignature` |

Nothing is forced: a use case or hook that declares nothing is not checked, and a bus with no
`AccessControl` runs as before. Every wire — REST, FastAPI, JSON-RPC, gRPC, MCP, a queue (a
Lambda fed by SQS included), an ASGI app of the project's and a context hosted for another
service — authenticates by itself when the bus is guarded — see [Entrypoints](#entrypoints-authenticate-by-themselves).

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
    WhenDenied,
    security,
)
from sincpro_framework.auth.domain import exceptions


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

A backend the provider asks that does not answer — a registry, a key store, an identity server
down — is never the caller's credentials failing: the provider raises `ServiceUnavailableError`,
and the caller is told to retry — 503 with `Retry-After` — never 401 nor 500.

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
            raise exceptions.Unauthenticated("unknown key")         # mine, and it fails
        return Identity.user(self.keys[key], tenant="bo")

    def has_permission(
        self,
        identity: Identity,
        permission: str,
        resource: Any = None,
        context: Mapping[str, Any] | None = None,
    ) -> bool:
        if resource is not None and getattr(resource, "amount", 0) > 10_000:
            raise exceptions.PermissionDenied(identity.subject, permission, "above the approval limit")
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
yours proves itself with `sincpro_framework.runtime.testing.AuthProviderContract`.

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
  `@auth.authenticated` is anybody authenticated. A use case that declares nothing is open —
  anybody, anonymous included; `@entrypoints.internal` keeps one off every entrypoint.
- `self.auth.allows(...)` asks before an optional step; `self.auth.check(permission, resource)`
  decides on the loaded resource, which no interceptor can see — the provider gets it.
- `auth.by_field("direction", {Direction.INBOUND: P.RECEIVE, Direction.OUTBOUND: P.PAY})` in
  `requires` is a requirement the Command's own field chooses — a value it names nothing for is
  refused; asked with no Command (a catalog), it is one among them.
- A provider words the refusal of a whole `any_of` with `refusing_any(identity, permissions,
  reasons)`; without it, each reason is joined with "and".

## Roles, rules, and a check on every call

`requires` takes more than a permission: `auth.role(...)` is Odoo's `groups=` — met by one of the
roles named, read off the closure a `GrantResolver` gives, so a manager holds the clerk role its
level implies — and `auth.rule(name, check)` is whatever the project decides, asked with the call
(`AccessCall`: the identity, the use case, the Command, the resource and the execution's
context). `@auth.every_call` is an interceptor of the project's own: asked on every use case of
every bus guarded, open or declared, before it runs. In a handler `self.auth` answers the same
terms: `allows(...)` and `check(...)` take any requirement, `has_role(...)` a role.

```python
from sincpro_framework.auth import AccessCall, Identity, PermissionDenied, security


class LedgerPermission(Permission):
    READ = "ledger.entry.read"


class QueryEntry(DataTransferObject):
    number: str
    owner: str


class CommandClose(DataTransferObject):
    period: str


class Done(DataTransferObject):
    by: str


ledger = UseFramework("ledger", log_after_execution=False)
ledger_auth = AccessControl[LedgerPermission]()
suspended_tenants = {"pe"}
own_entry = ledger_auth.rule("its own entry", lambda call: call.dto.owner == call.identity.subject)


@ledger_auth.every_call
def tenant_active(call: AccessCall) -> bool:
    return call.context.get("tenant_id") not in suspended_tenants


@ledger.feature(QueryEntry)
@ledger_auth.requires(LedgerPermission.READ, own_entry)
class ReadEntry(Feature):
    def execute(self, dto: QueryEntry) -> Done:
        return Done(by=security.identity.subject)


@ledger.feature(CommandClose)
@ledger_auth.requires(ledger_auth.role("ledger.manager", "ledger.admin"))
class Close(Feature):
    def execute(self, dto: CommandClose) -> Done:
        return Done(by=self.auth.identity.subject if self.auth.has_role("ledger.admin") else "a manager")


ledger_auth.on(ledger)
ana = Identity.user("user:ana", roles={"ledger.manager"}, permissions={LedgerPermission.READ})
with ledger.context({"tenant_id": "bo"}), security.as_identity(ana):
    assert ledger(QueryEntry(number="E-1", owner="user:ana"), Done).by == "user:ana"
    assert ledger(CommandClose(period="2026-11"), Done).by == "a manager"
    try:
        ledger(QueryEntry(number="E-2", owner="user:bea"), Done)
    except PermissionDenied as refused:
        assert refused.reason == "its own entry does not hold"
with ledger.context({"tenant_id": "pe"}), security.as_identity(ana):
    try:
        ledger(QueryEntry(number="E-1", owner="user:ana"), Done)
    except PermissionDenied as refused:
        assert refused.reason == "tenant_active does not hold"
assert ledger_auth.describe().every_call == ["tenant_active"]
```

| | |
|---|---|
| `auth.role(*roles)` | met by one of them — the identity's roles, their closure once a `GrantResolver` resolved them; a role whose grants the token's scopes or the tenant's entitlements cut is not held ([grants](grants.md#resolved-per-call)) |
| `auth.any_of(...)` | one among any requirements, asked in order: the first that holds grants it, none after it is asked — `auth.any_of(auth.role("admin"), auth.rule("its owner", is_owner))` |
| `auth.rule(name, check)` | `check(AccessCall) -> bool`; refused, `PermissionDenied` names the rule; the check may raise an `AuthError` of its own — a step-up, `Unauthenticated`. Asked with no Command — `may_run` for a catalog, `allows`, a hook — a check that fails reading one is refused: write `call.dto is None or …` for a rule a catalog may list. `open` is not a rule's name |
| `any_of` with a step-up | when no member holds and one is a step-up (`Unauthenticated`), that one answers: authenticating again may meet it |
| `@auth.every_call` / `auth.every_call(check, name=…)` | asked on every use case, open or declared — a suspended tenant, maintenance, an IP allow-list read from the context; the system passes; `describe()` lists them. Asked by `may_run` with no Command, as a rule is: a check that fails reading one hides every use case from a catalog — read the context, or write `call.dto is None or …` |
| `self.auth.allows(r)` / `check(r)` / `has_role(...)` / `identity` | the same requirements, asked by a handler before an optional step |

## Asking without running

A catalog lists only what its caller may call — with the same declaration that guards the run,
the same decision the guard takes:

```python
from enum import StrEnum


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class FinancePermission(Permission):
    RECEIVE = "finance.payment.receive"
    PAY = "finance.payment.pay"


class CommandRegisterPayment(DataTransferObject):
    direction: Direction


finance_auth = AccessControl[FinancePermission]()
finance = UseFramework("finance-asking", log_after_execution=False)


@finance.feature(CommandRegisterPayment)
@finance_auth.requires(
    finance_auth.by_field(
        "direction",
        {Direction.INBOUND: FinancePermission.RECEIVE, Direction.OUTBOUND: FinancePermission.PAY},
    )
)
class RegisterPayment(Feature):
    def execute(self, dto: CommandRegisterPayment) -> None:
        return None


finance_auth.on(finance)
cashier = Identity.user("ana", permissions=[FinancePermission.RECEIVE])

assert finance_auth.may_run(finance, CommandRegisterPayment, cashier)        # one among them
outbound = CommandRegisterPayment(direction=Direction.OUTBOUND)
assert not finance_auth.may_run(finance, CommandRegisterPayment, cashier, dto=outbound)
assert finance_auth.requirements_of(CommandRegisterPayment, bus=finance) is not None
```

`refusal_of(bus, Command, identity)` answers why not — the refusal the guard would raise — and
`None` when it may; the identity in play is asked when none is given.

Asking is as cheap as the provider makes it: `may_run` asks `has_permission` once per permission.
A provider whose permissions come from a backend — a tenant's agents, a plan — reads them **once,
when it authenticates**, into `Identity.permissions` (what ASP.NET calls a claims transformation);
`has_permission` then answers from the identity, and a catalog of fifty use cases asks nothing
more. A copy of the decision in the catalog — its own "is this agent in the set" — is what this is
for: it would drift from the guard the day a declaration changes.

## Acting as someone

```python
def called_with(key: str) -> Identity:
    return auth.authenticate(Credentials(transport="http", headers={"X-Api-Key": key}))


with security.as_identity(called_with("k-accountant")):
    answer = billing(CommandCheckout(customer="c-1", total=500))
    assert isinstance(answer, Answer)
    assert (answer.by, answer.credit) == ("user:1", "skipped")

with security.as_identity(called_with("k-clerk")):
    try:
        billing(CommandIssueInvoice(total=10))
    except exceptions.PermissionDenied as refused:
        assert refused.requirement == BillingPermission.ISSUE_INVOICE

try:
    billing(CommandIssueInvoice(total=10))
except exceptions.Unauthenticated:
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
  dict anyone writes, so an identity stored there could be forged by writing it. It follows the
  execution into every bus it calls and into `thread_context()`.
- On a guarded bus, the identity let in — unless anonymous — is the execution's `user_id` when
  nothing said one, so every fact a use case records names it as `actor` (and an
  `AuditedMixin` record as `created_by`). A provider of the project's that gives `user_id`
  (`bus.context_provider(gives=["user_id"])`) or a scope that names it wins: the identity is
  only what fills it when nothing else did.

## Open, skipping, disabled, the system

```python
with security.as_system("cron: close cash registers"):
    billing(CommandCreditReport(customer="c-1"))

unchecked = AccessControl[BillingPermission](enabled=False)
```

| Situation | What happens |
|---|---|
| a use case or hook declares nothing | open: it runs for anybody, entered from outside or nested; the gateways log each one they publish open, and the manifest and OpenAPI say `open` |
| a use case that must not be reached from outside | `@entrypoints.internal`: on no entrypoint |
| `enabled=False` | nothing is checked; `on(...)` warns once |
| `when_denied=WhenDenied.SKIP` | a use case answers `None`, a hook's moment is left out — logged |
| `security.as_system(reason)` | every requirement is met; the reason is logged |
| a `replaces=` handler or hook that declares nothing | inherits the declaration of the one it replaces |
| one bus that must not be guarded | no `auth.on(bus)` for it |
| a new generation of the bus — `fresh()`, the runtime registry | guarded as the bus it came from, resolving its own handlers |
| a `fresh()` bus checked by other providers — a test's | `auth.using(providers=[...]).on(fresh, replaces=auth)`: the same declarations and `enabled`; its guard and its `auth` in the place of the original's, on every bus made of it too |

## Hooks

```python
from dataclasses import dataclass

from sincpro_framework.ddd import Entity

from sincpro_framework.data_layer.repositories import MemoryRepository
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

with security.as_identity(called_with("k-accountant")):
    invoices.save(Invoice(total=1))

with security.as_identity(called_with("k-clerk")):
    try:
        invoices.save(Invoice(total=1))
    except exceptions.PermissionDenied:
        pass
```

A hook's declaration holds whichever use case writes the aggregate: "writing an invoice requires
issuing them", decided before the hook's moment, inside the write. `auth.on(hooks)` registers the
guard as a gate of the collection — `Hooks.gate`, the extension point any component can use — and
the gate holds on every copy or combination made of it, before or after: a `without(...)` built
at import time is guarded all the same, and a replacement made in a `combined_with(...)` inherits
what the one it replaces required.

## Testing

```python
from sincpro_framework.runtime.testing import AuthProviderContract, RecordingProvider, granting

with granting(BillingPermission.ISSUE_INVOICE):
    billing(CommandIssueInvoice(total=1))

recording = RecordingProvider(keys)
recorded = AccessControl[BillingPermission](providers=[recording])
with security.as_identity(recorded.authenticate(Credentials(transport="http", headers={"x-api-key": "k-accountant"}))):
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
| to be someone, no provider, no bus | `with security.as_identity(Identity.user("user:1", permissions={...})):` |
| only some permissions, in one line | `with granting(BillingPermission.ISSUE_INVOICE):` |
| an entrypoint with fake tokens | `StaticProvider({"token-a": identity_a})` |
| no access control at all | `AccessControl(enabled=False)`, or no `auth.on(bus)` |
| what the provider was asked | `RecordingProvider(real_or_none)` → `.asked` |
| a provider of mine to behave | inherit `AuthProviderContract`; `make_provider_whose_backend_is_down()` checks it says so as `ServiceUnavailableError` |
| production's rules on a `fresh()` bus, with fake tokens | `auth.using(providers=[StaticProvider({...})]).on(test, replaces=auth)` |
| what a use case requires | `auth.requirements_of(CommandIssueInvoice)` |
| a declaration that never runs | `assert auth.verify() == []` — it builds no bus and closes no collection |

## What it says about itself

```python
described = auth.describe()
assert described.use_cases[f"{__name__}.CommandIssueInvoice"] == "billing.invoice.issue"
assert described.use_cases[f"{__name__}.CommandPrice"] == "open"
assert auth.verify() == []
```

## Entrypoints authenticate by themselves

A guarded bus needs nothing more on its entrypoints: each builds `Credentials` from what arrived,
the bus's `AccessControl` authenticates them, and the use case runs as that identity.

```python
from sincpro_framework.auth.server import (
    ApiKey,
    ApiKeyProvider,
    InMemoryApiKeys,
    ServiceTokenProvider,
)
from sincpro_framework.entrypoints.domain.surface import Exposure
from sincpro_framework.entrypoints import RpcGateway

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

rpc = RpcGateway({"billing": gated}, exposure=Exposure.CATALOG)
call = {"jsonrpc": "2.0", "id": 1, "method": "billing.issue_invoice", "params": {"total": 1}}

answered = rpc.handle(call, None, Credentials(transport="http", headers={"x-api-key": bot_key}))
assert answered["result"] == {"by": "apikey:ops-bot", "credit": ""}
refused = rpc.handle(call, None, Credentials(transport="http"))
assert refused["error"]["data"]["kind"] == "unauthenticated"
```

| Entrypoint | Credentials from | A refusal is |
|---|---|---|
| REST (`RestGateway`) | headers, cookies, query, method, target; the raw body when a provider verifies a signature | 401 with `WWW-Authenticate`, 403; the body's `error` carries `kind`, `reason` and the `requirement` not met |
| FastAPI (`FastApiGateway`) | the same, read off the request; the body only when a provider of the process verifies one | an `application/problem+json` 401 with `WWW-Authenticate`, or 403, with `kind` and `reason`; OpenAPI declares both on what requires something or when `every_call` checks |
| JSON-RPC (`RpcGateway`) | headers, cookies, query, method, target | a single call: HTTP 401 with `WWW-Authenticate`; `-32001` / `-32003` with `data.kind`, `data.reason`, `data.requirement` |
| gRPC (`GrpcGateway`) | metadata, the mTLS client certificate | `UNAUTHENTICATED` / `PERMISSION_DENIED`, with `sp-auth-refusal` and `www-authenticate` in the trailing metadata |
| MCP (`Entrypoint`, `McpGateway`) | the HTTP request of the tool call; with `auth=`, FastMCP's own bearer check first | a tool error; with `auth=`, FastMCP's 401 and — given `base_url` — the resource metadata (RFC 9728) |
| A queue (`QueueGateway`, `sqs_handler` on Lambda) | the message's headers; its raw body when a provider verifies a signature | dead-lettered, never retried, its `sincpro-dead-letter-kind` header `unauthenticated` or `permission_denied` |
| An ASGI app of the project's | `IdentityMiddleware(app, access=auth)` | 401 with `WWW-Authenticate`, 403 with the reason |
| A context hosted for another service | the caller's `ServiceTokenProvider` token | the refusal raised on the caller as itself |
| A cron, a script, a migration | nothing — nobody presents anything | runs as anonymous: an open use case runs, one that requires something is refused; the job opens `security.as_system(reason)` (or `as_identity`) to act as someone |

- **A call to another service** carries who it acts for: the calling process's provider issues a
  short JWT (`ServiceTokenProvider.credentials_for`) in its own header, and the host verifies it
  with the same keys. Only subject, kind, tenant and permissions travel — never the claims — and
  the request context it sends is never read for identity. The system does not cross services.
  Replicas' clocks may differ by `leeway_seconds` (5): a token signed a moment "ahead" by another
  replica, or a moment past its minute, is still accepted.
- **A bus nobody guards** is left alone by every entrypoint: nothing is authenticated, the
  identity is what the host opened.
- **Nothing here needs an extra**: `security`, `Credentials` and the middleware are the standard library;
  `grpc`, `fastmcp` and `starlette` are read only by the entrypoints that already need them.

## What no contract here covers

- **Kerberos, NTLM, SPNEGO** — handshakes over several round trips bound to a connection; done at
  the proxy, which hands on a header or a JWT.
- **A login screen, a user table, SAML, passkeys** — the identity provider's, or the project's
  own entity; what a service that manages its own accounts needs to hash, lock, link and issue is
  [accounts](accounts.md).
- **Masking fields of a response, record rules on every framework read** — #305.
- **Revocation in the middle of a long stream** — a WebSocket is authenticated when it connects.
