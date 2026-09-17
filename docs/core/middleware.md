# Middleware System

The Sincpro Framework provides a simple and flexible middleware system that allows you to add custom processing logic before your Features and ApplicationServices are executed.

## Philosophy

The middleware system follows the framework's core principles:
- **Simple**: Middleware is just a function that processes DTOs
- **Agnostic**: The framework doesn't dictate how you implement middleware
- **Developer Control**: You have complete control over what your middleware does

## How It Works

Middleware are simple functions that:
1. Receive a DTO as input
2. Can validate, transform, or enhance the DTO
3. Return the (possibly modified) DTO
4. Can raise exceptions if validation fails

```python
from typing import Any

def my_middleware(dto: Any) -> Any:
    """Simple middleware that validates or transforms a DTO"""
    # Your custom logic here
    if hasattr(dto, 'amount') and dto.amount <= 0:
        raise ValueError("Amount must be positive")
    
    # Return the DTO (modified or unchanged)
    return dto
```

## Usage Example

```python
from sincpro_framework import UseFramework

# Create your middleware function
def validate_payment(dto):
    if hasattr(dto, 'amount') and dto.amount <= 0:
        raise ValueError("Amount must be positive")
    return dto

def add_timestamp(dto):
    if hasattr(dto, '__dict__'):
        dto.timestamp = time.time()
    return dto

# Add middleware to your framework
framework = UseFramework("my_app")
framework.add_middleware(validate_payment)
framework.add_middleware(add_timestamp)

# Now all DTOs will be processed by your middleware first
result = framework(my_dto)
```

## Middleware Execution Order

Middleware execute in the order they are added:
1. First middleware processes the original DTO
2. Second middleware processes the result from first middleware
3. And so on...
4. Finally, your Feature or ApplicationService receives the processed DTO

## Common Use Cases

### Validation
```python
def validate_user_input(dto):
    if hasattr(dto, 'email') and '@' not in dto.email:
        raise ValueError("Invalid email format")
    return dto
```

### Authentication
```python
def check_authentication(dto):
    if hasattr(dto, 'user_id') and not is_authenticated(dto.user_id):
        raise PermissionError("User not authenticated")
    return dto
```

### Data Enrichment
```python
def enrich_user_data(dto):
    if hasattr(dto, 'user_id'):
        dto.user_profile = get_user_profile(dto.user_id)
    return dto
```

### Logging
```python
import logging

def log_requests(dto):
    logging.info(f"Processing DTO: {type(dto).__name__}")
    return dto
```

## Best Practices

1. **Keep It Simple**: Middleware should do one thing well
2. **Fail Fast**: Raise exceptions early if validation fails
3. **Be Safe**: Always check if attributes exist before accessing them
4. **Return the DTO**: Always return the DTO (modified or unchanged)
5. **Don't Break the Chain**: Ensure your middleware doesn't prevent other middleware from running

## Error Handling

If any middleware raises an exception, the entire pipeline stops and the exception is propagated to the caller:

```python
def strict_validation(dto):
    if not hasattr(dto, 'required_field'):
        raise ValueError("required_field is missing")
    return dto

# This will raise ValueError if required_field is missing
framework.add_middleware(strict_validation)
result = framework(my_dto)  # May raise ValueError
```

This simple approach gives you complete control over how you want to process your DTOs while maintaining the framework's philosophy of simplicity and developer control.