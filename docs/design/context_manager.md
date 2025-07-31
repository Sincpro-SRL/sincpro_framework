# Context Manager for Sincpro Framework

The Context Manager provides automatic metadata propagation and scope management for the Sincpro Framework using Python's `contextvars` for thread-safe context storage.

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

## Quick Start

### 1. Simple Context Usage

```python
from sincpro_framework import UseFramework

app = UseFramework("my-service")

# Use context with the new simplified API
with app.context({"correlation_id": "123", "user.id": "admin"}) as app_with_context:
    result = app_with_context(some_dto)
```
### 2. Nested Contexts

```python
# Nested contexts with overrides
with app.context({"correlation_id": "outer", "environment": "prod"}) as outer_app:
    with outer_app.context({"correlation_id": "inner"}) as inner_app:  # Override
        result = inner_app(dto)  # correlation_id="inner", environment="prod"
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

### Multiple Executions in Same Context

```python
# Context persists across multiple executions
with app.context({"correlation_id": "batch-001", "batch_id": "B123"}) as app_with_context:
    result1 = app_with_context(dto1)
    result2 = app_with_context(dto2)
    # Both executions share the same context
```

### Error Handling with Context

```python
try:
    with app.context({"correlation_id": "error-test", "user.id": "admin"}) as app_with_context:
        result = app_with_context(dto_that_causes_error)
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
    app = UseFramework(f"worker-{thread_id}")
    with app.context({"thread_id": thread_id}) as app_with_context:
        # Each thread has its own isolated context
        result = app_with_context(SomeDTO(data=f"thread-{thread_id}"))
        return result

# Run multiple threads - contexts are properly isolated
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = [executor.submit(worker, i) for i in range(5)]
    results = [f.result() for f in futures]
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
- Keep context data simple and lightweight

## Integration with Distributed Systems

The context manager is designed to work seamlessly with distributed tracing systems:

```python
# HTTP Middleware example (conceptual)
class ContextMiddleware:
    def __init__(self, app):
        self.app = app
    
    def process_request(self, request):
        # Extract context from HTTP headers
        context_attrs = {
            "correlation_id": request.headers.get("X-Correlation-ID"),
            "user.id": request.headers.get("X-User-ID"),
            "trace.id": request.headers.get("X-Trace-ID")
        }
        
        # Execute request within context
        with self.app.context(context_attrs) as app_with_context:
            return app_with_context(request_dto)
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

1. **Remove manual context passing** from DTOs
2. **Update handlers** to use `self.get_context_value()` and `self.context`
3. **Add context scope** around your main execution flows using `with app.context({}) as app_with_context:`
4. **Update error handling** to use enriched exception information

## Examples

See `examples/context_manager_demo.py` for a complete working example demonstrating all features of the context manager.