# 📋 DTOs (Data Transfer Objects)

Data models including commands and responses for the framework.

## 📋 Overview

This framework includes **3** Pydantic models that provide
data validation, serialization, and type safety.

### ✨ Features

- **Automatic Validation** - Input data is validated automatically
- **Type Safety** - Full type hints and IDE support
- **JSON Schema** - Auto-generated schemas for API documentation
- **Serialization** - Easy conversion to/from JSON

---

## 📊 Data Models

### MakeTransactionCommand

**Module:** `dynamic.MakeTransactionCommand`

MakeTransactionCommand

**Fields:**

- `card_number` (<class 'str'>) - Required
  - Default: `PydanticUndefined`
- `amount` (<class 'float'>) - Required
  - Default: `PydanticUndefined`
- `merchant_id` (<class 'str'>) - Required
  - Default: `PydanticUndefined`

---

### ValidateCommand

**Module:** `dynamic.ValidateCommand`

ValidateCommand

**Fields:**

- `card_number` (<class 'str'>) - Required
  - Default: `PydanticUndefined`
- `cvv` (<class 'str'>) - Required
  - Default: `PydanticUndefined`

---

### PaymentCommand

**Module:** `dynamic.PaymentCommand`

PaymentCommand

**Fields:**

- `card_number` (<class 'str'>) - Required
  - Default: `PydanticUndefined`
- `amount` (<class 'float'>) - Required
  - Default: `PydanticUndefined`
- `merchant_id` (<class 'str'>) - Required
  - Default: `PydanticUndefined`

---
