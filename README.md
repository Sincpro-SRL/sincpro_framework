# 🚀 Sincpro Framework: Application Layer Framework within Hexagonal Architecture

## ⚡ Quick Start

Here's a quick example to get you started with the Sincpro Framework:

### 🏁 Quick Example

```python
from sincpro_framework import UseFramework, Feature, DataTransferObject

# 1. Initialize the framework
framework = UseFramework("cybersource")

# 2. Add Dependencies (Example dependencies)
from sincpro_framework import Database

db = Database()
framework.add_dependency("db", db)

# 3. Error Handler (Optional)
framework.add_global_error_handler(lambda e: print(f"Error: {e}"))


# 4. Create a Use Case with DTOs
class GreetingParams(DataTransferObject):
    name: str


@framework.feature(GreetingParams)
class GreetingFeature(Feature):
    def execute(self, dto: GreetingParams) -> str:
        self.db.store(f"Greeting {dto.name}")
        return f"Hello, {dto.name}!"


# 5. Execute the Use Case
result = framework(GreetingParams(name="Alice"))
print(result)  # Hello, Alice!
```

That is the whole framework: a use case, its DTO, and a bus that executes it. Observability
comes with it — a span per DTO, errors reported, logs correlated — without configuring
anything.

When the same catalog has to be reachable from outside the process, an
[entrypoint](#entrypoints-exposing-the-bus) publishes it over a protocol without touching
the use case. That is transport, and it comes later.

Now you are ready to explore more complex use cases! 🚀

## 📑 Table of Contents

1. [Overview of Hexagonal Architecture](#overview-of-hexagonal-architecture)
    - [Key Layers of Hexagonal Architecture](#key-layers-of-hexagonal-architecture)
    - [Why Use a Unified Bus Pattern?](#why-use-a-unified-bus-pattern)
2. [Key Features of the Sincpro Framework](#key-features-of-the-sincpro-framework)
    - [DTO Validation with Pydantic](#dto-validation-with-pydantic)
    - [Dependency Injection](#dependency-injection)
    - [Inversion of Control (IoC)](#inversion-of-control-ioc)
    - [Context Manager for Metadata Propagation](#context-manager-for-metadata-propagation)
    - [Middleware System](#middleware-system)
    - [Error Handling at Different Levels](#error-handling-at-different-levels)
    - [Bus Pattern for Component Communication](#bus-pattern-for-component-communication)
    - [Decoupled Logic Execution](#decoupled-logic-execution)
    - [Application Service Orchestration](#application-service-orchestration)
    - [IDE Support with Typing](#ide-support-with-typing)
3. [Features vs. Application Service](#features-vs-application-service)
4. [Example Usage for a Payment Gateway](#example-usage-for-a-payment-gateway)
    - [Configuring the Framework](#configuring-the-framework)
    - [Best Practices for Imports](#best-practices-for-imports)
    - [Sample Configuration in `__init__.py`](#sample-configuration-in-__init__py)
5. [Recommended Infrastructure Structure](#recommended-infrastructure-structure)
    - [dependencies.py — Adapter Registration](#dependenciespy--adapter-registration)
    - [framework.py — Wiring with DependencyContextType](#frameworkpy--wiring-with-dependencycontexttype)
    - [\_\_init\_\_.py — Bootstrap the Bounded Context](#__init__py--bootstrap-the-bounded-context)
    - [Testing Dependency Consistency](#testing-dependency-consistency)
6. [Creating a Feature](#creating-a-feature)
7. [Creating an Application Service](#creating-an-application-service)
8. [Executing a Use Case](#executing-a-use-case)
9. [Summary](#summary)
10. [Middleware System](#middleware-system-1)
11. [Error Handling](#error-handling)
12. [Documentation](#-documentation)
13. [Entrypoints: exposing the bus](#entrypoints-exposing-the-bus) — transport, not domain
    - [MCP tools (`entrypoint_mcp`)](#mcp-tools-entrypoint_mcp)
    - [JSON-RPC (`entrypoint_rpc`)](#json-rpc-entrypoint_rpc)
14. [Observability](#observability) — tracing (OTLP) + errors (Sentry/GlitchTip)
15. [Configuration or settings](#configuration-or-settings)
16. [Variables](#variables)
17. [Tests & coverage](#tests--coverage)
18. [Python 3.14 & Free-Threading Notes](#python-314--free-threading-notes)

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

### 🧬 Middleware System

- Allows registering custom functions that run before every Feature or ApplicationService execution.
- Middleware execute **in order**: each one receives the DTO output from the previous step.
- Common uses: validation, authentication checks, data enrichment, and logging.
- Any middleware that raises an exception stops the pipeline immediately.

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

### `entrypoint_mcp`

See [Entrypoints](#entrypoints-exposing-the-bus) for the full section.

- One line publishes the bus as MCP tools: `build_mcp_server(instance).run()`.
- Features and ApplicationServices become typed tools. Docstrings are the LLM context.
- Domain code stays host-agnostic. This extra is MCP only.

### `entrypoint_rpc`

See [Entrypoints](#entrypoints-exposing-the-bus) for the full section.

- One process, several instances: `RpcGateway({"qr": qr, "cybersource": cybersource}).run()`.
- Methods are `qr.features.CommandCreateQREconomico` / `siat.app_services.CommandGenerateCUFD`.
- `context` on the JSON-RPC request is `framework.context` + optional `with_trace`. OpenRPC 1.4 discovery.

### Observability (tracing + errors)

- **Tracing** (optional): OpenTelemetry spans on every DTO, export via OTLP (`sincpro-framework[opentelemetry]` + `OTEL_EXPORTER_OTLP_ENDPOINT`).
- **Errors** (optional): Sentry/GlitchTip capture on bus exceptions (`sincpro-framework[sentry]` + `SENTRY_PYTHON_DSN` in conf). Isolated client — does not call `sentry_sdk.init()`, does not reuse Odoo's client.
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
every Feature and ApplicationService in this bounded context automatically inherits the typed
attributes.

```python
# apps/my_domain/infrastructure/framework.py
from sincpro_framework import ApplicationService as _ApplicationService
from sincpro_framework import DataTransferObject  # re-exported for convenience
from sincpro_framework import Feature as _Feature
from sincpro_framework import UseFramework

from .dependencies import DependencyContextType, register_dependencies


class Feature(_Feature, DependencyContextType):
    """Base Feature for this bounded context — typed deps included."""

    pass


class ApplicationService(_ApplicationService, DependencyContextType):
    """Base ApplicationService for this bounded context — typed deps included."""

    pass


def config_framework(name: str) -> UseFramework[DependencyContextType]:
    """Create and configure the framework instance."""
    instance = UseFramework[DependencyContextType](name)
    register_dependencies(instance)
    return instance
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

Assert every name declared on `DependencyContextType` is present on `framework.deps`. If a new
dependency is added to the typing class but forgotten in `register_dependencies`, this test
catches it without registering a Feature.

```python
# tests/my_domain/test_framework_setup.py
from my_sdk.apps.my_domain import my_framework
from my_sdk.apps.my_domain.infrastructure.dependencies import DependencyContextType


def test_declared_deps_are_registered():
    for dep_name in DependencyContextType.__annotations__:
        assert dep_name in my_framework.deps, f"Missing dep: {dep_name}"
```

**Why this matters:**

- Iterates `DependencyContextType.__annotations__` automatically — adding a new dependency to
  the context covers it in the test without any manual edits.
- Catches mismatches between what is declared in `DependencyContextType` and what is actually
  registered via `add_dependency`.

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

This structured approach ensures high-quality, maintainable software that can adapt to evolving business needs. 🚀

## 🧬 Middleware System

The Sincpro Framework provides a simple and flexible middleware system that lets you add custom processing logic **before** your Features and ApplicationServices are executed.

### Philosophy

The middleware system follows the framework's core principles:
- **Simple**: Middleware is just a function that processes DTOs.
- **Agnostic**: The framework doesn't dictate how you implement middleware.
- **Developer Control**: You have complete control over what your middleware does.

### How It Works

Middleware are plain functions that:
1. Receive a DTO as input.
2. Can validate, transform, or enhance the DTO.
3. Return the (possibly modified) DTO.
4. Can raise exceptions if validation fails.

```python
from typing import Any

def my_middleware(dto: Any) -> Any:
    """Simple middleware that validates or transforms a DTO."""
    if hasattr(dto, 'amount') and dto.amount <= 0:
        raise ValueError("Amount must be positive")
    return dto
```

### Usage

```python
from sincpro_framework import UseFramework

def validate_payment(dto):
    if hasattr(dto, 'amount') and dto.amount <= 0:
        raise ValueError("Amount must be positive")
    return dto

def add_timestamp(dto):
    import time
    if hasattr(dto, '__dict__'):
        dto.timestamp = time.time()
    return dto

framework = UseFramework("my_app")
framework.add_middleware(validate_payment)
framework.add_middleware(add_timestamp)

# All DTOs are processed by middleware before reaching the Feature/Service
result = framework(my_dto)
```

### Execution Order

Middleware execute **in the order they are added**:
1. First middleware processes the original DTO.
2. Second middleware processes the result from the first.
3. And so on…
4. Finally, your Feature or ApplicationService receives the fully processed DTO.

### Common Use Cases

#### Validation
```python
def validate_user_input(dto):
    if hasattr(dto, 'email') and '@' not in dto.email:
        raise ValueError("Invalid email format")
    return dto
```

#### Authentication
```python
def check_authentication(dto):
    if hasattr(dto, 'user_id') and not is_authenticated(dto.user_id):
        raise PermissionError("User not authenticated")
    return dto
```

#### Data Enrichment
```python
def enrich_user_data(dto):
    if hasattr(dto, 'user_id'):
        dto.user_profile = get_user_profile(dto.user_id)
    return dto
```

#### Logging
```python
import logging

def log_requests(dto):
    logging.info(f"Processing DTO: {type(dto).__name__}")
    return dto
```

### Error Handling

If any middleware raises an exception, the entire pipeline stops and the exception propagates to the caller:

```python
def strict_validation(dto):
    if not hasattr(dto, 'required_field'):
        raise ValueError("required_field is missing")
    return dto

framework.add_middleware(strict_validation)
result = framework(my_dto)  # Raises ValueError if required_field is missing
```

### Best Practices

1. **Keep it simple**: Each middleware should do one thing well.
2. **Fail fast**: Raise exceptions early when validation fails.
3. **Be safe**: Always check if attributes exist before accessing them.
4. **Return the DTO**: Always return the DTO (modified or unchanged).
5. **Don't break the chain**: Ensure your middleware doesn't silently swallow exceptions.

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

Hand-written architecture decisions stay in
[`docs/architecture/`](docs/architecture/) and remain authoritative: the wiki
references them, it does not replace them.

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

The bus reports **before** the error handler runs. A handler that swallows an unexpected exception still produces a GlitchTip event. Expected domain errors can be excluded per instance:

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

Register the provider once at startup, before any `framework(dto)` call:

```python
from sincpro_framework.tracing import setup_otlp_provider

setup_otlp_provider("payments-service")
```

The provider is a **process-level singleton** — only the first call registers it. Subsequent calls (e.g. from a second bounded context) are no-ops.

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

The framework comes with a module or component to allow us to create configuratio or settings based on files or
environment variables.
You need to inherit from `SincproConfig` from module `sincpro_framework.sincpro_conf`

```python
from sincpro_framework.sincpro_conf import SincproConfig


class PostgresConf(SincproConfig):
    host: str = "localhost"
    port: int = 5432
    user: str = "my_user"


class MyConfig(SincproConfig):
    log_level: str = "DEBUG"
    token: str = "defult_my_token"
    postgresql: PostgresConf = PostgresConf()

```

This class should be mapped based on yaml file like this, we have a feature to use ENV variables in the yaml file
using the prefix `$ENV:`

```yaml
log_level: "INFO"
token: "$ENV:MY_SECRET_TOKEN"
postgresql:
  host: localhost
  port: 12345
  user: custom_user
```

### Environment Variable Handling

When using `$ENV:` prefix in your configuration files, the framework will:

1. Look for the environment variable specified after `$ENV:`
2. If the environment variable exists, use its value
3. If the environment variable doesn't exist:
   - Use the default value defined in your configuration class
   - Issue a warning indicating that the environment variable is missing
   - Proceed with execution rather than raising an error

This behavior allows applications to run with partial configurations in development environments or when not all environment variables are available, while still logging the fallback at info level.

Example of fallback to default values:

```python
# Configuration class with default
class ApiConfig(SincproConfig):
    api_key: str = "dev_default_key"  # Default value as fallback

# In config.yml
api_key: "$ENV:API_KEY"  # References environment variable

# If API_KEY environment variable is not set, the framework will:
# 1. Log info: "Environment variable [API_KEY] is not set for field [api_key]. Using default value: dev_default_key"
# 2. Use the default value "dev_default_key"
# 3. Continue execution without error
```

Then you can use the config object in your code where it will be loaded all the settings from the yaml file
for that you will require use the following funciton `build_config_obj`

```python
from sincpro_framework.sincpro_conf import build_config_obj
from .my_config import MyConfig

config = build_config_obj(MyConfig, '/path/to/your/config.yml')

assert isinstance(config.log_level, str)
assert isinstance(config.postgresql, PostgresConf)
```

## 📦 Variables

The framework use a default setting file where live in the module folder inside of
`sincpro_framework/conf/sincpro_framework_conf.yml`
where you can define some behavior currently we support the following settings:

- `sincpro_framework_log_level`: Log level for the framework logger. Default: `DEBUG`.
- `otlp_endpoint`: OTLP exporter endpoint for distributed tracing. Resolved from `OTEL_EXPORTER_OTLP_ENDPOINT` env var. Default: `null` (tracing disabled). Requires `sincpro-framework[opentelemetry]`.
- `sincpro_framework_log_level`: `INFO` or `DEBUG`. Resolved from `SINCPRO_FRAMEWORK_LOG_LEVEL`. Default: `DEBUG`. A service sets its own level through the environment — never by assigning into the framework's settings, which also ran too late because the logger is configured at import time.
- `otlp_traces_sample_rate`: share of new traces to record, `0.0`-`1.0`. Resolved from `OTEL_TRACES_SAMPLER_ARG`. Default: `1.0`. An upstream sampling decision always wins over this ratio.
- `app_release`: deployed artifact and version, `artifact:version`. Resolved from `APP_RELEASE` — the standard on every Sincpro service. Feeds both the GlitchTip release and the OTel `service.name`.
- `otel_service_name`: names the artifact when `APP_RELEASE` carries only a version. Resolved from `OTEL_SERVICE_NAME`.
- `tenant`: GlitchTip `environment` and the `tenant` tag. Resolved from `TENANT`.
- `sentry_dsn`: GlitchTip/Sentry DSN. Resolved from `SENTRY_PYTHON_DSN`. Default: `null` (error reporting disabled). Requires `sentry-sdk` (or `sincpro-framework[sentry]`). The framework uses an isolated client with `release=APP_RELEASE` and never calls `sentry_sdk.init()`. Odoo may capture the same error separately. Use `UseFramework.ignore_sentry_exceptions(...)` for expected errors.

Override the config file using another

```bash
export SINCPRO_FRAMEWORK_CONFIG_FILE = /path/to/your/config.yml
```

## 🧪 Tests & coverage

The `Makefile` is the single entry point — CI calls the same targets you run locally.

```bash
make test                 # test suite + coverage report in the terminal + coverage.xml
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
  `dynamic_dep_registry`, the middleware list) are safe as long as nothing mutates them
  concurrently with in-flight executions — true today, not enforced. See the docstring on
  `UseFramework.add_dependency`.

Every spot above is also marked in the source with a `# PYTHON 3.14 FREE-THREADING:` comment
— `grep -rn "PYTHON 3.14 FREE-THREADING" sincpro_framework/ tests/` finds all of them.
