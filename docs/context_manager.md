# Context Manager for Sincpro Framework

The Context Manager provides automatic metadata propagation, scope encapsulation, and uniform error handling for the Sincpro Framework using Python's `contextvars` for thread-safe context storage.

## Key Features

### 1. Automatic Metadata Propagation
- Context attributes flow automatically to all Features and ApplicationServices within scope
- No need to manually pass correlation IDs, user info, or other metadata
- Supports complex distributed tracing scenarios

### 2. Thread-Safe Context Storage
- Uses Python's `contextvars` module for proper context isolation
- Works correctly with asyncio, threading, and concurrent operations
- No context leakage between different execution paths

### 3. Nested Context Support
- Supports nested contexts with override capabilities
- Inner contexts inherit outer context values
- Inner contexts can override specific values while preserving others

### 4. Context-Aware Error Handling
- Exceptions are automatically enriched with context information
- Provides better debugging and tracing capabilities
- Maintains context information in error logs

### 5. Validation & Configuration
- Configure allowed context keys for security
- Type validation for context values
- Length limits for keys and values
- Default attributes that are always present

## Quick Start

### 1. Configure the Context Manager

```python
from sincpro_framework import UseFramework

framework = UseFramework("my-service")

# Configure context manager with validation
framework.configure_context_manager(
    default_attrs={
        "service.name": "my-service",
        "service.version": "1.0.0"
    },
    allowed_keys={
        "correlation_id", "user.id", "session.id", 
        "feature_flag", "environment"
    },
    validate_types=True,
    max_key_length=50,
    max_value_length=1000
)
```

### 2. Use Context in Your Code

```python
# Basic usage
with framework.context({"correlation_id": "123", "user.id": "admin"}):
    result = framework(some_dto)
    # Context automatically propagated to all handlers

# Nested contexts with overrides
with framework.context({"correlation_id": "outer", "environment": "prod"}):
    with framework.context({"correlation_id": "inner"}):  # Override
        result = framework(dto)  # correlation_id="inner", environment="prod"
```

### 3. Access Context in Features and ApplicationServices

```python
class MyFeature(Feature):
    def execute(self, dto: MyDTO) -> MyResponse:
        # Get specific context value
        correlation_id = self.get_context_value("correlation_id", "unknown")
        user_id = self.get_context_value("user.id")
        
        # Get full context dictionary
        full_context = self.context
        
        # Use context in business logic
        logger.info(f"Processing request {correlation_id} for user {user_id}")
        
        return MyResponse(...)

class MyApplicationService(ApplicationService):
    def execute(self, dto: MyDTO) -> MyResponse:
        # ApplicationServices also have access to context
        session_id = self.get_context_value("session.id")
        
        # Execute features - context is automatically inherited
        feature_result = self.feature_bus.execute(FeatureDTO(...))
        
        return MyResponse(...)
```

## Advanced Usage

### Context Validation

```python
# Configure strict validation
framework.configure_context_manager(
    allowed_keys={"correlation_id", "user.id"},  # Only these keys allowed
    validate_types=True,  # Validate value types
    max_key_length=20,    # Max key length
    max_value_length=100  # Max string value length
)

# This will raise ValueError due to validation
with framework.context({"invalid_key": "value"}):  # Not in allowed_keys
    pass

with framework.context({"correlation_id": "x" * 200}):  # Too long
    pass
```

### Error Handling with Context

```python
try:
    with framework.context({"correlation_id": "error-test", "user.id": "admin"}):
        result = framework(dto_that_causes_error)
except Exception as e:
    # Exception is automatically enriched with context
    if hasattr(e, 'context_info'):
        correlation_id = e.context_info["context_data"]["correlation_id"]
        user_id = e.context_info["context_data"]["user.id"]
        timestamp = e.context_info["timestamp"]
        print(f"Error {e} occurred at {timestamp} for user {user_id}")
```

### Accessing Context from Anywhere

```python
from sincpro_framework import get_current_context

# Get current context from anywhere in your code
current_context = get_current_context()
correlation_id = current_context.get("correlation_id")
```

### Thread Safety

```python
import threading
from concurrent.futures import ThreadPoolExecutor

def worker(thread_id):
    with framework.context({"thread_id": thread_id}):
        # Each thread has its own isolated context
        result = framework(SomeDTO(data=f"thread-{thread_id}"))
        return result

# Run multiple threads - contexts are properly isolated
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = [executor.submit(worker, i) for i in range(5)]
    results = [f.result() for f in futures]
```

## Context Configuration Options

### ContextConfig Parameters

- **`default_attrs`**: Dictionary of attributes always present in context
- **`allowed_keys`**: Set of allowed context keys (None = all keys allowed)
- **`validate_types`**: Whether to validate context value types
- **`max_key_length`**: Maximum length for context keys
- **`max_value_length`**: Maximum length for string context values

### Example Configuration

```python
framework.configure_context_manager(
    default_attrs={
        "service.name": "payment-service",
        "service.version": "2.1.0",
        "environment": "production"
    },
    allowed_keys={
        "correlation_id",
        "user.id", 
        "session.id",
        "request.id",
        "feature_flag",
        "service.name",
        "service.version",
        "environment"
    },
    validate_types=True,
    max_key_length=50,
    max_value_length=500
)
```

## Best Practices

### 1. Context Key Naming
- Use descriptive, hierarchical names: `user.id`, `service.name`
- Be consistent across your application
- Include namespace to avoid conflicts

### 2. Context Scope Management
- Keep context scope as narrow as possible
- Use nested contexts for temporary overrides
- Clean up context when exiting scope (automatic with `with` statement)

### 3. Error Handling
- Always check for context enrichment in exception handlers
- Log context information for debugging
- Include correlation IDs in all log messages

### 4. Performance Considerations
- Context lookup is fast but not free - cache values if used frequently
- Avoid storing large objects in context
- Use validation judiciously in high-performance scenarios

### 5. Security
- Use `allowed_keys` to prevent injection of unwanted context
- Validate sensitive context values
- Don't store secrets in context - use proper secret management

## Integration with Distributed Systems

The context manager is designed to work seamlessly with distributed tracing systems:

```python
# HTTP Middleware example (conceptual)
class ContextMiddleware:
    def __init__(self, framework):
        self.framework = framework
    
    def process_request(self, request):
        # Extract context from HTTP headers
        context_attrs = {
            "correlation_id": request.headers.get("X-Correlation-ID"),
            "user.id": request.headers.get("X-User-ID"),
            "trace.id": request.headers.get("X-Trace-ID")
        }
        
        # Execute request within context
        with self.framework.context(context_attrs):
            return self.framework(request_dto)
```

## Migration Guide

### From Manual Context Passing

**Before:**
```python
class MyFeature(Feature):
    def execute(self, dto: MyDTO) -> MyResponse:
        correlation_id = dto.correlation_id  # Manual passing
        logger.info(f"Processing {correlation_id}")
        return MyResponse(...)
```

**After:**
```python
class MyFeature(Feature):
    def execute(self, dto: MyDTO) -> MyResponse:
        correlation_id = self.get_context_value("correlation_id")  # Automatic
        logger.info(f"Processing {correlation_id}")
        return MyResponse(...)
```

### Integration Steps

1. **Configure the context manager** in your framework initialization
2. **Remove manual context passing** from DTOs
3. **Update handlers** to use `self.get_context_value()` and `self.context`
4. **Add context scope** around your main execution flows
5. **Update error handling** to use enriched exception information

## Examples

See `examples/context_manager_demo.py` for a complete working example demonstrating all features of the context manager.