# Bootstrap — skeleton, import order, and typed dependencies

## The three files of a context

```
<context>/
  __init__.py                  # bus instance FIRST, then import services
  infrastructure/
    dependencies.py            # <Ctx>DependencyContextType + register_dependencies
    framework.py               # typed Feature / ApplicationService + config_*_framework
  services/                    # one file per use case
    __init__.py                # re-imports every service module
  domain/                      # only if it has its own vocabulary
  adapters/                    # only if it talks to its own external system
```

`infrastructure/` and `services/` are mandatory. Do not create empty `domain/`/`adapters/` for
symmetry. Three repo variants (`domains/<ctx>/`, `apps/<ctx>/`, one context at the package root)
are in [module-structure.md](module-structure.md).

## `dependencies.py` — declare the adapters once

```python
from sincpro_framework import UseFramework
from my_sdk.adapters import TokenAdapter, PaymentAdapter


class DependencyContextType:
    """Typing helper — gives Features/AppServices IDE autocomplete for injected deps.
    NOT instantiated; used only as a mixin."""

    token_adapter: TokenAdapter
    payment_adapter: PaymentAdapter


def register_dependencies(
    framework: UseFramework[DependencyContextType],
) -> UseFramework[DependencyContextType]:
    framework.add_dependency("token_adapter", TokenAdapter())      # named class, never a lambda
    framework.add_dependency("payment_adapter", PaymentAdapter())
    return framework
```

The attribute names on `DependencyContextType` **must** match the `add_dependency` names. A test
that executes a throwaway Feature reading every attribute is the check that wiring is complete —
see `unregistered_dependencies` in `sincpro_framework.runtime.testing`.

## `framework.py` — the typed bases

```python
from sincpro_framework import ApplicationService as _ApplicationService
from sincpro_framework import DataTransferObject
from sincpro_framework import Feature as _Feature
from sincpro_framework import UseFramework
from sincpro_framework.ddd import Hook as _Hook

from .dependencies import DependencyContextType, register_dependencies


class Feature(_Feature, DependencyContextType):
    """Base Feature for this bounded context — typed deps included."""


class ApplicationService(_ApplicationService, DependencyContextType):
    """Base ApplicationService for this bounded context."""


class Hook(_Hook, DependencyContextType):
    """Base repository hook for this bounded context — only when it has hooks."""


def config_framework(name: str) -> UseFramework[DependencyContextType]:
    instance = UseFramework[DependencyContextType](name)
    register_dependencies(instance)
    return instance
```

Define both bases even if the context has no ApplicationService yet — `sincpro_payments_sdk` does
exactly that. Do not add an ApplicationService because the base exists.

For a typed `self.context`, parameterize: `Feature[Command, Response, MyContext]`,
`Hook[MyContext]`.

## `__init__.py` — the order is load-bearing

```python
from .infrastructure.framework import ApplicationService, DataTransferObject, Feature, config_framework

my_framework = config_framework("my-domain")

# Import services AFTER creating the instance — decorators register against it.
from . import services  # noqa: E402, F401
```

`services/__init__.py` re-imports every service module, so `from . import services` registers all of
them. `isort` is configured not to reorder `__init__.py` for this reason.

After the services, in this order:

1. What needs the handlers or configures the bus: `my_framework.ignore_sentry_exceptions(...)`,
   interceptors, error handlers, `auth.on(my_framework)` (`sincpro-framework-core`,
   `sincpro-framework-auth`).
2. Gateways, in `entrypoints/`, last. Adding a bus to a gateway builds it, and the first execution
   builds it too. A built bus refuses every later registration — Feature, dependency, interceptor —
   with `BusAlreadyBuilt`.

A process with several contexts registers each bus once the instance exists
(`from sincpro_framework.registry import registry` then `registry.add(billing)`). The entrypoint
imports the context packages and reads `registry.all()`. Creating a `UseFramework` does not add
it, and `fresh()` is not the registered bus until the project adds that generation. The registry
is this process, in add order; it does not build or sort the contexts. Another replica builds
its own (`docs/registry/README.md`).

## What the bus offers outside a Feature

- `my_framework.deps.token_adapter` — the same instance a Feature sees as `self.token_adapter`.
- `my_framework(Command(...), Response)` — execute.
- `my_framework.context({...})` — the context manager (`sincpro-framework-core`).

Inside a Feature/ApplicationService use `self.<name>`; outside, use `.deps`.

## Handler lifetime

One `Feature`/`ApplicationService` instance per `UseFramework`, reused by every execution on every
thread. `self.context` is per call; everything else on `self` is shared. Keep request data in locals.

## Missing dependency

`unregistered_dependencies(my_framework)` (from `sincpro_framework.runtime.testing`) returns
`{handler: [names]}` for every declared name nobody registered. Assert it is `{}` in a setup test.
See [testing.md](testing.md).
