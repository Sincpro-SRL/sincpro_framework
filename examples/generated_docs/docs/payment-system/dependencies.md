# 🔌 Dependencies

Dependency injection system components and API.

## 📋 Overview

This framework includes **3** dependency components:

- **1** dependency functions
- **2** dependency classes

---

## ⚙️ Dependency Functions

Functions available through the dependency injection system.

### check_health

**Module:** `ipykernel_164104.875856287`

```python
()
```

Health check function for the framework

---

## 🏗️ Dependency Classes

Classes available through the dependency injection system.

### Database

**Module:** `dynamic.Database`

Database class to handle transactions

**Methods:**

#### get_transaction

```python
(self, tx_id)
```

#### save_transaction

```python
(self, data)
```

---

### VisaAdapter

**Module:** `dynamic.VisaAdapter`

Adapter for Visa payment processing

**Methods:**

#### check_status

```python
(self, tx_id)
```

#### make_request

```python
(self, card_data)
```

---
