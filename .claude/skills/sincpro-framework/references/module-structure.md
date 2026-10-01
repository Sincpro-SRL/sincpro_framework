# Module structure — folders, repo variants, where a new file goes

Where `sincpro_framework` code lives in a service. The reasoning that decides what goes in each
folder (dependency direction, `common/`, domain vs adapters) is in
[context-boundaries.md](context-boundaries.md).

The framework recognises five layer folders by name — `domain`, `adapters`, `services`,
`infrastructure`, `entrypoints` — and treats the path before the first of them as the bounded
context. `sincpro_framework.testing.layer_violations` judges imports by those names, so use them
exactly.

## Three variants — follow the one of this repo

| If you see | Variant | Canonical |
|---|---|---|
| `domains/<ctx>/` | **Several contexts** | `sincpro_mcp_odoo` (primary) |
| `apps/<ctx>/` | **Several contexts, SDK** | `sincpro_payments_sdk` |
| `services/<module>/` at package root, one bus | **One context** | `sincpro_siat_soap` |

Do not mix `domains/` and `apps/` in one repo. The use-case folder is always `services/`, never
`use_cases/`. A new multi-context project follows Variant A.

`entrypoints/` is optional transport. An SDK whose callers import the bus in-process has none. A
process that speaks HTTP/MCP/RPC/gRPC/queues adds it at the **package root**, where it builds
gateways over the buses — it never holds a use case.

## The shared kernel is `common/`

Sincpro's general architecture guidelines name the cross-context folder `shared/`. Python
`sincpro_framework` repos name it **`common/`**, and `layer_violations` enforces its rule under that
name (`common-imports-no-context`).

- `sincpro_mcp_odoo`: `domains/common/` is a foundational **bounded context with its own bus**.
  Other contexts depend on it by injecting that bus, never by importing its Features.
- `sincpro_payments_sdk`: `apps/common/` holds domain + adapters only, no bus — types and auth
  adapters shared by `qr` and `bank_account`.
- `sincpro_siat_soap` has a `shared/` helper folder. Do not copy that name for a new kernel.

Give `common/` a bus when it has use cases of its own; keep it passive when it only shares types and
adapters. Rules that hold in every variant:

- Contexts form a layered, acyclic graph; `common/` imports no sibling context.
- Code two contexts need and neither owns goes in `common/domain/` or `common/adapters/`. A module
  with one consumer belongs to that consumer, however general its name sounds.
- An adapter never imports another adapter. Composing two is a Feature's job.
- No `utils/`, `helpers/`, `models/`, `core/`.

## Variant A — several contexts (primary)

```
my_service/
  config.py  exceptions.py  logger.py  conf/
  domains/
    common/                    # foundational context (bus optional)
    sales/  billing/  ...
  entrypoints/                 # optional — gateways over the buses
```

Nothing domain-specific at the package root. A context that does not exist yet has **no folder**.

Every bounded context:

```
<context>/
  __init__.py                  # bus instance, then `from . import services`
  infrastructure/              # ALWAYS
    dependencies.py            # <Ctx>DependencyContextType + register_dependencies
    framework.py               # typed Feature / ApplicationService + config_<ctx>_framework
  services/                    # ALWAYS — one file per use case
    __init__.py                # imports every use-case module
  domain/                      # only if it has its own vocabulary
  adapters/                    # only if it talks to its own external system
```

`infrastructure/` and `services/` are mandatory; `domain/` and `adapters/` exist only when there is
something to put in them. In `sincpro_mcp_odoo`, `customer_sale` has neither: quotation vocabulary
lives in `domains/common/domain/quotation.py`.

One bus per context; never share one. Name the bus after the context (`sales`, `cybersource`,
`siat_soap_sdk`) or `<context>_mcp` where that is the local convention. Do not rename a live bus.

## Variant B — several contexts, SDK layout

```
my_sdk/
  exceptions.py
  infrastructure/              # cross-context HTTP client, credentials
  apps/
    common/                    # domain + adapters, no bus
    cybersource/  qr/  bank_account/
```

Each full context:

```
apps/<context>/
  __init__.py                  # bus = config_framework("…"); then import services
  infrastructure/framework.py
  infrastructure/dependencies.py
  services/<group>/            # tokenization/, bnb/, ... one file per use case inside
  domain/
  adapters/
```

The consumer talks to the bus, never to handler classes:

```python
from my_sdk.apps.cybersource import cybersource

result = cybersource(CommandCreatePaymentMethod(...), ResponseCreatePaymentMethod)
```

A service-group `__init__.py` exports DTOs only.

## Variant C — one context

```
my_sdk/
  infrastructure/  adapters/  domain/  services/<module>/  conf/
```

One bus in the package `__init__.py`. `services/` is grouped by capability (`billing/`,
`digital_files/`, …), one file per use case inside. Use this variant only when there is truly one
bounded context; the moment there are two lifecycles, split as in Variant A or B.

## Bootstrap (identical in every variant)

```python
# infrastructure/framework.py
class Feature(_Feature, CtxDependencyContextType):
    pass


class ApplicationService(_ApplicationService, CtxDependencyContextType):
    pass


def config_ctx_framework(name: str) -> UseFramework[CtxDependencyContextType]:
    instance = UseFramework[CtxDependencyContextType](name)
    register_dependencies(instance)
    return instance


# __init__.py
ctx = config_ctx_framework("sincpro-ctx")
from . import services  # noqa: E402 — AFTER the instance exists
```

`@ctx.feature(Command)` runs at import time against that instance. Full skeleton and what follows
the services import: [bootstrap.md](bootstrap.md).

## Where a new file goes

```
A DTO + Feature/ApplicationService for one use case?
  → <context>/services/<verb_noun>.py

Pure vocabulary (DTO shared by use cases, aggregate, value object, Protocol, enum, rule)?
  → <context>/domain/   or  common/domain/ when 2+ contexts need it and neither owns it

A wrapper for an external system, or a mechanism whose replacement changes results?
  → <context>/adapters/ or  common/adapters/ when 2+ contexts need it

Wiring (bus builder, dependencies, tables, settings, logger)?
  → <context>/infrastructure/

A route, tool, RPC method or consumer for use cases that already exist?
  → an exposure decorator on the handler in services/ + the gateway in entrypoints/
  → omit entrypoints/ entirely if callers import the bus in-process
```

## What not to add

- Empty `domain/` / `adapters/` so every context looks the same
- A folder for a context that does not exist yet
- `models/`, `utils/`, `helpers/`, `use_cases/`, `core/`
- `shared/` as the kernel name of a new Python project
- A root `services/` in a multi-context repo
- A second dispatcher next to `@bus.feature`
- A use case, a `UseFramework` or an exception handler inside `entrypoints/`
