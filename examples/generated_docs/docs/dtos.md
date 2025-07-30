# 📋 DTOs (Data Transfer Objects)

!!! info "Data Models Overview"
    Data Transfer Objects provide type-safe data validation, serialization, and schema generation.
    All DTOs are built using Pydantic for automatic validation and documentation.

## 📋 Overview

This framework includes **2** Pydantic models that provide:

### ✨ Key Features

=== "🔒 Validation"

    - **Automatic Data Validation** - Input data is validated automatically against schemas
    - **Type Checking** - Runtime type validation with descriptive error messages
    - **Custom Validators** - Support for custom validation logic and constraints

=== "🔄 Serialization"

    - **JSON Serialization** - Easy conversion to/from JSON format
    - **Dictionary Support** - Convert to/from Python dictionaries
    - **Custom Serializers** - Support for complex data type serialization

=== "📖 Documentation"

    - **JSON Schema** - Auto-generated schemas for API documentation
    - **Type Hints** - Full type hints and IDE support
    - **Field Descriptions** - Comprehensive field documentation

=== "🔧 Development"

    - **IDE Support** - Full autocomplete and type checking
    - **Testing** - Easy to test with predictable validation
    - **Debugging** - Clear error messages and field information

---

## 📊 Data Models

The following **2** data models are available in this framework:

### 📊 ProductCreateCommand

!!! info "Module Information"
    **Module:** `tmp.generate_example_docs`

!!! note "Description"
    Command to create a new product in the catalog

#### 🔧 Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | `str` | ✅ Yes | *No default* | None |
| `description` | `str` | ✅ Yes | *No default* | None |
| `price` | `float` | ✅ Yes | *No default* | None |
| `category` | `str` | ✅ Yes | *No default* | None |
| `in_stock` | `bool` | ❌ No | `True` | None |


??? example "Usage Example"
    ```python
    from your_module import ProductCreateCommand

    # Create an instance of ProductCreateCommand
    dto = ProductCreateCommand(
        name="example_value",
        description="example_value",
        price=123.45,
        category="example_value",
        in_stock=True
    )

    # Access fields
    print(dto.name)

    # Convert to dictionary
    dto_dict = dto.model_dump()

    # Create from dictionary
    dto_from_dict = ProductCreateCommand.model_validate(dto_dict)
    ```

### 📊 UserCreateCommand

!!! info "Module Information"
    **Module:** `tmp.generate_example_docs`

!!! note "Description"
    Command to create a new user account with validation rules

#### 🔧 Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `username` | `str` | ✅ Yes | *No default* | None |
| `email` | `str` | ✅ Yes | *No default* | None |
| `full_name` | `str` | ✅ Yes | *No default* | None |
| `age` | `int` | ❌ No | `18` | None |
| `is_admin` | `bool` | ❌ No | `False` | None |


??? example "Usage Example"
    ```python
    from your_module import UserCreateCommand

    # Create an instance of UserCreateCommand
    dto = UserCreateCommand(
        username="example_value",
        email="example_value",
        full_name="example_value",
        age=123,
        is_admin=True
    )

    # Access fields
    print(dto.username)

    # Convert to dictionary
    dto_dict = dto.model_dump()

    # Create from dictionary
    dto_from_dict = UserCreateCommand.model_validate(dto_dict)
    ```

---

## 📚 General Usage Guide

### Creating DTO Instances

```python
# Create from keyword arguments
dto = YourDTO(field1='value1', field2='value2')

# Create from dictionary
data = {'field1': 'value1', 'field2': 'value2'}
dto = YourDTO.model_validate(data)

# Create from JSON string
json_data = '{"field1": "value1", "field2": "value2"}'
dto = YourDTO.model_validate_json(json_data)
```

### Working with DTOs

```python
# Access fields
print(dto.field1)
print(dto.field2)

# Convert to dictionary
dto_dict = dto.model_dump()

# Convert to JSON
dto_json = dto.model_dump_json()

# Get JSON schema
schema = YourDTO.model_json_schema()
```

### Validation and Error Handling

```python
from pydantic import ValidationError

try:
    dto = YourDTO(invalid_field='value')
except ValidationError as e:
    print(f'Validation errors: {e.errors()}')
    for error in e.errors():
        print(f'Field: {error["loc"]}, Error: {error["msg"]}')
```
