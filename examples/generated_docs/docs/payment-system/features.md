# ⚡ Features

Framework features and capabilities.

## 📋 Overview

This framework includes **3** feature components that provide
core functionality and capabilities.

---

## 🎯 Available Features

### ValidateFeature

**Module:** `dynamic.ValidateFeature`

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

### RefundFeature

**Module:** `dynamic.RefundFeature`

Feature to handle refund processing

**Methods:**

#### execute

```python
(self, dto: RefundCommand) -> RefundResponse
```

Execute refund processing
Args:
    dto (RefundCommand): Data transfer object containing refund details
Returns:
    RefundResponse: Response containing refund details

---

### PaymentFeature

**Module:** `dynamic.PaymentFeature`

Feature to handle payment processing

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
