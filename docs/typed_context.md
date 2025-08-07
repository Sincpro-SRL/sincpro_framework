# Typed Context Support in Sincpro Framework

This document describes the new typed context functionality that provides type safety and IDE support for context access in Features and ApplicationServices.

## Overview

The Sincpro Framework now supports typed context access through TypedDict definitions and mixin classes. This improvement addresses the need for developers to know what context keys are available and their types, providing better IDE support and type safety.

## Key Features

- ✅ **Type safety** for known context keys using TypedDict
- ✅ **IDE autocompletion** and hints for context access
- ✅ **Helper methods** for common access patterns
- ✅ **Full backward compatibility** with existing dict-based context
- ✅ **Support for both Feature and ApplicationService** patterns
- ✅ **Mixin-based approach** for clean inheritance patterns

## Basic Usage

### 1. Define Your Context Structure

First, define your context keys using TypedDict:

```python
from typing_extensions import NotRequired, TypedDict
from sincpro_framework import ContextTypeMixin

class KnownContextKeys(TypedDict, total=False):
    """Define your context structure with types"""
    TOKEN: NotRequired[str]
    USER_ID: NotRequired[str]
    CORRELATION_ID: NotRequired[str]
    ENVIRONMENT: NotRequired[str]
```

### 2. Create a Context Type Mixin

Create a mixin class that provides typed context access:

```python
class MyContextType(ContextTypeMixin):
    """Context type mixin that provides typed context access"""
    context: KnownContextKeys  # This gives IDE support

    def get_token(self) -> str | None:
        """Helper method to get token with proper typing"""
        return self.context.get("TOKEN")

    def get_user_id(self) -> str | None:
        """Helper method to get user ID with proper typing"""
        return self.context.get("USER_ID")
```

### 3. Use in Features and ApplicationServices

Apply the mixin to get typed context access:

```python
from sincpro_framework import Feature, ApplicationService

# For Features
class MyFeature(Feature, MyContextType):
    def execute(self, dto: MyInputDTO) -> MyResponseDTO:
        # Type-safe context access
        token = self.get_token()  # Returns str | None
        user_id = self.context.get("USER_ID")  # IDE knows this is str | None
        
        # Use the context in your business logic
        return MyResponseDTO(token=token, user_id=user_id)

# For ApplicationServices  
class MyApplicationService(ApplicationService, MyContextType):
    def execute(self, dto: MyInputDTO) -> MyResponseDTO:
        # Same type-safe access patterns
        correlation_id = self.get_context_value("CORRELATION_ID", "generated")
        
        # Execute features with context
        result = self.feature_bus.execute(SomeFeatureDTO())
        return MyResponseDTO(result=result)
```

## Advanced Patterns

### Enhanced Context Methods

All Features and ApplicationServices now have enhanced context methods:

```python
class MyFeature(Feature):
    def execute(self, dto: MyInputDTO) -> MyResponseDTO:
        # Enhanced methods available on all Features/ApplicationServices
        token = self.get_context_value("TOKEN", "default_token")
        has_user = self.has_context_key("USER_ID")
        
        if has_user:
            user_id = self.get_context_value("USER_ID")
            # Process with user context
        
        return MyResponseDTO(token=token)
```

### The Original Issue Pattern

The implementation supports the exact pattern described in the original issue:

```python
# From the original issue
class KnownContextKeys(TypedDict, total=False):
    """Known context keys with their types"""
    TOKEN: NotRequired[str]
    SIAT_ENV: NotRequired[SIATEnvironment]

class DependencyContextType(ContextTypeMixin):
    proxy_siat: ProxySiatServices
    context: KnownContextKeys

    def soap_client(self, wsdl: str) -> Client:
        """Helper function"""
        if self.context.get("TOKEN") and self.context.get("SIAT_ENV"):
            return self.proxy_siat.get_client_for_service(
                wsdl, self.context["TOKEN"], self.context["SIAT_ENV"]
            )
        return self.proxy_siat.get_client_for_service(wsdl)

class Feature(Feature, DependencyContextType):
    pass

class ApplicationService(ApplicationService, DependencyContextType):
    pass
```

### TypedContext Wrapper

For advanced use cases, you can use the TypedContext wrapper directly:

```python
from sincpro_framework import TypedContext, create_typed_context

# Create a typed context wrapper
context_dict = {"TOKEN": "abc123", "USER_ID": "admin"}
typed_context = create_typed_context(context_dict)

# Use with full dict interface but with typing support
token = typed_context.get("TOKEN")  # Typed access
typed_context["NEW_KEY"] = "value"  # Supports all dict operations
```

## Migration Guide

### For Existing Code

The new typing system is fully backward compatible. Existing code continues to work without changes:

```python
# This still works exactly as before
class LegacyFeature(Feature):
    def execute(self, dto: MyInputDTO) -> MyResponseDTO:
        token = self.context.get("TOKEN", "default") if self.context else "default"
        return MyResponseDTO(token=token)
```

### Adding Types to Existing Features

To add typing to existing features, simply add the mixin:

```python
# Before
class MyFeature(Feature):
    def execute(self, dto: MyInputDTO) -> MyResponseDTO:
        token = self.context.get("TOKEN")
        return MyResponseDTO(token=token)

# After - just add the mixin
class MyFeature(Feature, MyContextType):
    def execute(self, dto: MyInputDTO) -> MyResponseDTO:
        token = self.get_token()  # Now with type safety
        return MyResponseDTO(token=token)
```

## Best Practices

### 1. Define Context Keys Centrally

Create a single place for your context definitions:

```python
# contexts.py
class BaseContextKeys(TypedDict, total=False):
    """Base context keys used across the application"""
    CORRELATION_ID: NotRequired[str]
    USER_ID: NotRequired[str]
    TENANT_ID: NotRequired[str]

class AuthContextKeys(BaseContextKeys, total=False):
    """Authentication-specific context keys"""
    TOKEN: NotRequired[str]
    REFRESH_TOKEN: NotRequired[str]
    EXPIRES_AT: NotRequired[str]
```

### 2. Create Reusable Context Mixins

Build reusable mixins for common patterns:

```python
class AuthContextMixin(ContextTypeMixin):
    """Reusable authentication context mixin"""
    context: AuthContextKeys
    
    def get_current_user_id(self) -> str | None:
        return self.context.get("USER_ID")
    
    def is_authenticated(self) -> bool:
        return self.context.get("TOKEN") is not None
    
    def get_tenant_id(self) -> str | None:
        return self.context.get("TENANT_ID")
```

### 3. Document Your Context Keys

Always document what each context key represents:

```python
class PaymentContextKeys(TypedDict, total=False):
    """Context keys for payment processing"""
    PAYMENT_TOKEN: NotRequired[str]      # Payment gateway token
    MERCHANT_ID: NotRequired[str]        # Merchant identifier
    PAYMENT_METHOD: NotRequired[str]     # Credit card, debit, etc.
    TRANSACTION_ID: NotRequired[str]     # Unique transaction identifier
```

## API Reference

### TypedContext Class

```python
class TypedContext(Generic[TContext]):
    def get(self, key: str, default: Any = None) -> Any
    def __getitem__(self, key: str) -> Any
    def __setitem__(self, key: str, value: Any) -> None
    def __contains__(self, key: str) -> bool
    def copy(self) -> Dict[str, Any]
    def update(self, other: Dict[str, Any]) -> None
    @property
    def raw_dict(self) -> Dict[str, Any]
```

### ContextTypeMixin Class

```python
class ContextTypeMixin:
    def get_context_value(self, key: str, default: Any = None) -> Any
    def has_context_key(self, key: str) -> bool
```

### Enhanced Feature/ApplicationService Methods

```python
class Feature:
    def get_context_value(self, key: str, default: Any = None) -> Any
    def has_context_key(self, key: str) -> bool

class ApplicationService:
    def get_context_value(self, key: str, default: Any = None) -> Any  
    def has_context_key(self, key: str) -> bool
```

## Examples

See `examples/typed_context_demo.py` for a comprehensive example demonstrating all the features of the typed context system.

## Type Checking

The typed context system works well with static type checkers like mypy and pyright. To get the best type checking experience:

1. Use TypedDict for context definitions
2. Apply context mixins to your Features and ApplicationServices
3. Use the helper methods for context access
4. Configure your IDE for Python type hints

This provides compile-time checking and excellent IDE support for context operations.