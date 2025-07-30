# ⚡ Features

!!! info "Framework Features Overview"
    Features are the core business logic components that process commands and provide
    the main functionality of your application. Each feature implements a specific capability.

## 📋 Overview

This framework includes **2** feature components that provide
core functionality and capabilities.

### ✨ What are Features?

=== "🎯 Purpose"

    Features represent discrete business capabilities that:
    
    - Process specific commands or requests
    - Implement business logic and rules
    - Return structured responses
    - Maintain single responsibility principle

=== "🔄 Execution Pattern"

    ```python
    # Standard feature execution pattern
    feature = SomeFeature()
    command = SomeCommand(param='value')
    result = feature.execute(command)
    ```

=== "🏗️ Architecture"

    - **Command-Response Pattern** - Each feature processes a command and returns a response
    - **Type Safety** - Strong typing with Pydantic models for validation
    - **Testability** - Easy to unit test and mock
    - **Composability** - Features can be combined and orchestrated

---

## 🎯 Available Features

The following **2** features are available in this framework:

### 🏗️ ProductCreateFeature

!!! info "Class Information"
    **Module:** `tmp.generate_example_docs`
    **Type:** Class

!!! note "Description"
    Feature to handle product catalog management and creation

#### 🔧 Methods

##### `execute`

```python
(self, dto: ProductCreateCommand) -> ProductResponse
```

!!! note "Method Description"
    Execute product creation in the catalog system

Args:
    dto (ProductCreateCommand): Command with product details
    
Returns:
    ProductResponse: Created product information

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `self` | `Any` | *No description provided* |
| `dto` | `<class ProductCreateCommand>` | *No description provided* |


---

??? example "Usage Example"
    ```python
    from your_module import ProductCreateFeature

    # Create an instance of ProductCreateFeature
    instance = ProductCreateFeature()

    # Call a method
    result = instance.execute()
    print(result)
    ```

### 🏗️ UserCreateFeature

!!! info "Class Information"
    **Module:** `tmp.generate_example_docs`
    **Type:** Class

!!! note "Description"
    Feature to handle user creation with comprehensive validation and processing

#### 🔧 Methods

##### `execute`

```python
(self, dto: UserCreateCommand) -> UserCreatedResponse
```

!!! note "Method Description"
    Execute user creation with full validation

This method processes user creation requests, validates input data,
and returns a comprehensive response with user details.

Args:
    dto (UserCreateCommand): Command containing user registration details
    
Returns:
    UserCreatedResponse: Response with created user information and status
    
Raises:
    ValidationError: If user data is invalid
    DuplicateUserError: If username or email already exists

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `self` | `Any` | *No description provided* |
| `dto` | `<class UserCreateCommand>` | *No description provided* |


---

??? example "Usage Example"
    ```python
    from your_module import UserCreateFeature

    # Create an instance of UserCreateFeature
    instance = UserCreateFeature()

    # Call a method
    result = instance.execute()
    print(result)
    ```

---

## 📚 Usage Guide

### Implementing a Feature

```python
from sincpro_framework import Feature
from your_dtos import YourCommand, YourResponse

class YourFeature(Feature):
    """Your feature description"""
    
    def execute(self, dto: YourCommand) -> YourResponse:
        # Implement your business logic here
        result = self.process_command(dto)
        return YourResponse(success=True, data=result)
        
    def process_command(self, dto: YourCommand):
        # Your processing logic
        pass
```

### Using Features in Your Application

```python
# Direct usage
feature = YourFeature()
command = YourCommand(param='value')
response = feature.execute(command)
print(response.data)

# Through framework
framework = UseFramework('my_app')
response = framework(command)  # Auto-routes to appropriate feature
```

### Testing Features

```python
import pytest

def test_your_feature():
    feature = YourFeature()
    command = YourCommand(param='test_value')
    
    response = feature.execute(command)
    
    assert response.success is True
    assert response.data == expected_result
```
