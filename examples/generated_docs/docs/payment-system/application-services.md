# 🏢 Application Services

Business logic and application layer services.

## 📋 Overview

This framework includes **1** application services
that handle business logic and orchestrate domain operations.

---

## 💼 Available Services

### MakeTransaction

**Module:** `dynamic.MakeTransaction`

Second layer of the framework, orchestration of features

**Methods:**

#### execute

```python
(self, dto: MakeTransactionCommand) -> MakeTransactionResponse
```

Execute transaction processing
Args:
    dto (MakeTransactionCommand): Data transfer object containing transaction details
Returns:
    MakeTransactionResponse: Response containing transaction details

---
