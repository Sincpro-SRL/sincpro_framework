# Features and ApplicationServices: what answers a Command

A bus routes each DTO, by its class, to the one handler registered for it. There are two kinds of
handler, two layers:

| | Feature | ApplicationService |
|---|---|---|
| What it is | One use case, atomic: it does one thing with its dependencies | An orchestration: it runs several Features to reach one result |
| What it may call | its dependencies | its dependencies and `self.feature_bus` — the Features of its bus |
| Registered with | `@bus.feature(Command)` | `@bus.app_service(Command)` |
| Its error chain | `add_feature_error_handler` | `add_app_service_error_handler` |

An ApplicationService does not call another ApplicationService: `feature_bus` knows only Features.
A flow that needs two orchestrations is one ApplicationService over their Features, or a
[`Pipeline`](pipelines.md).

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## Registering one

```python
from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework


class CommandReserve(DataTransferObject):
    sku: str
    quantity: int


class ResponseReserve(DataTransferObject):
    reserved: int


class CommandCheckout(DataTransferObject):
    sku: str
    quantity: int


class ResponseCheckout(DataTransferObject):
    reserved: int
    total: int


class Prices:
    def unit_price(self, sku: str) -> int:
        return 10


def new_shop() -> UseFramework:
    shop = UseFramework("shop-handlers", log_after_execution=False)
    shop.add_dependency("prices", Prices())

    @shop.feature(CommandReserve)
    class Reserve(Feature):
        def execute(self, dto: CommandReserve) -> ResponseReserve:
            return ResponseReserve(reserved=dto.quantity)

    @shop.app_service(CommandCheckout)
    class Checkout(ApplicationService):
        prices: Prices                               # a dependency, typed for the editor

        def execute(self, dto: CommandCheckout) -> ResponseCheckout:
            reserved = self.feature_bus(
                CommandReserve(sku=dto.sku, quantity=dto.quantity), ResponseReserve
            )
            return ResponseCheckout(
                reserved=reserved.reserved,
                total=reserved.reserved * self.prices.unit_price(dto.sku),
            )

    return shop


shop = new_shop()
assert shop(CommandCheckout(sku="A-1", quantity=3), ResponseCheckout) == ResponseCheckout(
    reserved=3, total=30
)
```

- **One DTO, one handler, one layer.** A second handler for a DTO, or the same DTO in both layers,
  is refused at the line that registers it (`DTOAlreadyRegistered`). So is a second use case with
  the same class name on one bus — every wire publishes a use case by its name.
- **A list registers one handler for several DTOs**: `@bus.feature([CommandA, CommandB])`. An
  event is a DTO, so a Feature registered for an event class is its subscriber.
- **`execute` is synchronous.** An `async def execute` is refused: the bus never awaits it. An async
  caller reaches the bus with `bus.get_async_bus()`.
- **Its identity is `<bus>.<Class>`** — `shop-handlers.CommandCheckout` — what remote buses, queues
  and outcomes route and say.

## What a handler has

| On `self` | What it is |
|---|---|
| each dependency, by its name | what `bus.add_dependency(name, adapter)` registered; typed once, on the context's `DependencyContextType`, which [the context's own bases](#every-bounded-context-wraps-the-bases) inherit |
| `self.context` | the context of this execution: tenant, user, correlation — per call, never shared |
| `self.feature_bus` (ApplicationService) | the Features of the same bus: `self.feature_bus(dto, Response)` |

## One instance per generation: state stays in locals

A bus creates one instance of each handler when it is built, and that instance serves every
execution, on every thread. A registration after the build makes a new generation of the bus with
new instances. So a handler keeps nothing of a request on `self`:

```python
def execute(self, dto):
    order = self.repository.get(dto.order_id)        # ✅ a local: this request's own
    self.order = order                               # ❌ shared with every concurrent request
```

What must outlive a request — a cache, a counter, a connection — is a dependency.

## Replacing one: `replaces=`

A module takes the place of a handler without editing it, naming the one it replaces:

```python
shop = new_shop()
generic = shop.handler_of(CommandReserve)
assert generic is not None


@shop.feature(CommandReserve, replaces=generic)
class ReserveAtMostTwo(Feature):
    def execute(self, dto: CommandReserve) -> ResponseReserve:
        return ResponseReserve(reserved=min(dto.quantity, 2))


assert shop(CommandCheckout(sku="A-1", quantity=3), ResponseCheckout).reserved == 2
assert shop.replaced_for(CommandReserve) == (f"{generic.__module__}.{generic.__qualname__}",)
```

- **It must replace the one answering now**: replacing a handler another replacement already took,
  or one never registered, is refused naming who answers. Two modules cannot overwrite each other
  without knowing.
- **It keeps the contract**: when both declare a response class, the replacement's is that class or
  a narrower one.
- **What it replaced is said**: in the build log, in `bus.replaced_for(Command)`, on the use case's
  span, and to the access guard — a replacement that declares no permission keeps the one it
  replaces.

## Registered after the bus was built

A Feature, an ApplicationService, a dependency or a replacement registered after the first
execution answers from the next execution, which builds a new generation of the bus first; a
request already running finishes with the handler it started on.

```python
class CommandRestock(DataTransferObject):
    sku: str


shop = new_shop()
shop(CommandReserve(sku="A-1", quantity=1))           # built here


@shop.feature(CommandRestock)
class Restock(Feature):
    def execute(self, dto: CommandRestock) -> ResponseReserve:
        return ResponseReserve(reserved=0)


assert shop(CommandRestock(sku="A-1"), ResponseReserve) == ResponseReserve(reserved=0)
```

How a whole module of these is installed: [extending a bus](extending.md).

## Dependencies: one name, one adapter

```python
class RemotePrices:
    def unit_price(self, sku: str) -> int:
        return 12


shop = new_shop()
shop.add_dependency("prices", RemotePrices(), replaces=shop.deps.prices)

assert shop(CommandCheckout(sku="A-1", quantity=1), ResponseCheckout).total == 12
```

- A name is taken once. A second adapter under it is refused (`DependencyAlreadyRegistered`),
  naming the class already there — unless it names what it replaces with `replaces=`.
- A `replaces=` that names another object, or nothing, works with a warning.
- `bus.deps.<name>` reads a dependency from outside a handler — a test, an entrypoint.
- In a test, `override_dependencies(bus, name=double)` puts a double in place for a block and the
  real one back after (`sincpro_framework.runtime.testing`).

## Every bounded context wraps the bases

A bounded context never hands its handlers the framework's `Feature` and `ApplicationService`
directly. Its `framework.py` wraps them once with the context's `DependencyContextType` — the class
that names and types every dependency it registers — and its handlers inherit those bases. `Hook`
is wrapped the same way when the context has repository hooks.

```python
from sincpro_framework import ApplicationService as _ApplicationService
from sincpro_framework import Feature as _Feature


class ShopDependencies:
    """What `dependencies.py` registers on the shop's bus, typed."""

    prices: Prices


class Feature(_Feature, ShopDependencies):
    """Base Feature of the shop."""


class ApplicationService(_ApplicationService, ShopDependencies):
    """Base ApplicationService of the shop."""


class CommandQuoteOne(DataTransferObject):
    sku: str


class ResponseQuoteOne(DataTransferObject):
    price: int


shop = new_shop()


@shop.feature(CommandQuoteOne)
class QuoteOne(Feature):
    def execute(self, dto: CommandQuoteOne) -> ResponseQuoteOne:
        return ResponseQuoteOne(price=self.prices.unit_price(dto.sku))    # typed: Prices


assert shop(CommandQuoteOne(sku="A-1"), ResponseQuoteOne) == ResponseQuoteOne(price=10)
```

- A service file imports `Feature` from its context (`from my_context import Feature`), never from
  `sincpro_framework`.
- Both bases exist from the start, even before the context has an ApplicationService.
- `as _Feature` is the one import alias the framework's own guides accept: the context's base takes
  the name, so the framework's takes another on that line.
- `self.context` is typed with a third parameter, `Feature[Command, Response, Context]`. The
  parameters are given all three or none: `Feature[Command, Response]` is a `TypeError`.

## Why it is this way

- **Two layers, not one, not many.** A Feature is the unit a test, a wire and a replacement name; an
  ApplicationService is where a flow is written down. A third layer that orchestrates
  orchestrations would hide the flow; a single layer would mix one use case with the order of many.
- **Routed by class, refused at the line.** A Command's class is its address, so a handler is found
  in one lookup and a clash is a mistake that can be named where it was made.
- **A base per context, because a context's dependencies are its own.** Typing them once, on the
  base every handler inherits, gives each `self.<dep>` its type and autocomplete, and makes a
  renamed dependency one change instead of one per handler.
- **Handlers are instances, built by the bus.** Dependencies are handed once, at build, not looked up
  per call; the price is the rule that state lives in locals or in dependencies.
- **Replacing names what it replaces.** It is what lets a core be extended by modules it does not
  know, and lets two modules be installed together without one silently overriding the other.
