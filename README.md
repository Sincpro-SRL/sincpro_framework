# 🚀 Sincpro Framework: Application Layer Framework within Hexagonal Architecture

## ⚡ Quick Start

Here's a quick example to get you started with the Sincpro Framework:

### 🏁 Quick Example

```python
from sincpro_framework import DataTransferObject, Feature, UseFramework

# 1. The bus of one bounded context
framework = UseFramework("greetings")


# 2. A dependency: every Feature reads it as self.greeter
class Greeter:
    def greet(self, name: str) -> str:
        return f"Hello, {name}!"


framework.add_dependency("greeter", Greeter())


# 3. A use case: its command, its response, and the Feature that answers it
class CommandGreet(DataTransferObject):
    name: str


class ResponseGreet(DataTransferObject):
    message: str


@framework.feature(CommandGreet)
class Greet(Feature):
    def execute(self, dto: CommandGreet) -> ResponseGreet:
        return ResponseGreet(message=self.greeter.greet(dto.name))


# 4. Execute it through the bus
result = framework(CommandGreet(name="Alice"), ResponseGreet)
print(result.message)  # Hello, Alice!
```

That is the whole framework: a use case, its DTO, and a bus that executes it. Observability
comes with it — a span per DTO, errors reported, logs correlated — without configuring
anything.

When the same catalog has to be reachable from outside the process, an
[entrypoint](docs/entrypoints/README.md) publishes it over a protocol without touching
the use case. That is transport, and it comes later.

Now you are ready to explore more complex use cases! 🚀

## 📑 Table of Contents

1. [Overview of Hexagonal Architecture](#-overview-of-hexagonal-architecture)
    - [Key Layers of Hexagonal Architecture](#-key-layers-of-hexagonal-architecture)
    - [Why Use a Unified Bus Pattern?](#-why-use-a-unified-bus-pattern)
2. [Key Features of the Sincpro Framework](#-key-features-of-the-sincpro-framework)
    - [DTO Validation with Pydantic](#-dto-validation-with-pydantic)
    - [Dependency Injection](#-dependency-injection)
    - [Inversion of Control (IoC)](#-inversion-of-control-ioc)
    - [Context Manager for Metadata Propagation](#-context-manager-for-metadata-propagation)
    - [Interceptors](#interceptors)
    - [Error Handling at Different Levels](#-error-handling-at-different-levels)
    - [Bus Pattern for Component Communication](#-bus-pattern-for-component-communication)
    - [Decoupled Logic Execution](#-decoupled-logic-execution)
    - [Application Service Orchestration](#-application-service-orchestration)
    - [IDE Support with Typing](#-ide-support-with-typing)
    - [Crons](#crons)
    - [Migrations](#migrations)
    - [Data analysis](#data-analysis)
    - [Runtime use cases](#runtime-use-cases)
    - [Workflows (experimental)](#workflows-experimental)
    - [Persistence and the ORM](#persistence-and-the-orm)
3. [Features vs. Application Service](#-features-vs-application-service)
4. [Example Usage for a Payment Gateway](#-example-usage-for-a-payment-gateway)
    - [Configuring the Framework](#-configuring-the-framework)
    - [Best Practices for Imports](#-best-practices-for-imports)
    - [Sample Configuration in `__init__.py`](#-sample-configuration-in-__init__py)
5. [Recommended Infrastructure Structure](#-recommended-infrastructure-structure)
    - [dependencies.py — Adapter Registration](#dependenciespy--adapter-registration)
    - [framework.py — Wiring with DependencyContextType](#frameworkpy--wiring-with-dependencycontexttype)
    - [\_\_init\_\_.py — Bootstrap the Bounded Context](#__init__py--bootstrap-the-bounded-context)
    - [Testing Dependency Consistency](#testing-dependency-consistency)
    - [Handler Lifetime — one instance serves every execution](#handler-lifetime--one-instance-serves-every-execution)
    - [Swapping a Dependency in a Test](#swapping-a-dependency-in-a-test)
    - [Asserting What a Use Case Published](#asserting-what-a-use-case-published)
    - [Checking Imports and Layers](#checking-imports-and-layers)
6. [Creating a Feature](#-creating-a-feature)
7. [Creating an Application Service](#-creating-an-application-service)
8. [Executing a Use Case](#-executing-a-use-case)
9. [Summary](#-summary)
10. [Error Handling](#-error-handling)
11. [Persistence (ORM)](#persistence-orm) — aggregates, repository, queries, hooks, events
    - [What it covers](#what-it-covers)
    - [How to, by topic](#how-to-by-topic)
12. [Crons](#crons-1) — a registry per bounded context, a background process, one run per tick
13. [Migrations](#migrations-1) — every context and store on one timeline, one command
14. [Data analysis](#data-analysis-1) — a query read once, merged page by page, handed to pandas, polars or DuckDB
15. [Runtime use cases](#runtime-use-cases-1) — use cases stored as source, loaded onto the bus without a deploy
16. [Workflows (experimental)](#workflows-experimental-1) — Commands composed as data, for a node editor's preview
17. [Documentation](#-documentation)
18. [Entrypoints: exposing the bus](docs/entrypoints/README.md) — transport, not domain
    - [MCP tools](docs/entrypoints/mcp.md)
    - [JSON-RPC](docs/entrypoints/rpc.md)
    - [gRPC](docs/entrypoints/grpc.md)
    - [Bounded contexts across services](docs/entrypoints/bounded-contexts-across-services.md) — the context map: a context hosted by another service
18. [Observability](#observability) — tracing (OTLP) + errors (Sentry/GlitchTip)
19. [Configuration or settings](#configuration-or-settings)
20. [Variables](#-variables)
21. [Tests & coverage](#-tests--coverage)
22. [Python 3.14 & Free-Threading Notes](#-python-314--free-threading-notes)

## 🔍 Overview of Hexagonal Architecture

Hexagonal Architecture, also known as **Ports and Adapters**, is an architectural approach that aims to decouple core
business logic from external dependencies. It organizes the system into distinct layers: domain, application, and
infrastructure, enhancing maintainability, scalability, and adaptability.

### 🏗️ Key Layers of Hexagonal Architecture

- **Core Domain**: This layer encapsulates essential entities, value objects, and domain services representing the core
  business rules and behaviors. It is kept strictly independent from infrastructure concerns, preserving business logic
  integrity.
- **Application Layer**: Orchestrates user requests, processes domain responses, and mediates interactions between
  domain and external systems to ensure effective workflow execution.
- **Infrastructure Layer**: Contains adapters for interacting with databases, APIs, messaging systems, and other
  services. It handles data transformation to ensure compatibility with the domain and application layers.

### 🤔 Why Use a Unified Bus Pattern?

The Sincpro Framework adopts a **unified bus pattern** as a single point of entry for managing use cases, dependencies,
and services within a bounded context. This simplifies the architecture by encapsulating all requirements of a given
context, ensuring a clear and consistent structure.

Using a unified bus allows developers to access all dependencies through a single environment, eliminating the need for
repeated imports or initialization. This approach ensures each bounded context is self-sufficient, independently
scalable, and minimizes coupling while enhancing modularity.

## 🔑 Key Features of the Sincpro Framework

The Sincpro Framework follows hexagonal architecture principles, promoting modularity, scalability, and development
efficiency. Here are its core features:

### ✅ DTO Validation with Pydantic

- Utilizes **Pydantic** to validate Data Transfer Objects (DTOs).
- Ensures only well-structured data is allowed into core business logic, reducing errors and maintaining data integrity.

### 🧩 Dependency Injection

- Facilitates integration of user-defined dependencies, promoting modular design.
- Enhances unit testing by allowing easy mocking or replacement of dependencies.

### 🔄 Inversion of Control (IoC)

- Automates the instantiation and configuration of components, reducing boilerplate code.
- Encourages loose coupling, making systems more adaptable and maintainable.

### Interceptors

See [docs/core/interceptors.md](docs/core/interceptors.md) for the contract and runnable recipes.

- `@bus.interceptor(CommandX)` runs a function **around** one use case — `@bus.interceptor()`
  around all of them — however it is executed: at the entry, from an ApplicationService, a workflow
  step or a scheduled tick.
- It can veto, adjust the Command (`model_copy`), adjust the response, or answer by itself; it
  never changes their class.
- Recipes: credit check, audit trail, idempotency, cache, retry on a stale write, feature flags,
  timing.
- `@bus.feature(CommandX, replaces=CoreFeature)` has another handler answer **instead** of the
  core's — explicitly: the build log, the span and `introspection.describe()` say who replaced it.

### 📡 Context Manager for Metadata Propagation

- Provides automatic metadata propagation across Features and ApplicationServices without manual parameter passing.
- Uses Python's `contextvars` for thread-safe context storage and isolation.
- Supports nested contexts with override capabilities for complex workflows.
- Enriches exceptions with context information for better debugging and observability.

```python
# Simple context usage
with app.context({"correlation_id": "123", "user.id": "admin"}) as app_with_context:
    result = app_with_context(some_dto)  # Context automatically available in handlers

# Nested contexts with overrides
with app.context({"env": "prod", "user": "admin"}) as outer_app:
    with outer_app.context({"env": "staging"}) as inner_app:  # Override env, inherit user
        inner_app(dto)  # env="staging", user="admin"

# Access context in Features and ApplicationServices
class PaymentFeature(Feature):
    def execute(self, dto: PaymentDTO) -> PaymentResponse:
        correlation_id = self.context.get("correlation_id")
        user_id = self.context.get("user.id")
        # Use context in business logic...
```

#### Propagating context into a `ThreadPoolExecutor`

- The context overlay lives in a `ContextVar`, which is isolated **per OS thread**. A plain
  `executor.submit(bus.execute, dto)` runs `execute` in a *new* thread that never saw the
  overlay's `set()` — every Feature's `self.context` there silently falls back to the (usually
  empty) shared context.
- `bus.thread_context()` captures the calling thread's current context and returns a
  `ThreadContextBus` — pass `.execute` (not the raw bus) to the executor instead.
- Call `thread_context()` **once per task you submit**, not once for a whole batch: a captured
  `contextvars.Context` can only be entered by one thread at a time, so sharing a single one
  across concurrent workers raises `RuntimeError`.

```python
from concurrent.futures import ThreadPoolExecutor

class SyncManyFeature(ApplicationService):
    def execute(self, dto: SyncManyDTO) -> SyncManyResponse:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                # captured here, in this thread, once per task
                executor.submit(self.feature_bus.thread_context().execute, item_dto)
                for item_dto in dto.items
            ]
            results = [f.result() for f in futures]
        return SyncManyResponse(results=results)
```

#### Async fan-out with `get_async_bus()`

- `thread_context()` is for **sync** code manually managing a `ThreadPoolExecutor`. If the
  caller is already `async def` (an async host handler, a script) and wants to fan out
  several DTOs concurrently instead, use `bus.get_async_bus()` (or the shortcut
  `framework.get_async_bus()`).
- Solves the same context-propagation problem as `thread_context()`, via `asyncio.to_thread`
  itself (stdlib already propagates `contextvars` into the worker thread — no
  `ThreadContextBus` involved here). Opposite reuse rule: an `AsyncBus` is stateless, so get
  it **once** and `await`/`asyncio.gather` many calls on it — each call gets its own fresh
  context snapshot, unlike `ThreadContextBus`'s single-use-per-snapshot restriction.
- Runs each call via `asyncio.to_thread` (stdlib), so `Feature`/`ApplicationService` stay
  100% sync — no "async Feature" variant to maintain.

```python
import asyncio

async def handle_request(framework, dto_a, dto_b, dto_c):
    async_bus = framework.get_async_bus()
    result_a, result_b, result_c = await asyncio.gather(
        async_bus(dto_a), async_bus(dto_b), async_bus(dto_c),
    )
    return result_a, result_b, result_c
```

**Cancellation and fan-out reliability**

- Cancelling the awaiting coroutine (e.g. a `asyncio.wait_for(...)` timeout) does **not**
  stop the `Feature`/`ApplicationService` already running in its worker thread — Python
  cannot forcibly kill a thread. Design for that (idempotency, no assumption that a timeout
  actually aborted the work) rather than relying on cancellation to stop it.
- For fan-out with proper partial-failure handling, prefer `asyncio.TaskGroup` (3.11+) over
  `asyncio.gather`: it cancels sibling tasks on the first failure and raises an
  `ExceptionGroup`, instead of `gather`'s default of leaving siblings running and swallowing
  all-but-the-first exception unless `return_exceptions=True` is passed.

### ⚠️ Error Handling at Different Levels

- Provides centralized error handling at three independent levels: **global**, **app service**, and **feature**.
- First registered handler executes first. On re-raise, the framework delegates to the next handler in the chain.
- Handlers can be registered **at any point** — before or after the first execution — and take effect immediately.
- Ensures consistent error management, improving overall reliability.

### 🚌 Bus Pattern for Component Communication

- Implements a bus mechanism to facilitate communication between **Feature** and **ApplicationService** components.
- Decouples component interactions, resulting in more flexible and scalable business logic.

### 🧩 Decoupled Logic Execution

- Supports independent execution of use cases through the **Feature** component, promoting separation of concerns.
- For example, a user registration workflow can be broken down into steps like input validation, profile creation, and
  email notification.

### 🎻 Application Service Orchestration

- Uses a **feature bus** to orchestrate multiple features into complex business workflows (e.g., customer onboarding).
- Integrates smaller use cases into cohesive flows to manage entire business processes effectively.

### 💻 IDE Support with Typing

- Uses type hints to enhance code quality and support features like autocompletion and type checking.
- Parameterize the bus as `UseFramework[DependencyContextType]`. Features get `self.token_adapter`; callers outside a Feature get the same instance as `framework.deps.token_adapter`.

### Events over brokers

See [docs/events/brokers.md](docs/events/brokers.md).

- The domain keeps `publish(event)` and registering an event on a bus; Kafka, RabbitMQ, Redis
  or NATS carry it through FastStream, behind the `[faststream]` extra.
- `FastStreamQueue(broker).start()` for synchronous code, `aput` for async; `subscribe(broker,
  Subscriber(...))` subscribes every event the buses registered.

### Caching

See [docs/caching/](docs/caching/README.md).

- Infrastructure the use case never sees: the composition says which Queries keep their answers —
  `QueryCaching(store).on(bus, Query, CachePolicy(ttl=…, vary_by=("tenant_id",)))`.
- An answer is tagged by what the repository noted it read and let go of when a commit writes it;
  `KeyValueStore` is the provider contract — memory in the core, Redis/Valkey and Memcached as extras.

### Crons

See [Crons](#crons-1) for the full section.

- A registry per bounded context (`cron_payments = Crons("cron-payments")`), one `Cron` class per
  cron, the buses it calls injected by their own names — a component beside the buses, not on one.
- By default in a background process: an in-memory orchestrator looks every 5 seconds and gives
  each run a thread of its own; one run per tick across replicas, explicit policies for
  overlapping and missed ticks.

### Migrations

See [Migrations](#migrations-1) for the full section.

- Every context and every store on one timeline: `status` says where the whole system stands,
  `upgrade` and `downgrade --to` move it as one, from a Makefile.
- The core needs no database: `MigrationEngine` is one abstract class for any store; Alembic is
  the engine shipped for SQL, behind the `[migrations]` extra.

### Data analysis

See [Data analysis](#data-analysis-1) for the full section.

- What a `Criteria` answered, held as a `DataFrame` under the repository's fingerprint of the
  read: the next page continues it, asking again reads nothing, a narrower filter is answered
  from a complete read.
- Handed on as Parquet or Arrow to a client, or through the Arrow PyCapsule interface to pandas,
  polars and DuckDB — pyarrow behind the `[data-analysis]` extra.

### Runtime use cases

See [Runtime use cases](#runtime-use-cases-1) for the full section.

- Commands, Responses and a Feature or ApplicationService stored as Python source, loaded onto
  the bus while the service runs; a stored handler may `replaces=` one in code.
- A new generation of the bus is built, checked and swapped in whole; a refused one leaves the
  bus as it was.

### Workflows (experimental)

See [Workflows (experimental)](#workflows-experimental-1) for the full section.

- The Commands a bus answers composed as JSON — `execute`, `code`, `for_each`, `fail`, `when` —
  validated against the live bus, run with a trace, drawn as a graph for a node editor.

### Persistence and the ORM

See [Persistence (ORM)](#persistence-orm) for the full section.

- An aggregate is a plain dataclass; a Feature reads and writes it through `self.repository`.
- One way to ask — `Criteria` — and one shape back — a page with its cursor, count and definition.
- Hooks, domain events, change tracking, event sourcing and an outbox on the same repository.

### `entrypoint_mcp`

See [docs/entrypoints/](docs/entrypoints/README.md) for the full section.

- One line publishes the bus as MCP tools: `build_mcp_server(instance).run()`.
- Features and ApplicationServices become typed tools. Docstrings are the LLM context.
- Domain code stays host-agnostic. This extra is MCP only.

### `entrypoint_rpc`

See [docs/entrypoints/](docs/entrypoints/README.md) for the full section.

- One process, several instances: `RpcGateway({"qr": qr, "cybersource": cybersource}).run()`.
- Methods are `qr.features.CommandCreateQREconomico` / `siat.app_services.CommandGenerateCUFD`.
- `context` on the JSON-RPC request is `framework.context` + optional `with_trace`. OpenRPC 1.4 discovery.

### `entrypoint_grpc`

See [docs/entrypoints/grpc.md](docs/entrypoints/grpc.md) for the full section.

**A bounded context hosted by another service.** When two services run the same code, the context
map places a context in the other one, and the caller's code does not change:

```yaml
context_map:                                   # the conf file; SINCPRO_CONTEXT_MAP wins per context
  - context: billing
    at: grpc://billing-service:50051?timeout=5
```

`billing(CommandIssueInvoice(...), ResponseIssueInvoice)` is then answered by the service at that
address, which hosts it in one line — `billing.serve(address)`, `billing.serve(address,
Attach.THREAD)` beside a REST API, `Attach.PROCESS` in a subprocess of its own, or
`open_host_routes([billing])` on the REST API's own port for `http://` addresses. `bytes`, `Decimal`
and the request context travel as they are, in chunks, and an exception raised there is raised here
as itself. The component is `sincpro_framework.remote_execution`. See [docs/entrypoints/bounded-contexts-across-services.md](docs/entrypoints/bounded-contexts-across-services.md).

- Same catalog, gRPC wire: `GrpcGateway({"qr": qr}).run("0.0.0.0:50051")`.
- Methods are `/qr.Features/CommandCreateQREconomico` — unary, `google.protobuf.Struct` in and out.
- No `protoc` and no generated stubs: server reflection for discovery, `.write_proto_files()` when a Go/TS client wants the contract.

### Observability (tracing + errors)

- **Tracing** (optional): OpenTelemetry spans on every DTO, export via OTLP (`sincpro-framework[opentelemetry]` + `OTEL_EXPORTER_OTLP_ENDPOINT`).
- **Errors** (optional): Sentry/GlitchTip capture on bus exceptions (`sincpro-framework[sentry]` + `SENTRY_PYTHON_DSN` in conf). Isolated client — does not call `sentry_sdk.init()`, does not reuse Odoo's client.
- **Metrics** (optional): every use case timed by bounded context and outcome, with nothing to declare; `@metrics.counts` / `sums` / `measures` on a use case by field reference (`of(Command).field`); Prometheus `/metrics` (`sincpro-framework[prometheus]`) or OTLP (`[opentelemetry]`), picked by `SINCPRO_METRICS_BACKEND` — [guide](docs/observability/metrics.md).
- Independent: you can enable traces, errors, both, or neither.

## ⚙️ Features vs. Application Service

- **Feature**: Represents a discrete, self-contained use case focused on specific functionality, easy to develop and
  maintain.
- **ApplicationService**: Orchestrates multiple features for broader business objectives, providing reusable components
  and workflows.

## 💳 Example Usage for a Payment Gateway

The following example shows how to configure the Sincpro Framework for a payment gateway integration, such as
CyberSource. It is recommended to name the framework instance to clearly represent the bounded context it serves.

### 🔧 Configuring the Framework

To set up the Sincpro Framework, configuration should be performed at the application layer within the `use_cases`
directory of each bounded context.

```plaintext
sincpro_payments_sdk/
├── pyproject.toml
├── README.md
├── apps/
│   ├── cybersource/
│   │   ├── adapters/
│   │   │   ├── cybersource_rest_api_adapter.py
│   │   │   └── __init__.py
│   │   ├── domain/
│   │   │   ├── card.py
│   │   │   ├── customer.py
│   │   │   └── __init__.py
│   │   ├── infrastructure/
│   │   │   ├── logger.py
│   │   │   ├── aws_services.py
│   │   │   ├── orm.py
│   │   │   └── __init__.py
│   │   └── use_cases/
│   │       ├── tokenization/
│   │       │   ├── new_tokenization_feature.py
│   │       │   └── __init__.py
│   │       ├── payments/
│   │       │   ├── token_and_payment_service.py
│   │       │   └── __init__.py
│   │       └── __init__.py
│   ├── qr/
│   ├── sms_payment/
│   ├── bank_api/
│   ├── online_payment_gateway/
│   └── paypal_integration/
└── tests
```

### 📋 Best Practices for Imports

Each use case should import both the **DTO for input parameters** and the **DTO for responses** to maintain clarity and
consistency.

### 📝 Sample Configuration in `__init__.py`

```python
from typing import Type

from sincpro_framework import Feature as _Feature
from sincpro_framework import UseFramework as _UseFramework
from sincpro_framework import ApplicationService as _ApplicationService

from sincpro_payments_sdk.apps.cybersource.adapters.cybersource_rest_api_adapter import (
    ESupportedCardType,
    TokenizationAdapter,
)
from sincpro_payments_sdk.infrastructure.orm import with_transaction as db_session
from sincpro_payments_sdk.infrastructure.aws_services import AwsService as aws_service

# Create an instance of the framework
cybersource = _UseFramework()

# Register dependencies
cybersource.add_dependency("token_adapter", TokenizationAdapter())
cybersource.add_dependency("ECardType", ESupportedCardType)
cybersource.add_dependency("db_session", db_session)
cybersource.add_dependency("aws_service", aws_service)


# Define a custom Feature class to access the dependencies
class Feature(_Feature):
    token_adapter: TokenizationAdapter
    ECardType: Type[ESupportedCardType]
    db_session: ...
    aws_service: ...
    logger: ...


# Define a custom Application Service class to access dependencies
class ApplicationService(_ApplicationService):
    token_adapter: TokenizationAdapter
    ECardType: Type[ESupportedCardType]
    db_session: ...
    aws_service: ...
    logger: ...
    feature_bus: ...


# Add use cases (Application Services and Features)
from . import tokenization

__all__ = ["cybersource", "tokenization", "Feature"]
```

## 🏗️ Recommended Infrastructure Structure

When bootstrapping a bounded context with `UseFramework`, the recommended practice is to split
framework wiring into three dedicated files under `apps/<domain>/infrastructure/`:

```plaintext
apps/
└── my_domain/
    ├── infrastructure/
    │   ├── dependencies.py   # registers adapters; declares DependencyContextType
    │   ├── framework.py      # defines local Feature/ApplicationService + config_framework()
    │   └── error_handler.py  # (optional) registers error handlers
    ├── services/
    │   ├── feature_a.py
    │   └── feature_b.py
    └── __init__.py           # creates the framework instance and imports services
```

### `dependencies.py` — Adapter Registration

Declare all external adapters in one place and expose a `DependencyContextType` typing helper.
This class is **not** instantiated — it is used only as a mixin to give `Feature` and
`ApplicationService` subclasses IDE autocomplete for injected attributes.

```python
# apps/my_domain/infrastructure/dependencies.py
from sincpro_framework import UseFramework

from my_sdk.adapters import PaymentAdapter, TokenizationAdapter


class DependencyContextType:
    """Typing helper — gives Features/AppServices IDE autocomplete for injected deps."""

    token_adapter: TokenizationAdapter
    payment_adapter: PaymentAdapter


def register_dependencies(framework: UseFramework[DependencyContextType]) -> UseFramework[DependencyContextType]:
    """Register all adapters with the framework instance."""
    framework.add_dependency("token_adapter", TokenizationAdapter())
    framework.add_dependency("payment_adapter", PaymentAdapter())
    return framework
```

### `framework.py` — Wiring with DependencyContextType

Combine the framework base classes with `DependencyContextType` using multiple inheritance so that
every Feature, ApplicationService and repository hook in this bounded context automatically
inherits the typed attributes. Each also takes the request's context type as a parameter —
`Feature[Command, Response, MyContext]`, `Hook[MyContext]` — for a typed `self.context`.

```python
# apps/my_domain/infrastructure/framework.py
from sincpro_framework import ApplicationService as _ApplicationService
from sincpro_framework import DataTransferObject  # re-exported for convenience
from sincpro_framework import Feature as _Feature
from sincpro_framework import UseFramework
from sincpro_framework.ddd import Hook as _Hook

from .dependencies import DependencyContextType, register_dependencies


class Feature(_Feature, DependencyContextType):
    """Base Feature for this bounded context — typed deps included."""

    pass


class ApplicationService(_ApplicationService, DependencyContextType):
    """Base ApplicationService for this bounded context — typed deps included."""

    pass


class Hook(_Hook, DependencyContextType):
    """Base repository hook for this bounded context — typed deps, `self.context`, `self.bus`."""

    pass


def config_framework(name: str) -> UseFramework[DependencyContextType]:
    """Create and configure the framework instance."""
    instance = UseFramework[DependencyContextType](name)
    register_dependencies(instance)
    return instance
```

**Hooks are lazy, like the bus.** A repository reads its hooks the first time it saves or reads,
not when it is built — so it is registered in `dependencies.py` like any other dependency, and a
hook imports its base from the context, as a Feature does:

```text
services/hooks/__init__.py      billing_hooks = Hooks()
services/hooks/invoices.py      from my_domain import Hook
                                @billing_hooks.on(Invoice) class NumbersInvoices(Hook): ...
infrastructure/dependencies.py  billing_hooks.inject(framework)
                                framework.add_dependency("repository", Repository(database, billing_hooks))
```

The same names Features receive as `self.token_adapter` are available on the root as
`my_framework.deps.token_adapter`. Use `self.<name>` inside a Feature / ApplicationService;
use `.deps` from SDK callers, tests, and entrypoints.

### `__init__.py` — Bootstrap the Bounded Context

Create the framework instance first, then import the service modules so that the `@framework.feature`
and `@framework.app_service` decorators register against the already-created instance.

```python
# apps/my_domain/__init__.py
from .infrastructure.framework import (
    ApplicationService,
    DataTransferObject,
    Feature,
    config_framework,
)

my_framework = config_framework("my-domain")

# Import services AFTER creating the instance so decorators register against it
from .services import feature_a, feature_b  # noqa: E402, F401
```

### Testing Dependency Consistency

Every name a Feature or ApplicationService declares — on itself or on the
`DependencyContextType` it inherits — has to be registered with `add_dependency`, or the handler
fails the first time it runs. One assertion checks the whole bus, every handler at once:

```python
# tests/my_domain/test_framework_setup.py
from sincpro_framework.testing import unregistered_dependencies

from my_sdk.apps.my_domain import my_framework


def test_every_declared_dependency_is_registered():
    assert unregistered_dependencies(my_framework) == {}
```

A failure names each handler with the dependencies it declares and nobody registered. The
framework's own attributes (`feature_bus`, `context`, the logger) are never reported.

### Handler Lifetime — one instance serves every execution

A `Feature` or `ApplicationService` is built **once** per `UseFramework` and reused by every
execution, on every thread. Anything written to `self` inside `execute` is therefore shared:
two concurrent calls overwrite each other's value, and the next call sees the last one.

```python
# ❌ request data on self — concurrent executions read each other's value
def execute(self, dto):
    self.order = self.repository.get(dto.order_id)
    return self._total()

# ✅ request data in locals — self holds only the injected dependencies
def execute(self, dto):
    order = self.repository.get(dto.order_id)
    return self._total(order)
```

`self.context` is safe: it is isolated per execution.

### Swapping a Dependency in a Test

`override_dependencies` keeps the production wiring and replaces only the adapters the test
names, for the length of the block. Every Feature and ApplicationService of that bus sees the
double, and so does `framework.deps`; the real adapter is back afterwards, even if the test
fails. A name that was never registered is refused, so a typo cannot leave the real adapter
talking to the outside world.

```python
from sincpro_framework.testing import override_dependencies

from my_sdk.apps.payments import payments


def test_declined_card_is_not_retried():
    gateway = FakeGateway(declines=True)

    with override_dependencies(payments, gateway=gateway):
        result = payments(CommandCharge(amount=10), ResponseCharge)

    assert result.status == "declined"
    assert gateway.calls == 1
```

It patches the instances the bus holds, so it is meant for tests, not for live traffic.

### Asserting What a Use Case Published

`RecordingQueue` is a queue like any other: put it behind the `Publisher` the bus already
takes, and it keeps every event in order — handing each one on to the queue it wraps, when
given one, so the subscribers still hear it.

```python
from sincpro_framework.events import Publisher, Subscriber, SyncQueue
from sincpro_framework.testing import RecordingQueue


def test_issuing_publishes_invoice_issued():
    published = RecordingQueue(SyncQueue(Subscriber(accounting)))
    billing = build_billing(publisher=Publisher(published))

    billing(CommandIssue(invoice_id="F-1"))

    assert [one.invoice_id for one in published.of(InvoiceIssued)] == ["F-1"]
```

### Checking Imports and Layers

Two checks read the package's source — without importing it, so an SDK that opens a network
connection on import is safe to check — and return what they find:

```python
from sincpro_framework.testing import import_cycles, layer_violations


def test_no_import_cycles():
    assert import_cycles("my_sdk") == []


def test_layers():
    assert layer_violations("my_sdk") == []
```

`import_cycles` reports modules that import each other while loading, e.g.
`my_sdk.domain.codes → my_sdk → my_sdk.domain.codes`. Imports inside a function or under
`if TYPE_CHECKING:` are not counted — they are how a cycle is deliberately broken. The
bootstrap this framework asks for (the context `__init__` creates the bus, then imports
`services`, which take the bus back) is not a cycle while the bus is bound before that import.

`layer_violations` applies the layering conventions. A module belongs to the first
`domain` / `adapters` / `services` / `infrastructure` / `entrypoints` in its path, and to the
context named by the path before it:

| Rule | Meaning |
|---|---|
| `domain-is-vocabulary` | `domain/` imports only `domain/` of its own context |
| `adapters-are-independent` | an adapter does not import a different adapter |
| `services-reused-through-bus` | a registered Feature/ApplicationService class, or a function, is never imported from a service module — its DTO goes on the bus. DTO names are free (`Command…`, `Query…`, anything) |
| `entrypoints-are-outermost` | only entrypoints import entrypoints |
| `contexts-are-acyclic` | two contexts never depend on each other |
| `common-imports-no-context` | a context named `common` imports no sibling context |

They are conventions, not a cage: pass the ones a project does not follow in `ignore`.

```python
assert layer_violations("my_sdk", ignore=["adapters-are-independent"]) == []
```

**Fixing a cycle.** Break it where the dependency points the wrong way, not with a local
import:

- **Two modules share a type** → move the type down, into the `domain/` both already import.
- **A module takes a name from its package `__init__`** → bind that name before the
  `__init__` imports anything that leads back to the module (the bus goes first), or import
  it from the module that defines it.
- **Two contexts need each other** → the more foundational one stops reaching up: move the
  shared piece into it, or pass the value in the Command.
- **An adapter needs another adapter** → a Feature composes both; the record they exchange
  goes to `domain/`.

---

## 🛠️ Creating a Feature

To create a new **Feature**, follow these steps:

1. **Create a Module for the Feature**: Add a new Python file in the appropriate folder under `use_cases`.
2. **Import the Framework and Required Classes**: Import the configured framework instance and `DataTransferObject`.
3. **Define the Parameter and Response DTOs**: Use `DataTransferObject` to create classes for input parameters and
   responses.
4. **Create the Feature Class**: Define the `Feature` class by inheriting from the custom `Feature` class.

### 🖋️ Example of Creating a Feature

```python
from sincpro_payments_sdk.apps.cybersource import cybersource, DataTransferObject, Feature


# Define parameter DTO
class TokenizationParams(DataTransferObject):
    card_number: str
    expiration_date: str
    cardholder_name: str


# Define response DTO
class TokenizationResponse(DataTransferObject):
    token: str
    status: str


# Create the Feature class
@cybersource.feature(TokenizationParams)
class NewTokenizationFeature(Feature):
    def execute(self, dto: TokenizationParams) -> TokenizationResponse:
        # Example usage of dependencies
        cybersource.logger.info("Starting tokenization process")
        token = self.token_adapter.create_token(
            card_number=dto.card_number,
            expiration_date=dto.expiration_date,
            cardholder_name=dto.cardholder_name
        )
        return TokenizationResponse(token=token, status="success")
```

## 🔄 Creating an Application Service

**ApplicationService** is used to coordinate multiple features while maintaining reusability and consistency. It
orchestrates features into cohesive workflows.

### 💡 Example of Creating an Application Service

```python
from sincpro_payments_sdk.apps.cybersource import cybersource, DataTransferObject, ApplicationService
from sincpro_payments_sdk.apps.cybersource.use_cases.tokenization import TokenizationParams


# Define parameter DTO
class PaymentServiceParams(DataTransferObject):
    card_number: str
    expiration_date: str
    cardholder_name: str
    amount: float


# Define response DTO
class PaymentServiceResponse(DataTransferObject):
    status: str
    transaction_id: str


# Create the Application Service class
@cybersource.app_service(PaymentServiceParams)
class PaymentOrchestrationService(ApplicationService):
    def execute(self, dto: PaymentServiceParams) -> PaymentServiceResponse:
        # Create the command DTO for tokenization
        tokenization_command = TokenizationParams(
            card_number=dto.card_number,
            expiration_date=dto.expiration_date,
            cardholder_name=dto.cardholder_name
        )
        tokenization_result = self.feature_bus.execute(tokenization_command)

        # Example usage of dependencies
        cybersource.logger.info("Proceeding with payment after tokenization")
        # Proceed with payment using the token (pseudo code for payment processing)
        transaction_id = "12345"  # Simulated transaction ID
        return PaymentServiceResponse(status="success", transaction_id=transaction_id)
```

## ⚙️ Executing a Use Case

Once a **Feature** or **ApplicationService** is defined, it can be executed by passing the appropriate **DTO** instance.

### 📌 Example of Executing a Use Case

```python
from sincpro_payments_sdk.apps.cybersource import cybersource
from sincpro_payments_sdk.apps.cybersource.use_cases.tokenization import TokenizationParams, TokenizationResponse
from sincpro_payments_sdk.apps.cybersource.use_cases.payments import PaymentServiceParams, PaymentServiceResponse

# Example of executing a Feature
feature_dto = TokenizationParams(
    card_number="4111111111111111",
    expiration_date="12/25",
    cardholder_name="John Doe"
)

# Execute the feature
feature_result = cybersource(feature_dto, TokenizationResponse)
print(f"Tokenization Result: {feature_result.token}, Status: {feature_result.status}")

# Example of executing an Application Service
service_dto = PaymentServiceParams(
    card_number="4111111111111111",
    expiration_date="12/25",
    cardholder_name="John Doe",
    amount=100.00
)

# Execute the application service
service_result = cybersource(service_dto, PaymentServiceResponse)
print(f"Payment Status: {service_result.status}, Transaction ID: {service_result.transaction_id}")
```

## 📚 Summary

The Sincpro Framework provides a robust solution for managing the application layer within a hexagonal architecture. By
focusing on decoupling business logic from external dependencies, the framework promotes modularity, scalability, and
maintainability.

- **Features**: Handle specific, self-contained business actions.
- **ApplicationServices**: Orchestrate multiple features for cohesive workflows.
- **`entrypoint_mcp`**: Publish that catalog as MCP tools (`sincpro-framework[mcp]`).
- **`entrypoint_rpc`**: Publish one or more instances as JSON-RPC 2.0 methods (`sincpro-framework[rpc]`).
- **`entrypoint_grpc`**: Publish that same catalog as unary gRPC services (`sincpro-framework[grpc]`).

This structured approach ensures high-quality, maintainable software that can adapt to evolving business needs. 🚀

## ⚠️ Error Handling

The framework provides three independent error handler scopes: **global** (framework bus), **feature**, and **app service**. Register a handler with the corresponding method — handlers can be added before or after the first execution and always take effect immediately.

### Basic usage

An error handler receives the exception. Return a value to suppress it:

```python
from sincpro_framework import UseFramework

framework = UseFramework("my_app")

def handle_error(error: Exception):
    return {"error": str(error)}  # suppresses the exception

framework.add_global_error_handler(handle_error)
```

### Scoped handlers

Each scope intercepts only the errors produced at that level:

```python
# Only feature errors
framework.add_feature_error_handler(feature_handler)

# Only app service errors
framework.add_app_service_error_handler(app_service_handler)

# Everything that reaches the root bus
framework.add_global_error_handler(global_handler)
```

### Registration lifecycle

Handlers can be registered at any point — before the bus is built or after — and take effect immediately:

```python
framework = UseFramework("my_app")

framework.add_global_error_handler(base_handler)  # before first execution

framework(some_dto)  # first call triggers build

framework.add_global_error_handler(extra_handler)  # after build — also works
```

---

### 🔗 Advanced: Handler chaining

Every call to `add_*_error_handler` adds the handler to a chain. The **first registered handler executes first**. If it re-raises, the framework automatically delegates to the next handler in the chain.

| Registration order | Role | Executes |
|---|---|---|
| `add(h1)` first | Auth — intercepts auth errors early | First |
| `add(h2)` second | Logging — records the error, then delegates | Second |
| `add(h3)` third | Base — produces the structured error response | Last |

#### Example: three-layer chain

```python
# Registration order: auth → observability → base
# Execution order: auth → observability → base

def auth_handler(error: Exception):
    """First — intercepts auth errors; delegates everything else."""
    if isinstance(error, AuthenticationError):
        return {"ok": False, "detail": "unauthenticated", "code": 401}
    raise error  # delegates to observability_handler

def observability_handler(error: Exception):
    """Second — logs error, then delegates to base_handler."""
    log.error("unhandled error", exc_info=error)
    raise error  # delegates to base_handler

def base_handler(error: Exception):
    """Last — always returns a structured error, never raises."""
    return {"ok": False, "detail": str(error)}

framework.add_global_error_handler(auth_handler)          # 1st = runs first
framework.add_global_error_handler(observability_handler) # 2nd
framework.add_global_error_handler(base_handler)          # 3rd = final fallback
```

## Persistence (ORM)

`sincpro_framework.ddd` is the vocabulary — aggregates, `Criteria`, repositories, events — and
needs nothing installed. `sincpro_framework.orm` is the SQLAlchemy adapter:
`pip install sincpro-framework[sqlalchemy]`.

### What it covers

| Capability | How |
|---|---|
| Aggregates | A plain `@dataclass` that inherits `Entity`: `id` (UUID v7), `created_at`, `updated_at`, `version` |
| Relations | Read from the annotations and the foreign keys: many2one, one2many, many2many, another context's bus, any function |
| Extension | A subclass of an aggregate keeps its inherited fields in the parent's table, its own in its table |
| Writes | `save` one or many, `remove`, `archive`; a stale write is refused (`StaleAggregate`) |
| Transactions | `repository.context()`: one unit of work, commits together or not at all |
| Reads | `get`, `get_by`, `exists`, `first`, `one`, `count`, `pluck`, `distinct`, `search`, `fetch_all`, `stream` |
| Queries | `Criteria`: filters, `all` / `any` / `negate`, ordering, cursor or offset pages, what to bring back of each record |
| Aggregation | `measures`, `group_by`, `pivot`, `export`, `explain` |
| Multi-tenant | `narrowed(criteria)`: a repository that only sees — and writes — what the criteria allows |
| Hooks | `Rule` functions and `Hook` classes on `before_*` / `after_*` of every write and read |
| Mixins | `ArchivableMixin`, `AuditedMixin` (who wrote it), `ChangeTrackingMixin` (what changed) |
| Domain events | Recorded by the aggregate, published by a Feature, answered by other buses |
| Event sourcing | Events stored as the state (`event_columns()`) and folded back |
| Outbox | `EventTrackableMixin` + `delivery_columns()` and a relay with `for_update` / `skip_locked` |
| Testing | `MemoryRepository`: the same vocabulary with no database |

### Example

```python
from dataclasses import dataclass

from sqlalchemy import Column, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd import Criteria, Entity, EntityCollection
from sincpro_framework.orm import Database, Repository, entity_table, map_aggregates


@dataclass
class Product(Entity):                     # id, created_at, updated_at, version come from Entity
    name: str = ""
    price: int = 0


class Products(EntityCollection[Product]): ...


catalog = registry()
product_table = entity_table(
    "product",
    catalog.metadata,
    Column("name", Text, nullable=False),
    Column("price", Integer, nullable=False),   # a page is only ordered by NOT NULL columns
)
map_aggregates(catalog, {Product: product_table})

database = Database("sqlite:///catalog.sqlite3")
catalog.metadata.create_all(database.engine)          # a project runs its migrations instead
repository = Repository(database)                     # add_dependency("repository", repository)

repository.save([Product(name="Coffee", price=30), Product(name="Tea", price=20)])

page = repository.search(Products, Criteria.model_validate({
    "where": {"field": "price", "operator": ">", "value": 25},
    "order": [{"field": "price", "descending": True}],
    "pagination": {"limit": 10},
}))
print([product.name for product in page.items])        # ['Coffee']
```

### How to, by topic

Every row links the step-by-step section of **[the guide](docs/persistence/guide.md)** — where
every example runs as part of the test suite — and the page that explains it in depth.

| Topic | How to | In depth |
|---|---|---|
| Aggregates, tables, repository | [Guide §1–3](docs/persistence/guide.md#1-the-aggregate) | [introduction.md](docs/persistence/introduction.md), [reference.md](docs/persistence/reference.md) |
| Writing and units of work | [Guide §4–5](docs/persistence/guide.md#4-writing) | [lifecycle.md](docs/persistence/lifecycle.md) |
| Reading with `Criteria` | [Guide §6](docs/persistence/guide.md#6-reading) | [criteria.md](docs/persistence/criteria.md) |
| Related records | [Guide §7](docs/persistence/guide.md#7-relations) | [specification.md](docs/persistence/specification.md), [relations.md](docs/persistence/relations.md) |
| Hooks | [Guide §8](docs/persistence/guide.md#8-hooks) | [hooks.md](docs/persistence/hooks.md) |
| Domain events | [Guide §9](docs/persistence/guide.md#9-domain-events) | [events/README.md](docs/events/README.md) |
| Change tracking | [Guide §10](docs/persistence/guide.md#10-change-tracking) | [change-tracking.md](docs/events/change-tracking.md) |
| Event sourcing | [Guide §11](docs/persistence/guide.md#11-event-sourcing) | [shapes.md §3](docs/shapes.md#3-the-facts-are-the-state) |
| Outbox | [Guide §12](docs/persistence/guide.md#12-an-outbox) | [shapes.md §2](docs/shapes.md#2-a-database-per-context) |
| Testing | [Guide §13](docs/persistence/guide.md#13-testing) | [testing.md](docs/persistence/testing.md) |
| Extending an aggregate | [Guide §14](docs/persistence/guide.md#14-extending-an-aggregate) | [reference.md](docs/persistence/reference.md) |
| Why it is designed this way | — | [design.md](docs/persistence/design.md), [decisions.md](docs/persistence/decisions.md) |

## Crons

A cron is one more caller of the buses, like an RPC method: a class registered on the registry of
its bounded context, the buses it orchestrates injected by name.

```python
from sincpro_framework.cron import Cron as _Cron
from sincpro_framework.cron import CronGateway, CronProcess, Crons, Tick


class CronDependencyContextType:
    cybersource: UseFramework


class Cron(_Cron, CronDependencyContextType):          # the bounded context's base cron
    pass


cron_payments = Crons[CronDependencyContextType]("cron-payments")
cron_payments.add_dependency("cybersource", cybersource)


@cron_payments.cron("0 2 * * *", timezone="America/La_Paz")
class ReconcileTransactions(Cron):
    def run(self, tick: Tick) -> None:
        self.cybersource(CommandReconcile(day=tick.scheduled_for.date()))


def build_crons() -> CronGateway:                       # module-level: runs in the child
    return CronGateway([cron_payments])


crons = CronProcess(build_crons).start()                # at startup; crons.stop() at shutdown
```

| | |
|---|---|
| When | a five-field expression in a timezone (DST handled) or `every=timedelta(…)` |
| Where | `CronProcess`: a spawned child running the in-memory orchestrator — looks every 5 s (`jitter=` optional), a thread per run (`workers=` caps them) |
| Once per tick | the first claim of `(name, scheduled_for)` in `runs` wins; `tick.once(key)` for one step. In memory by default; crons on several replicas share a `CronRuns` the project implements |
| Policies | `overlap` (`SKIP` / `ALLOW`), `missed` (`SKIP` / `RUN_LATEST` / `RUN_ALL`) within `missed_window` |
| Seeing it | the registry's own logger and spans; `gateway.plan(until)`, `gateway.status()` |
| Testing | `ManualClock` — time moves when the test says so |

The whole of it, runnable: [docs/cron/](docs/cron/README.md).

## Migrations

The project writes each migration step; the framework orders every chain of every context and
store into one timeline and moves the whole system along it.

```python
from sincpro_framework.migrations import ContextMigrations, Migrations, command_line
from sincpro_framework.orm.migrations import AlembicEngine            # the [migrations] extra

billing_migrations = ContextMigrations("billing", Path("domains/billing/entrypoints/migrations"))
billing_migrations.store("main", AlembicEngine(billing_tables, database))

migrations = Migrations([common_migrations, billing_migrations])      # entrypoints/migrations.py
raise SystemExit(command_line(migrations))                           # status, upgrade, downgrade …
```

| | |
|---|---|
| Order | each chain in its own order, `requires` first, the oldest UUIDv7 among the rest |
| Where it stands | per store, read from the store: up to date, behind, ahead, dirty |
| Moving | `upgrade [--to]`, `downgrade --to` across every context and store; irreversible steps refuse |
| Any store | implement `MigrationEngine` — five methods; `InMemoryEngine` for tests |
| CI | `check`: edited bodies, forked chains, drift |

The whole of it, runnable: [docs/migrations/](docs/migrations/README.md).

## Data analysis

The same question never sent to the database twice: a read is held, continued page by page, and
narrowed without asking again. A utility, not an engine — the computing is pandas', polars' or
DuckDB's.

```python
from sincpro_framework.data_analysis import QueryCache

cache = QueryCache(max_rows=2_000_000)
page = cache.fetch(repository, InvoiceLine, posted)                  # one read
more = cache.fetch(repository, InvoiceLine, posted, pages=3)         # only pages 2 and 3
sales = cache.fetch_all(repository, InvoiceLine, posted)             # complete
sales.narrow({"field": "journal", "operator": "=", "value": "SAL"})  # no read at all
polars.DataFrame(sales)                                              # or sales.to_parquet()
```

| | |
|---|---|
| Key | `repository.fingerprint(target, criteria)` — filter, order, mask and scope; never the page |
| Pages | read with the keyset cursor and without a count, merged by `id` |
| Narrowing | only on a complete frame — `NotComplete` otherwise |
| Out | Parquet, Arrow IPC, columnar JSON with exact decimals, `__arrow_c_stream__`; `version` is an ETag |
| Extra | the core needs nothing; Parquet and Arrow need `[data-analysis]` (pyarrow) |

The whole of it, runnable: [docs/data_analysis/](docs/data_analysis/README.md).

## Runtime use cases

A use case stored as source — its Commands, its Responses and the one handler that answers them
— loaded onto the bus without a deploy.

```python
from sincpro_framework.runtime_use_cases import BusRegistry, RuntimeUseCase

registry = BusRegistry(billing, store)                              # the code's bus + what is stored
registry.check(RuntimeUseCase(name="quote", source=source, version=2))          # refused before it is saved
store.save(RuntimeUseCase(name="quote", source=source, version=2))
registry.reload()                                                   # a new generation, swapped in whole
registry.execute("sincpro_runtime.billing.quote.CommandQuote", {"amount": 100})
```

| | |
|---|---|
| Truth | the Python source; the Command is the one `execute` declares |
| Generations | `billing.fresh()` plus what is stored, built beside the one answering, swapped with one assignment |
| Refused | syntax, imports, module body, a Command the code already answers — named with its version |
| Code | a stored use case imports the code's Commands, calls its Features, `replaces=` one of them |
| Store | `UseCaseStore` (`active`, `save`) — `InMemoryUseCases` in the core, `SqlUseCases` in `orm` (`[sqlalchemy]`), or yours |

The whole of it, runnable: [docs/runtime_use_cases/](docs/runtime_use_cases/README.md).

## Workflows (experimental)

The Commands a bus answers composed as JSON — what a node editor previews and an agent tries. The
vocabulary may change.

```python
from sincpro_framework.workflows import FileWorkflows, Workflows

workflows = Workflows(billing, FileWorkflows(Path("workflows")))    # one <name>.json each
run = workflows.run("bill_order", {"order_id": 1})                    # a trace of every step
workflows.draw("bill_order")                                          # the graph, as Mermaid
```

| | |
|---|---|
| Steps | `execute` a Command, `code` (a snippet), `for_each`, `fail` — each with an optional `when` |
| References | whole values only: `$input.x`, `$steps.<id>.x`, `$item.x` — checked before anything runs |
| For agents | `Workflows.schema()`, `catalog()`, `validate()` (every issue at once), `dry_run()`, `draw()` |

The whole of it, runnable: [docs/workflows/](docs/workflows/README.md).

## 📖 Documentation

This repository's documentation is generated with
[openwiki](https://github.com/langchain-ai/openwiki) and lives in
[`openwiki/`](openwiki/) as browsable Markdown.

```bash
make docs-init   # first generation (once per repository)
make docs        # regenerate from code changes
make docs-view   # local explorer: node graph + Markdown reader
```

Requires Node >= 22 — nothing else. The `Makefile` runs openwiki through
`npx --yes openwiki@$(OPENWIKI_VERSION)`, so there is no global install and the
version is pinned per run.

Provider, model and endpoint are already set in the `Makefile`
(`OPENWIKI_PROVIDER`, `OPENWIKI_MODEL_ID`, `OPENAI_COMPATIBLE_BASE_URL`), so the
only thing you have to supply is the API key:

```bash
export OPENAI_COMPATIBLE_API_KEY=<key>   # or store it in ~/.openwiki/.env (chmod 600)
```

The key is the one value that is **never** committed — not in the `Makefile`,
not anywhere in the repository. `make docs` fails with a clear message when it
is missing. Any of the other parameters can be overridden from the shell.
The scope of the wiki is controlled in
[`openwiki/INSTRUCTIONS.md`](openwiki/INSTRUCTIONS.md), and what the agent is
not allowed to read, in [`.openwikiignore`](.openwikiignore).

Hand-written documentation stays in [`docs/`](docs/README.md), organised by layer
(core, persistence, events, entrypoints, observability), and remains authoritative:
the wiki references it, it does not replace it.

> **Migration note (4.0.0)** — up to 3.x the framework shipped its own
> documentation generator (`sincpro_framework.generate_documentation`, with
> `build_documentation()` and the `ai_context/` JSON files). It has been
> removed. Projects that used it replace their `scripts/generate_doc.py` with
> the `make docs` above.
## Observability

The bus always instruments. Extras and env vars only decide **where** data goes.

| Signal | Extra | Env | Backend |
|---|---|---|---|
| Logs (`trace_id` / `span_id`) | none | — | stdout / your logger |
| Tracing (spans) | `[opentelemetry]` | `OTEL_EXPORTER_OTLP_ENDPOINT` | Tempo / Jaeger |
| Errors (exceptions) | `[sentry]` | `SENTRY_PYTHON_DSN` (framework conf) | GlitchTip / Sentry |
| Metrics (every use case, plus what it declares) | `[prometheus]` / `[opentelemetry]` | `SINCPRO_METRICS_BACKEND` | Prometheus `/metrics` / OTLP — [metrics guide](docs/observability/metrics.md) |

Missing extra or missing DSN in conf → no-op, the bus still raises. Framework events are independent from the host: Odoo may also capture the same exception with its own release. That is intended.

### What works without any extra install

Every `framework(dto)` call automatically tags all internal log lines with a `trace_id` and `span_id`. These are UUID-based identifiers — enough to correlate all logs produced by a single execution even without an external tracing backend.

You can also read them inside any Feature or ApplicationService:

```python
class MyFeature(Feature):
    def execute(self, dto: MyDTO) -> MyResponse:
        trace_id = self.context.get("trace_id")  # always present
        ...
```

### Installing with OpenTelemetry

```bash
pip install sincpro-framework[opentelemetry]
```

This installs:
- `opentelemetry-api` + `opentelemetry-sdk`
- `opentelemetry-exporter-otlp-proto-grpc` (primary)
- `opentelemetry-exporter-otlp-proto-http` (fallback)

### When a use case fails

A failure is described by the handler that raised it and logged **once**, by the outermost
bus — however many ApplicationServices or other bounded contexts' buses it crossed:

```json
{
  "event": "SendInvoice failed: Fault: CUFD vencido",
  "dto": "CommandSendInvoice(nit=123, cuf='ABC')",
  "failed_in": "siat-soap-sdk",
  "handler": "SendInvoice",
  "layer": "feature",
  "chain": "CommandBillOrder → CommandSendInvoice",
  "error_type": "Fault",
  "error_at": "sincpro_siat_soap.infrastructure.soap_client:88 in SoapClient._send",
  "raised_at": "zeep.proxy:52 in OperationProxy.__call__",
  "correlation_id": "req-42",
  "exception": "Traceback ..."
}
```

- `app_name` is the bus that logged it — the outermost — and `failed_in` the bus whose
  handler raised: siat called from inside another context's Feature still says
  `failed_in: "siat-soap-sdk"`.
- `error_at` is the last line of the handler's own package the error went through;
  `raised_at` is the line that raised, when it is somewhere else (a SOAP or HTTP library).
  The logger's own `filename` / `func_name` name whoever executed the DTO, never `bus.py`.
- The exception leaving the bus carries a note with the same facts, so whoever catches it —
  Odoo, a test, an error handler that maps it to the SDK's own exception — sees where it
  came from at the end of the traceback:
  `[sincpro] CommandBillOrder → CommandSendInvoice: SendInvoice (siat-soap-sdk) failed at …`.
- Every key set with `context()` is on every log line. Keep some out:
  `UseFramework("siat", hide_in_logs=["TOKEN"])`.
- A failure describes the DTO by its `repr` — on the error line, in GlitchTip, in the note.
  A field that must not leave the process is kept out the way pydantic keeps it out of a
  repr: `password: SecretStr`, or `otp: str = Field(repr=False)`.
- An error handler that re-raises changes nothing above. One that answers leaves a
  `warning` — `… (answered by an error handler)` — because the caller never sees the error.
- An expected error, one passed to `ignore_sentry_exceptions`, is logged at `info` with no
  traceback: it is traffic, not a fault.
- A transport (JSON-RPC, gRPC, the background queue) logs only what no bus logged:
  `process.was_reported(error)`.

### Sentry / GlitchTip (errors)

Same silent contract as OTel. The bus **always** tries to report exceptions; if `sentry-sdk` is missing or the DSN in conf is unset, it is a no-op.

```bash
pip install sincpro-framework[sentry]
export SENTRY_PYTHON_DSN=https://KEY@glitchtip.sincpro.dev/1
```

Conf (`sincpro_framework/conf/sincpro_framework_conf.yml`) resolves `sentry_dsn` from `SENTRY_PYTHON_DSN`. If that env is set, `app.observability.status.sentry` is `on:init`, not `off`. The framework does **not** call `sentry_sdk.init()` and does not reuse the host client.

Each framework event uses an isolated Sentry `Client` whose `release` is the deployed artifact and its version — literally `APP_RELEASE` (`sincpro_mcp_odoo:0.8.0`), or `{distribution}:{version}` for an SDK (`sincpro-payments-sdk:5.0.3`). The bus is **not** part of the release: two buses of one deployment ship the same release and are told apart by the `sincpro.instance` tag. Framework-internal errors use `sincpro-framework:<framework version>`.

`APP_RELEASE` is the standard on every deployed service, so it answers first. Only when there is no artifact at all does the bus name stand in for it, so events stay separable per bounded context.

GlitchTip `environment` is `TENANT` (same value as the `tenant` tag) so events can be filtered by tenant in the UI.

Odoo may capture the same exception with Odoo's release. That second event is intended — two products, two releases, same traceback.

The bus reports **before** the error handler runs, once, from the handler that raised: an ApplicationService the error crosses sends nothing more. The event carries a `sincpro.handler` tag and a `sincpro` context with the DTO, the chain, `error_at` and the execution's context. A handler that swallows an unexpected exception still produces a GlitchTip event. Expected domain errors can be excluded per instance:

```python
app = UseFramework("payment-cybersource")  # release auto-detected from the caller package
app.ignore_sentry_exceptions(ValidationError, InsufficientFunds)
```

Pass `package="sincpro-payments-sdk"` to `UseFramework` when the caller is not the library itself (tests, a thin adapter).

Observability is optional and must never break the bus. After `build_root_bus()` (or the first `app(dto)` call) every instance exposes a probe:

```python
status = app.observability.status          # ObservabilityStatus
status.sentry.state                        # off | on | failed
status.otel.reason
```

- `off` — extra not installed or conf DSN missing (`sdk_missing`, `dsn_missing`)
- `on` — isolated client ready (`init`)
- `failed` — DSN present but client construction broke; the bus still runs. Logged as **warning**.

The instance logger emits: `observability sentry=on:init otel=off:no_endpoint`.

Do not send traces to GlitchTip (`traces_sample_rate=0`); Tempo stays on OTLP.

### What OpenTelemetry adds

| Without OTel | With OTel |
|---|---|
| UUID-based trace/span IDs in logs | Real OTel spans with proper trace IDs |
| No span hierarchy | `ApplicationService` span wraps `Feature` spans |
| No OTLP export | Exports to Jaeger, Grafana Tempo, Honeycomb, etc. |
| No W3C traceparent propagation | Parent context from HTTP headers via `carrier=` |
| No external span adoption | Auto-adopts active span from FastAPI, Celery, etc. |

When OTel is installed, auto-adoption of outer spans happens transparently — any active span already in the OTel context (set by FastAPI OpenTelemetry middleware, a Celery task decorator, etc.) becomes the parent of all sincpro spans without any extra setup.

### Configuring the OTLP exporter

Set the endpoint in your environment or the framework config file:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

Or in `sincpro_framework/conf/sincpro_framework_conf.yml`:

```yaml
otlp_endpoint: $ENV:OTEL_EXPORTER_OTLP_ENDPOINT
```

Nothing registers it by hand: a bus brings tracing up when it starts, from the endpoint the
configuration names above. The provider is a **process-level singleton** — the first bus to
start installs it, and a second bounded context in the same process finds it already there.

What each door is for, and how a transport span joins a bus's trace, is in
[the observability docs](docs/observability/README.md).

### The `with_trace()` context manager

Use `with_trace()` when you need explicit control over the trace boundary — for example, to group multiple `framework(dto)` calls under a single trace, or to accept a trace from an upstream caller.

**Fresh trace** (useful in CLI scripts, workers, or test harnesses):

```python
with framework.with_trace() as fw:
    result = fw(MyDTO(...), MyResponse)
    # all logs inside this block share the same trace_id/span_id
```

**Propagate from upstream HTTP headers** (W3C `traceparent`):

```python
# headers = {"traceparent": "00-<trace_id>-<parent_id>-01", ...}
with framework.with_trace(carrier=request.headers) as fw:
    result = fw(ProcessOrderDTO(...), OrderResult)
```

**Explicit IDs** (re-use IDs from a previous system that doesn't speak W3C):

```python
with framework.with_trace(trace_id="abc123", span_id="def456") as fw:
    result = fw(MyDTO(...), MyResponse)
```

### Span attributes

Every span produced by the framework carries:

| Attribute | Value | Purpose |
|---|---|---|
| `sincpro.layer` | `"feature"` or `"application_service"` | Identify which bus layer handled the DTO |
| `sincpro.instance` | The framework instance name (e.g. `"payments"`) | Distinguish bounded contexts inside one deployment |
| `error.type` | Exception class, on the span of the handler that raised | Filter failed spans by cause |
| `code.function.name` / `code.file.path` / `code.line.number` | The consumer's line that failed (`error_at`) | Jump from the trace to the code |

### The observability API: automatic first, two doors when you need them

**Nothing has to be called.** Creating a `UseFramework` and building it is the whole
setup:

- **It auto-configures.** The endpoint, the DSN, the release, the tenant and the
  sampling rate are read from the environment by the framework itself
  (`OTEL_EXPORTER_OTLP_ENDPOINT`, `SENTRY_PYTHON_DSN`, `APP_RELEASE`, `TENANT`,
  `OTEL_TRACES_SAMPLER_ARG`, `OTEL_SERVICE_NAME`, `SINCPRO_FRAMEWORK_LOG_LEVEL`).
  A service does **not** assign anything into the framework's settings: if the pod has
  the variable, the bus has it too.
- **It auto-instruments.** Every DTO gets a span named after it, with `sincpro.layer`
  and `sincpro.instance`; every unhandled exception reaches GlitchTip under the right
  release; every internal log line carries the active `trace_id`. Identity is resolved
  once — the calling library, or `APP_RELEASE` for a service.
- **It degrades on its own.** Extras missing, endpoint absent, collector down, a span
  processor that raises: all of it is a status you can read, never an exception the bus
  has to survive.

So a library embedded in Odoo calls none of this. `UseFramework(...)`, and done.

Two doors exist for what the framework cannot know on its own:

```python
# 1. Per bus — you already have it
framework.observability.identity   # sincpro-odoo-mcp:0.8.0:sales_mcp
framework.observability.status     # {"sentry": {...}, "otel": {...}}

# 2. The transport — only for a service that also serves HTTP
from sincpro_framework.observability import process

process.identity                     # sincpro-odoo-mcp:0.8.0 — no bus segment
process.status                       # on:installed | on:host | off:...
process.tracer("asgi")               # for OpenTelemetryMiddleware / HTTPXClientInstrumentor
process.bind_logger(access_log)      # that logger now stamps the request's trace_id
process.trace_ids()                  # the same ids, for a structlog processor
process.record_error(error, "asgi")  # a 500 that dies before any Feature runs
```

Everything else under `sincpro_framework.observability` is an implementation detail of
those two. There is nothing to monkeypatch and no private module to import.

#### A bus never takes the global TracerProvider

Transport instrumentation asks OTel for the *global* tracer. If a bus answered there,
every request would be exported as if it belonged to that bounded context — with three
buses in one process, `POST /mcp` came out labelled as the first one that booted.

So the rule is: when nobody owns the global, the framework installs a **process**
provider there — same endpoint, same tenant, identity without the bus segment. When a
host (Odoo) already owns it, nothing is installed. Building any bus is what triggers
this, which is why a service builds its buses before it serves the first request.

The result is one trace, several identities, and no shared Resource:

```
service.name=sincpro-odoo-mcp:0.8.0              POST /mcp                  (ASGI)
  └── service.name=...:0.8.0:common_mcp            CommandListAvailableTools  (bus)
  └── service.name=...:0.8.0:sales_mcp             CommandCreateQuote         (bus)
        └── service.name=sincpro-odoo-mcp:0.8.0      GET /odoo/registry       (httpx)
access log / uvicorn                              same trace_id, process logger
```

They share the `trace_id` because OTel propagates through contextvars — not because
they share a provider. Filter the deployment by `sincpro-odoo-mcp:0.8.0`, a bounded
context by its full `service.name` or by the `sincpro.instance` attribute.

#### What a service's boot looks like

```python
from sincpro_framework.observability import process

def main() -> None:
    configure_process_logging(settings.log_level)   # your own stdlib/structlog routing

    # Eager: building a bus installs the process provider, which the ASGI middleware
    # needs before it opens its first span.
    for bus in ALL_BUSES:
        bus.build_root_bus()

    process.bind_logger(access_log)
    logger.info("otel proceso=%s:%s", process.status.state, process.status.reason)

    serve(middleware=[Middleware(OpenTelemetryMiddleware), Middleware(JsonAccessLog)])
```

What stays yours: which URLs to exclude, which library loggers to route, what not to
log, the origin guard, the routes. The framework gives the sink, the identity, the
tracer per layer and the ids for the logs; the service wires its own transport.

### Embedding sincpro inside another instrumented service (Odoo, FastAPI, etc.)

Every bus gets its **own** TracerProvider, whose `service.name` is `artifact:version:bus` — e.g. `sincpro_mcp_odoo:0.8.0:common_mcp`. The root span created by `with_trace()` and the DTO spans under it all come from that provider, so one trace reports one identity. If the host application (Odoo, FastAPI, Celery) already registered the global OTel provider, sincpro leaves it untouched and keeps its own provider internal.

The trace relationship is still preserved: OTel propagates the active parent span via `contextvars` (process-wide), so sincpro spans are automatically children of whatever span the host has active at call time. In Tempo/Jaeger the full tree is visible and filterable:

```
service.name=odoo                            →  GET /web/dataset/call_kw    (Odoo HTTP span)
service.name=sincpro_mcp_odoo:0.8.0:sales    →    └── CreateOrderDTO         (application_service)
service.name=sincpro_mcp_odoo:0.8.0:sales    →         └── ValidateStockDTO  (feature)
```

Sampling is respected across the boundary: sincpro uses `ParentBased`, so a decision already taken upstream — by the host, or by an incoming `traceparent` — always wins and a sampled request is never truncated halfway through.

How much of the traffic this bus starts on its own is recorded comes from OTel's standard variable:

```bash
export OTEL_TRACES_SAMPLER_ARG=0.1   # record 10% of the traces born in this bus
```

`1.0` (the default) records everything, `0.0` records nothing. An unusable value falls back to `1.0` with an info log — `settings` is built at import time, so a typo in one deployment variable must not make the framework unimportable.

## Configuration or settings

A project's configuration is one document — a YAML file plus the environment — and a settings
class is any `SincproConfig`. `build_config_obj(Shape, file, path)` builds one from the document;
the full guide is [docs/core/settings.md](docs/core/settings.md).

```python
from sincpro_framework.sincpro_conf import SincproConfig, build_config_obj


class PostgresConf(SincproConfig):
    host: str = "localhost"
    port: int = 5432
    user: str = "my_user"


class MyConfig(SincproConfig):
    log_level: str = "DEBUG"
    token: str = "default_my_token"
    postgresql: PostgresConf = PostgresConf()


config = build_config_obj(MyConfig, "/path/to/your/config.yml", "my_project")
```

```yaml
my_project:
  log_level: "INFO"
  token: "$ENV:MY_SECRET_TOKEN"
  postgresql:
    host: localhost
    port: 12345
```

`path` names the project's section of the file (optional: without it the whole file is the
section) and may be dotted to reach a section inside it. A nested class is read from the section
of its name; one with a default keeps it when its section is absent. The object is assignable
unless its class sets `model_config = ConfigDict(frozen=True)`.

### Environment variables

`$ENV:NAME` reads the variable. When it is unset, or holds what the field cannot accept, the field
takes its default and an info line is logged — the settings are built at import time, and a typo
in one deployment variable never makes the process fail to start:

```python
class ApiConfig(SincproConfig):
    api_key: str = "dev_default_key"

# config.yml:  api_key: "$ENV:API_KEY"
# API_KEY unset → info: "Environment variable [API_KEY] is not set for field [api_key].
#                        Using default value: dev_default_key"
```

A root class that declares `env_prefix: ClassVar[str] = "PAYMENTS"` also reads variables by path
(`PAYMENTS__QR__TIMEOUT` sets `qr.timeout`); without it, nothing is read by path.

### A project with bounded contexts

The shared settings, a class per context inheriting them, and the global holding every context:

```python
class SharedSettings(SincproConfig):
    environment: Environment = Environment.TEST


class QRSettings(SharedSettings):
    timeout: float = 10.0


class PaymentsSettings(SharedSettings):
    qr: QRSettings


settings = build_config_obj(PaymentsSettings, FILE, "sincpro_payments_sdk")   # the global
qr_bus.add_dependency("settings", settings.qr)                                  # a context's own
qr = build_config_obj(QRSettings, FILE, "sincpro_payments_sdk.qr")             # a shape alone
```

A section inherits a shared field it does not set (`qr.environment`) from the nearest section
above, and overrides it by setting it. Everything required and missing is one validation error
naming each path. `Secret[str]` masks a value everywhere it is printed. `describe_settings(settings)`
lists each value with where it came from — file, section inherited, variable or default.
`sincpro_framework.testing.settings_scope_violations` reports a context reading another context's
section, for a team that wants the rule. A shared class inheriting `FrameworkSettings` hands the
framework its log, OTLP, Sentry and release settings — no `framework_settings.x = …` lines.

## 📦 Variables

The framework use a default setting file where live in the module folder inside of
`sincpro_framework/conf/sincpro_framework_conf.yml`
where you can define some behavior currently we support the following settings:

- `sincpro_framework_log_level`: Log level for the framework logger. Default: `DEBUG`.
- `otlp_endpoint`: OTLP exporter endpoint for distributed tracing. Resolved from `OTEL_EXPORTER_OTLP_ENDPOINT` env var. Default: `null` (tracing disabled). Requires `sincpro-framework[opentelemetry]`.
- `sincpro_framework_log_level`: `INFO` or `DEBUG`. Resolved from `SINCPRO_FRAMEWORK_LOG_LEVEL`. Default: `DEBUG`. A service sets its own level through the environment, or through its own settings by inheriting `FrameworkSettings` ([docs/core/settings.md](docs/core/settings.md#the-frameworks-own-settings-frameworksettings)), which configures the log again when the level is among what it sets.
- `otlp_traces_sample_rate`: share of new traces to record, `0.0`-`1.0`. Resolved from `OTEL_TRACES_SAMPLER_ARG`. Default: `1.0`. An upstream sampling decision always wins over this ratio.
- `app_release`: deployed artifact and version, `artifact:version`. Resolved from `APP_RELEASE` — the standard on every Sincpro service. Feeds both the GlitchTip release and the OTel `service.name`.
- `otel_service_name`: names the artifact when `APP_RELEASE` carries only a version. Resolved from `OTEL_SERVICE_NAME`.
- `tenant`: GlitchTip `environment` and the `tenant` tag. Resolved from `TENANT`.
- `metrics_backend`: `auto`, `prometheus`, `otel` or `off`. Resolved from `SINCPRO_METRICS_BACKEND`. Default: `auto` — OpenTelemetry when a meter provider or an OTLP endpoint is configured, otherwise nothing. `prometheus` requires `sincpro-framework[prometheus]` and serves `/metrics` ([docs/observability/metrics.md](docs/observability/metrics.md)).
- `sentry_dsn`: GlitchTip/Sentry DSN. Resolved from `SENTRY_PYTHON_DSN`. Default: `null` (error reporting disabled). Requires `sentry-sdk` (or `sincpro-framework[sentry]`). The framework uses an isolated client with `release=APP_RELEASE` and never calls `sentry_sdk.init()`. Odoo may capture the same error separately. Use `UseFramework.ignore_sentry_exceptions(...)` for expected errors.

Override the config file using another

```bash
export SINCPRO_FRAMEWORK_CONFIG_FILE = /path/to/your/config.yml
```

## 🧪 Tests & coverage

The `Makefile` is the single entry point — CI calls the same targets you run locally.

```bash
make test                 # unit suites + coverage report in the terminal + coverage.xml
make test-realworld       # the ledger cases over a populated database (docs/persistence/testing.md)
make test-stress          # the same at 25 000 entries, timed; SINCPRO_REALWORLD_ENTRIES and DATABASE_URL apply
make test-coverage        # the above + HTML report in htmlcov/
make test-coverage-open   # the above + opens htmlcov/index.html in the browser
make test_one t=tests/test_async_bus.py   # a single file/test, verbose, no coverage
make clean-coverage       # remove .coverage, coverage.xml and htmlcov/
```

`make test` fails when total coverage drops below `COVERAGE_MIN` (65% by default), so a
regression breaks the build instead of passing silently. Raise the floor as coverage grows,
or override it for a single run:

```bash
make test COVERAGE_MIN=80
```

Coverage is measured over `sincpro_framework` with branch coverage enabled; the settings
live in `[tool.coverage.*]` in `pyproject.toml`. All generated artifacts
(`.coverage`, `coverage.xml`, `htmlcov/`) are git-ignored.

## 🧵 Python 3.14 & Free-Threading Notes

**Regular Python 3.14 (GIL build): fully supported today, no breaking changes.**
Verified end-to-end on 3.14.7 — every dependency (core and all extras: `opentelemetry`,
`sentry`, `mcp`, `rpc`) installs from prebuilt wheels with no source builds, `pyright`
reports 0 issues, and the full test suite passes (215/215). CI (`.github/workflows/02-check_code.yaml`)
now runs `"3.12"`, `"3.13"`, `"3.14"`.

**Free-threaded Python (`python3.14t`, PEP 703/779): not implemented or targeted yet — this
section documents current findings only, so the challenges are visible before anyone
attempts it.** Everything below was verified hands-on (3.14.7 vs. 3.14.7t), not inferred:

- **Dependency gap, not a code gap.** `dependency-injector` (core, required — see `ioc.py`)
  and `grpcio` (transitive dep of the optional `opentelemetry-exporter-otlp-proto-grpc`)
  have no free-threaded wheel yet. Importing either on `python3.14t` makes CPython print
  `RuntimeWarning: The global interpreter lock (GIL) has been enabled to load module
  '...', which has not declared that it can run safely without the GIL` and silently
  re-enable the GIL for the rest of the process. `pydantic-core` (the framework's other
  compiled dependency) is fine — it already ships a `cp314t` wheel.
- **`contextvars` propagation to a bare `ThreadPoolExecutor.submit` differs by build.**
  Verified with a minimal repro outside this framework: on 3.12.11 and 3.14.7 (GIL builds)
  a bare `executor.submit(fn)` loses the caller's `contextvars.Context`, same as always —
  this is the bug `ThreadContextBus`/`thread_context()` exists to fix. On 3.14.7t
  (free-threaded), it's already propagated with no `thread_context()` involved. This
  doesn't make `thread_context()` wrong or unnecessary — most users run a GIL build, and
  code shouldn't silently depend on a free-threaded-only behavior — but it does mean
  `tests/test_thread_context_bus.py::test_bare_submit_loses_context_in_new_thread` and
  `::test_fan_out_without_thread_context_loses_context_for_every_worker` encode a
  GIL-build-specific assumption and would legitimately fail if that suite is ever run on a
  free-threaded interpreter.
- **`asyncio`'s free-threading support only matured in 3.14.** Relevant to `get_async_bus()`
  / `AsyncBus`, which is built on `asyncio.to_thread`: prefer 3.14+ over 3.13t for that path.
- Registries built once at startup (`feature_registry`, `app_service_registry`,
  `dynamic_dep_registry`, the interceptor chains) are safe as long as nothing mutates them
  concurrently with in-flight executions — true today, not enforced. See the docstring on
  `UseFramework.add_dependency`.

Every spot above is also marked in the source with a `# PYTHON 3.14 FREE-THREADING:` comment
— `grep -rn "PYTHON 3.14 FREE-THREADING" sincpro_framework/ tests/` finds all of them.
