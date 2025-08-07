# Simple Typed Context Support

This document describes the simple typed context support added to the Sincpro Framework. This implementation provides type safety for context access without complex mixins or helper methods.

## Overview

The framework now supports generic typing for context, allowing developers to define their expected context structure using TypedDict and get proper IDE support and type checking.

## Key Features

- ✅ **Simple typing approach** - no mixins or helper methods needed
- ✅ **TypedDict integration** for defining context structure
- ✅ **Full backward compatibility** with existing dict-based context
- ✅ **IDE support** with autocompletion and type hints
- ✅ **Works with both Features and ApplicationServices**

## Usage

### 1. Define Your Context Structure

```python
from typing_extensions import NotRequired, TypedDict

class MyContextKeys(TypedDict, total=False):
    """Define your context structure with types"""
    TOKEN: NotRequired[str]
    USER_ID: NotRequired[str]
    CORRELATION_ID: NotRequired[str]
```

### 2. Initialize Framework with Context Type

```python
from sincpro_framework import UseFramework

# Initialize framework with typed context
framework: UseFramework[MyContextKeys] = UseFramework()
```

### 3. Use in Features and ApplicationServices

```python
from sincpro_framework import Feature, ApplicationService, DataTransferObject

class MyRequestDTO(DataTransferObject):
    operation: str

class MyResponseDTO(DataTransferObject):
    result: str
    token: str = ""

@framework.feature(MyRequestDTO)
class MyFeature(Feature[MyRequestDTO, MyResponseDTO]):
    def execute(self, dto: MyRequestDTO) -> MyResponseDTO:
        # IDE knows these return str | None based on TypedDict
        token = self.context.get("TOKEN")
        user_id = self.context.get("USER_ID")
        
        # Still works with any key for backward compatibility
        correlation_id = self.context.get("CORRELATION_ID", "default")
        
        return MyResponseDTO(
            result=f"Processed {dto.operation}",
            token=token or "no-token"
        )
```

### 4. Using with Context

```python
# Use with context data
with framework.context({
    "TOKEN": "abc123",
    "USER_ID": "user456",
    "CORRELATION_ID": "req789"
}) as fw:
    request = MyRequestDTO(operation="create")
    response = fw(request, MyResponseDTO)
    print(f"Response: {response}")
```

## Benefits

1. **Type Safety**: TypedDict provides compile-time type checking for known context keys
2. **IDE Support**: Autocompletion and type hints for context access
3. **Backward Compatibility**: Existing code continues to work without changes
4. **Simplicity**: No complex mixins or inheritance patterns needed
5. **Flexibility**: Can access both typed and untyped context keys

## Backward Compatibility

The implementation is fully backward compatible:

```python
# This still works without type parameters
framework = UseFramework()

@framework.feature(MyRequestDTO)
class OldStyleFeature(Feature):
    def execute(self, dto: MyRequestDTO) -> MyResponseDTO:
        # Works exactly as before
        token = self.context.get("TOKEN")
        return MyResponseDTO(result="works")
```

## Type Checking

When using a TypedDict context, type checkers (like mypy, pyright) will:

- Provide autocompletion for known context keys
- Type `self.context.get("TOKEN")` as `str | None`
- Show warnings for typos in context key names
- Provide better refactoring support

This gives developers confidence when working with context while maintaining the flexibility of the original dict-based approach.