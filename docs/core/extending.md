# Extending a bus: modules that plug in

A **module** is code that adds to a bus it does not own — a rate limit, an audit trail, the
Bolivian tax rules in place of the generic ones, a Redis cache in place of the in-memory one. It
never edits the bus's code: it registers on it, through the same points the bus's own code uses.

```
                          a module registers here
                                   │
   context ─▶ interceptors ─▶ ApplicationService / Feature ─▶ response ─▶ incomes / outcomes
      ▲             ▲                     ▲         ▲                            ▲
 context_provider   interceptor(...)   replaces=   dependencies             on_start / on_completion
                                                   add_dependency(...)      on_failure
```

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## What a module is

A function that takes the bus, or an object with one. What it is parametrised with goes in its
constructor; what it registers goes in `attach`.

```python
from typing import Any

from sincpro_framework import CallNext, DataTransferObject, Feature, UseFramework


class CommandQuote(DataTransferObject):
    amount: int


class ResponseQuote(DataTransferObject):
    total: int


class RateLimited(Exception):
    pass


def new_shop() -> UseFramework:
    shop = UseFramework("shop", log_after_execution=False)

    @shop.feature(CommandQuote)
    class Quote(Feature):
        def execute(self, dto: CommandQuote) -> ResponseQuote:
            return ResponseQuote(total=dto.amount)

    return shop


class RateLimit:
    """At most `limit` executions of the Commands it is given, across every generation of the
    bus — the count lives here, in the module, never in a handler."""

    def __init__(self, limit: int, *commands: type) -> None:
        self.limit = limit
        self.commands = commands
        self.calls = 0

    def attach(self, bus: UseFramework) -> None:
        @bus.interceptor(*self.commands, sequence=5)
        def rate_limit(dto: Any, call_next: CallNext[Any]) -> Any:
            self.calls += 1
            if self.calls > self.limit:
                raise RateLimited(f"more than {self.limit} calls")
            return call_next(dto)
```

It is installed with `extend`:

```python
shop = new_shop()
shop.extend(RateLimit(2, CommandQuote).attach)

assert shop(CommandQuote(amount=10), ResponseQuote).total == 10
assert shop(CommandQuote(amount=20), ResponseQuote).total == 20
try:
    shop(CommandQuote(amount=30))
    raise AssertionError("the third call should have been limited")
except RateLimited:
    pass
```

`extend` records the module, not what it registered one by one: a bus made with `fresh()` runs
the module again against itself.

## What a module can add, and with what

| It adds… | With | See |
|---|---|---|
| A use case | `bus.feature(Command)` / `bus.app_service(Command)` | [Features and ApplicationServices](features.md) |
| A use case **in the place of** another | `bus.feature(Command, replaces=Handler)` | [Features](features.md#replacing-one-replaces) |
| A rule around a request — veto, adjust, audit, cache | `bus.interceptor(...)`, with `sequence`/`before`/`after`/`replaces` | [Interceptors](interceptors.md) |
| An adapter, or one in the place of another | `bus.add_dependency(name, adapter, replaces=old)` | [below](#replacing-an-adapter), [Features](features.md#dependencies-one-name-one-adapter) |
| An answer to an error | `bus.add_*_error_handler(...)` | [Error handlers](error-handlers.md) |
| What the execution knows (tenant, credentials) | `bus.context_provider(needs=..., gives=...)` | [Context](context-manager.md) |
| Something that hears a use case start or end | `bus.on_start` / `on_completion` / `on_failure` | [Incomes](incomes.md), [Outcomes](outcomes.md) |
| A rule on an aggregate, whoever writes it | a repository `Hook` | [Hooks](../persistence/hooks.md) |
| Fields on an entity it does not own | an extension of the entity | [Extensions](../persistence/extensions.md) |
| Who may run what | `AccessControl(...).on(bus)` — a module the framework ships | [Auth](../auth/README.md) |

## Installing it before or after the bus was built

A bus is built the first time it runs. What a module registers **after** that answers from the
next execution: the bus builds a new generation of itself then, with new handler instances,
and logs one line saying what came in.

```python
shop = new_shop()
assert shop(CommandQuote(amount=10), ResponseQuote).total == 10     # built here


class Bolivia:
    """The tax rules of a country, in the place of the generic quote."""

    def __init__(self, rate_percent: int) -> None:
        self.rate_percent = rate_percent

    def attach(self, bus: UseFramework) -> None:
        generic = bus.handler_of(CommandQuote)
        rate = self.rate_percent

        @bus.feature(CommandQuote, replaces=generic)
        class TaxedQuote(Feature):
            def execute(self, dto: CommandQuote) -> ResponseQuote:
                return ResponseQuote(total=dto.amount + dto.amount * rate // 100)


shop.extend(Bolivia(13).attach)

assert shop(CommandQuote(amount=100), ResponseQuote).total == 113
```

- **Whole or nothing.** An extension runs under the bus's build lock: an execution never meets
  half of it.
- **What is running finishes where it started.** A request already running stays on the
  generation it began on; the next one runs on the new generation.
- **A wiring error is said where it can be fixed.** A generation that cannot be built — an
  interceptor naming a Command nobody answers — raises at the next execution, and the generation
  that was answering stays. Call `bus.build_root_bus()` to bring the bus up to date at a line
  of your choosing: after installing modules, or where an entrypoint mounts the bus.

## Replacing an adapter

A name is taken once. Another object takes its place only naming what it replaces — so a module
cannot overwrite an adapter by chance:

```python
class MemoryRates:
    def rate(self) -> int:
        return 10


class RemoteRates:
    def rate(self) -> int:
        return 15


class CommandRate(DataTransferObject):
    pass


class ResponseRate(DataTransferObject):
    rate: int


rates = UseFramework("rates", log_after_execution=False)
memory = MemoryRates()
rates.add_dependency("rates", memory)


@rates.feature(CommandRate)
class Rate(Feature):
    rates: MemoryRates

    def execute(self, dto: CommandRate) -> ResponseRate:
        return ResponseRate(rate=self.rates.rate())


assert rates(CommandRate(), ResponseRate).rate == 10

rates.add_dependency("rates", RemoteRates(), replaces=memory)

assert rates(CommandRate(), ResponseRate).rate == 15
```

Without `replaces`, a second object under a taken name raises `DependencyAlreadyRegistered`,
naming the class of the one already there. A `replaces` that names something else — or nothing
— is a warning, and the new object takes the name.

## Where a module holds its state

| State | Lives in | Why |
|---|---|---|
| A counter, a cache, a connection the module owns | the module object, or a dependency | Every generation of the bus shares them |
| Anything about one request | locals of `execute`, or the context | A handler serves every request of its generation |
| Anything on `self` of a Feature | nowhere | Each generation creates its handlers again |

## Order between modules

Interceptors of several modules run by `before`/`after`, then `sequence` (lower first, the
outermost), then the order they were registered — one algorithm for every extension point
([`common/ordering`](../../sincpro_framework/common/ordering/placement.py)). A module that must
run first says so with a low `sequence`; `AccessControl` guards with the lowest of all, so no
module answers before access is decided.

## Proving a module in its own CI

A module is tested against a bus of its own, the way it will be installed:

```python
def test_the_rate_limit_refuses_the_call_over_its_limit() -> None:
    bus = new_shop()
    bus.extend(RateLimit(1, CommandQuote).attach)

    bus(CommandQuote(amount=1))
    try:
        bus(CommandQuote(amount=2))
        raise AssertionError("the limit should have refused it")
    except RateLimited:
        pass


test_the_rate_limit_refuses_the_call_over_its_limit()
```

For adapters, `sincpro_framework.runtime.testing.override_dependencies(bus, name=double)` puts a
double in the place of a registered one for the length of a block, and the real one back after.
