# ⚡ Features

Framework features and capabilities.

## 📋 Overview

This framework includes **2** feature components that provide
core functionality and capabilities.

---

## 🎯 Available Features

### ValidateFeatureSincpro

**Module:** `dynamic.ValidateFeatureSincpro`

Feature to validate payment card details

**Methods:**

#### execute

```python
(self, dto: ValidateCommand) -> ValidateResponse
```

Execute card validation
Args:
    dto (ValidateCommand): Data transfer object containing card details
Returns:
    ValidateResponse: Response indicating if the card is valid

---

### PaymentFeatureSincpro

**Module:** `dynamic.PaymentFeatureSincpro`

Feature to handle payment processing in Sincpro Framework

**Methods:**

#### execute

```python
(self, dto: PaymentCommand) -> PaymentResponse
```

Execute payment processing
Args:
    dto (PaymentCommand): Data transfer object containing payment details
Returns:
    PaymentResponse: Response containing transaction details

---
