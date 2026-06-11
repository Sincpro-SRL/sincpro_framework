# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
make install

# Run all tests
make test

# Run tests with verbose output
make test_debug

# Run a single test file or test function
make test_one t=tests/path/to/test_file.py::test_function_name

# Format code (autoflake + isort + black + yamllint)
make format

# Type check with pyright
make lint

# Pre-commit hook runs format + lint; CI uses:
make verify-format
```

Python version: 3.12+. Package manager: Poetry (local venv at `.venv/`).

## Architecture

Sincpro Framework implements **Hexagonal Architecture + CQRS** via a three-layer bus system. The central idea: all business logic is invoked by dispatching a DTO through a single entry point (`UseFramework.__call__`), which routes to the correct handler by matching `dto.__class__.__name__` against a registry.

### Layers

1. **`FeatureBus`** (`bus.py`) — atomic operations. Each `Feature` handles exactly one DTO type.
2. **`ApplicationServiceBus`** (`bus.py`) — orchestration. Each `ApplicationService` can call Features internally via its injected `feature_bus`.
3. **`FrameworkBus`** (`bus.py`) — facade. Routes a DTO to `FeatureBus` first, then `ApplicationServiceBus`; raises `DTOAlreadyRegistered` if the same DTO is in both.

### Entry point: `UseFramework` (`use_bus.py`)

`UseFramework` is both the configuration API and the callable dispatcher. It holds the IoC container, middleware pipeline, and error-handler chains. The bus is built lazily on the first `__call__` (or explicitly via `build_root_bus()`). Features and ApplicationServices are registered **at import time** via decorators, so they must be imported before the first execution.

```python
framework = UseFramework("my-context")
framework.add_dependency("db", MyDatabase())

@framework.feature(MyDTO)
class MyFeature(Feature):
    db: MyDatabase  # injected attribute
    def execute(self, dto: MyDTO) -> MyResponse:
        ...

result = framework(MyDTO(field="value"), MyResponse)
```

### IoC / Dependency Injection (`ioc.py`)

Uses `dependency-injector` library. `FrameworkContainer` holds `Singleton` bus instances and `Dict` registries. The decorators `@framework.feature(DTO)` and `@framework.app_service(DTO)` mutate the container's registries at import time. `framework.add_dependency(name, instance)` registers named dependencies that are set as attributes on every Feature and ApplicationService instance before execution.

### Registration rule

A DTO class name must be unique across the entire `UseFramework` instance — it cannot be registered as both a Feature and an ApplicationService. To handle the same DTO in two different ways, create separate `UseFramework` instances (one per bounded context).

### Context propagation (`context/`)

`framework.context({"key": "val"})` returns a context manager (`FrameworkContext`). On `__enter__` it merges the new dict over the existing context, sets it on the `UseFramework` instance, and immediately pushes it to all registered Feature/ApplicationService instances as `self.context`. On `__exit__` the parent context is restored.

```python
with framework.context({"correlation_id": "abc"}) as fw:
    result = fw(MyDTO(...))
```

### Error handlers (`error_handler.py`)

Three scopes: global, feature-level, app-service-level. Each scope holds an ordered list of `Callable[[Exception], Any]`. Handlers are composed into a chain: re-raising passes to the next handler; returning a value short-circuits. Registered via `add_global_error_handler`, `add_feature_error_handler`, `add_app_service_error_handler`.

### Middleware (`middleware.py`)

`Middleware` is a Protocol: `(dto) -> dto`. Added via `framework.add_middleware(fn)`. The pipeline runs before execution; if a middleware returns a different DTO class, the class is monkey-patched back to the original for registry lookup.

### Configuration (`sincpro_conf.py`)

`SincproConfig` subclasses `pydantic.BaseModel`. YAML values starting with `$ENV:VAR_NAME` are resolved from environment variables at validation time. Use `build_config_obj(MyConfigClass, path, sub_key=None)` to load a config file.

### DDD Value Objects (`ddd/value_object.py`)

`ValueObject(base_type, validate_fn, name)` creates a subclass of a primitive (int, str, etc.) with optional validation. Fully Pydantic-compatible. `new_value_object()` is the deprecated API — use `ValueObject()` instead.

### Auto-documentation (`generate_documentation/`)

`DocumentationService` (in `generate_documentation/service.py`) introspects the framework's registered Features and ApplicationServices and generates MkDocs Markdown + YAML. Used to produce the `sincpro_framework_ai_guide.json` file (symlinked at root).

## Key files

| File | Role |
|---|---|
| `sincpro_framework/use_bus.py` | `UseFramework` — main API |
| `sincpro_framework/bus.py` | `FeatureBus`, `ApplicationServiceBus`, `FrameworkBus` |
| `sincpro_framework/ioc.py` | IoC container + `@feature`/`@app_service` decorator logic |
| `sincpro_framework/sincpro_abstractions.py` | `DataTransferObject`, `Feature`, `ApplicationService`, `Bus` base classes |
| `sincpro_framework/middleware.py` | `Middleware` protocol + `MiddlewarePipeline` |
| `sincpro_framework/error_handler.py` | `ErrorHandler` type + chain builder |
| `sincpro_framework/context/` | `FrameworkContext` context manager + `ContextMixin` |
| `sincpro_framework/sincpro_conf.py` | YAML config loader + `SincproConfig` base |
| `sincpro_framework/ddd/value_object.py` | `ValueObject` factory |
| `tests/use_container/test_use_framework.py` | Canonical end-to-end usage example |
