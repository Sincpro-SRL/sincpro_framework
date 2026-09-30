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

A use case reuses another by importing its **Command and Response** and executing it on the bus it
already has.

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
| Importing the Feature/ApplicationService **class** from another service | Import Command + Response only — the class double-registers and bypasses the bus |
| A `lambda` dependency | A named class (or named factory) in `dependencies.py` |
| A function in `domain/` whose first argument is an adapter | An adapter on the bus; domain is vocabulary |

Recognise the smell in review: `from ...services.<other> import <not Command/Response>`, a function
whose first argument is `bus`/`feature_bus`/a client, or a comment saying "thin wrapper so other
services don't repeat this". That comment is the confession.

## 3. Service files export only Commands and Responses

```python
# ✅ another service may import these
from ...services.create_contact import CommandCreateContact, ResponseCreateContact

# ❌ never
from ...services.create_contact import CreateContact, contact_command, existing_contact
```

`services/__init__.py` re-exports Commands/Responses; importing the package is what registers the
Features (a side effect). Never `OldFeature = NewFeature`.

## 4. `domain/` is vocabulary, not I/O

`domain/` imports nothing from adapters, infrastructure or another context's internals. A function
that takes a client/store/gateway is I/O wearing a domain name. The discriminator is in
`sincpro_architecture_guidelines`: *would replacing this with something equivalent change business
behaviour?* Yes → adapter; no → infrastructure. DTOs, aggregates, value objects, pure rules and
policy constants are domain.

## 5. One bus per context, built before services import

`@bus.feature(Command)` runs at import time against the instance. Import `services` **after**
`config_*_framework(...)`. The bus freezes after the first real execution — register everything
first; adding a Feature needs a restart.

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

One exception type per layer, context in the message. Expected traffic (validation, "already
exists", auth) goes in `bus.ignore_sentry_exceptions(...)`. Unexpected exceptions must reach
Sentry — that is why the Feature runs on the bus, not in a helper. Do not swallow a Feature's
exception in a wrapper outside the bus.

**The error-handler trap:** what a handler returns becomes the bus's answer. A handler written only
to *watch* (`lambda error: log.error(error)`) returns `None` and silently swallows the failure.
Re-raise to delegate to the next handler.

## Related knowledge

- `sincpro_framework_use_cases` — the full reuse contract
- `framework_gotchas` (`knowledge://framework_gotchas`) — symptom → cause → fix
- `framework_context_boundaries` — dependency direction, `common/`
