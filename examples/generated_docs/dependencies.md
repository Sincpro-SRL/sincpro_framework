# Dependencies

Dependency injection system components and API.

## Dependency Functions

### check_health

**Module:** `__main__`

```python
()
```

Health check function for the framework


## Dependency Classes

### Database

**Module:** `__main__`

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


### VisaAdapter

**Module:** `__main__`

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

