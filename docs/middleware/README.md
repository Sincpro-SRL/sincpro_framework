# 🔧 Sincpro Framework Middleware System

The Sincpro Framework now includes a powerful middleware system that allows you to add cross-cutting concerns like validation, authorization, and caching to your application without modifying existing business logic.

## ✨ Key Features

- **🚀 No Breaking Changes**: Existing code works without modification
- **🔧 Easy Integration**: Add middleware with a single method call
- **⚡ Priority-Based Execution**: Control middleware execution order
- **🛡️ Comprehensive Error Handling**: Middleware can handle and recover from errors
- **🎯 Type-Safe**: Full type hints and IDE support

## 🧩 Available Middleware

### ValidationMiddleware
Add business rule validation beyond Pydantic validation.

```python
from sincpro_framework.middleware import ValidationMiddleware, ValidationRule

validation = ValidationMiddleware(strict_mode=True)
validation.add_validation_rule(
    "PaymentDTO",
    ValidationRule(
        name="positive_amount",
        validator=lambda dto: dto.amount > 0,
        error_message="Payment amount must be positive"
    )
)
framework.add_middleware(validation)
```

### AuthorizationMiddleware (ABAC)
Implement Attribute-Based Access Control with flexible policies.

```python
from sincpro_framework.middleware import (
    AuthorizationMiddleware, AuthorizationPolicy, 
    PermissionAction, has_role, owns_resource
)

auth = AuthorizationMiddleware()
auth.set_user_context_provider(lambda dto: get_user_context(dto.user_id))

policy = AuthorizationPolicy(
    name="payment_access",
    resource="payment",
    action=PermissionAction.CREATE,
    conditions=[has_role("payment_processor"), owns_resource()]
)
auth.add_policy("PaymentDTO", policy)
framework.add_middleware(auth)
```

### CachingMiddleware
Add intelligent caching with TTL and tag-based invalidation.

```python
from sincpro_framework.middleware import (
    CachingMiddleware, CacheConfig, InMemoryCacheProvider
)

cache = CachingMiddleware(InMemoryCacheProvider())
cache.configure_caching(
    "UserProfileDTO",
    CacheConfig(
        ttl_seconds=300,
        cache_key_generator=lambda dto: f"profile:{dto.user_id}",
        invalidation_tags=["user_profiles"]
    )
)
framework.add_middleware(cache)
```

## 🚀 Quick Start

```python
from sincpro_framework import UseFramework, Feature, DataTransferObject
from sincpro_framework.middleware import ValidationMiddleware, ValidationRule

# Create framework
framework = UseFramework("my_app")

# Add validation middleware
validation = ValidationMiddleware()
validation.add_validation_rule(
    "PaymentDTO", 
    ValidationRule(
        "positive_amount", 
        lambda dto: dto.amount > 0, 
        "Amount must be positive"
    )
)
framework.add_middleware(validation)

# Define your DTOs and Features as usual
class PaymentDTO(DataTransferObject):
    amount: float
    user_id: str

@framework.feature(PaymentDTO)
class ProcessPaymentFeature(Feature):
    def execute(self, dto: PaymentDTO):
        # Business logic here - validation happens automatically
        return {"status": "success", "amount": dto.amount}

# Use the framework - middleware runs automatically
result = framework(PaymentDTO(amount=100.0, user_id="user123"))
```

## 🔄 Middleware Pipeline

Middleware executes in priority order:

1. **Pre-execution**: All middleware run in priority order (lower numbers first)
2. **Business Logic**: Your Feature or ApplicationService executes
3. **Post-execution**: All middleware run in reverse priority order
4. **Error Handling**: If any step fails, middleware can handle the error

```python
# Priority examples (lower = higher priority)
validation_middleware = ValidationMiddleware()  # priority=10
auth_middleware = AuthorizationMiddleware()     # priority=20  
cache_middleware = CachingMiddleware(...)       # priority=30

# Execution order: validation → auth → cache → business logic → cache → auth → validation
```

## 🎛️ Advanced Features

### Conditional Middleware
```python
# Only cache for specific conditions
cache_config = CacheConfig(
    ttl_seconds=300,
    cache_condition=lambda ctx: ctx.dto.user_id.startswith("premium_")
)
```

### Error Recovery
```python
class ErrorHandlingMiddleware(BaseMiddleware):
    def on_error(self, context, error):
        if isinstance(error, ValidationError):
            return {"error": "validation_failed", "details": str(error)}
        return None  # Re-raise other errors
```

### Dynamic Control
```python
# Disable middleware for testing
framework.disable_middleware()
result = framework(dto)  # Runs without middleware

# Re-enable
framework.enable_middleware()
```

## 📁 Examples

- **Basic Example**: `examples/middleware_example.py`
- **Complete System**: `examples/complete_middleware_example.py`
- **ApplicationServices**: `examples/application_service_middleware_example.py`

## 🧪 Testing

The middleware system includes comprehensive tests:
- 47 middleware-specific tests
- Full integration tests with UseFramework
- Backwards compatibility tests (all existing tests still pass)

```bash
# Run middleware tests
python -m pytest tests/middleware/ -v

# Run all tests
python -m pytest tests/ -v
```

## 🔧 Creating Custom Middleware

```python
from sincpro_framework.middleware import BaseMiddleware, MiddlewareContext

class CustomMiddleware(BaseMiddleware):
    def __init__(self):
        super().__init__("custom", priority=50)
    
    def pre_execute(self, context: MiddlewareContext) -> MiddlewareContext:
        # Run before business logic
        print(f"Processing: {type(context.dto).__name__}")
        context.add_metadata("start_time", time.time())
        return context
    
    def post_execute(self, context: MiddlewareContext, result) -> any:
        # Run after business logic
        duration = time.time() - context.get_metadata("start_time")
        print(f"Completed in {duration:.2f}s")
        return result
    
    def on_error(self, context: MiddlewareContext, error: Exception):
        # Handle errors
        print(f"Error occurred: {error}")
        return None  # Re-raise error

# Add to framework
framework.add_middleware(CustomMiddleware())
```

## 📋 Best Practices

1. **Use appropriate priorities**: Validation (10), Authorization (20), Caching (30)
2. **Keep middleware focused**: Each middleware should handle one concern
3. **Handle errors gracefully**: Use `on_error` for recovery strategies
4. **Test thoroughly**: Ensure middleware works with all your DTOs
5. **Document policies**: Clearly document authorization policies and validation rules

## 🔄 Migration

**No migration needed!** The middleware system is fully backward compatible. Your existing code will work without any changes. Simply add middleware as needed.

The middleware system enhances the Sincpro Framework's existing capabilities while maintaining the clean, simple API you're already using.