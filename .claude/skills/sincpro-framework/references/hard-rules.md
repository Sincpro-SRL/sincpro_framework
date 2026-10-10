# Hard rules — and the incident each prevents

`UseFramework` is not a style preference. It is what produces, for free: a Sentry/GlitchTip event on
every unexpected exception, a trace span per Feature, and typed dependency injection of the context.
A parallel "execute this for me" path throws those away. The symptom is a production incident with
nothing in Sentry and no span to open. That already happened once.

## 1. Every use case runs on the bus

A use case is a `Feature` or an `ApplicationService` registered with `@bus.feature(Command)` /
`@bus.app_service(Command)`. Not a homemade dispatcher (`run()`, `_TOOLS`, a dict of classes), not a
function that takes `feature_bus` as an argument.

```python
# ✅
@bus.feature(CommandCreateContact)
class CreateContact(Feature):
    def execute(self, dto: CommandCreateContact) -> ResponseCreateContact: ...
```

If you find yourself writing a registry next to `@bus.feature`, stop: the bus already is one.

## 2. Reuse is a Command on the injected bus

A use case reuses another by importing its **DTOs** (its Command and Response) and executing the
Command on the bus it already has.

```python
# ✅ same context
created = self.feature_bus.execute(CommandCreateContact(...), ResponseCreateContact)

# ✅ another context's bus, injected in dependencies.py
connection = self.common_mcp(CommandResolveTenantConnection(...), TenantConnection)
```

| Forbidden | What to do instead |
|---|---|
| `def contact_command(...)` in `create_contact.py`, imported elsewhere | Import `CommandCreateContact`; build it at the call site |
| `def existing_contact(feature_bus, ...)` | Execute the Command; the Feature's raise **is** the answer |
| Importing the Feature/ApplicationService **class** from another service | Import its DTOs only — calling the class bypasses the bus: no span, no error report, no interceptors, no auth |
| A `lambda` dependency | A named class (or named factory) in `dependencies.py` |
| A function in `domain/` whose first argument is an adapter | An adapter on the bus; domain is vocabulary |
| Any module-level `def` in `domain/`, even a pure one | A method on the type it constrains (below) |

Recognise the smell in review: `from ...services.<other> import <a handler class or a function>`, a function
whose first argument is `bus`/`feature_bus`/a client, or a comment saying "thin wrapper so other
services don't repeat this". That comment is the confession.

### Rules live on the type they constrain

A pure rule about a model is a **method of that model**. The call site then names its owner, and
there is one place to look for everything a concept enforces.

| The rule | Its form | Example |
|---|---|---|
| Validates or normalises a raw value, no instance yet | `@staticmethod` | `Tenant.check_id(tenant_id)`, `User.normalize_email(email)` |
| Builds the model from another representation | `@classmethod` | `HostAddress.parse("ssh://…")`, `WorkspacePaths.of(root)`, `EventEntry.of(event)` |
| Reads or changes the record's own fields | instance method / property | `invoice.post()`, `workspace.paths`, `tenant.ensure_active()` |
| Several fields rendered or computed together | a value object with a method | `Brief(instruction=…, branch=…).render()` instead of a 7-parameter function |
| A question over many records | a method on the collection | `invoices.outstanding()` |

```python
# ❌ loose functions: who owns the rule?
def check_tenant_id(tenant_id: str) -> str: ...
def parse_host_address(address: str) -> HostAddress: ...

# ✅ the rule on its type
@dataclass
class Tenant(Entity):
    tenant_id: str = ""

    @staticmethod
    def check_id(tenant_id: str) -> str: ...


class HostAddress(DataTransferObject):
    @classmethod
    def parse(cls, address: str) -> "HostAddress": ...
```

One aggregate per file in `domain/`, the file named after it. Odoo models are the exception to the
`@staticmethod` form — the Odoo conventions forbid it there and win in an Odoo repo.

## 3. Service files export only DTOs

```python
# ✅ another service may import these
from ...services.create_contact import CommandCreateContact, ResponseCreateContact

# ❌ never
from ...services.create_contact import CreateContact, contact_command, existing_contact
```

`services/__init__.py` imports every service module; importing the package is what registers the
Features (a side effect). It may re-export DTOs, never handler classes. Never `OldFeature =
NewFeature`. DTO names are free — `Command*`/`Response*` is the Sincpro convention, not a rule the
framework checks; what it checks is that nobody imports a handler class or a function from a
service module (`layer_violations`, rule `services-reused-through-bus`).

## 4. `domain/` is vocabulary, not I/O

`domain/` imports only `domain/` — its own, or a lower context's — and `sincpro_framework.ddd`. A
function that takes a client/store/gateway is I/O wearing a domain name. The discriminator: *would
replacing this with something equivalent change business behaviour?* Yes → adapter; no →
infrastructure. DTOs, aggregates, value objects, ports (`Protocol`) the use cases call, pure rules
and policy constants are domain. A `Protocol` only an adapter calls (the contract a facade holds
its implementations by) is the adapter's, in its folder. Detail: [context-boundaries.md](context-boundaries.md).

## 5. One bus per context, built before services import

`@bus.feature(Command)` runs at import time against the instance. Import `services` **after**
`config_*_framework(...)`. The bus is built by its first execution or by the first gateway it is
added to; after that every registration raises `BusAlreadyBuilt` — register everything first,
build gateways last. See [bootstrap.md](bootstrap.md).

## 6. `self` holds dependencies, not request data

A handler instance is built once and reused by every execution, on every thread. Anything written to
`self` inside `execute` is shared between concurrent calls. `self.context` is the exception — it is
isolated per call.

```python
# ❌ request data on self — concurrent calls read each other
def execute(self, dto):
    self.order = self.repository.get(dto.order_id)
    return self._total()

# ✅ request data in locals
def execute(self, dto):
    order = self.repository.get(dto.order_id)
    return self._total(order)
```

## 7. Unique Command names across buses that can be called together

Two contexts registering `CommandAuthenticate` on buses reachable together is a collision the
framework may not catch. Name by design (`CommandCreateQREconomico`, not `CommandCreateQR`).

## 8. Expected errors are declared, unexpected ones must reach the reporter

An expected refusal is a `DomainError` subclass that declares what it is, with context in the
message. Every wire maps the kind to its own code (REST status, JSON-RPC code, gRPC status), so no
project writes its own exception handler:

```python
from sincpro_framework.ddd import DomainError
from sincpro_framework.common.failures import FailureKind


class InvoiceNotFound(DomainError):
    failure_kind = FailureKind.NOT_FOUND
```

Expected traffic (validation, "already exists", auth) also goes in
`bus.ignore_sentry_exceptions(...)`. Unexpected exceptions must reach Sentry — that is why the
Feature runs on the bus, not in a helper. Do not swallow a Feature's exception in a wrapper outside
the bus.

**The error-handler trap:** what a handler returns becomes the bus's answer. A handler written only
to *watch* (`lambda error: log.error(error)`) returns `None` and silently swallows the failure.
Re-raise to delegate to the next handler.

## 9. `entrypoints/` exposes; it never registers

`entrypoints/` builds gateways and, at most, hand-written routes that translate a body into a
Command and call the bus. It never creates a `UseFramework`, never declares `@bus.feature` /
`@bus.app_service`, and never writes `@app.exception_handler`. A use case that exists only to get a
route is a second surface: MCP, RPC and the tests do not see it, and it is registered only if that
router module happened to be imported. To publish an existing use case, decorate its handler in
`services/` (`@rest.post(...)`, `@mcp()`, …) and add the bus to a gateway —
`sincpro-framework-entrypoints`.

## Review checklist

- Every use case is `@bus.feature` / `@bus.app_service`; nothing dispatches by hand.
- No import of a handler class or a function from another service module.
- No function in `services/` or `domain/` whose first argument is a bus or a client.
- `dependencies.py` registers named instances; `DependencyContextType` names match.
- Nothing request-scoped is written to `self`.
- Errors are `DomainError` subclasses with `failure_kind`; no exception handler in the project.
- `entrypoints/` holds gateways only.
- `layer_violations("<package>") == []` and `import_cycles("<package>") == []` in the suite
  ([testing.md](testing.md)).

## Related

- [context-boundaries.md](context-boundaries.md) — dependency direction, `common/`
- [module-structure.md](module-structure.md) — where each file goes
- `sincpro-framework-core` — error handlers, interceptors, the context manager
